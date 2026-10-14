"""完整经济模型引擎：按参数装配、逐月演化、产出全部经济指标。

这一层是设计稿 §10「结算顺序」的完整实现，也是 GUI 调参工具的计算内核。

月度十五步
----------
严格按设计稿 §10 的顺序执行，每一步的输入输出都写明：

01 完工建设与上月结转          02 计算道路可达与供水供电
03 企业计划产能与岗位需求       04 劳动力匹配与招聘
05 家庭消费预算                 06 计算产能与供给量
07 分配消费、中间品、资本品     08 执行真实交易
09 支付工资、租金、规费         10 分红与房产收支
11 公共工资、税收、救济、捐赠   12 贷款利息与本金
13 结转收入、更新价格与工资     14 更新信仰、教育、人口
15 生成月报与会计断言

与既有引擎的关系
----------------
参考城（``ReferenceCity`` + ``simulate_month``）负责「首月基准必须逐分精确」；
本模块负责「长期演化会怎样」。两者共享同一套清算逻辑（``core/settlement.py``），
所以本模块跑出来的账同样守恒，残差恒为 0。
"""
from copy import deepcopy
from dataclasses import dataclass, field
from fractions import Fraction

from backend.core.errors import GameError
from backend.model.economy import cr_to_cents
from backend.model.parameters import DEFAULT_PARAMETERS, EconomyParameters

SECONDS_PER_MONTH = None   # 占位，明确本模型不做日内节律


# --- 数据结构 -----------------------------------------------------------

@dataclass
class SectorState:
    """一个行业的完整状态。"""

    sector_id: str
    name: str
    firm_count: int
    employees: int
    wage_per_employee_cents: int
    capacity_units: int          # K：满负荷产能（分）
    capital_units: int
    depreciation_cents: int
    fees_cents: int
    commercial_rent_cents: int

    # --- 本月快照 ---
    utilisation: Fraction = Fraction(0)
    revenue_cents: int = 0
    input_cost_cents: int = 0
    wage_bill_cents: int = 0
    profit_before_tax_cents: int = 0
    profit_tax_cents: int = 0
    dividend_cents: int = 0
    cash_cents: int = 0

    @property
    def profit_margin(self) -> Fraction:
        if self.revenue_cents <= 0:
            return Fraction(0)
        return Fraction(self.profit_before_tax_cents, self.revenue_cents)

    @property
    def cash_runway_months(self) -> float:
        """按本月固定支出算还能撑几个月。"""
        fixed = self.wage_bill_cents + self.commercial_rent_cents + self.fees_cents
        if fixed <= 0:
            return float("inf")
        return self.cash_cents / fixed


@dataclass
class CohortState:
    """一个收入层的状态。"""

    band_id: str
    name: str
    population: int
    households: int
    employed: int
    unemployed: int
    wage_income_cents: int = 0
    asset_income_cents: int = 0
    cash_cents: int = 0
    last_disposable_cents: int = 0
    consumption_ratio: float = 0.85
    basket: dict = field(default_factory=dict)
    property_tax_share: float = 0.3
    relief_share: float = 0.3
    skill_level: str = "basic"

    @property
    def gross_income_cents(self) -> int:
        return self.wage_income_cents + self.asset_income_cents

    @property
    def per_capita_cents(self) -> float:
        return self.gross_income_cents / self.population if self.population else 0.0


@dataclass
class ResourceState:
    """一种战略品的库存。"""

    resource_id: str
    name: str
    unit: str
    unit_price_cents: int
    stock: float
    produced: float = 0
    used: float = 0
    imported: float = 0
    exported: float = 0
    #: 参数给定的月度基准产量与全城用量，用于按产能比例分摊
    base_production: float = 0.0
    base_use: float = 0.0
    base_import: float = 0.0
    base_export: float = 0.0

    @property
    def closing(self) -> float:
        return self.stock + self.produced + self.imported - self.used - self.exported


@dataclass
class SimulationResult:
    """一个月的完整结果，既是月报也是 GUI 的数据源。"""

    month: int
    metrics: dict[str, float]
    sectors: list[dict]
    cohorts: list[dict]
    events: list[str]

    def get(self, key: str):
        return self.metrics.get(key)


# --- 装配 ---------------------------------------------------------------

def build_sectors(params: EconomyParameters) -> list[SectorState]:
    """按参数装配六行业。

    行业人数按就业人口与私企岗位占比分配，与参考城保持同一人均水平。
    """
    from backend.config import REFERENCE_CITY

    pop = params.section("population")
    city = params.section("city")

    sectors = []
    private_total = max(1, round(pop["employed"] * 0.92))
    reference = REFERENCE_CITY["firms"]["sectors"]
    weights = [s["employees"] for s in reference]
    total_weight = sum(weights)
    headcounts = [int(private_total * w / total_weight) for w in weights]
    headcounts[weights.index(max(weights))] += private_total - sum(headcounts)

    capacity = params.section("capacity")
    for row, headcount in zip(reference, headcounts):
        factor = headcount / row["employees"]
        revenue_cents = cr_to_cents(row["revenue"]) * factor
        # §4.2：K = 参考营业额 / 参考利用率
        capacity_units = int(round(revenue_cents / capacity["reference_utilisation"]))
        sectors.append(SectorState(
            sector_id=row["id"], name=row["name"],
            firm_count=max(1, round(row["firms"] * factor)),
            employees=headcount,
            wage_per_employee_cents=cr_to_cents(row["wage_per_employee"]),
            capacity_units=capacity_units,
            capital_units=max(1, round(row["revenue"] * factor)),
            depreciation_cents=int(cr_to_cents(row["depreciation"]) * factor),
            fees_cents=int(cr_to_cents(row["fees"]) * factor),
            commercial_rent_cents=int(cr_to_cents(row["commercial_rent"]) * factor),
            cash_cents=int(cr_to_cents(city["start_cash"]) * 0.02),
        ))
    return sectors


def build_cohorts(params: EconomyParameters, public_wage_cents: int = 0) -> list[CohortState]:
    """按参数装配三层收入层（沿用 P03 已验证的占比）。

    各层人数由人口恒等式推导，三层合计精确等于总人口；
    首月工资与资产收入按人口占比分企业工资总额与公共工资总额，
    合计等于全城工资，不多不少。
    """
    from backend.config import LABOR_CONSUMPTION

    pop = params.section("population")
    reference = LABOR_CONSUMPTION["income_bands"]

    weights = [b["population"] for b in reference]
    total_weight = sum(weights)
    populations = [int(pop["start"] * w / total_weight) for w in weights]
    populations[weights.index(max(weights))] += pop["start"] - sum(populations)

    hh_weights = [b["households"] for b in reference]
    hh_total = sum(hh_weights)
    households = [int(pop["households"] * w / hh_total) for w in hh_weights]
    households[hh_weights.index(max(hh_weights))] += pop["households"] - sum(households)

    emp_weights = [b["employed"] for b in reference]
    emp_w_sum = sum(emp_weights)
    employed = [int(pop["employed"] * w / emp_w_sum) for w in emp_weights]
    employed[emp_weights.index(max(emp_weights))] += pop["employed"] - sum(employed)

    private_wage = sum(s.wage_per_employee_cents * s.employees for s in build_sectors(params))
    total_wage = private_wage + public_wage_cents
    # 资产收入 = 企业分红 16000 + 净房产收入 21000 = 37000（设计稿 §2.5）。
    asset_total = cr_to_cents(params.get("household.asset_income_base", 37000))

    skill_order = ["basic", "skilled", "professional"]
    # 首月可支配收入：设计上第 1 个月居民还没领到工资，
    # 但设计稿 §2.5 给了一个基准可支配总额（157131 CR）。
    # 不给这个初值，`_household_budget` 首月会算出 0 预算，
    # 于是整座城市第一个月一单都开不了张（营业额只有出口那点）。
    opening_disposable = cr_to_cents(pop.get("opening_disposable_base", 157131))
    cohorts = []
    for index, row in enumerate(reference):
        share = populations[index] / (pop["start"] or 1)
        cohorts.append(CohortState(
            band_id=row["id"], name=row["name"],
            population=populations[index], households=households[index],
            employed=employed[index],
            unemployed=max(0, int(pop["unemployed"] * share)),
            wage_income_cents=int(total_wage * share),
            asset_income_cents=int(asset_total * share),
            last_disposable_cents=int(opening_disposable * share),
            consumption_ratio=row["spend_ratio"], basket=dict(row["basket"]),
            property_tax_share=row["property_tax_share"],
            relief_share=row["relief_share"],
            skill_level=skill_order[index] if index < len(skill_order) else "basic",
        ))
    return cohorts


def build_resources(params: EconomyParameters) -> list[ResourceState]:
    """按参数装配战略品库存。基准值来自设计稿 §8.2 的四项资源表。"""
    resources = []
    for row in params.section("resources")["items"]:
        resources.append(ResourceState(
            resource_id=row["id"], name=row["name"], unit=row["unit"],
            unit_price_cents=cr_to_cents(row["unit_price"]),
            stock=float(row["opening"]), produced=float(row["produced"]),
            used=float(row["used"]), imported=float(row["imported"]),
            exported=float(row["exported"]),
            base_production=float(row["produced"]), base_use=float(row["used"]),
            base_import=float(row["imported"]), base_export=float(row["exported"]),
        ))
    return resources


__all__ = ["CohortState", "ResourceState", "SectorState", "SimulationResult",
           "build_cohorts", "build_resources", "build_sectors"]