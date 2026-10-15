"""V2.1 经济系统起步包（阶段 0-13 全部完成）。

【2026-10-15】Bipole 城市聚合经济系统理论说明书 V2.1 完全取代 V1.0；
V1.0 实现（`backend/systems_v1_reference/`）已封存为参考代码，正式经济系统
代码全部在本包下按 V2.1 重建。

阶段 0 起步：账户边界判定（is_ocean / is_peer / peer_account）+ PeerFlow 数据类。
阶段 1 落地：七行业静态定义（IndustryId / WageTier / TaxTier / IndustryDefinition）。
阶段 2 落地：八群体居民（CohortIndustry / CohortDefinition）。
阶段 3 落地：完整账户体系（Account / AccountBook / Ledger）+ 工厂
`build_industrial_city_accounts`（V2.1 §14.12 期初资金）。
阶段 4 落地：银行与融资（Loan / LoanStatus / LoanBook / originate_loan /
repay_loan / transfer_loan，最简贷款基金制 V2.1 §8）。
阶段 5 落地：跨城交易（商品目录 Goods + 订单 Order + 在途库存 TransitStock +
双城镜像 settle_cross_city_trade + 政府两种模式 ORGANIZE/DIRECT，V2.1 §4）。
阶段 6 落地：通胀与工资传导（WorldState + 行业工资更新 alpha/gamma/clip +
Cneed 篮子权重 + household_budget 资金约束，V2.1 §6）。
阶段 7 落地：城市承载力 K 与宜居度 L（§10.6 总 K + §11.1 L 四分量）。
阶段 8 落地：跨城迁移（§11.2 城对公式 + §11.3 9 步强制流程 + §11.4 首月 1% 上限）。
阶段 9 落地：月结引擎（V2.1 §12 15 步严格同步顺序 + 防循环引用 + MonthReport 冻结存档）。
阶段 9 补：完整 WorldState/CityState/SectorState 聚合 + 4 项世界参数 + 报价公式 + 储蓄更新 + §5 生产链 + §7 行业月结 + §9.3 财政完整版 + §13 GDP/GVA。
阶段 10 落地：政府接口（§9.1 11 个动作 + §9.2 GovernmentAction 统一协议 + §9.3 G_income + §9.4 房产税）。
阶段 11 落地：宗教（§9.5 四效应 + §9.6 五档传教政策 + §9.7 教会月度资金流）。
阶段 12 落地：月结引擎实质业务（15 步真实实现：工资发放/消费需求/实际产量/财务结算/PROP 清零/教会捐赠/K/L/多城迁移/通胀更新/月报）。
阶段 13 落地：5 城预设（§15）+ 16 项硬安全边界（§15.1）+ 10 步校准器（§16）+ 13 情景测试（§17.2 A-M）。

完整跨城迁移与月结引擎由 V2.1 阶段 6+ 落地；其余模块参见
`docs/02_开发规划/V2.1推倒重建路线图-2026-10-15.md`。
"""
from backend.systems.v21.accounts import (
    OCEAN, OCEAN_ACCOUNT, PEER, PEER_ACCOUNT_PREFIX, EXTERNAL,
    MUNICIPAL, FIRM, HOUSEHOLD, CHURCH_KIND, PROP_KIND, OCEAN_KIND, PEER_KIND,
    Account, AccountBook,
    peer_account, is_ocean, is_peer, peer_city_id_of,
    firm_account_id, cohort_account_id, church_account_id,
    government_account_id, property_pass_account_id, landlords_account_id,
)
from backend.systems.v21.industries import (
    WageTier, TaxTier, IndustryId, IndustryDefinition, INDUSTRY_DEFINITIONS,
    WAGE_TIER_CR, TAX_TIER_RATE,
    wage_cr_per_worker, tax_rate, monthly_wage_bill_cents,
)
from backend.systems.v21.cohorts import (
    CohortIndustry, CohortDefinition, INDUSTRIAL_CITY_COHORT_DEFINITIONS,
    TOTAL_POPULATION_N, TOTAL_EMPLOYMENT_E, TOTAL_LABOR_FORCE_F, TOTAL_UNEMPLOYED_U,
    TOTAL_NON_LABOR_FORCE, TOTAL_INITIAL_SAVINGS_CR,
    PERSONAL_INCOME_TAX_RATE, AGRICULTURAL_WAGE_PIT_EXEMPT,
    total_population, total_employment, total_non_employed,
    is_pit_exempt, compute_pit_cents, compute_pit_cr,
    split_initial_savings,
)
from backend.systems.v21.ledger import Ledger, PeerFlow, build_industrial_city_accounts
from backend.systems.v21.banking import (
    LoanStatus, LoanPurpose, PROHIBITED_PURPOSES,
    DEFAULT_MAX_LOAN_TO_BANK_CASH_RATIO, DEFAULT_MAX_LOAN_TO_BORROWER_CASH_RATIO,
    DEFAULT_INTEREST_RATE_MONTHLY, DEFAULT_MAX_INTEREST_RATE_MONTHLY,
    Loan, LoanBook,
    bank_account_id, available_cash_for_lending,
    originate_loan, repay_loan, transfer_loan,
)
from backend.systems.v21.goods import (
    GoodCategory, ShipmentMode, SettlementTrigger,
    GoodDefinition, GOOD_DEFINITIONS, REQUIRED_17_CATEGORIES,
    assert_catalog_complete, good_ids, get_good,
)
from backend.systems.v21.orders import (
    OrderStatus, Order, MatchResult, OrderBook, match_order,
)
from backend.systems.v21.transit import (
    TransitShipment, TransitStock, dispatch_cross_city,
)
from backend.systems.v21.trade_settlement import (
    GovernmentTradeMode, TradeSettlement,
    settle_cross_city_trade, government_account_id_for_city,
)
from backend.systems.v21.inflation import (
    BasketCategory, DEFAULT_BASKET_WEIGHTS, DEFAULT_INFLATION_PI,
    DEFAULT_I_0, DEFAULT_C_0_CR_PER_PERSON,
    BasketWeights, CityPriceCoefficients, WorldState,
    compute_Cneed_c_t,
)
from backend.systems.v21.wages import (
    DEFAULT_WAGE_ALPHA, DEFAULT_WAGE_GAMMA,
    DEFAULT_WAGE_CAP_UP, DEFAULT_WAGE_CAP_DOWN,
    WagePolicyModifier, IndustryWageState, WageUpdateResult,
    compute_wstar_cents, compute_gW,
    update_industry_wage, update_all_industry_wages,
)
from backend.systems.v21.consumption import (
    DEFAULT_PIT_RATE, DEFAULT_SAVINGS_DRAW_LIMIT,
    HouseholdIncome, TaxableIncome, HouseholdBudget, CneedDemand,
    household_gross_income_cents, household_disposable_cents,
    compute_Cneed_demand_cents, effective_real_spending_cents,
)
from backend.systems.v21.capacity import (
    DEFAULT_LAMBDA, DEFAULT_E_TARGET, DEFAULT_JOB_USE_EXP,
    DEFAULT_FOOD_PER_PERSON, DEFAULT_S_BASE, DEFAULT_K_S,
    DEFAULT_H_FLOOR, DEFAULT_H_DIVISOR, DEFAULT_L_CNEED_MULTIPLIER,
    DEFAULT_L_WEIGHTS,
    smooth3, clip, KComponents, LComponents,
    compute_K_H, compute_K_F, compute_K_J, compute_s, compute_K,
    compute_h, compute_a, compute_v, compute_L, weights_sum_to_one,
)
from backend.systems.v21.migration import (
    DEFAULT_RHO, DEFAULT_BETA, DEFAULT_D0_KM,
    DEFAULT_OUTFLOW_CAP_RATE, DEFAULT_DEST_HOUSING_BUFFER,
    CitySnapshot, DistanceMatrix, MigrationFlow, MigrationResult,
    compute_M_cd, antisymmetric_pair,
    cap_outflow_1pct, cap_destination_housing,
    solve_multi_city_migration,
)
from backend.systems.v21.monthly_engine import (
    MonthlyStep, MonthlyContext, MonthReport, CityMonthReport,
    ConservationCheck,
    run_month_step, run_full_month, run_first_month, run_next_month,
    assert_conservation,
)
from backend.systems.v21.government import (
    DEFAULT_TAU_P, DEFAULT_TAU_H, DEFAULT_FAITHFUL_RATIO,
    GovernmentActionType, GovernmentAction, GovernmentActionResult,
    GovernmentRevenue, PropertyTaxInput,
    validate_government_action, compute_government_revenue,
    compute_property_tax_cents, execute_government_action,
)
from backend.systems.v21.monthly_engine_full import (
    MonthlyContextFull, _cohort_h_for_industry,
    _step_03_full, _step_04_full, _step_05_full, _step_08_full,
    _step_09_full, _step_12_full, _step_13_full, _step_14_full,
    _step_15_full, run_full_month_full,
)
from backend.systems.v21.religion import (
    DEFAULT_F, DEFAULT_GRANT_PER_FAITHFUL, DEFAULT_WAGE_TOLERANCE,
    DEFAULT_WORKTIME_F_COEF, DEFAULT_POLICY_COOLDOWN_MONTHS,
    FaithfulRatio, ReligionPolicy, ReligionPolicyParams,
    RELIGION_POLICY_PARAMS, PolicyCooldownState, ChurchCashFlow,
    get_policy_params,
    compute_grant_to_government_cents, compute_wage_tolerance_modifier,
    compute_worktime_ratio, compute_property_tax_exemption_factor,
    compute_church_cash_next,
)
from backend.systems.v21.pricing import (
    GoodPrice, compute_price_relative_change, update_good_price, update_all_good_prices,
)
from backend.systems.v21.labor import (
    compute_F_t, compute_Jop,
    integer_allocation_with_largest_remainder,
    IndustryJobCapacity, HireDecision, allocate_employment,
)
from backend.systems.v21.production import (
    DEFAULT_ETA_LABOR_EXP,
    compute_eta_labor, compute_eta_input, compute_eta_safety, compute_eta_worktime,
    compute_eta, compute_actual_output, compute_required_input,
)
from backend.systems.v21.industry_settlement import (
    IndustryRevenue, IndustryProfitComponents,
    compute_industry_tax_cents, compute_aftertax_profit_cents,
    compute_max_dividend_cents,
    IndustryCashDelta, IndustryLossTracker,
)
from backend.systems.v21.states import SectorState, CityState
from backend.systems.v21.gdp import (
    GVAComponents, compute_industry_GVA_cents,
    CityGDPReport, compute_city_GDP_report,
    loan_principal_does_not_count_in_GDP,
)
from backend.systems.v21.consumption import (
    SavingsDelta, compute_savings_next_cents,
)
from backend.systems.v21.inflation import (
    DEFAULT_MONTH_DAYS, DEFAULT_CURRENCY_SCALE,
    DEFAULT_PRICE_PRESSURE_K, DEFAULT_PRICE_RELATIVE_CAP, DEFAULT_GOODS_ROUNDING,
)
from backend.systems.v21.government import (
    GovernmentExpense, compute_government_expense,
    compute_fiscal_balance_cents, compute_government_cash_next,
    compute_government_debt_next,
)
from backend.systems.v21.loader import (
    find_project_root, PROJECT_ROOT, ECONOMY_V2_DIR,
    read_json, load_industries_config, load_cohorts_config, load_wallets_config,
    load_banking_config, load_goods_config, load_inflation_config,
    load_capacity_config, load_migration_config,
)
from backend.systems.v21.city_presets import (
    CityPreset, CITY_PRESETS, SafetyBoundError,
    validate_preset, assert_preset_safe, assert_all_presets_safe,
    initial_employment_dict,
)
from backend.systems.v21.calibrator import (
    DEFAULT_OPTIMIZATION_WEIGHTS, CalibrationInput, CalibrationReport,
    step_01_select_scale, step_02_allocate_employment, step_03_wages,
    step_04_residents, step_05_demand, step_06_settle,
    step_07_kl_and_migration, step_08_adjust, step_09_stability,
    step_10_loss, calibrate, calibrate_all_archetypes,
)
from backend.systems.v21.scenario_tests import (
    ScenarioResult,
    scenario_A_single_city_baseline, scenario_B_profit_tax_hike,
    scenario_C_food_import_reduction, scenario_D_industrial_city_food_imported,
    scenario_E_industry_shutdown, scenario_F_trade_blocked,
    scenario_G_academy_buys_foreign_magic, scenario_H_gov_increases_debt,
    scenario_I_security_drops_to_085, scenario_J_equal_livability,
    scenario_K_price_up_quantity_down, scenario_L_continuous_inflation,
    scenario_M_close_one_city_trade,
    SCENARIO_REGISTRY, run_all_scenarios, all_scenarios_passed,
)

__all__ = [
    # 阶段 0：账户边界
    'OCEAN', 'OCEAN_ACCOUNT', 'PEER', 'PEER_ACCOUNT_PREFIX', 'EXTERNAL',
    'MUNICIPAL', 'FIRM', 'HOUSEHOLD', 'CHURCH_KIND', 'PROP_KIND', 'OCEAN_KIND', 'PEER_KIND',
    'Account', 'AccountBook', 'peer_account',
    'is_ocean', 'is_peer', 'peer_city_id_of',
    'firm_account_id', 'cohort_account_id', 'church_account_id',
    'government_account_id', 'property_pass_account_id', 'landlords_account_id',
    'PeerFlow',
    # 阶段 1：行业体系
    'WageTier', 'TaxTier', 'IndustryId', 'IndustryDefinition', 'INDUSTRY_DEFINITIONS',
    'WAGE_TIER_CR', 'TAX_TIER_RATE',
    'wage_cr_per_worker', 'tax_rate', 'monthly_wage_bill_cents',
    # 阶段 2：居民群体
    'CohortIndustry', 'CohortDefinition', 'INDUSTRIAL_CITY_COHORT_DEFINITIONS',
    'TOTAL_POPULATION_N', 'TOTAL_EMPLOYMENT_E', 'TOTAL_LABOR_FORCE_F', 'TOTAL_UNEMPLOYED_U',
    'TOTAL_NON_LABOR_FORCE', 'TOTAL_INITIAL_SAVINGS_CR',
    'PERSONAL_INCOME_TAX_RATE', 'AGRICULTURAL_WAGE_PIT_EXEMPT',
    'total_population', 'total_employment', 'total_non_employed',
    'is_pit_exempt', 'compute_pit_cents', 'compute_pit_cr',
    'split_initial_savings',
    # 阶段 3：账户体系
    'Ledger', 'build_industrial_city_accounts',
    # 阶段 4：银行与融资
    'LoanStatus', 'LoanPurpose', 'PROHIBITED_PURPOSES',
    'DEFAULT_MAX_LOAN_TO_BANK_CASH_RATIO', 'DEFAULT_MAX_LOAN_TO_BORROWER_CASH_RATIO',
    'DEFAULT_INTEREST_RATE_MONTHLY', 'DEFAULT_MAX_INTEREST_RATE_MONTHLY',
    'Loan', 'LoanBook',
    'bank_account_id', 'available_cash_for_lending',
    'originate_loan', 'repay_loan', 'transfer_loan',
    # 阶段 5：跨城交易
    'GoodCategory', 'ShipmentMode', 'SettlementTrigger',
    'GoodDefinition', 'GOOD_DEFINITIONS', 'REQUIRED_17_CATEGORIES',
    'assert_catalog_complete', 'good_ids', 'get_good',
    'OrderStatus', 'Order', 'MatchResult', 'OrderBook', 'match_order',
    'TransitShipment', 'TransitStock', 'dispatch_cross_city',
    'GovernmentTradeMode', 'TradeSettlement',
    'settle_cross_city_trade', 'government_account_id_for_city',
    # 阶段 6：通胀与工资传导
    'BasketCategory', 'DEFAULT_BASKET_WEIGHTS', 'DEFAULT_INFLATION_PI',
    'DEFAULT_I_0', 'DEFAULT_C_0_CR_PER_PERSON',
    'BasketWeights', 'CityPriceCoefficients', 'WorldState',
    'compute_Cneed_c_t',
    'DEFAULT_WAGE_ALPHA', 'DEFAULT_WAGE_GAMMA',
    'DEFAULT_WAGE_CAP_UP', 'DEFAULT_WAGE_CAP_DOWN',
    'WagePolicyModifier', 'IndustryWageState', 'WageUpdateResult',
    'compute_wstar_cents', 'compute_gW',
    'update_industry_wage', 'update_all_industry_wages',
    'DEFAULT_PIT_RATE', 'DEFAULT_SAVINGS_DRAW_LIMIT',
    'HouseholdIncome', 'TaxableIncome', 'HouseholdBudget', 'CneedDemand',
    'household_gross_income_cents', 'household_disposable_cents',
    'compute_Cneed_demand_cents', 'effective_real_spending_cents',
    # 阶段 7：城市承载力 K + 宜居度 L
    'DEFAULT_LAMBDA', 'DEFAULT_E_TARGET', 'DEFAULT_JOB_USE_EXP',
    'DEFAULT_FOOD_PER_PERSON', 'DEFAULT_S_BASE', 'DEFAULT_K_S',
    'DEFAULT_H_FLOOR', 'DEFAULT_H_DIVISOR', 'DEFAULT_L_CNEED_MULTIPLIER',
    'DEFAULT_L_WEIGHTS',
    'smooth3', 'clip', 'KComponents', 'LComponents',
    'compute_K_H', 'compute_K_F', 'compute_K_J', 'compute_s', 'compute_K',
    'compute_h', 'compute_a', 'compute_v', 'compute_L', 'weights_sum_to_one',
    # 阶段 8：跨城迁移
    'DEFAULT_RHO', 'DEFAULT_BETA', 'DEFAULT_D0_KM',
    'DEFAULT_OUTFLOW_CAP_RATE', 'DEFAULT_DEST_HOUSING_BUFFER',
    'CitySnapshot', 'DistanceMatrix', 'MigrationFlow', 'MigrationResult',
    'compute_M_cd', 'antisymmetric_pair',
    'cap_outflow_1pct', 'cap_destination_housing',
    'solve_multi_city_migration',
    # 阶段 9：月结引擎
    'MonthlyStep', 'MonthlyContext', 'MonthReport', 'CityMonthReport',
    'ConservationCheck',
    'run_month_step', 'run_full_month', 'run_first_month', 'run_next_month',
    'assert_conservation',
    # 阶段 10：政府接口
    'DEFAULT_TAU_P', 'DEFAULT_TAU_H', 'DEFAULT_FAITHFUL_RATIO',
    'GovernmentActionType', 'GovernmentAction', 'GovernmentActionResult',
    'GovernmentRevenue', 'PropertyTaxInput',
    'validate_government_action', 'compute_government_revenue',
    'compute_property_tax_cents', 'execute_government_action',
    # 阶段 12：月结引擎 15 步实质业务
    'MonthlyContextFull', '_cohort_h_for_industry',
    '_step_03_full', '_step_04_full', '_step_05_full', '_step_08_full',
    '_step_09_full', '_step_12_full', '_step_13_full', '_step_14_full',
    '_step_15_full', 'run_full_month_full',
    # 阶段 11：宗教
    'DEFAULT_F', 'DEFAULT_GRANT_PER_FAITHFUL', 'DEFAULT_WAGE_TOLERANCE',
    'DEFAULT_WORKTIME_F_COEF', 'DEFAULT_POLICY_COOLDOWN_MONTHS',
    'FaithfulRatio', 'ReligionPolicy', 'ReligionPolicyParams',
    'RELIGION_POLICY_PARAMS', 'PolicyCooldownState', 'ChurchCashFlow',
    'get_policy_params',
    'compute_grant_to_government_cents', 'compute_wage_tolerance_modifier',
    'compute_worktime_ratio', 'compute_property_tax_exemption_factor',
    'compute_church_cash_next',
    # 阶段 9 补：完整状态 + 公式补完
    'GoodPrice', 'compute_price_relative_change', 'update_good_price', 'update_all_good_prices',
    'DEFAULT_ETA_LABOR_EXP',
    'compute_F_t', 'compute_Jop',
    'integer_allocation_with_largest_remainder',
    'IndustryJobCapacity', 'HireDecision', 'allocate_employment',
    'compute_eta_labor', 'compute_eta_input', 'compute_eta_safety', 'compute_eta_worktime',
    'compute_eta', 'compute_actual_output', 'compute_required_input',
    'IndustryRevenue', 'IndustryProfitComponents',
    'compute_industry_tax_cents', 'compute_aftertax_profit_cents',
    'compute_max_dividend_cents',
    'IndustryCashDelta', 'IndustryLossTracker',
    'SectorState', 'CityState',
    'GVAComponents', 'compute_industry_GVA_cents',
    'CityGDPReport', 'compute_city_GDP_report',
    'loan_principal_does_not_count_in_GDP',
    'SavingsDelta', 'compute_savings_next_cents',
    'DEFAULT_MONTH_DAYS', 'DEFAULT_CURRENCY_SCALE',
    'DEFAULT_PRICE_PRESSURE_K', 'DEFAULT_PRICE_RELATIVE_CAP', 'DEFAULT_GOODS_ROUNDING',
    'GovernmentExpense', 'compute_government_expense',
    'compute_fiscal_balance_cents', 'compute_government_cash_next',
    'compute_government_debt_next',
    # 阶段 1+2+3+4+5+6+7+8+9+10+11：JSON 加载器
    'find_project_root', 'PROJECT_ROOT', 'ECONOMY_V2_DIR',
    'read_json', 'load_industries_config', 'load_cohorts_config', 'load_wallets_config',
    'load_banking_config', 'load_goods_config', 'load_inflation_config',
    'load_capacity_config', 'load_migration_config',
    # 阶段 13：5 城预设 + 16 项硬边界
    'CityPreset', 'CITY_PRESETS', 'SafetyBoundError',
    'validate_preset', 'assert_preset_safe', 'assert_all_presets_safe',
    'initial_employment_dict',
    # 阶段 13：§16 校准器
    'DEFAULT_OPTIMIZATION_WEIGHTS', 'CalibrationInput', 'CalibrationReport',
    'step_01_select_scale', 'step_02_allocate_employment', 'step_03_wages',
    'step_04_residents', 'step_05_demand', 'step_06_settle',
    'step_07_kl_and_migration', 'step_08_adjust', 'step_09_stability',
    'step_10_loss', 'calibrate', 'calibrate_all_archetypes',
    # 阶段 13：§17.2 13 情景 A-M
    'ScenarioResult',
    'scenario_A_single_city_baseline', 'scenario_B_profit_tax_hike',
    'scenario_C_food_import_reduction', 'scenario_D_industrial_city_food_imported',
    'scenario_E_industry_shutdown', 'scenario_F_trade_blocked',
    'scenario_G_academy_buys_foreign_magic', 'scenario_H_gov_increases_debt',
    'scenario_I_security_drops_to_085', 'scenario_J_equal_livability',
    'scenario_K_price_up_quantity_down', 'scenario_L_continuous_inflation',
    'scenario_M_close_one_city_trade',
    'SCENARIO_REGISTRY', 'run_all_scenarios', 'all_scenarios_passed',
]
