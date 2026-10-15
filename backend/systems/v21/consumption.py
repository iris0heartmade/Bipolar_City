"""V2.1 阶段 6：居民消费预算与可支配收入（§6.4 + §6.5）。

【2026-10-15】Bipole 城市聚合经济系统理论说明书 V2.1 §6.4 + §6.5。

【V2.1 §6.4 居民实际收入与税收】
- Y_h,t^gross = paid_wages_h + paid_dividends_h + net_property_income_h
              + actually_paid_welfare_h + other_cash_income_h
- Taxable_h = wage_h(nonagricultural) + taxable_dividends_h
             + taxable_net_property_income_h + other_taxable_income_h
- PIT_h = tau_P * max(Taxable_h, 0)，tau_P 初始 = 0.08
- 农业工资免 PIT；是否对农业兼职收入或其他投资收入征税按收入性质，而非户籍永久免税。
- Household_disposable_h = Y_h^gross - PIT_h - property_tax_paid_h - other_actual_taxes_h。
- 公共救济是已付现金收入，但不默认作为 PIT 税基。

【V2.1 §6.5 标准消费需求与资金约束】
- D_h^nominal = P_h * Cneed_c,t
- available_h = S_h,t + actual_cash_income_h - actual_taxes_h - earmarked_obligations_h。
- optional_savings_draw <= savings_draw_limit * S_h,t；正常先用当前可支配收入。
- RealSpending_h <= max(0, available_h) 且 <= 有效订单可交付总价。
- 先保障有实际可购资源的粮食/基础住房/燃料，再按篮子权重购买可选服务。
- 预算不足按比例缩减可选需求；缺货不允许凭空扣钱；只计已成交消费。
- 宗教额外捐助等纯转移应从储蓄另扣，不得当作商品最终消费再计一次。

【V2.1 阶段 6 范围】
本模块承载：
- HouseholdIncome 数据类（5 类现金收入聚合）
- TaxableIncome 数据类（PIT 税基）
- household_gross_income_cents / household_disposable_cents 工具函数
- CneedDemand 计算（按 P_h 与 Cneed_c,t）
- HouseholdBudget（资金约束：S + income - taxes - obligations）
"""
from dataclasses import dataclass, field
from typing import Optional


# ===== V2.1 §6.4 居民收入数据 =====

@dataclass(frozen=True)
class HouseholdIncome:
    """V2.1 §6.4 居民群体当月现金收入。

    字段严格对应 §6.4 公式：
    - paid_wages_cents：已付工资（整数分）
    - paid_dividends_cents：已付分红（整数分）
    - net_property_income_cents：净房产收益（整数分）
    - paid_welfare_cents：已付公共救济（整数分；不作为 PIT 税基）
    - other_cash_income_cents：其它现金收入（整数分）

    注意：公共救济（paid_welfare_cents）不计入 PIT 税基（§6.4）。
    """
    paid_wages_cents: int = 0
    paid_dividends_cents: int = 0
    net_property_income_cents: int = 0
    paid_welfare_cents: int = 0           # 救济不计入 PIT
    other_cash_income_cents: int = 0

    def total_gross_cents(self) -> int:
        """V2.1 §6.4 Y_h^gross = 5 项合计。"""
        return (
            self.paid_wages_cents + self.paid_dividends_cents
            + self.net_property_income_cents + self.paid_welfare_cents
            + self.other_cash_income_cents
        )


@dataclass(frozen=True)
class TaxableIncome:
    """V2.1 §6.4 PIT 税基（不含公共救济）。

    字段：
    - nonag_wages_cents：非农工资（农业工资免税）
    - taxable_dividends_cents：应税分红
    - taxable_net_property_income_cents：应税净房产收益
    - other_taxable_income_cents：其它应税收入
    """
    nonag_wages_cents: int = 0
    taxable_dividends_cents: int = 0
    taxable_net_property_income_cents: int = 0
    other_taxable_income_cents: int = 0

    def total_taxable_cents(self) -> int:
        """Taxable_h = 4 项合计。"""
        return (
            self.nonag_wages_cents + self.taxable_dividends_cents
            + self.taxable_net_property_income_cents
            + self.other_taxable_income_cents
        )


# ===== V2.1 §6.4 + §6.5 可支配收入计算 =====

# tau_P = 0.08 (V2.1 §6.4)
DEFAULT_PIT_RATE = 0.08

# 默认储蓄动用比例上限（§3 world.savings_draw_limit = 0.10）
DEFAULT_SAVINGS_DRAW_LIMIT = 0.10


@dataclass(frozen=True)
class HouseholdBudget:
    """V2.1 §6.5 居民群体当月可用预算。

    available_h = S_h,t + actual_cash_income_h - actual_taxes_h - earmarked_obligations_h。
    """
    savings_cents: int                  # 当前储蓄
    cash_income_cents: int              # 当月现金收入
    taxes_cents: int = 0                # 当月税（PIT + 房产税 + 其他）
    earmarked_obligations_cents: int = 0    # 预留义务（房租、贷款等）

    def available_cents(self) -> int:
        """V2.1 §6.5 available_h 计算。"""
        return max(
            0,
            self.savings_cents + self.cash_income_cents
            - self.taxes_cents - self.earmarked_obligations_cents,
        )

    def max_savings_draw_cents(self, draw_limit: float = DEFAULT_SAVINGS_DRAW_LIMIT) -> int:
        """V2.1 §6.5 optional_savings_draw <= draw_limit * S_h,t。"""
        if draw_limit < 0 or draw_limit > 1.0:
            raise ValueError(f"draw_limit 越界 [0, 1]：{draw_limit}")
        return int(self.savings_cents * draw_limit)


def household_gross_income_cents(income: HouseholdIncome) -> int:
    """V2.1 §6.4 Y_h^gross = 5 项合计（短工具函数）。"""
    return income.total_gross_cents()


def household_disposable_cents(
    gross_income_cents: int,
    pit_cents: int,
    property_tax_cents: int = 0,
    other_taxes_cents: int = 0,
) -> int:
    """V2.1 §6.4 Household_disposable_h = Y_h^gross - PIT_h - property_tax - other_taxes。"""
    return gross_income_cents - pit_cents - property_tax_cents - other_taxes_cents


# ===== V2.1 §6.5 标准消费需求 =====

@dataclass(frozen=True)
class CneedDemand:
    """V2.1 §6.5 D_h^nominal = P_h * Cneed_c,t。

    字段：
    - population_P_h：群体人口 P_h（与 §6.5 一致）
    - Cneed_c_t_cr_per_person：城市本地实际生活成本 CR/人/月
    """
    population_P_h: int
    Cneed_c_t_cr_per_person: float

    def nominal_demand_cr(self) -> float:
        """V2.1 §6.5 D_h^nominal = P_h * Cneed_c,t（CR/人/月 → 群体总需求 CR）。"""
        return self.population_P_h * self.Cneed_c_t_cr_per_person

    def nominal_demand_cents(self) -> int:
        """名义需求（整数分）。"""
        return int(self.nominal_demand_cr() * 100)


def compute_Cneed_demand_cents(
    population_P_h: int,
    Cneed_c_t_cr_per_person: float,
) -> int:
    """V2.1 §6.5 短工具：D_h^nominal = P_h * Cneed_c,t（整数分）。"""
    return int(population_P_h * Cneed_c_t_cr_per_person * 100)


# ===== V2.1 §6.5 资金约束 =====

def effective_real_spending_cents(
    *,
    budget: HouseholdBudget,
    effective_order_value_cents: int,    # 有效订单可交付总价（V2.1 §6.5）
    draw_limit: float = DEFAULT_SAVINGS_DRAW_LIMIT,
) -> int:
    """V2.1 §6.5 RealSpending_h <= min(available_h, 有效订单可交付总价)。

    资金约束：先满足基本生活需求，再按篮子权重购买可选服务。
    本函数返回实际可消费金额（受 available + 订单上限双重约束）。
    """
    available = budget.available_cents()
    return max(0, min(available, effective_order_value_cents))


# ===== V2.1 §6.6 储蓄月度更新 =====

@dataclass(frozen=True)
class SavingsDelta:
    """V2.1 §6.6 储蓄月度变化（6 项）。"""
    real_cash_receipts_cents: int = 0
    real_tax_payments_cents: int = 0
    actual_consumer_purchases_cents: int = 0
    other_cash_transfers_out_cents: int = 0
    other_cash_transfers_in_cents: int = 0


def compute_savings_next_cents(
    *,
    S_h_current_cents: int,
    delta: SavingsDelta,
) -> int:
    """V2.1 §6.6 S_h,t+1 = S_h,t + 收 − 税 − 消 − 转移出 + 转移入。

    注意：
    - 若住房支出包含在 actual_consumer_purchases，切勿再次扣租金
    - S_h 不能在默认模式无授权地变成负数；资金不足产生购买缩减、欠账事件或显式贷款
    - 迁移带走的现金计入 other_cash_transfers_out（不重复扣 S）
    """
    return (
        S_h_current_cents
        + delta.real_cash_receipts_cents
        - delta.real_tax_payments_cents
        - delta.actual_consumer_purchases_cents
        - delta.other_cash_transfers_out_cents
        + delta.other_cash_transfers_in_cents
    )
