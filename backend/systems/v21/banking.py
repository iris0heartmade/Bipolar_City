"""V2.1 阶段 4：银行与融资（§8）。

【2026-10-15】Bipole 城市聚合经济系统理论说明书 V2.1 §8 银行与融资。

【V2.1 §8 关键约束】
- §8.1 最简贷款基金制：new_loan_principal <= Bank_cash_available_for_lending
- §8.1 银行贷出本金时：银行现金 ↓、贷款资产 ↑；借款方现金 ↑、债务 ↑
- §8.1 偿还本金时反向
- §8.1 利息从借款方支付到持有债权的银行/其他资产持有人
- §8.1 金融服务费可以计入服务营业收入
- §8.1 **新贷款本金不计入 GDP 和营业收入**（V2.1 §13 强调）
- §8.2 银行债权可按产权交易转让；**债务人的本金和利息义务保留**
- §8.2 多城之间银行可交叉持债
- §8.2 **不实现**现代商业银行的存款创造/准备金/信用乘数
- §8.3 **不允许**负债不受限制地救援全部亏损行业
- §8.3 借贷受现金、风险、债务上限或合同约束

【V2.1 阶段 4 范围】
本模块承载：Loan 数据类、LoanStatus 枚举、LoanBook 集合、originate_loan
（贷款发起）、repay_loan（还本付息）、transfer_loan（债权转让）。
利息计提 monthly 化、坏账核销等高级功能由 V2.1 阶段 9 月结引擎集成。
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from backend.core.errors import GameError
from backend.systems.v21.accounts import OCEAN_ACCOUNT, firm_account_id
from backend.systems.v21.ledger import Ledger


# V2.1 §8.1 默认银行参数
DEFAULT_MAX_LOAN_TO_BANK_CASH_RATIO = 1.0   # 贷款本金不超过银行可用现金
DEFAULT_MAX_LOAN_TO_BORROWER_CASH_RATIO = 5.0   # 单笔贷款本金不超过借款方现金
DEFAULT_INTEREST_RATE_MONTHLY = 0.01          # 默认 1%/月
DEFAULT_MAX_INTEREST_RATE_MONTHLY = 0.10      # 10%/月 上限


class LoanStatus(str, Enum):
    """V2.1 §8 贷款状态。"""
    ACTIVE = 'active'             # 活跃
    REPAID = 'repaid'             # 已还清
    DEFAULTED = 'defaulted'       # 违约
    WRITTEN_OFF = 'written_off'   # 核销


class LoanPurpose(str, Enum):
    """贷款用途（V2.1 §8.3 防无限救援）。"""
    BUSINESS_OPERATING = 'business_operating'   # 经营周转
    BUSINESS_EXPANSION = 'business_expansion'   # 业务扩张
    GOVERNMENT_DEFICIT = 'government_deficit'   # 财政赤字（V2.1 §8.3 受限）
    EMERGENCY_RELIEF = 'emergency_relief'       # 紧急救济（V2.1 §8.3 禁用）
    PERSONAL = 'personal'                       # 个人消费


# 紧急救济明确禁用（V2.1 §8.3 不允许负债无限救援）
PROHIBITED_PURPOSES = frozenset({LoanPurpose.EMERGENCY_RELIEF})


@dataclass(frozen=True)
class Loan:
    """V2.1 §8.1 贷款数据。

    principal_cents：原始本金（整数分）
    interest_rate_monthly：月利率（小数，如 0.01 = 1%/月）
    monthly_interest_cents = principal_cents * interest_rate_monthly（首月）
    """
    loan_id: str
    principal_cents: int
    interest_rate_monthly: float
    start_month: int
    maturity_month: int                        # 预计到期月（含宽限期）
    borrower_account: str                     # 借款方账户
    lender_account: str                       # 当前持有债权的账户
    original_lender_account: str              # 原始债权人（不可变，V2.1 §8.2）
    status: LoanStatus = LoanStatus.ACTIVE
    purpose: LoanPurpose = LoanPurpose.BUSINESS_OPERATING
    grace_period_months: int = 0              # 宽限期（V2.1 阶段 4 简化）
    notes: str = ''

    def monthly_interest_cents(self) -> int:
        """V2.1 §8.1 首月利息（简化：principal * monthly_rate，向下取整）。"""
        return int(self.principal_cents * self.interest_rate_monthly)

    def is_overdue(self, current_month: int) -> bool:
        """是否已过到期月。"""
        return current_month > self.maturity_month and self.status == LoanStatus.ACTIVE

    def outstanding_principal_cents(self, repaid_cents: int) -> int:
        """剩余未还本金。"""
        return max(0, self.principal_cents - repaid_cents)


class LoanBook:
    """V2.1 §8 银行账本：所有贷款的集合。

    loan_id 唯一；按 lender_account 聚合可计算各账户持有的债权总价值。
    """
    def __init__(self):
        self._loans: dict[str, Loan] = {}
        # lender_account -> {loan_id -> repaid_principal_cents}（累计已还本金）
        self._repaid_principal: dict[str, dict[str, int]] = {}

    def add(self, loan: Loan) -> Loan:
        """添加贷款。loan_id 必须唯一。"""
        if loan.loan_id in self._loans:
            raise KeyError(f"贷款 ID 重复：{loan.loan_id}")
        if loan.principal_cents <= 0:
            raise ValueError(f"贷款本金必须为正：{loan.loan_id}")
        if not 0.0 <= loan.interest_rate_monthly <= DEFAULT_MAX_INTEREST_RATE_MONTHLY:
            raise ValueError(f"月利率越界 [{loan.interest_rate_monthly}]：{loan.loan_id}")
        if loan.maturity_month < loan.start_month:
            raise ValueError(f"到期月早于起息月：{loan.loan_id}")
        if loan.purpose in PROHIBITED_PURPOSES:
            raise ValueError(f"贷款用途 {loan.purpose} 被 V2.1 §8.3 禁用：{loan.loan_id}")
        self._loans[loan.loan_id] = loan
        self._repaid_principal[loan.loan_id] = 0
        return loan

    def get(self, loan_id: str) -> Optional[Loan]:
        return self._loans.get(loan_id)

    def __contains__(self, loan_id: str) -> bool:
        return loan_id in self._loans

    def __iter__(self):
        return iter(self._loans.values())

    def loans_by_lender(self, lender_account: str) -> list[Loan]:
        """V2.1 §8.2：某账户持有的所有贷款。"""
        return [loan for loan in self._loans.values() if loan.lender_account == lender_account]

    def loans_by_borrower(self, borrower_account: str) -> list[Loan]:
        """某账户作为借款方的所有贷款。"""
        return [loan for loan in self._loans.values() if loan.borrower_account == borrower_account]

    def total_outstanding_principal_cents(self, lender_account: str) -> int:
        """V2.1 §8.2：某账户持有的所有贷款剩余未还本金。"""
        total = 0
        for loan in self.loans_by_lender(lender_account):
            repaid = self._repaid_principal.get(loan.loan_id, 0)
            total += loan.outstanding_principal_cents(repaid)
        return total

    def total_debt_principal_cents(self, borrower_account: str) -> int:
        """某账户的债务总额。"""
        total = 0
        for loan in self.loans_by_borrower(borrower_account):
            repaid = self._repaid_principal.get(loan.loan_id, 0)
            total += loan.outstanding_principal_cents(repaid)
        return total

    def _record_repayment(self, loan_id: str, repaid_cents: int):
        """内部：记录已还本金。

        还清后状态变 REPAID（V2.1 §8）。Loan 是 frozen dataclass，
        但状态作为还款动作的副作用需要变更，用 object.__setattr__ 绕开 frozen。
        """
        if loan_id not in self._loans:
            raise KeyError(f"贷款不存在：{loan_id}")
        loan = self._loans[loan_id]
        previous = self._repaid_principal.get(loan_id, 0)
        new_repaid = min(loan.principal_cents, previous + repaid_cents)
        self._repaid_principal[loan_id] = new_repaid
        if new_repaid >= loan.principal_cents:
            # Loan 是 frozen dataclass，状态变更用 object.__setattr__ 绕开
            object.__setattr__(loan, 'status', LoanStatus.REPAID)


# ===== 银行核心操作 =====

def bank_account_id() -> str:
    """V2.1 §8.1 BNK 行业经营现金账户 ID（与 BNK 行业共用）。"""
    return firm_account_id('BNK')


def available_cash_for_lending(ledger: Ledger, bank_account: str = None) -> int:
    """V2.1 §8.1 银行可贷出现金（基金制）= max(0, bank_cash - 已贷出未还本金合计）。"""
    bank = bank_account or bank_account_id()
    if bank not in ledger.book:
        return 0
    bank_cash = ledger.book[bank].available_cents
    # 借出未还本金 = sum 银行持有的所有贷款的 outstanding
    if not hasattr(ledger, 'loan_book') or ledger.loan_book is None:
        return bank_cash
    outstanding = ledger.loan_book.total_outstanding_principal_cents(bank)
    return max(0, bank_cash - outstanding)


def originate_loan(
    ledger: Ledger,
    loan_book: LoanBook,
    *,
    loan_id: str,
    principal_cents: int,
    interest_rate_monthly: float,
    start_month: int,
    maturity_month: int,
    borrower_account: str,
    lender_account: Optional[str] = None,
    purpose: LoanPurpose = LoanPurpose.BUSINESS_OPERATING,
    grace_period_months: int = 0,
    max_loan_to_bank_cash_ratio: float = DEFAULT_MAX_LOAN_TO_BANK_CASH_RATIO,
    max_loan_to_borrower_cash_ratio: float = DEFAULT_MAX_LOAN_TO_BANK_CASH_RATIO,
    notes: str = '',
) -> Loan:
    """V2.1 §8.1 发起贷款。

    1. 校验基金制：principal <= bank_cash_available_for_lending
    2. 校验比例：principal <= max_loan_to_borrower_cash_ratio * borrower_cash
    3. 校验用途：不能是 PROHIBITED_PURPOSES（§8.3）
    4. 校验利率：0..MAX_INTEREST
    5. 创建 Loan 数据
    6. 过账：bank.cash -= principal；borrower.cash += principal
    7. 计入 LoanBook
    8. **新贷款本金不计入 GDP 和营业收入**（V2.1 §13）

    Returns: 创建的 Loan 数据
    """
    if principal_cents <= 0:
        raise ValueError("贷款本金必须为正")
    if interest_rate_monthly < 0 or interest_rate_monthly > DEFAULT_MAX_INTEREST_RATE_MONTHLY:
        raise ValueError(f"月利率越界 [0, {DEFAULT_MAX_INTEREST_RATE_MONTHLY}]：{interest_rate_monthly}")
    if purpose in PROHIBITED_PURPOSES:
        raise ValueError(f"贷款用途 {purpose} 被 V2.1 §8.3 禁用")

    lender = lender_account or bank_account_id()
    if lender not in ledger.book:
        raise ValueError(f"贷款方账户不存在：{lender}")
    if borrower_account not in ledger.book:
        raise ValueError(f"借款方账户不存在：{borrower_account}")
    if lender == borrower_account:
        raise ValueError("贷款方与借款方不能相同")

    # 基金制（V2.1 §8.1）
    available = available_cash_for_lending(ledger, lender)
    if principal_cents > available * max_loan_to_bank_cash_ratio:
        raise GameError(
            f"贷款本金 {principal_cents} 超过银行可贷出现金 {available} "
            f"× 比例 {max_loan_to_bank_cash_ratio}（V2.1 §8.1 基金制）"
        )

    # 借款方现金比例（V2.1 §8.3 风险约束）
    borrower_cash = ledger.book[borrower_account].available_cents
    if principal_cents > borrower_cash * max_loan_to_borrower_cash_ratio:
        raise GameError(
            f"贷款本金 {principal_cents} 超过借款方现金 {borrower_cash} "
            f"× 比例 {max_loan_to_borrower_cash_ratio}（V2.1 §8.3 风险约束）"
        )

    # 创建 Loan 数据
    loan = Loan(
        loan_id=loan_id,
        principal_cents=principal_cents,
        interest_rate_monthly=interest_rate_monthly,
        start_month=start_month,
        maturity_month=maturity_month,
        borrower_account=borrower_account,
        lender_account=lender,
        original_lender_account=lender,
        status=LoanStatus.ACTIVE,
        purpose=purpose,
        grace_period_months=grace_period_months,
        notes=notes,
    )
    loan_book.add(loan)

    # 过账：银行现金 ↓；借款方现金 ↑
    # 注意：使用 ledger.pay() 让 cash_cents 真正过账；新贷款本金不计入 GDP（V2.1 §13）
    ledger.pay(lender, borrower_account, principal_cents, 'loan_origination')

    return loan


def repay_loan(
    ledger: Ledger,
    loan_book: LoanBook,
    loan_id: str,
    principal_cents: int,
    interest_cents: int = 0,
) -> tuple[int, int]:
    """V2.1 §8.1 还本付息。

    1. borrower -> lender 还本
    2. borrower -> lender 付息（利息从 borrower 支付到 lender）
    3. 记录已还本金；status=REPAID 当还清

    Returns: (实际还本, 实际付息)（短付可能为 0）
    """
    if loan_id not in loan_book:
        raise KeyError(f"贷款不存在：{loan_id}")
    loan = loan_book.get(loan_id)
    if loan.status != LoanStatus.ACTIVE:
        raise GameError(f"贷款 {loan_id} 状态为 {loan.status}，不能还本付息")

    if principal_cents <= 0 and interest_cents <= 0:
        return 0, 0

    # 计算短付
    actual_principal = min(principal_cents, loan.principal_cents - loan_book._repaid_principal.get(loan_id, 0))
    actual_principal = max(0, actual_principal)
    actual_interest = max(0, interest_cents)

    # 过账
    if actual_principal > 0:
        ledger.pay(loan.borrower_account, loan.lender_account, actual_principal, 'loan_principal_repay')
    if actual_interest > 0:
        ledger.pay(loan.borrower_account, loan.lender_account, actual_interest, 'loan_interest_repay')

    # 记录已还本金
    if actual_principal > 0:
        loan_book._record_repayment(loan_id, actual_principal)

    return actual_principal, actual_interest


def transfer_loan(
    loan_book: LoanBook,
    loan_id: str,
    new_lender_account: str,
    transfer_price_cents: int = 0,
) -> Loan:
    """V2.1 §8.2 银行债权转让。

    关键约束：债务人（borrower）的本金和利息义务**保留**；
    仅 lender_account 变更为 new_lender_account。

    transfer_price_cents：转让价格（V2.1 §8.2 债权可按产权交易转让）。
    本方法只变更 loan.lender_account，不处理转让价款的过账；
    转让价款的实际付款由调用方通过 ledger.pay() 处理。
    """
    if loan_id not in loan_book:
        raise KeyError(f"贷款不存在：{loan_id}")
    loan = loan_book.get(loan_id)
    if loan.status != LoanStatus.ACTIVE:
        raise GameError(f"贷款 {loan_id} 状态为 {loan.status}，不能转让")

    # 创建新 Loan（保留原 loan_id 但 lender_account 改变；original_lender_account 不可变）
    new_loan = Loan(
        loan_id=loan.loan_id,
        principal_cents=loan.principal_cents,
        interest_rate_monthly=loan.interest_rate_monthly,
        start_month=loan.start_month,
        maturity_month=loan.maturity_month,
        borrower_account=loan.borrower_account,
        lender_account=new_lender_account,
        original_lender_account=loan.original_lender_account,
        status=loan.status,
        purpose=loan.purpose,
        grace_period_months=loan.grace_period_months,
        notes=loan.notes,
    )
    # 替换
    loan_book._loans[loan_id] = new_loan
    # 保留已还本金记录
    return new_loan
