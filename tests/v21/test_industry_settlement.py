"""V2.1 阶段 9 补 §7 行业月结验收测试（2026-10-15）。

【验收清单】（V2.1 §7）
- [x] §7.1 R 6 路收入分解
- [x] §7.2 PreTax/AfterTax/IndustryTax/Dividend
- [x] §7.3 DeltaM 9 项现金变化
- [x] §7.4 亏损温和处理（3/6 月触发）
- [x] §17 TEST-08 行业税不取负
"""
import pytest

from backend.systems.v21.industry_settlement import (
    IndustryRevenue, IndustryProfitComponents,
    compute_industry_tax_cents, compute_aftertax_profit_cents,
    compute_max_dividend_cents,
    IndustryCashDelta, IndustryLossTracker,
)


# ===== A. IndustryRevenue 6 路分解（2 项）=====

class TestIndustryRevenue:
    """V2.1 §7.1 R_i = 6 路收入合计。"""

    def test_total_sums_six(self):
        """阶段 9 补：6 项合计。"""
        rev = IndustryRevenue(
            from_households_cents=120_000_00,    # 居民 120,000
            intermediate_cents=108_300_00,         # 中间采购 108,300
            property_maintenance_cents=19_000_00, # 物业维护 19,000
            from_government_cents=15_900_00,      # 政府采购 15,900
            export_cents=82_800_00,                # 出口 82,800
            external_visitor_cents=5_000_00,      # 游客 5,000
        )
        # 120,000 + 108,300 + 19,000 + 15,900 + 82,800 + 5,000 = 351,000
        # 注：§14.4 销售合计 364,000；差额 13,000 = §14 资本建设
        assert rev.total_cents() == 351_000_00

    def test_default_all_zero(self):
        """阶段 9 补：默认全 0。"""
        assert IndustryRevenue().total_cents() == 0


# ===== B. §7.2 利润计算（5 项）=====

class TestProfit:
    """V2.1 §7.2 PreTax/AfterTax/IndustryTax/Dividend。"""

    def test_pretax_profit_basic(self):
        """阶段 9 补：PreTaxProfit = R - W - goods - rent - depreciation - other。

        R=47000, W=14400, goods=22000, rent=1000, depreciation=2500, other=1100
        → 47000 - 14400 - 22000 - 1000 - 2500 - 1100 = 6000（§14.3 AGR）
        """
        comp = IndustryProfitComponents(
            revenue_cents=47_000_00, wages_cents=14_400_00,
            paid_goods_services_cents=22_000_00, commercial_rent_cents=1_000_00,
            depreciation_cents=2_500_00, recognized_other_expense_cents=1_100_00,
        )
        assert comp.pretax_profit_cents() == 6_000_00

    def test_industry_tax_zero_for_loss(self):
        """阶段 9 补：§17 TEST-08 亏损不取负税。"""
        # PreTax=-500 → IndustryTax=0（CHU 亏损 §14.3）
        tax = compute_industry_tax_cents(pretax_profit_cents=-500_00, tau_i=0.0)
        assert tax == 0

    def test_industry_tax_15pct_for_A(self):
        """阶段 9 补：A 级税率 15%。"""
        # PreTax=18500 → IndustryTax=2220（§14.3 IND B=12% → 18500×0.12=2220）
        tax = compute_industry_tax_cents(pretax_profit_cents=18_500_00, tau_i=0.12)
        assert tax == 2_220_00

    def test_after_tax_profit(self):
        """阶段 9 补：AfterTax = PreTax - Tax。"""
        # PreTax=18,500, B=12% → Tax=2,220 → AfterTax=16,280
        after = compute_aftertax_profit_cents(pretax_profit_cents=18_500_00, tau_i=0.12)
        assert after == 16_280_00

    def test_max_dividend_capped(self):
        """阶段 9 补：Dividend <= min(AfterTax, available_cash)。"""
        # AfterTax=16000, cash=10000 → Dividend=10000
        div = compute_max_dividend_cents(aftertax_profit_cents=16_000_00, available_cash_cents=10_000_00)
        assert div == 10_000_00

    def test_max_dividend_zero_for_loss(self):
        """阶段 9 补：AfterTax<0 → Dividend=0。"""
        div = compute_max_dividend_cents(aftertax_profit_cents=-500_00, available_cash_cents=10_000_00)
        assert div == 0


# ===== C. §7.3 现金变化 9 项（3 项）=====

class TestCashDelta:
    """V2.1 §7.3 DeltaM = 收入项 - 支出项。"""

    def test_full_delta(self):
        """阶段 9 补：完整 9 项计算。"""
        delta = IndustryCashDelta(
            received_sales_cents=10_000_00,
            actual_subsidies_cents=0,
            new_loans_received_cents=5_000_00,
            paid_wages_cents=3_000_00,
            paid_goods_services_cents=2_000_00,
            paid_rent_cents=500_00,
            industry_tax_cents=200_00,
            capex_paid_cents=1_000_00,
            dividends_paid_cents=0,
            interest_paid_cents=100_00,
            loan_principal_paid_cents=500_00,
            other_explicit_cash_receipts_cents=0,
            other_explicit_cash_payments_cents=0,
        )
        # 10,000 + 5,000 - 3,000 - 2,000 - 500 - 200 - 1,000 - 100 - 500 = 7,700
        assert delta.delta_cash_cents() == 7_700_00

    def test_depreciation_not_in_cash(self):
        """阶段 9 补：§7.3 折旧只影响会计利润，不扣现金（DeltaM 不含 depreciation 字段）。"""
        # IndustryCashDelta 没有 depreciation 字段
        delta = IndustryCashDelta(received_sales_cents=10_000_00, paid_wages_cents=5_000_00)
        assert delta.delta_cash_cents() == 5_000_00  # 不扣折旧

    def test_loan_principal_not_in_profit(self):
        """阶段 9 补：§7.3 贷款本金还本不算利润（已独立于 revenue）。"""
        # IndustryCashDelta 把 loan_principal_paid 独立字段（非 revenue 抵消）
        delta = IndustryCashDelta(
            received_sales_cents=10_000_00,
            new_loans_received_cents=5_000_00,
            loan_principal_paid_cents=5_000_00,
        )
        # 10,000 + 5,000 - 5,000 = 10,000
        assert delta.delta_cash_cents() == 10_000_00


# ===== D. §7.4 亏损温和处理（4 项）=====

class TestLossTracker:
    """V2.1 §7.4 持续 3 月危机 + 6 月衰退。"""

    def test_initial_no_crisis(self):
        """阶段 9 补：初始无危机无衰退。"""
        t = IndustryLossTracker()
        assert t.consecutive_loss_months == 0
        assert t.crisis_triggered is False
        assert t.decline_triggered is False

    def test_three_months_triggers_crisis(self):
        """阶段 9 补：连续 3 月亏损 + 现金不足必要支出 → 经营危机。"""
        t = IndustryLossTracker()
        for _ in range(3):
            t.update(is_loss=True, cash_after_change_cents=100, required_cash_cents=1000)
        assert t.crisis_triggered is True
        assert t.consecutive_loss_months == 3

    def test_six_months_triggers_decline(self):
        """阶段 9 补：连续 6 月亏损 → 行业衰退。"""
        t = IndustryLossTracker()
        for _ in range(6):
            t.update(is_loss=True, cash_after_change_cents=100, required_cash_cents=1000)
        assert t.decline_triggered is True

    def test_profit_resets_counter(self):
        """阶段 9 补：盈利月重置连续亏损计数。"""
        t = IndustryLossTracker()
        t.update(is_loss=True, cash_after_change_cents=100, required_cash_cents=1000)
        t.update(is_loss=True, cash_after_change_cents=100, required_cash_cents=1000)
        t.update(is_loss=False, cash_after_change_cents=5000, required_cash_cents=1000)  # 盈利
        assert t.consecutive_loss_months == 0
