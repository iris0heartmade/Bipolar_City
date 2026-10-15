"""V2.1 阶段 2：居民群体（Cohort）数据类。

【2026-10-15】Bipole 城市聚合经济系统理论说明书 V2.1 §1.4 + §14.2 + §6.4。

V1.0 时期按收入分群（高/中/低 3 群）已封存到 `backend/systems_v1_reference/`。
V2.1 重构为按行业归属的 h=0..7 八群体：

  h=0 未明确归属   (P=70,  E=0)   # 失业/未分类居民
  h=1 AGR 居民     (P=480, E=240) # 农业
  h=2 IND 居民     (P=1120,E=610) # 工业
  h=3 SRV 居民     (P=1050,E=530) # 服务业
  h=4 CHU 居民     (P=40,  E=20)  # 教会
  h=5 BNK 居民     (P=45,  E=20)  # 银行
  h=6 MAG 居民     (P=90,  E=30)  # 魔法学院
  h=7 GOV 居民     (P=105, E=50)  # 公务员

【关键约束】（V2.1 §1.4 + §14.1 + §14.2）
- sum_h P_h = N = 3,000
- sum_i E_i = E = 1,500
- 严禁 P_h == E_h：群体中可有失业居民
- 失业总量 150，不要求 h=0 群体恰好 150
- 期初群体储蓄合计 300,000 CR，按收入等级/人数分配，**务必逐群体非负**

【PIT 规则】（V2.1 §6.4）
- 通用 PIT 税率 8%
- 农业（AGR）工资免 PIT；其它收入不免
"""
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from backend.systems.v21.industries import IndustryId


class CohortIndustry(int, Enum):
    """V2.1 §1.4 居民归属群体 h=0..7。

    h=0 表示未明确归属行业的居民；h=1..7 与 IndustryId 1:1 对应
    （h=1 → AGR、h=2 → IND、...、h=7 → GOV）。
    """
    NONE = 0       # h=0
    AGR = 1         # h=1
    IND = 2         # h=2
    SRV = 3         # h=3
    CHU = 4         # h=4
    BNK = 5         # h=5
    MAG = 6         # h=6
    GOV = 7         # h=7

    @property
    def industry_id(self) -> Optional[IndustryId]:
        """h=0 返回 None；h=1..7 返回对应 IndustryId。"""
        if self == CohortIndustry.NONE:
            return None
        return IndustryId(self.name)


@dataclass(frozen=True)
class CohortDefinition:
    """群体静态定义（V2.1 §14.2 工业新城基准）。"""
    cohort_id: CohortIndustry
    name_cn: str
    industry_id: Optional[IndustryId]   # h=0 为 None；h=1..7 为对应行业
    population_P_h: int                # 期初人数 P_h
    employees_E_h: int                 # 期初在职 E_h
    notes: str = ''

    @property
    def non_employed(self) -> int:
        """非就业人数 = P_h - E_h。包含失业与劳动力外人口。"""
        return self.population_P_h - self.employees_E_h

    @property
    def has_unemployed(self) -> bool:
        """群体中是否有失业/非就业居民。"""
        return self.non_employed > 0


# V2.1 §14.2 八群体标准定义（工业新城首月）
INDUSTRIAL_CITY_COHORT_DEFINITIONS: dict[CohortIndustry, CohortDefinition] = {
    CohortIndustry.NONE: CohortDefinition(
        cohort_id=CohortIndustry.NONE, name_cn='未明确归属',
        industry_id=None, population_P_h=70, employees_E_h=0,
        notes='失业/未分类居民（V2.1 §1.4：失业总量 150，不要求 h=0 恰好 150）',
    ),
    CohortIndustry.AGR: CohortDefinition(
        cohort_id=CohortIndustry.AGR, name_cn='AGR 居民',
        industry_id=IndustryId.AGR, population_P_h=480, employees_E_h=240,
        notes='农业；农业工资免 PIT（V2.1 §6.4）',
    ),
    CohortIndustry.IND: CohortDefinition(
        cohort_id=CohortIndustry.IND, name_cn='IND 居民',
        industry_id=IndustryId.IND, population_P_h=1_120, employees_E_h=610,
    ),
    CohortIndustry.SRV: CohortDefinition(
        cohort_id=CohortIndustry.SRV, name_cn='SRV 居民',
        industry_id=IndustryId.SRV, population_P_h=1_050, employees_E_h=530,
    ),
    CohortIndustry.CHU: CohortDefinition(
        cohort_id=CohortIndustry.CHU, name_cn='CHU 居民',
        industry_id=IndustryId.CHU, population_P_h=40, employees_E_h=20,
    ),
    CohortIndustry.BNK: CohortDefinition(
        cohort_id=CohortIndustry.BNK, name_cn='BNK 居民',
        industry_id=IndustryId.BNK, population_P_h=45, employees_E_h=20,
    ),
    CohortIndustry.MAG: CohortDefinition(
        cohort_id=CohortIndustry.MAG, name_cn='MAG 居民',
        industry_id=IndustryId.MAG, population_P_h=90, employees_E_h=30,
    ),
    CohortIndustry.GOV: CohortDefinition(
        cohort_id=CohortIndustry.GOV, name_cn='GOV 居民',
        industry_id=IndustryId.GOV, population_P_h=105, employees_E_h=50,
    ),
}


# V2.1 §14.1 工业新城首月人口恒等式
TOTAL_POPULATION_N = 3_000
TOTAL_EMPLOYMENT_E = 1_500
TOTAL_LABOR_FORCE_F = 1_650     # floor(0.55 * 3000)
TOTAL_UNEMPLOYED_U = TOTAL_LABOR_FORCE_F - TOTAL_EMPLOYMENT_E  # 150
TOTAL_NON_LABOR_FORCE = TOTAL_POPULATION_N - TOTAL_LABOR_FORCE_F  # 1,350

# V2.1 §14.2 期初群体储蓄合计
TOTAL_INITIAL_SAVINGS_CR = 300_000

# V2.1 §6.4 个人所得税
PERSONAL_INCOME_TAX_RATE = 0.08
AGRICULTURAL_WAGE_PIT_EXEMPT = True  # 农业工资免 PIT


def total_population() -> int:
    """sum_h P_h，工业新城首月 = 3,000。"""
    return sum(d.population_P_h for d in INDUSTRIAL_CITY_COHORT_DEFINITIONS.values())


def total_employment() -> int:
    """sum_i E_i，工业新城首月 = 1,500。"""
    return sum(d.employees_E_h for d in INDUSTRIAL_CITY_COHORT_DEFINITIONS.values())


def total_non_employed() -> int:
    """sum_h (P_h - E_h) = N - E = 3,000 - 1,500 = 1,500。"""
    return sum(d.non_employed for d in INDUSTRIAL_CITY_COHORT_DEFINITIONS.values())


def is_pit_exempt(industry_id: Optional[IndustryId], income_kind: str) -> bool:
    """判断某笔收入是否免 PIT（V2.1 §6.4）。

    income_kind: 'wage' / 'dividend' / 'property' / 'other'
    仅 AGR 行业工资免 PIT；其它收入不豁免。
    """
    if income_kind != 'wage':
        return False
    if industry_id != IndustryId.AGR:
        return False
    return AGRICULTURAL_WAGE_PIT_EXEMPT


def compute_pit_cents(
    taxable_income_cents: int,
    industry_id: Optional[IndustryId] = None,
    income_kind: str = 'wage',
) -> int:
    """计算 PIT（整数分）。

    仅 AGR 行业工资免 PIT；其它场景按 8% 通用税率。
    负数 taxable_income_cents 返回 0（亏损不退税）。
    """
    if taxable_income_cents <= 0:
        return 0
    if is_pit_exempt(industry_id, income_kind):
        return 0
    return int(round(taxable_income_cents * PERSONAL_INCOME_TAX_RATE))


def compute_pit_cr(
    taxable_income_cr: float,
    industry_id: Optional[IndustryId] = None,
    income_kind: str = 'wage',
) -> int:
    """计算 PIT（整数 CR）。通用 8%；仅 AGR 工资免。"""
    if is_pit_exempt(industry_id, income_kind):
        return 0
    if taxable_income_cr <= 0:
        return 0
    return int(round(taxable_income_cr * PERSONAL_INCOME_TAX_RATE))


def split_initial_savings(
    weights: dict[CohortIndustry, float],
) -> dict[CohortIndustry, int]:
    """按权重分配 TOTAL_INITIAL_SAVINGS_CR（300,000 CR）到各群体（整数 CR）。

    算法：最大余数法。每个群体取 floor(300000 * w / sum_w)；
    残留（向下取整损失）分配给权重最大的群体，确保 sum 等于 300,000。
    所有群体结果非负。
    """
    total_weight = sum(weights.values())
    if total_weight <= 0:
        raise ValueError("split_initial_savings: 权重之和必须为正")
    raw = {h: TOTAL_INITIAL_SAVINGS_CR * w / total_weight for h, w in weights.items()}
    result = {h: int(raw[h]) for h in weights}   # 向下取整
    residual = TOTAL_INITIAL_SAVINGS_CR - sum(result.values())
    if residual > 0:
        max_h = max(weights, key=lambda h: weights[h])
        result[max_h] += residual
    elif residual < 0:
        # 理论上不应发生（向下取整不可能超额）
        raise ValueError(f"split_initial_savings: 残留为负 {residual} CR")
    # 务必逐群体非负（V2.1 §14.2 强制）
    for h, v in result.items():
        if v < 0:
            raise ValueError(f"群体 {h.name} 储蓄为负：{v} CR")
    return result
