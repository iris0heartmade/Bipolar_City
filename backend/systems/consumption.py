"""P03 消费预算：由上期可支配收入决定本期支出，并受真实现金容量封顶。

核心纪律：
- 消费预算只能来自「上期已实现的收入」与「实际储蓄」，不能用尚未到账的收入预支
  （通俗比喻：不能拿下个月的工资牌去刷卡）。
- 本期预算再被「可动用容量」封顶：期初钱包 + 本期到手现金 − 本期必付现金
  （房租、奉献、所得税、房产税）。钱不够就是不够，不允许透支到下个月。
- 未花预算留在家庭钱包，不能当作商户收入。
- 首月沿用 P00 校准总额以对齐已验证的 +27100；第 2 月起才启用分层反馈。
"""
from dataclasses import dataclass, field
from typing import Sequence

from backend.config import LABOR_CONSUMPTION
from backend.model.economy import cr_to_cents
from backend.model.households import HouseholdRegistry

RESERVED_NEEDS = ("rent", "food", "fuel")


def allocate_by_share(total_cents: int, weights: Sequence[float]) -> list[int]:
    """把总额按权重拆成若干份，残差归入第一份，保证合计精确等于 total_cents。

    权重可以是「份额」（0.6/0.3/0.1）也可以是「户数」（375/262/113），
    都按权重和归一化后再分摊——直接拿权重当乘数会把 375 户读成 375 倍，
    这是分摊函数最容易被忽略的一个坑。

    浮点份额相加未必正好是 1（例如 0.6+0.3+0.1），所以绝不依赖权重和，
    而是让第一份吸收差额——聚合舍入残差必须归属明确，不能凭空少一分或多一分。
    """
    if len(weights) == 0:
        return []
    if any(weight < 0 for weight in weights):
        raise ValueError("分摊权重不能为负")
    if total_cents <= 0:
        return [0] * len(weights)
    weight_sum = sum(weights)
    if weight_sum <= 0:
        raise ValueError("分摊权重之和必须为正")
    parts = [int(total_cents * weight / weight_sum) for weight in weights]
    parts[0] += total_cents - sum(parts)
    return parts


def sync_cohort_cash(registry: HouseholdRegistry, household_cash_cents: int) -> None:
    """把家庭主钱包余额同步到各收入层，供消费预算判断「本月真实可动用多少钱」。

    主钱包只有一个聚合账户，这里按各层税前毛收入比例做预算视角的切分
    （规则见 labor_consumption_v1.json 的 cash_rules.wallet_split）。
    这不是现金转移：没有交易发生，全城现金一分不变。
    房东子账户在月度步骤 12b 已把净房产收入分配回主钱包、月末归零，
    所以这里只读主钱包就是完整的家庭可动用现金。

    全城毛收入归零时（全员失业或收入完全塌掉）改按户数平分：
    钱包里的钱是实实在在在那儿的，不能因为收入为零就说大家都没钱。
    """
    weights = [c.gross_income_cents for c in registry]
    if sum(weights) <= 0:
        weights = [c.households for c in registry]
    total = sum(weights)
    if total <= 0:
        for cohort in registry:
            cohort.cash_cents = 0
        return
    assigned = 0
    for cohort, weight in zip(list(registry), weights):
        cohort.cash_cents = int(household_cash_cents * weight / total)
        assigned += cohort.cash_cents
    registry.cohorts[0].cash_cents += household_cash_cents - assigned   # 残差归入第一层


@dataclass
class BandBudget:
    """单个收入层的本月支出计划，单位为整数分。"""

    band_id: str
    disposable_cents: int          # 上期可支配收入
    spend_cents: int               # 商品服务消费
    reserved_cents: int            # 先行预留（基本生活）
    inflow_cents: int              # 本期确定到手的现金收入
    mandatory_cents: int           # 本期必付现金（房租＋奉献＋两项税）
    capacity_cents: int            # 本期可动用容量
    basket_split: dict[str, int] = field(default_factory=dict)

    @property
    def saving_cents(self) -> int:
        """当期结余：可动用容量花完之后剩下的部分，留在家庭钱包。"""
        return self.capacity_cents - self.spend_cents


@dataclass
class ConsumptionPlan:
    """全城消费计划。"""

    month: int
    bands: list[BandBudget] = field(default_factory=list)
    calibrated: bool = False       # 是否走了首月校准缩放

    @property
    def total_spend_cents(self) -> int:
        return sum(b.spend_cents for b in self.bands)

    @property
    def total_disposable_cents(self) -> int:
        return sum(b.disposable_cents for b in self.bands)

    @property
    def total_inflow_cents(self) -> int:
        return sum(b.inflow_cents for b in self.bands)

    @property
    def total_mandatory_cents(self) -> int:
        return sum(b.mandatory_cents for b in self.bands)

    @property
    def total_capacity_cents(self) -> int:
        return sum(b.capacity_cents for b in self.bands)

    @property
    def total_saving_cents(self) -> int:
        return sum(b.saving_cents for b in self.bands)

    def band(self, band_id: str) -> BandBudget:
        return next(b for b in self.bands if b.band_id == band_id)


def _split_basket(spend_cents: int, basket: dict[str, float],
                  categories: list[str]) -> dict[str, int]:
    """按篮子比例拆分消费额，余数归入最后一项，合计必须等于总额。"""
    if spend_cents <= 0:
        return {name: 0 for name in categories}
    parts = {}
    allocated = 0
    for name in categories[:-1]:
        value = int(spend_cents * basket[name])
        parts[name] = value
        allocated += value
    parts[categories[-1]] = spend_cents - allocated      # 残差归属最后一项
    return parts


def _reserved_for_band(disposable: int, basket: dict[str, float]) -> int:
    """基本生活预留：食品与燃料按篮子比例估算，房租由住宅租金单独记账。"""
    return int(disposable * (basket.get("food", 0.0) + basket.get("fuel", 0.0)))


def build_plan(registry: HouseholdRegistry, month: int, *,
               household_cash_cents: int, relief_cents: int,
               residential_rent_cents: int, donation_cents: int,
               band_tax: dict) -> "ConsumptionPlan":
    """生成全城消费计划。

    本函数自洽：调用方只需给出家庭主钱包余额与本月各项市政/房产口径，
    钱包的分层同步与首月的「上期」引导都在内部完成，不留隐式前置条件。

    第 1 个月：对齐 P00 校准总额 119000，各层按原始意愿缩放到该总额；
    第 2 个月起：各层消费 = min(上期可支配收入 × spend_ratio, 可动用容量)，不再缩放，
    形成真实的收入—消费反馈。
    """
    config = LABOR_CONSUMPTION["consumption"]
    categories = LABOR_CONSUMPTION["basket_categories"]
    cohorts = list(registry)
    plan = ConsumptionPlan(month=month, calibrated=(month == 1))

    sync_cohort_cash(registry, household_cash_cents)

    # --- 分摊：救济与房租、奉献都按配置里的份额/户数拆到各层 ---
    relief_parts = allocate_by_share(relief_cents, [c.relief_share for c in cohorts])
    rent_parts = allocate_by_share(residential_rent_cents, [c.households for c in cohorts])
    donation_parts = allocate_by_share(donation_cents, [c.households for c in cohorts])

    # 首月没有「上期」可言：用期初现金近似上期可支配收入（校准项 A-01 的口径）。
    # 这条引导放在本函数里而不是调用方，是为了让 build_plan 单独调用也成立。
    bootstrapping = month == 1

    for index, cohort in enumerate(cohorts):
        inflow = cohort.wage_income_cents + cohort.asset_income_cents + relief_parts[index]
        mandatory = (band_tax["income_tax"][cohort.band_id]
                     + band_tax["property_tax"][cohort.band_id]
                     + rent_parts[index] + donation_parts[index])
        capacity = max(0, cohort.cash_cents + inflow - mandatory)

        # 可支配收入与可用现金都已包含救济：期初现金按 A-01 含救济，
        # 后续月份由 monthly._carry_over 用「收入−税+救济」更新，此处不得重复加算。
        available = max(0, cohort.cash_cents)
        disposable = available if bootstrapping \
            else min(cohort.last_disposable_cents, available)
        spend = min(int(disposable * cohort.spend_ratio), capacity)

        plan.bands.append(BandBudget(
            band_id=cohort.band_id,
            disposable_cents=disposable,
            spend_cents=spend,
            reserved_cents=_reserved_for_band(disposable, cohort.basket),
            inflow_cents=inflow,
            mandatory_cents=mandatory,
            capacity_cents=capacity,
            basket_split=_split_basket(spend, cohort.basket, categories),
        ))

    if plan.calibrated:
        target = cr_to_cents(config["month_1_reference_total"])
        _calibrate_to_total(plan, registry, target, categories)

    for band in plan.bands:
        if band.spend_cents > band.capacity_cents:
            raise ValueError(f"收入层 {band.band_id} 消费超过可动用容量："
                             f"{band.spend_cents} > {band.capacity_cents}")
    return plan


def _calibrate_to_total(plan: "ConsumptionPlan", registry: HouseholdRegistry,
                        target: int, categories: list[str]) -> None:
    """把首月消费缩放到校准总额，全整数运算，不引入浮点误差。

    首月总额是对齐 P02 已验证基准的锚点。若全城可动用容量本来就小于该总额，
    说明期初现金或必付支出配错了——直接报错，不允许偷偷少花把基准糊过去。
    """
    target_capacity = plan.total_capacity_cents
    if target > target_capacity:
        raise ValueError(
            f"首月校准总额 {target} 分 超过全城可动用容量 {target_capacity} 分，"
            f"请检查期初现金、房租与税收配置")
    wanted = plan.total_spend_cents
    if wanted <= 0:
        raise ValueError("首月各层消费意愿合计为零，无法缩放到校准总额")

    for band in plan.bands:
        band.spend_cents = band.spend_cents * target // wanted
    drift = target - plan.total_spend_cents
    biggest = max(plan.bands, key=lambda b: b.capacity_cents)
    biggest.spend_cents += drift
    if biggest.spend_cents > biggest.capacity_cents:
        raise ValueError(f"首月校准总额 {target} 超出收入层 {biggest.band_id} 的可动用容量 "
                         f"{biggest.capacity_cents}，请检查期初现金与必付支出配置")

    for band in plan.bands:
        band.basket_split = _split_basket(band.spend_cents,
                                          registry[band.band_id].basket, categories)