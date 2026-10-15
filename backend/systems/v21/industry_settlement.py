"""V2.1 阶段 9 补：行业月结（§7 行业营业额/税/利润/现金）。

【2026-10-15】Bipole 城市聚合经济系统理论说明书 V2.1 §7 行业月结。

【V2.1 §7.1 收入来源（实际到账，不是产值估算）】
- R_i = R_i,household + R_i,intermediate + R_i,property_maintenance
       + R_i,government + R_i,export + R_i,external_visitor
- 宗教信徒"捐款"属 CHURCH 非营利收入转移，不重复当营业额
- 政府对行业直接补贴列补贴收入，不伪装为市场营业额

【V2.1 §7.2 成本与利润】
- W_i = E_i * w_i（GOV 从市政资金支付，其他六行业从经营资金支付）
- PreTaxProfit_i = R_i - W_i - paid_goods_services_cost_i
                  - commercial_rent_i - depreciation_i - recognized_other_expense_i
- IndustryTax_i = tau_i * max(0, PreTaxProfit_i)  [A=15, B=12, C=8, 无=0]
- AfterTaxProfit_i = PreTaxProfit_i - IndustryTax_i
- Dividend_i <= max(0, AfterTaxProfit_i) 且不超过可支付现金
- 亏损部门不自动被税收补贴，需有明确财政救助动作

【V2.1 §7.3 经营现金变化（9 项）】
DeltaM_i = received_sales + actual_subsidies + new_loans_received
         - paid_wages - paid_goods_services - paid_rent - industry_tax
         - capex_paid - dividends_paid - interest_paid - loan_principal_paid
         + other_explicit_cash_receipts - other_explicit_cash_payments
- 折旧只影响会计利润，不再额外扣一次现金
- 债务本金不计入当期利润
- 产业经营现金与同名行业居民储蓄是不同账户；负现金不能自动由居民储蓄填补

【V2.1 §7.4 行业亏损的温和处理】
- 先用经营现金缓冲；可尝试有资金支持的贷款
- 下调非必要投入和招工
- 持续 3 个月现金无法覆盖必要支出才进入经营危机
- 6 个月持续亏损触发行业衰退事件
- 不做"刚亏损一个月就瞬间倒闭全行业"

【V2.1 阶段 9 补范围】
本模块承载：
- IndustryRevenue 数据类（6 路收入分解）
- IndustryProfitComponents 数据类（R/W/cost/rent/depreciation/other）
- compute_pretax_profit()
- compute_industry_tax()
- compute_aftertax_profit()
- compute_max_dividend_cents()
- IndustryCashDelta 数据类（9 项）
- compute_industry_cash_delta()
- IndustryLossTracker 数据类（3/6 月触发）
- update_loss_tracker()
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


# ===== V2.1 §7.1 收入分解 =====

@dataclass(frozen=True)
class IndustryRevenue:
    """V2.1 §7.1 R_i 6 路收入分解（整数分）。"""
    from_households_cents: int = 0
    intermediate_cents: int = 0
    property_maintenance_cents: int = 0
    from_government_cents: int = 0
    export_cents: int = 0
    external_visitor_cents: int = 0

    def total_cents(self) -> int:
        """R_i = 6 项合计。"""
        return (
            self.from_households_cents + self.intermediate_cents
            + self.property_maintenance_cents + self.from_government_cents
            + self.export_cents + self.external_visitor_cents
        )


# ===== V2.1 §7.2 利润计算 =====

@dataclass(frozen=True)
class IndustryProfitComponents:
    """V2.1 §7.2 利润计算 6 输入（整数分）。"""
    revenue_cents: int
    wages_cents: int                          # W_i = E_i * w_i
    paid_goods_services_cents: int
    commercial_rent_cents: int
    depreciation_cents: int                   # §7.3 只影响会计利润
    recognized_other_expense_cents: int

    def pretax_profit_cents(self) -> int:
        """V2.1 §7.2 PreTaxProfit = R - W - paid_goods - rent - depreciation - other。

        注意 §7.3：折旧只影响会计利润（计入此处），但实际不扣一次现金。
        """
        return (
            self.revenue_cents - self.wages_cents
            - self.paid_goods_services_cents - self.commercial_rent_cents
            - self.depreciation_cents - self.recognized_other_expense_cents
        )


def compute_industry_tax_cents(pretax_profit_cents: int, tau_i: float) -> int:
    """V2.1 §7.2 IndustryTax = tau_i * max(0, PreTaxProfit)。

    §17 TEST-08：禁止亏损征负税。
    """
    if tau_i < 0 or tau_i > 1.0:
        raise ValueError(f"tau_i 越界 [0, 1]：{tau_i}")
    if pretax_profit_cents <= 0:
        return 0
    return int(pretax_profit_cents * tau_i)


def compute_aftertax_profit_cents(pretax_profit_cents: int, tau_i: float) -> int:
    """V2.1 §7.2 AfterTaxProfit = PreTaxProfit - IndustryTax。"""
    tax = compute_industry_tax_cents(pretax_profit_cents, tau_i)
    return pretax_profit_cents - tax


def compute_max_dividend_cents(
    aftertax_profit_cents: int,
    available_cash_cents: int,
) -> int:
    """V2.1 §7.2 Dividend <= max(0, AfterTaxProfit) 且不超过可支付现金。"""
    return min(max(0, aftertax_profit_cents), max(0, available_cash_cents))


# ===== V2.1 §7.3 现金变化（9 项）=====

@dataclass(frozen=True)
class IndustryCashDelta:
    """V2.1 §7.3 DeltaM_i 9 项输入（整数分）。"""
    received_sales_cents: int = 0                # 实际销售收款
    actual_subsidies_cents: int = 0              # 实际政府补贴
    new_loans_received_cents: int = 0            # 新贷款本金
    paid_wages_cents: int = 0                    # 实际工资支出
    paid_goods_services_cents: int = 0
    paid_rent_cents: int = 0
    industry_tax_cents: int = 0
    capex_paid_cents: int = 0
    dividends_paid_cents: int = 0
    interest_paid_cents: int = 0
    loan_principal_paid_cents: int = 0
    other_explicit_cash_receipts_cents: int = 0
    other_explicit_cash_payments_cents: int = 0

    def delta_cash_cents(self) -> int:
        """V2.1 §7.3 DeltaM_i = 收入项 - 支出项。"""
        return (
            self.received_sales_cents
            + self.actual_subsidies_cents
            + self.new_loans_received_cents
            - self.paid_wages_cents
            - self.paid_goods_services_cents
            - self.paid_rent_cents
            - self.industry_tax_cents
            - self.capex_paid_cents
            - self.dividends_paid_cents
            - self.interest_paid_cents
            - self.loan_principal_paid_cents
            + self.other_explicit_cash_receipts_cents
            - self.other_explicit_cash_payments_cents
        )


# ===== V2.1 §7.4 亏损温和处理 =====

@dataclass
class IndustryLossTracker:
    """V2.1 §7.4 行业亏损跟踪。

    - consecutive_loss_months：连续亏损月数
    - crisis_triggered：是否已触发经营危机（>= 3 月）
    - decline_triggered：是否已触发衰退事件（>= 6 月）
    - max_required_cash_cents：每月必要支出基准
    """
    consecutive_loss_months: int = 0
    crisis_triggered: bool = False
    decline_triggered: bool = False
    max_required_cash_cents: int = 0

    def update(
        self,
        *,
        is_loss: bool,
        cash_after_change_cents: int,
        required_cash_cents: int,
    ) -> 'IndustryLossTracker':
        """V2.1 §7.4 月度更新：跟踪连续亏损月数与触发事件。

        触发条件：
        - crisis（3 月）：连续 3 月现金无法覆盖必要支出
        - decline（6 月）：连续 6 月亏损
        """
        if is_loss:
            self.consecutive_loss_months += 1
        else:
            self.consecutive_loss_months = 0

        if (self.consecutive_loss_months >= 3
                and cash_after_change_cents < required_cash_cents):
            self.crisis_triggered = True
        if self.consecutive_loss_months >= 6:
            self.decline_triggered = True
        return self
