"""V2.1 阶段 10：政府接口（§9.1 + §9.2 + §9.3 + §9.4）。

【2026-10-15】Bipole 城市聚合经济系统理论说明书 V2.1 §9 财政、政府接口及宗教。

【V2.1 §9.1 政府可实施的经济动作（非完整政治职权）】
- TAX：税率
- SPEND：公共采购、救济、运行和维护
- BUILD：资本建设
- EMPLOY：政府招聘/工资
- TRADE_ORGANIZE：组织/审批私有贸易
- TRADE_DIRECT：政府钱包实际贸易
- FINANCE：财政融资/借贷
- SERVICE：政府运营服务
- RELIGION_POLICY：宗教政策
- RESERVE：战略库存
- POLICY_EXTENSION：其他待作者设计的未来行动

【V2.1 §9.2 所有政府动作的统一输出协议】
GovernmentAction={
    action_id, actor, legal_permission, target_city,
    payer_account, payee_account, money_amount,
    input_goods, output_goods, start_month, settle_month,
    state_delta_after_delivery, cancellation_terms
}
- 任何未来新增政府权限均不得越过资金/资源守恒。

【V2.1 §9.3 财政收入】
G_income = personal_income_tax + industry_profit_tax + property_tax
         + actual_trade_tax_and_fees + public_asset_operating_remittance
- 财政贷款本金不算收入；还本不算费用；真正的贸易利润只在政府持有并实际卖货时出现。

【V2.1 §9.4 房产税及宗教减免】
- tau_P = 0.08
- tau_H = 0.0012/月
- 宗教初始信徒比 f = 0.55
- PropertyTax = AssessedPropertyValue * tau_H * (1 - 0.15 * f)
- 以后如果能精确记录免税教会资产，可改为显式税基减免，不得叠加两种方式。

【V2.1 阶段 10 范围】
本模块承载：
- GovernmentActionType 枚举（10 个动作 + POLICY_EXTENSION 11 个）
- GovernmentAction frozen dataclass（§9.2 13 字段统一协议）
- GovernmentActionResult 数据类（执行结果）
- 财政收支 G_income 计算（§9.3）
- 房产税 PropertyTax 计算（§9.4 + 宗教减免）
- government_action_validator 校验（资金/资源守恒）
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


# ===== V2.1 §9.1 政府动作枚举 =====

class GovernmentActionType(str, Enum):
    """V2.1 §9.1 政府可实施的经济动作。"""
    TAX = 'tax'                              # 税率（参数）
    SPEND = 'spend'                          # 公共采购/救济/运行/维护
    BUILD = 'build'                          # 资本建设
    EMPLOY = 'employ'                        # 政府招聘/工资
    TRADE_ORGANIZE = 'trade_organize'        # 组织/审批私有贸易
    TRADE_DIRECT = 'trade_direct'            # 政府钱包实际贸易
    FINANCE = 'finance'                      # 财政融资/借贷
    SERVICE = 'service'                      # 政府运营服务
    RELIGION_POLICY = 'religion_policy'      # 宗教政策
    RESERVE = 'reserve'                      # 战略库存
    POLICY_EXTENSION = 'policy_extension'    # 其他待设计


# ===== V2.1 §9.4 默认参数 =====

DEFAULT_TAU_P = 0.08                  # 房产税税率（V2.1 §9.4 注：PIT 单独 §6.4 = 0.08）
DEFAULT_TAU_H = 0.0012                # 房产税月率
DEFAULT_FAITHFUL_RATIO = 0.55         # 初始信徒比（§9.4 同步 §9.5）


# ===== V2.1 §9.2 统一协议 =====

@dataclass(frozen=True)
class GovernmentAction:
    """V2.1 §9.2 所有政府动作的统一输出协议。

    字段严格对应 §9.2：
    - action_id：动作唯一编号
    - action_type：动作类型（GovernmentActionType）
    - actor：发起方账户（默认 = government）
    - legal_permission：法律依据字符串
    - target_city：目标城市
    - payer_account：付款账户
    - payee_account：收款账户
    - money_amount_cents：金额（整数分）
    - input_goods：投入商品 dict[good_id, qty]
    - output_goods：产出商品 dict[good_id, qty]
    - start_month：开始月份
    - settle_month：结算月份
    - state_delta_after_delivery：交付后状态变化 dict
    - cancellation_terms：取消条款 dict
    """
    action_id: str
    action_type: GovernmentActionType
    actor: str = 'government'
    legal_permission: str = ''
    target_city: str = ''
    payer_account: str = 'government'
    payee_account: str = ''
    money_amount_cents: int = 0
    input_goods: dict[str, float] = field(default_factory=dict)
    output_goods: dict[str, float] = field(default_factory=dict)
    start_month: int = 0
    settle_month: int = 0
    state_delta_after_delivery: dict = field(default_factory=dict)
    cancellation_terms: dict = field(default_factory=dict)


@dataclass
class GovernmentActionResult:
    """V2.1 §9.2 政府动作执行结果。"""
    action: GovernmentAction
    executed: bool
    actual_money_cents: int = 0       # 实际支付金额（受 payer 现金约束）
    error_message: str = ''
    ledger_transactions: list[tuple[str, str, int, str]] = field(default_factory=list)


# ===== V2.1 §9.2 校验器 =====

def validate_government_action(action: GovernmentAction) -> list[str]:
    """V2.1 §9.2 校验：未来新增政府权限均不得越过资金/资源守恒。

    返回错误消息列表（空 = 通过）。
    """
    errors: list[str] = []

    if not action.action_id:
        errors.append('action_id 不能为空')

    if action.money_amount_cents < 0:
        errors.append(f'资金金额不能为负：{action.money_amount_cents}')

    if not action.payer_account:
        errors.append('payer_account 不能为空')

    # §9.3 财政贷款本金不算收入（FINANCE 类型不允许用于伪造收入）
    if action.action_type == GovernmentActionType.FINANCE:
        if action.input_goods or action.output_goods:
            # 借贷不应有商品产出（除非用于储备）
            pass  # 占位：未来由具体 FINANCE 子类型细化

    # §9.5 宗教政策的法律依据必须明确（5 档之一）
    if action.action_type == GovernmentActionType.RELIGION_POLICY:
        if not action.legal_permission:
            errors.append('RELIGION_POLICY 必须指定 legal_permission（五档之一）')

    return errors


# ===== V2.1 §9.3 财政收入计算 =====

@dataclass
class GovernmentRevenue:
    """V2.1 §9.3 G_income 8 项收入聚合（5 主项 + 3 扩展项）。"""
    personal_income_tax_cents: int = 0
    industry_profit_tax_cents: int = 0
    property_tax_cents: int = 0
    actual_trade_tax_and_fees_cents: int = 0
    public_asset_operating_remittance_cents: int = 0
    central_transfer_cents: int = 0                # §9.3 扩展
    church_donation_cents: int = 0                 # §9.3 扩展（教会给政府）
    other_actual_revenues_cents: int = 0            # §9.3 扩展

    def total_cents(self) -> int:
        """V2.1 §9.3 G_income = 8 项合计。"""
        return (
            self.personal_income_tax_cents
            + self.industry_profit_tax_cents
            + self.property_tax_cents
            + self.actual_trade_tax_and_fees_cents
            + self.public_asset_operating_remittance_cents
            + self.central_transfer_cents
            + self.church_donation_cents
            + self.other_actual_revenues_cents
        )


def compute_government_revenue(rev: GovernmentRevenue) -> int:
    """V2.1 §9.3 短工具：G_income 总和。"""
    return rev.total_cents()


# ===== V2.1 §9.3 财政支出计算 =====

@dataclass
class GovernmentExpense:
    """V2.1 §9.3 G_expense 8 项支出聚合。"""
    public_wages_cents: int = 0
    welfare_cents: int = 0
    facility_operating_purchases_cents: int = 0
    maintenance_cents: int = 0
    capital_construction_cents: int = 0
    subsidies_cents: int = 0
    debt_interest_cents: int = 0
    other_actual_government_purchases_cents: int = 0

    def total_cents(self) -> int:
        """V2.1 §9.3 G_expense = 8 项合计。"""
        return (
            self.public_wages_cents
            + self.welfare_cents
            + self.facility_operating_purchases_cents
            + self.maintenance_cents
            + self.capital_construction_cents
            + self.subsidies_cents
            + self.debt_interest_cents
            + self.other_actual_government_purchases_cents
        )


def compute_government_expense(exp: GovernmentExpense) -> int:
    """V2.1 §9.3 短工具：G_expense 总和。"""
    return exp.total_cents()


# ===== V2.1 §9.3 财政结余 + 现金 + 债务更新 =====

def compute_fiscal_balance_cents(rev: GovernmentRevenue, exp: GovernmentExpense) -> int:
    """V2.1 §9.3 Fiscal_balance = G_income - G_expense。"""
    return rev.total_cents() - exp.total_cents()


def compute_government_cash_next(
    M_G_current_cents: int,
    rev: GovernmentRevenue,
    exp: GovernmentExpense,
    new_borrowing_cents: int,
    principal_repayment_cents: int,
) -> int:
    """V2.1 §9.3 M_G,t+1 = M_G,t + Fiscal_balance + new_borrowing - principal_repayment。"""
    fiscal = compute_fiscal_balance_cents(rev, exp)
    return M_G_current_cents + fiscal + new_borrowing_cents - principal_repayment_cents


def compute_government_debt_next(
    Debt_G_current_cents: int,
    new_borrowing_cents: int,
    principal_repayment_cents: int,
) -> int:
    """V2.1 §9.3 Debt_G,t+1 = Debt_G,t + new_borrowing - principal_repayment。"""
    return Debt_G_current_cents + new_borrowing_cents - principal_repayment_cents


# ===== V2.1 §9.4 房产税计算 =====

@dataclass(frozen=True)
class PropertyTaxInput:
    """V2.1 §9.4 房产税输入。"""
    assessed_value_cents: int           # 应税房产评估值（整数分）
    faithful_ratio: float = DEFAULT_FAITHFUL_RATIO  # 本城信徒比 [0, 1]
    tau_H: float = DEFAULT_TAU_H        # 月税率

    def __post_init__(self):
        if self.assessed_value_cents < 0:
            raise ValueError(f"评估值不能为负：{self.assessed_value_cents}")
        if not 0.0 <= self.faithful_ratio <= 1.0:
            raise ValueError(f"信徒比越界 [0, 1]：{self.faithful_ratio}")


def compute_property_tax_cents(inp: PropertyTaxInput) -> int:
    """V2.1 §9.4 PropertyTax = AssessedPropertyValue * tau_H * (1 - 0.15 * f)。

    短期用 (1-0.15*f) 折扣近似；未来精确记录免税教会资产后可改为显式税基减免，
    不得叠加两种方式。
    """
    discount = 1.0 - 0.15 * inp.faithful_ratio
    tax = inp.assessed_value_cents * inp.tau_H * discount
    return int(tax)  # 向下取整到分


# ===== V2.1 §9.1 高级动作调度（占位）=====

def execute_government_action(
    action: GovernmentAction,
    payer_cash_cents: int = 0,
) -> GovernmentActionResult:
    """V2.1 §9.2 执行政府动作（占位实现）。

    步骤：
    1. validate_government_action 校验
    2. 校验 payer 现金（实际支付 ≤ payer_cash_cents）
    3. 返回结果（实际过账由调用方通过 ledger.pay() 处理）

    完整业务实现由阶段 10 后续 PR 补充。
    """
    errors = validate_government_action(action)
    if errors:
        return GovernmentActionResult(
            action=action,
            executed=False,
            actual_money_cents=0,
            error_message='; '.join(errors),
        )

    actual = min(action.money_amount_cents, max(0, payer_cash_cents))
    return GovernmentActionResult(
        action=action,
        executed=actual > 0 or action.money_amount_cents == 0,
        actual_money_cents=actual,
    )
