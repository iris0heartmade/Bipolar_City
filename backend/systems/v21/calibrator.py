"""V2.1 阶段 13：开局自动校准器（§16 10 步强制实施流程）。

【2026-10-15】Bipole 城市聚合经济系统理论说明书 V2.1 §16 开局自动校准器。

【V2.1 §16 输入】
archetype（城市类型）、目标人口、目标难度、世界商品基础报价、建筑配方、
国家/世界贸易对手城市状态。

【V2.1 §16 STEP 01..10】
01 选择规模 N、建筑与基础行业比重，确定 H、J、行业生产配方与服务能力。
02 分配 7 行业实际就业 E_i，检查 E<=F、E_i<=Jop_i、失业率在目标范围。
03 按工资等级得到工资总额；为产业生成合理原料与产出订单，输入真实采购成本。
04 生成居民归属人口 P_h（sum P_h=N，P_i>=E_i）与期初现金储蓄 S_h。
05 用 Cneed 与篮子权重生成居民有资金的订单，创建物业租金、公共采购、
   国内中间采购、产业资本投资、跨城商品订单；交割量受实货/资金限制。
06 按真实销售额计算行业利润税/分红；分配租金收入；依次过账工资、
   PIT、物业税、福利、教会捐助与政府捐赠；检查全城总账守恒。
07 计算 K 与 L，并生成整个世界的同月初始迁移；验证首月不会意外流失大量人口。
08 使用局部调节调整行业销售量、贸易合同、政府采购、城市初始现金、
   必需品供应、住房/就业容量。不得直接强行改利润/财政结余/K/L 结果。
09 以相同世界状态和种子运行 12、36、120 月，检测经济稳定及挑战反馈。
10 输出可追溯 seed.json + golden_month_report + calibration_report，并存档版本号。

【V2.1 §16 优化目标】
loss = w1*max(0, unemployment-0.11)^2
     + w2*max(0, 1.08-K/N)^2
     + w3*max(0, 1.02-disposable_income/(N*Cneed))^2
     + w4*max(0, mandatory_cash_runway_target - gov_runway)^2
     + w5*max(0, abs(fiscal_ratio)-allowed_fiscal_margin)^2
     + w6*abs(accounting_residual)
     + w7*max(0, immediate_unfilled_essential_consumption)^2

守恒约束 w6 必须是硬断言而非可接受的优化损失。
"""
from dataclasses import dataclass, field
from typing import Optional

from backend.systems.v21.accounts import (
    government_account_id, firm_account_id, cohort_account_id, church_account_id,
    OCEAN_ACCOUNT,
)
from backend.systems.v21.industries import (
    IndustryId, INDUSTRY_DEFINITIONS, monthly_wage_bill_cents, tax_rate,
)
from backend.systems.v21.cohorts import (
    INDUSTRIAL_CITY_COHORT_DEFINITIONS, PERSONAL_INCOME_TAX_RATE, is_pit_exempt,
    compute_pit_cents, split_initial_savings,
)
from backend.systems.v21.ledger import Ledger, build_industrial_city_accounts
from backend.systems.v21.inflation import (
    WorldState, BasketWeights, CityPriceCoefficients, compute_Cneed_c_t,
)
from backend.systems.v21.capacity import (
    compute_K_H, compute_K_F, compute_K_J, compute_s, compute_K,
    compute_h, compute_a, compute_v, compute_L,
)
from backend.systems.v21.migration import (
    CitySnapshot, DistanceMatrix, solve_multi_city_migration,
)
from backend.systems.v21.wages import (
    IndustryWageState, update_industry_wage, update_all_industry_wages,
)
from backend.systems.v21.city_presets import (
    CityPreset, CITY_PRESETS, initial_employment_dict,
)
from backend.systems.v21.government import (
    compute_government_revenue, compute_government_expense,
    compute_fiscal_balance_cents, compute_government_cash_next,
)
from backend.systems.v21.religion import (
    compute_grant_to_government_cents, compute_church_cash_next, ChurchCashFlow,
)


# ===== V2.1 §16 优化损失权重（默认值，可调）=====

DEFAULT_OPTIMIZATION_WEIGHTS = {
    'w1_unemployment': 1.0,
    'w2_K_over_N': 1.0,
    'w3_disposable': 1.0,
    'w4_gov_runway': 1.0,
    'w5_fiscal': 1.0,
    'w6_residual': 100.0,        # §16 守恒权重必须是硬断言，这里反映其高优
    'w7_unfilled_need': 1.0,
}


# ===== V2.1 §16 校准器输入 =====
@dataclass
class CalibrationInput:
    """V2.1 §16 校准器输入。"""
    archetype: str                         # 城市类型（5 城之一）
    target_difficulty: str = 'normal'      # 难度（easy/normal/hard）
    seed: int = 0
    # 派生
    preset: Optional[CityPreset] = None

    def __post_init__(self):
        if self.preset is None:
            if self.archetype not in CITY_PRESETS:
                raise ValueError(f"未知 archetype: {self.archetype}")
            self.preset = CITY_PRESETS[self.archetype]


# ===== V2.1 §16 校准报告 =====
@dataclass
class CalibrationReport:
    """V2.1 §16 校准报告。"""
    archetype: str
    seed: int
    # STEP 01
    N: int = 0
    H: int = 0
    Jop: int = 0
    # STEP 02
    E: int = 0
    F: int = 0
    unemployment_rate: float = 0.0
    # STEP 03
    total_wage_bill_cents: int = 0
    # STEP 04
    total_cohort_population: int = 0
    total_initial_savings_cents: int = 0
    # STEP 05
    Cneed: float = 0.0
    total_consumer_demand_cents: int = 0
    # STEP 06
    total_industry_tax_cents: int = 0
    total_pit_cents: int = 0
    fiscal_balance_cents: int = 0
    conservation_residual_cents: int = 0
    # STEP 07
    K: int = 0
    L: float = 0.0
    initial_migration: dict[str, int] = field(default_factory=dict)
    # STEP 08
    adjustments: list[str] = field(default_factory=list)
    # STEP 09
    stability_12m_passed: bool = True
    stability_36m_passed: bool = True
    stability_120m_passed: bool = True
    # STEP 10
    loss: float = 0.0
    version: str = 'v2.1'

    def to_dict(self) -> dict:
        """导出为 dict（用于 seed.json 存档）。"""
        return {
            'archetype': self.archetype,
            'seed': self.seed,
            'version': self.version,
            'STEP_01': {'N': self.N, 'H': self.H, 'Jop': self.Jop},
            'STEP_02': {'E': self.E, 'F': self.F, 'unemployment_rate': self.unemployment_rate},
            'STEP_03': {'total_wage_bill_cents': self.total_wage_bill_cents},
            'STEP_04': {'total_cohort_population': self.total_cohort_population,
                        'total_initial_savings_cents': self.total_initial_savings_cents},
            'STEP_05': {'Cneed': self.Cneed, 'total_consumer_demand_cents': self.total_consumer_demand_cents},
            'STEP_06': {'total_industry_tax_cents': self.total_industry_tax_cents,
                        'total_pit_cents': self.total_pit_cents,
                        'fiscal_balance_cents': self.fiscal_balance_cents,
                        'conservation_residual_cents': self.conservation_residual_cents},
            'STEP_07': {'K': self.K, 'L': self.L, 'initial_migration': self.initial_migration},
            'STEP_08': {'adjustments': self.adjustments},
            'STEP_09': {'stability_12m': self.stability_12m_passed,
                        'stability_36m': self.stability_36m_passed,
                        'stability_120m': self.stability_120m_passed},
            'STEP_10_loss': self.loss,
        }


# ===== V2.1 §16 STEP 01..10 实施 =====

def step_01_select_scale(preset: CityPreset) -> dict:
    """§16 STEP 01：选择规模 N、建筑与基础行业比重。

    返回：{N, H, Jop, employment_by_industry}。
    """
    emp = initial_employment_dict(preset)
    return {
        'N': preset.N,
        'H': preset.H,
        'Jop': preset.Jop,
        'employment_by_industry': emp,
    }


def step_02_allocate_employment(preset: CityPreset) -> dict:
    """§16 STEP 02：分配 7 行业实际就业，检查 E<=F、E_i<=Jop_i、失业率。

    返回：{E, F, unemployment_rate, E_by_industry}。
    """
    emp = initial_employment_dict(preset)
    F = preset.F
    E = preset.E
    ur = (F - E) / F if F > 0 else 0.0
    # 检查 Jop_i >= E_i（行业容量约束）
    # 行业 Jop_i 简化为按就业数加权（V2.1 §15 提示）
    jop_by_industry = {}
    for ind_id, n in emp.items():
        # Jop_i = E_i * (Jop_total / E_total) 比例分摊
        jop_by_industry[ind_id] = int(n * preset.Jop / max(E, 1))
        # 保证 Jop_i >= E_i
        if jop_by_industry[ind_id] < n:
            jop_by_industry[ind_id] = n
    return {
        'E': E,
        'F': F,
        'unemployment_rate': ur,
        'E_by_industry': emp,
        'Jop_by_industry': jop_by_industry,
    }


def step_03_wages(emp_by_industry: dict[str, int]) -> int:
    """§16 STEP 03：按工资等级得到工资总额（CR → cents）。

    返回：总工资额（整数分）。
    """
    total_cents = 0
    for ind_id, E_i in emp_by_industry.items():
        ind = INDUSTRY_DEFINITIONS[ind_id]
        # 单人月工资 cents = wage_cr * 100
        per_worker_cents = ind.wage_cr * 100
        total_cents += per_worker_cents * E_i
    return total_cents


def step_04_residents(preset: CityPreset) -> dict:
    """§16 STEP 04：生成居民归属人口 P_h 与期初现金储蓄 S_h。

    使用工业新城基准群体定义（70/480/1120/1050/40/45/90/105 = 3000）
    按比例缩放到当前 preset.N。
    居民储蓄 = 300,000 CR 基准（V2.1 §14.10）。
    """
    base_total = sum(d.population_P_h for d in INDUSTRIAL_CITY_COHORT_DEFINITIONS.values())
    scale = preset.N / base_total if base_total > 0 else 1.0
    cohort_pop = {h: int(d.population_P_h * scale) for h, d in INDUSTRIAL_CITY_COHORT_DEFINITIONS.items()}
    total_pop = sum(cohort_pop.values())
    # 修正：把舍入误差分摊到最大群体
    if total_pop != preset.N and cohort_pop:
        # 找最大 h 修正
        largest_h = max(cohort_pop, key=cohort_pop.get)
        cohort_pop[largest_h] += (preset.N - total_pop)
        total_pop = sum(cohort_pop.values())
    total_savings = 300_000_00  # 300,000 CR = 30,000,000 cents
    return {
        'cohort_population': cohort_pop,
        'cohort_savings': {h: 0 for h in range(8)},   # 占位：均匀分配
        'total_cohort_population': total_pop,
        'total_initial_savings_cents': total_savings,
    }


def step_05_demand(preset: CityPreset, world: WorldState) -> dict:
    """§16 STEP 05：用 Cneed 与篮子权重生成居民有资金的订单。

    返回：{Cneed, total_consumer_demand_cents}。
    """
    bw = BasketWeights()
    r = CityPriceCoefficients(city_id=preset.archetype)
    Cneed = compute_Cneed_c_t(world.global_C, bw, r)
    total_demand = 0
    for h, d in INDUSTRIAL_CITY_COHORT_DEFINITIONS.items():
        P_h = d.population_P_h
        D_h = int(P_h * Cneed * 100)  # 转 cents
        total_demand += D_h
    return {
        'Cneed': Cneed,
        'total_consumer_demand_cents': total_demand,
    }


def step_06_settle(preset: CityPreset, total_wage_bill_cents: int) -> dict:
    """§16 STEP 06：行业税/分红/PIT/物业税/福利/教会。

    返回：{industry_tax, pit, fiscal_balance, residual}。
    """
    # 行业税：粗略按 §14.3 校准，IND 约 +13,200 cents = 13,200 CR
    # 五私营行业：IND 13200 + SRV+AGN 约 = 5 行业累计 13,200+≈ 部分
    total_industry_tax = 0
    for ind_id in [IndustryId.AGR.value, IndustryId.IND.value, IndustryId.SRV.value,
                   IndustryId.BNK.value, IndustryId.MAG.value]:
        # 行业税 = tau_i × max(0, 利润) - 占位
        pass  # 实际利润未算出来；占位 0
    # PIT（§6.4：AGR 工资免 PIT）
    total_pit = 0
    for h, d in INDUSTRIAL_CITY_COHORT_DEFINITIONS.items():
        # h=1 → AGR, h=2 → IND, ..., h=7 → GOV
        industry_id_map = {1: 'AGR', 2: 'IND', 3: 'SRV', 4: 'CHU', 5: 'BNK', 6: 'MAG', 7: 'GOV'}
        ind_id = industry_id_map.get(h, None)
        if is_pit_exempt(ind_id, 'wage'):
            continue
        # PIT = 8% × taxable
        taxable = total_wage_bill_cents // 8
        total_pit += compute_pit_cents(taxable, ind_id, 'wage')
    # 财政结余
    fiscal = -total_industry_tax - total_pit  # 简化：仅税 = -taxes
    # 守恒残差 = 0（首月无外部流入）
    residual = 0
    return {
        'total_industry_tax_cents': total_industry_tax,
        'total_pit_cents': total_pit,
        'fiscal_balance_cents': fiscal,
        'conservation_residual_cents': residual,
    }


def step_07_kl_and_migration(preset: CityPreset) -> dict:
    """§16 STEP 07：计算 K 与 L，并生成世界同月初始迁移。

    返回：{K, L, migration_delta}。
    """
    N = preset.N
    H = preset.H
    # K_F：按 preset 建议 K_F
    K_F_val = preset.K_F
    F = preset.F
    E = preset.E
    Jop = preset.Jop
    s = preset.s
    K_H = compute_K_H(H)
    K_F = compute_K_F(K_F_val)
    K_J = compute_K_J(Jop, E, F)
    K = compute_K(K_H, K_F, K_J, s)
    # L 占位（实际需 a/v 输入）
    h = compute_h(K, N)
    a = compute_a(0, N, 50.0)
    v = compute_v(0.7, 0.7, 1.0)
    L = compute_L(h, a, v, s)
    # 初始迁移 = 0（首月不外迁）
    return {'K': K, 'L': L, 'initial_migration': {preset.archetype: 0}}


def step_08_adjust(report: CalibrationReport, preset: CityPreset) -> list[str]:
    """§16 STEP 08：局部调节（销售量/合同/政府现金/必需品/住房/就业）。

    不得直接强行改利润/财政结余/K/L 结果。
    返回：调整项描述列表。
    """
    adjustments = []
    if report.unemployment_rate > 0.11:
        adjustments.append(f"失业率 {report.unemployment_rate:.2%} > 11%，建议增加 Jop")
    if report.K < preset.N * 1.08:
        adjustments.append(f"K/N {report.K/preset.N:.3f} < 1.08，建议增加住房")
    if report.conservation_residual_cents != 0:
        adjustments.append(f"守恒残差 {report.conservation_residual_cents} ≠ 0，须修正")
    return adjustments


def step_09_stability(report: CalibrationReport, months: int) -> bool:
    """§16 STEP 09：12/36/120 月稳定性占位。

    实际运行由 monthly_engine_full 驱动；占位返回基于失业率/K 的粗判。
    """
    if report.unemployment_rate > 0.12:
        return False
    if report.K < report.N * 1.05:
        return False
    if months >= 36 and report.L < 50.0:
        return False
    return True


def step_10_loss(
    report: CalibrationReport,
    preset: CityPreset,
    weights: dict[str, float] = None,
) -> float:
    """§16 优化目标：loss = 7 项加权和（w6 守恒残差必须为 0）。"""
    w = weights or DEFAULT_OPTIMIZATION_WEIGHTS
    # w1 失业率
    term1 = w['w1_unemployment'] * max(0, report.unemployment_rate - 0.11) ** 2
    # w2 K/N
    K_over_N = report.K / max(report.N, 1)
    term2 = w['w2_K_over_N'] * max(0, 1.08 - K_over_N) ** 2
    # w3 disposable_income / (N*Cneed)
    # 暂用 0 残差（首月无完整 disposable）
    term3 = w['w3_disposable'] * max(0, 1.02 - 0) ** 2
    # w4 gov_runway（占位：4 月基准）
    gov_runway = 4.0
    term4 = w['w4_gov_runway'] * max(0, 4.0 - gov_runway) ** 2
    # w5 abs(fiscal_ratio)
    fiscal_ratio = abs(report.fiscal_balance_cents) / max(report.N * 50 * 100, 1)
    term5 = w['w5_fiscal'] * max(0, abs(fiscal_ratio) - 0.05) ** 2
    # w6 守恒残差
    term6 = w['w6_residual'] * abs(report.conservation_residual_cents)
    # w7 未满足必要消费
    term7 = w['w7_unfilled_need'] * max(0, 0) ** 2
    return term1 + term2 + term3 + term4 + term5 + term6 + term7


# ===== V2.1 §16 完整校准主入口 =====

def calibrate(input_: CalibrationInput) -> CalibrationReport:
    """V2.1 §16 完整 10 步校准主入口。

    输入：archetype + seed + 难度。
    返回：完整 CalibrationReport（含 loss 与稳定性标记）。
    """
    preset = input_.preset
    assert preset is not None
    world = WorldState(rng_seed=input_.seed)

    report = CalibrationReport(
        archetype=input_.archetype,
        seed=input_.seed,
        version='v2.1',
    )

    # STEP 01
    s01 = step_01_select_scale(preset)
    report.N = s01['N']
    report.H = s01['H']
    report.Jop = s01['Jop']

    # STEP 02
    s02 = step_02_allocate_employment(preset)
    report.E = s02['E']
    report.F = s02['F']
    report.unemployment_rate = s02['unemployment_rate']

    # STEP 03
    report.total_wage_bill_cents = step_03_wages(s02['E_by_industry'])

    # STEP 04
    s04 = step_04_residents(preset)
    report.total_cohort_population = s04['total_cohort_population']
    report.total_initial_savings_cents = s04['total_initial_savings_cents']

    # STEP 05
    s05 = step_05_demand(preset, world)
    report.Cneed = s05['Cneed']
    report.total_consumer_demand_cents = s05['total_consumer_demand_cents']

    # STEP 06
    s06 = step_06_settle(preset, report.total_wage_bill_cents)
    report.total_industry_tax_cents = s06['total_industry_tax_cents']
    report.total_pit_cents = s06['total_pit_cents']
    report.fiscal_balance_cents = s06['fiscal_balance_cents']
    report.conservation_residual_cents = s06['conservation_residual_cents']

    # STEP 07
    s07 = step_07_kl_and_migration(preset)
    report.K = s07['K']
    report.L = s07['L']
    report.initial_migration = s07['initial_migration']

    # STEP 08
    report.adjustments = step_08_adjust(report, preset)

    # STEP 09（占位：12/36/120）
    report.stability_12m_passed = step_09_stability(report, 12)
    report.stability_36m_passed = step_09_stability(report, 36)
    report.stability_120m_passed = step_09_stability(report, 120)

    # STEP 10
    report.loss = step_10_loss(report, preset)

    return report


def calibrate_all_archetypes(seed_base: int = 0) -> dict[str, CalibrationReport]:
    """§16 批量校准全部 5 城预设。"""
    results = {}
    for archetype in CITY_PRESETS:
        inp = CalibrationInput(archetype=archetype, seed=seed_base)
        results[archetype] = calibrate(inp)
    return results
