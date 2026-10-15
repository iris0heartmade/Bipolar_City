"""V2.1 阶段 6 居民消费预算与可支配收入验收测试（2026-10-15）。

【验收清单】（V2.1 §6.4 + §6.5）
- [x] HouseholdIncome 5 项现金收入聚合
- [x] TaxableIncome 4 项应税收入
- [x] household_gross_income_cents 工具
- [x] household_disposable_cents 工具
- [x] HouseholdBudget available 计算
- [x] max_savings_draw 上限校验
- [x] CneedDemand 名义需求计算
- [x] effective_real_spending_cents 双重约束（available + 有效订单）
- [x] 救济不计入 PIT 税基（§6.4）
- [x] 默认参数 pit_rate=0.08, savings_draw_limit=0.30
"""
import pytest

from backend.systems.v21.consumption import (
    DEFAULT_PIT_RATE, DEFAULT_SAVINGS_DRAW_LIMIT,
    HouseholdIncome, TaxableIncome, HouseholdBudget, CneedDemand,
    household_gross_income_cents, household_disposable_cents,
    compute_Cneed_demand_cents, effective_real_spending_cents,
)


# ===== A. 默认参数（2 项）=====

class TestDefaultParams:
    """V2.1 §6.4 + §6.5 默认参数。"""

    def test_pit_rate_is_0_08(self):
        """阶段 6：tau_P = 0.08（V2.1 §6.4 PIT 税率）。"""
        assert DEFAULT_PIT_RATE == 0.08

    def test_savings_draw_limit_is_0_10(self):
        """阶段 9 补：savings_draw_limit = 0.10（V2.1 §3 world.savings_draw_limit，§6.5 储蓄动用比例上限）。"""
        assert DEFAULT_SAVINGS_DRAW_LIMIT == 0.10


# ===== B. HouseholdIncome（4 项）=====

class TestHouseholdIncome:
    """V2.1 §6.4 居民收入聚合。"""

    def test_total_gross_basic(self):
        """阶段 6：Y_gross = 5 项合计。"""
        income = HouseholdIncome(
            paid_wages_cents=100_000,
            paid_dividends_cents=10_000,
            net_property_income_cents=5_000,
            paid_welfare_cents=20_000,         # 救济
            other_cash_income_cents=2_000,
        )
        # 100,000 + 10,000 + 5,000 + 20,000 + 2,000 = 137,000
        assert income.total_gross_cents() == 137_000

    def test_default_all_zero(self):
        """阶段 6：默认 5 项全 0（无收入）。"""
        income = HouseholdIncome()
        assert income.total_gross_cents() == 0

    def test_welfare_included_in_gross_not_in_pit(self):
        """阶段 6：救济计入 gross 但不计 PIT 税基（V2.1 §6.4 末段）。"""
        income = HouseholdIncome(paid_wages_cents=100_000, paid_welfare_cents=50_000)
        taxable = TaxableIncome(nonag_wages_cents=100_000)  # 仅 100,000
        assert income.total_gross_cents() == 150_000
        # PIT 只对 taxable 计算（不含救济）
        assert taxable.total_taxable_cents() == 100_000

    def test_household_gross_income_cents_helper(self):
        """阶段 6：短工具函数 household_gross_income_cents 与 income.total_gross_cents 一致。"""
        income = HouseholdIncome(paid_wages_cents=100_000, paid_dividends_cents=10_000)
        assert household_gross_income_cents(income) == 110_000


# ===== C. TaxableIncome（3 项）=====

class TestTaxableIncome:
    """V2.1 §6.4 PIT 税基。"""

    def test_total_taxable_basic(self):
        """阶段 6：Taxable = 4 项合计。"""
        t = TaxableIncome(
            nonag_wages_cents=100_000,
            taxable_dividends_cents=10_000,
            taxable_net_property_income_cents=5_000,
            other_taxable_income_cents=2_000,
        )
        assert t.total_taxable_cents() == 117_000

    def test_agricultural_wage_excluded_from_pit_base(self):
        """阶段 6：农业工资不在 PIT 税基（V2.1 §6.4 农业工资免 PIT）。"""
        # AGR 工资 = 50,000，non_ag 工资 = 80,000
        # Taxable 仅含 non_ag
        t = TaxableIncome(nonag_wages_cents=80_000)
        # AGR 工资 50,000 不在 taxable 中
        assert t.total_taxable_cents() == 80_000

    def test_pit_zero_when_taxable_zero(self):
        """阶段 6：taxable=0 时 PIT=0（max(Taxable, 0)）。"""
        t = TaxableIncome()
        assert t.total_taxable_cents() == 0


# ===== D. household_disposable_cents（3 项）=====

class TestHouseholdDisposable:
    """V2.1 §6.4 Household_disposable_h = Y_gross - PIT - property_tax - other_taxes。"""

    def test_disposable_basic(self):
        """阶段 6：基础 disposable = gross - pit - property - other。"""
        # 200,000 - 8,000 - 1,000 - 500 = 190,500
        result = household_disposable_cents(
            gross_income_cents=200_000,
            pit_cents=8_000,
            property_tax_cents=1_000,
            other_taxes_cents=500,
        )
        assert result == 190_500

    def test_disposable_no_taxes(self):
        """阶段 6：无税时 disposable = gross。"""
        assert household_disposable_cents(gross_income_cents=100_000, pit_cents=0) == 100_000

    def test_disposable_can_be_negative(self):
        """阶段 6：高税时 disposable 可能为负（财政困局信号，不阻断）。"""
        # 100,000 - 200,000 = -100,000（理论极端）
        result = household_disposable_cents(gross_income_cents=100_000, pit_cents=200_000)
        assert result == -100_000


# ===== E. HouseholdBudget（5 项）=====

class TestHouseholdBudget:
    """V2.1 §6.5 available_h = S + income - taxes - earmarked_obligations。"""

    def test_available_basic(self):
        """阶段 6：基础 available = 储蓄 + 收入 - 税 - 预留义务。"""
        budget = HouseholdBudget(
            savings_cents=100_000,
            cash_income_cents=50_000,
            taxes_cents=10_000,
            earmarked_obligations_cents=5_000,
        )
        # 100,000 + 50,000 - 10,000 - 5,000 = 135,000
        assert budget.available_cents() == 135_000

    def test_available_floor_zero(self):
        """阶段 6：负值裁剪到 0（V2.1 §6.5 max(0, ...)）。"""
        budget = HouseholdBudget(
            savings_cents=10_000,
            cash_income_cents=5_000,
            taxes_cents=10_000,        # 高税
            earmarked_obligations_cents=10_000,
        )
        # 10,000 + 5,000 - 10,000 - 10,000 = -5,000 → max(0, ...) = 0
        assert budget.available_cents() == 0

    def test_max_savings_draw_basic(self):
        """阶段 9 补：max_savings_draw = savings * draw_limit。"""
        budget = HouseholdBudget(savings_cents=100_000, cash_income_cents=0)
        # 100,000 * 0.10 = 10,000
        assert budget.max_savings_draw_cents() == 10_000

    def test_max_savings_draw_custom_limit(self):
        """阶段 6：自定义 draw_limit。"""
        budget = HouseholdBudget(savings_cents=100_000, cash_income_cents=0)
        # 100,000 * 0.50 = 50,000
        assert budget.max_savings_draw_cents(draw_limit=0.5) == 50_000

    def test_max_savings_draw_out_of_range(self):
        """阶段 6：draw_limit 越界 [0, 1] 抛 ValueError。"""
        budget = HouseholdBudget(savings_cents=100_000, cash_income_cents=0)
        with pytest.raises(ValueError, match='draw_limit 越界'):
            budget.max_savings_draw_cents(draw_limit=1.5)


# ===== F. CneedDemand + compute_Cneed_demand_cents（4 项）=====

class TestCneedDemand:
    """V2.1 §6.5 D_h^nominal = P_h * Cneed_c,t。"""

    def test_nominal_demand_basic(self):
        """阶段 6：500 人 × 60 CR/人/月 = 30,000 CR = 3,000,000 分。"""
        demand = CneedDemand(population_P_h=500, Cneed_c_t_cr_per_person=60.0)
        assert demand.nominal_demand_cr() == 30_000.0
        assert demand.nominal_demand_cents() == 3_000_000

    def test_nominal_demand_zero_population(self):
        """阶段 6：零人口时需求 = 0。"""
        demand = CneedDemand(population_P_h=0, Cneed_c_t_cr_per_person=60.0)
        assert demand.nominal_demand_cents() == 0

    def test_helper_function_matches_dataclass(self):
        """阶段 6：compute_Cneed_demand_cents 与 CneedDemand 一致。"""
        helper_result = compute_Cneed_demand_cents(
            population_P_h=100,
            Cneed_c_t_cr_per_person=50.0,
        )
        dc_result = CneedDemand(population_P_h=100, Cneed_c_t_cr_per_person=50.0).nominal_demand_cents()
        assert helper_result == dc_result == 500_000

    def test_nominal_demand_floors_to_cents(self):
        """阶段 6：CR 值转分时向下取整。"""
        # 50.5 CR/人 × 100 人 = 5050 CR = 505,000 分
        demand = CneedDemand(population_P_h=100, Cneed_c_t_cr_per_person=50.5)
        assert demand.nominal_demand_cents() == 505_000


# ===== G. effective_real_spending_cents（4 项）=====

class TestEffectiveRealSpending:
    """V2.1 §6.5 RealSpending_h <= min(available, 有效订单可交付总价)。"""

    def test_real_spending_limited_by_budget(self):
        """阶段 6：订单价值高但预算不足时，受预算约束。"""
        budget = HouseholdBudget(savings_cents=10_000, cash_income_cents=0)
        # available = 10,000；订单价值 50,000 → min(10,000, 50,000) = 10,000
        result = effective_real_spending_cents(
            budget=budget, effective_order_value_cents=50_000,
        )
        assert result == 10_000

    def test_real_spending_limited_by_order_value(self):
        """阶段 6：预算高但订单价值不足时，受订单约束（缺货不允许凭空扣钱）。"""
        budget = HouseholdBudget(savings_cents=100_000, cash_income_cents=50_000)
        # available = 150,000；订单价值 30,000 → min(150,000, 30,000) = 30,000
        result = effective_real_spending_cents(
            budget=budget, effective_order_value_cents=30_000,
        )
        assert result == 30_000

    def test_real_spending_floor_zero(self):
        """阶段 6：完全无预算时返回 0（不能负）。"""
        budget = HouseholdBudget(
            savings_cents=0, cash_income_cents=0,
            taxes_cents=0, earmarked_obligations_cents=0,
        )
        # available = 0；订单 100,000 → min(0, 100,000) = 0
        result = effective_real_spending_cents(
            budget=budget, effective_order_value_cents=100_000,
        )
        assert result == 0

    def test_real_spending_equal_constraints(self):
        """阶段 6：两个约束相等时返回该值。"""
        budget = HouseholdBudget(savings_cents=10_000, cash_income_cents=0)
        # available = 10,000；订单 10,000 → 返回 10,000
        result = effective_real_spending_cents(
            budget=budget, effective_order_value_cents=10_000,
        )
        assert result == 10_000
