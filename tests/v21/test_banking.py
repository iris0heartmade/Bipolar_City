"""V2.1 阶段 4 银行与融资验收测试（2026-10-15）。

【验收清单】（V2.1 §8）
- [x] LoanStatus / LoanPurpose 枚举完整性
- [x] Loan 数据类：monthly_interest / is_overdue / outstanding_principal
- [x] LoanBook：add/get/iter/loans_by_lender/loans_by_borrower
- [x] LoanBook：total_outstanding_principal_cents / total_debt_principal_cents
- [x] available_cash_for_lending：基金制（bank_cash - 已贷出）
- [x] originate_loan：成功路径（loan_book + ledger 双过账）
- [x] originate_loan：8 类校验失败
- [x] repay_loan：还本 + 付息 + status=REPAID
- [x] transfer_loan：债权转让保留 borrower 义务
- [x] JSON 配置：default_params / prohibited_purposes / initial_state

【术语】
- Loan（贷款）：银行贷出的本金+利息合同
- lender（贷款方）：当前持有债权的账户（V2.1 §8.2 可转让）
- borrower（借款方）：欠钱的账户（V2.1 §8.2 义务保留）
- original_lender（原始债权人）：贷款首次放出的账户（V2.1 §8.2 不可变）
- Fund（基金制）：V2.1 §8.1 要求贷款本金 ≤ 银行可贷出现金（即银行现金减去已贷出未还本金）
"""
import pytest

from backend.core.errors import GameError
from backend.systems.v21.accounts import (
    OCEAN_ACCOUNT, firm_account_id, cohort_account_id, church_account_id,
    government_account_id,
)
from backend.systems.v21.banking import (
    LoanStatus, LoanPurpose,
    PROHIBITED_PURPOSES,
    DEFAULT_MAX_LOAN_TO_BANK_CASH_RATIO,
    DEFAULT_MAX_LOAN_TO_BORROWER_CASH_RATIO,
    DEFAULT_INTEREST_RATE_MONTHLY,
    DEFAULT_MAX_INTEREST_RATE_MONTHLY,
    Loan, LoanBook,
    bank_account_id, available_cash_for_lending,
    originate_loan, repay_loan, transfer_loan,
)
from backend.systems.v21.ledger import Ledger, build_industrial_city_accounts
from backend.systems.v21.loader import load_banking_config


# ===== 测试夹具 =====

@pytest.fixture
def ledger():
    """构建测试用最小账本：1 个银行 + 1 个企业（借款方）+ ocean + PROP。"""
    book = build_industrial_city_accounts(
        city_id='test_city',
        industry_opening_cents={'BNK': 10_000_00, 'IND': 5_000_00},  # 银行 10000 CR、企业 5000 CR
        cohort_opening_cents={h: 0 for h in range(8)},
        government_opening_cents=0,
        church_opening_cents=0,
        ocean_opening_cents=0,
    )
    return Ledger(city_id='test_city', book=book)


@pytest.fixture
def ledger_with_loan_book(ledger):
    """给 ledger 注入 LoanBook（用于测试 available_cash_for_lending 联动）。"""
    ledger.loan_book = LoanBook()
    return ledger


@pytest.fixture
def loan_book():
    return LoanBook()


# ===== A. LoanStatus / LoanPurpose 枚举（3 项）=====

class TestEnums:
    """V2.1 §8 贷款状态与用途枚举。"""

    def test_loan_status_has_4_values(self):
        """阶段 4：LoanStatus 必须有 ACTIVE/REPAID/DEFAULTED/WRITTEN_OFF 四个值。"""
        assert LoanStatus.ACTIVE.value == 'active'
        assert LoanStatus.REPAID.value == 'repaid'
        assert LoanStatus.DEFAULTED.value == 'defaulted'
        assert LoanStatus.WRITTEN_OFF.value == 'written_off'

    def test_loan_purpose_has_5_values(self):
        """阶段 4：LoanPurpose 必须有 5 种用途。"""
        assert LoanPurpose.BUSINESS_OPERATING.value == 'business_operating'
        assert LoanPurpose.BUSINESS_EXPANSION.value == 'business_expansion'
        assert LoanPurpose.GOVERNMENT_DEFICIT.value == 'government_deficit'
        assert LoanPurpose.EMERGENCY_RELIEF.value == 'emergency_relief'
        assert LoanPurpose.PERSONAL.value == 'personal'

    def test_emergency_relief_is_prohibited(self):
        """阶段 4：紧急救济用途被 V2.1 §8.3 禁用（不允许负债无限救援）。"""
        assert LoanPurpose.EMERGENCY_RELIEF in PROHIBITED_PURPOSES
        assert len(PROHIBITED_PURPOSES) == 1


# ===== B. Loan dataclass 数据计算（5 项）=====

class TestLoanDataClass:
    """V2.1 §8.1 Loan 数据类的派生属性计算。"""

    def test_monthly_interest_cents_basic(self):
        """阶段 4：本金 10,000 CR × 1%/月 = 100 CR = 10,000 分（向下取整）。"""
        loan = Loan(
            loan_id='L1', principal_cents=1_000_000, interest_rate_monthly=0.01,
            start_month=1, maturity_month=12, borrower_account='cohorts:H1',
            lender_account='firms:BNK', original_lender_account='firms:BNK',
        )
        assert loan.monthly_interest_cents() == 10_000  # 10000 分 = 100 CR

    def test_monthly_interest_cents_floors_fractional(self):
        """阶段 4：首月利息 = int(principal * rate)，向下取整到分。"""
        # 333 分 × 0.01 = 3.33 分 → int() = 3 分
        loan = Loan(
            loan_id='L2', principal_cents=333, interest_rate_monthly=0.01,
            start_month=1, maturity_month=2, borrower_account='cohorts:H1',
            lender_account='firms:BNK', original_lender_account='firms:BNK',
        )
        assert loan.monthly_interest_cents() == 3

    def test_is_overdue_before_or_at_maturity(self):
        """阶段 4：到期月当月不算超期；过了到期月才超期。"""
        loan = Loan(
            loan_id='L3', principal_cents=100_000, interest_rate_monthly=0.01,
            start_month=1, maturity_month=12, borrower_account='cohorts:H1',
            lender_account='firms:BNK', original_lender_account='firms:BNK',
        )
        # 当月不超期
        assert loan.is_overdue(12) is False
        # 之前不超期
        assert loan.is_overdue(5) is False
        # 过了到期月才超期
        assert loan.is_overdue(13) is True

    def test_outstanding_principal_cents(self):
        """阶段 4：剩余未还本金 = max(0, principal - repaid)。"""
        loan = Loan(
            loan_id='L4', principal_cents=100_000, interest_rate_monthly=0.01,
            start_month=1, maturity_month=12, borrower_account='cohorts:H1',
            lender_account='firms:BNK', original_lender_account='firms:BNK',
        )
        assert loan.outstanding_principal_cents(repaid_cents=0) == 100_000
        assert loan.outstanding_principal_cents(repaid_cents=40_000) == 60_000
        assert loan.outstanding_principal_cents(repaid_cents=100_000) == 0

    def test_outstanding_principal_cents_never_negative(self):
        """阶段 4：超额还款不能导致剩余本金为负。"""
        loan = Loan(
            loan_id='L5', principal_cents=100_000, interest_rate_monthly=0.01,
            start_month=1, maturity_month=12, borrower_account='cohorts:H1',
            lender_account='firms:BNK', original_lender_account='firms:BNK',
        )
        # 还了 200,000 分（远超本金）→ max(0, 100000 - 200000) = 0
        assert loan.outstanding_principal_cents(repaid_cents=200_000) == 0


# ===== C. LoanBook.add() 校验（6 项）=====

class TestLoanBookAddValidation:
    """V2.1 §8 LoanBook.add() 的输入校验。"""

    def test_add_normal_loan_succeeds(self, loan_book):
        """阶段 4：正常贷款成功添加。"""
        loan = Loan(
            loan_id='L1', principal_cents=100_000, interest_rate_monthly=0.01,
            start_month=1, maturity_month=12, borrower_account='cohorts:H1',
            lender_account='firms:BNK', original_lender_account='firms:BNK',
        )
        result = loan_book.add(loan)
        assert result is loan
        assert 'L1' in loan_book
        assert loan_book.get('L1') is loan

    def test_add_duplicate_loan_id_raises(self, loan_book):
        """阶段 4：loan_id 重复抛 KeyError。"""
        loan1 = Loan(
            loan_id='L1', principal_cents=100_000, interest_rate_monthly=0.01,
            start_month=1, maturity_month=12, borrower_account='cohorts:H1',
            lender_account='firms:BNK', original_lender_account='firms:BNK',
        )
        loan_book.add(loan1)
        loan2 = Loan(
            loan_id='L1', principal_cents=200_000, interest_rate_monthly=0.01,
            start_month=1, maturity_month=12, borrower_account='cohorts:H2',
            lender_account='firms:BNK', original_lender_account='firms:BNK',
        )
        with pytest.raises(KeyError, match='贷款 ID 重复'):
            loan_book.add(loan2)

    def test_add_non_positive_principal_raises(self, loan_book):
        """阶段 4：本金 ≤ 0 抛 ValueError。"""
        loan = Loan(
            loan_id='L1', principal_cents=0, interest_rate_monthly=0.01,
            start_month=1, maturity_month=12, borrower_account='cohorts:H1',
            lender_account='firms:BNK', original_lender_account='firms:BNK',
        )
        with pytest.raises(ValueError, match='贷款本金必须为正'):
            loan_book.add(loan)

    def test_add_negative_interest_rate_raises(self, loan_book):
        """阶段 4：负利率抛 ValueError（不允许负利率剥削）。"""
        loan = Loan(
            loan_id='L1', principal_cents=100_000, interest_rate_monthly=-0.01,
            start_month=1, maturity_month=12, borrower_account='cohorts:H1',
            lender_account='firms:BNK', original_lender_account='firms:BNK',
        )
        with pytest.raises(ValueError, match='月利率越界'):
            loan_book.add(loan)

    def test_add_excessive_interest_rate_raises(self, loan_book):
        """阶段 4：利率超 10%/月 上限抛 ValueError。"""
        loan = Loan(
            loan_id='L1', principal_cents=100_000, interest_rate_monthly=0.11,
            start_month=1, maturity_month=12, borrower_account='cohorts:H1',
            lender_account='firms:BNK', original_lender_account='firms:BNK',
        )
        with pytest.raises(ValueError, match='月利率越界'):
            loan_book.add(loan)

    def test_add_maturity_before_start_raises(self, loan_book):
        """阶段 4：到期月早于起息月抛 ValueError。"""
        loan = Loan(
            loan_id='L1', principal_cents=100_000, interest_rate_monthly=0.01,
            start_month=12, maturity_month=6, borrower_account='cohorts:H1',
            lender_account='firms:BNK', original_lender_account='firms:BNK',
        )
        with pytest.raises(ValueError, match='到期月早于起息月'):
            loan_book.add(loan)


# ===== D. LoanBook 集合操作（5 项）=====

class TestLoanBookCollection:
    """V2.1 §8 LoanBook 的查询与过滤。"""

    def test_get_returns_loan_or_none(self, loan_book):
        """阶段 4：get() 返回 Loan 或 None。"""
        loan = Loan(
            loan_id='L1', principal_cents=100_000, interest_rate_monthly=0.01,
            start_month=1, maturity_month=12, borrower_account='cohorts:H1',
            lender_account='firms:BNK', original_lender_account='firms:BNK',
        )
        loan_book.add(loan)
        assert loan_book.get('L1') is loan
        assert loan_book.get('NOPE') is None

    def test_contains_check(self, loan_book):
        """阶段 4：`in` 运算符按 loan_id 判断。"""
        loan = Loan(
            loan_id='L1', principal_cents=100_000, interest_rate_monthly=0.01,
            start_month=1, maturity_month=12, borrower_account='cohorts:H1',
            lender_account='firms:BNK', original_lender_account='firms:BNK',
        )
        loan_book.add(loan)
        assert 'L1' in loan_book
        assert 'NOPE' not in loan_book

    def test_iter_returns_all_loans(self, loan_book):
        """阶段 4：迭代返回所有贷款。"""
        for i in range(3):
            loan = Loan(
                loan_id=f'L{i}', principal_cents=100_000, interest_rate_monthly=0.01,
                start_month=1, maturity_month=12, borrower_account='cohorts:H1',
                lender_account='firms:BNK', original_lender_account='firms:BNK',
            )
            loan_book.add(loan)
        loans = list(loan_book)
        assert len(loans) == 3
        assert {l.loan_id for l in loans} == {'L0', 'L1', 'L2'}

    def test_loans_by_lender_filters(self, loan_book):
        """阶段 4：loans_by_lender 按当前持有债权的账户过滤。"""
        # 银行放 2 笔，企业自己放 1 笔
        bank_loans = [
            Loan(
                loan_id=f'B{i}', principal_cents=100_000, interest_rate_monthly=0.01,
                start_month=1, maturity_month=12, borrower_account='cohorts:H1',
                lender_account='firms:BNK', original_lender_account='firms:BNK',
            ) for i in range(2)
        ]
        firm_loan = Loan(
            loan_id='F1', principal_cents=50_000, interest_rate_monthly=0.01,
            start_month=1, maturity_month=12, borrower_account='cohorts:H2',
            lender_account='firms:IND', original_lender_account='firms:IND',
        )
        for l in bank_loans + [firm_loan]:
            loan_book.add(l)

        assert len(loan_book.loans_by_lender('firms:BNK')) == 2
        assert len(loan_book.loans_by_lender('firms:IND')) == 1
        assert len(loan_book.loans_by_lender('firms:SRV')) == 0

    def test_loans_by_borrower_filters(self, loan_book):
        """阶段 4：loans_by_borrower 按借款方账户过滤。"""
        loans = [
            Loan(
                loan_id=f'L{i}', principal_cents=100_000, interest_rate_monthly=0.01,
                start_month=1, maturity_month=12, borrower_account='firms:IND',
                lender_account='firms:BNK', original_lender_account='firms:BNK',
            ) for i in range(3)
        ]
        loans.append(Loan(
            loan_id='L3', principal_cents=100_000, interest_rate_monthly=0.01,
            start_month=1, maturity_month=12, borrower_account='firms:SRV',
            lender_account='firms:BNK', original_lender_account='firms:BNK',
        ))
        for l in loans:
            loan_book.add(l)

        assert len(loan_book.loans_by_borrower('firms:IND')) == 3
        assert len(loan_book.loans_by_borrower('firms:SRV')) == 1


# ===== E. LoanBook 总额计算（3 项）=====

class TestLoanBookTotals:
    """V2.1 §8.2 贷款余额聚合。"""

    def test_total_outstanding_principal_basic(self, loan_book):
        """阶段 4：某账户持有的贷款剩余未还本金合计。"""
        # 银行持有 3 笔：100000 + 200000 + 300000 分
        for i, principal in enumerate([100_000, 200_000, 300_000]):
            loan_book.add(Loan(
                loan_id=f'L{i}', principal_cents=principal, interest_rate_monthly=0.01,
                start_month=1, maturity_month=12, borrower_account='cohorts:H1',
                lender_account='firms:BNK', original_lender_account='firms:BNK',
            ))
        assert loan_book.total_outstanding_principal_cents('firms:BNK') == 600_000

    def test_total_outstanding_decreases_after_repayment(self, loan_book):
        """阶段 4：还本后 outstanding 减少。"""
        loan_book.add(Loan(
            loan_id='L1', principal_cents=100_000, interest_rate_monthly=0.01,
            start_month=1, maturity_month=12, borrower_account='cohorts:H1',
            lender_account='firms:BNK', original_lender_account='firms:BNK',
        ))
        assert loan_book.total_outstanding_principal_cents('firms:BNK') == 100_000
        loan_book._record_repayment('L1', 30_000)
        assert loan_book.total_outstanding_principal_cents('firms:BNK') == 70_000
        loan_book._record_repayment('L1', 70_000)
        # 还清 → outstanding = 0
        assert loan_book.total_outstanding_principal_cents('firms:BNK') == 0

    def test_total_debt_principal_for_borrower(self, loan_book):
        """阶段 4：借款方的债务总额。"""
        # 同一借款方借 2 笔
        for i in range(2):
            loan_book.add(Loan(
                loan_id=f'L{i}', principal_cents=150_000, interest_rate_monthly=0.01,
                start_month=1, maturity_month=12, borrower_account='firms:IND',
                lender_account='firms:BNK', original_lender_account='firms:BNK',
            ))
        assert loan_book.total_debt_principal_cents('firms:IND') == 300_000
        # 还 1 笔 50,000 分
        loan_book._record_repayment('L0', 50_000)
        assert loan_book.total_debt_principal_cents('firms:IND') == 250_000


# ===== F. available_cash_for_lending（4 项）=====

class TestAvailableCashForLending:
    """V2.1 §8.1 基金制：银行可贷出现金 = bank_cash - 已贷出未还本金。"""

    def test_no_loans_then_available_equals_bank_cash(self, ledger):
        """阶段 4：无未还贷款时 = 银行现金。"""
        # ledger fixture 中 BNK 行业现金 10,000 CR = 1,000,000 分
        ledger.loan_book = LoanBook()
        assert available_cash_for_lending(ledger, 'firms:BNK') == 1_000_000

    def test_no_loan_book_attr_returns_bank_cash(self, ledger):
        """阶段 4：ledger 没有 loan_book 属性时 = 银行现金（兼容未注入场景）。"""
        assert not hasattr(ledger, 'loan_book')
        assert available_cash_for_lending(ledger, 'firms:BNK') == 1_000_000

    def test_subtracts_outstanding_loans(self, ledger_with_loan_book):
        """阶段 4：未还本金从可贷现金中扣除（V2.1 §8.1 基金制）。

        过账后银行 cash 已扣 200,000：1,000,000 - 200,000 = 800,000；
        未还本金 200,000；可贷现金 = 800,000 - 200,000 = 600,000。
        """
        originate_loan(
            ledger_with_loan_book, ledger_with_loan_book.loan_book,
            loan_id='L1', principal_cents=200_000,
            interest_rate_monthly=0.01, start_month=1, maturity_month=12,
            borrower_account='firms:IND', max_loan_to_borrower_cash_ratio=5.0,
        )
        # 过账后：bank_cash 800,000 - outstanding 200,000 = 600,000 可贷
        assert available_cash_for_lending(ledger_with_loan_book, 'firms:BNK') == 600_000

    def test_returns_zero_when_outstanding_exceeds_cash(self, ledger_with_loan_book):
        """阶段 4：未还本金超过银行现金时返回 0（不返回负数）。"""
        # 手工构造：未还本金超额
        ledger_with_loan_book.loan_book.add(Loan(
            loan_id='L1', principal_cents=200_000, interest_rate_monthly=0.01,
            start_month=1, maturity_month=12, borrower_account='cohorts:H1',
            lender_account='firms:BNK', original_lender_account='firms:BNK',
        ))
        # 手工减银行现金到 100,000 分（模拟挤兑/破产）
        ledger_with_loan_book.book['firms:BNK'].cash_cents = 100_000
        # 200,000 未还 > 100,000 现金 → max(0, 100000 - 200000) = 0
        assert available_cash_for_lending(ledger_with_loan_book, 'firms:BNK') == 0

    def test_returns_zero_when_bank_account_missing(self, ledger):
        """阶段 4：银行账户不存在时返回 0。"""
        assert available_cash_for_lending(ledger, 'firms:NOPE') == 0

    def test_default_bank_account_is_firms_BNK(self, ledger):
        """阶段 4：默认 bank_account_id() = firms:BNK（V2.1 §1.5 共用钱包）。"""
        assert bank_account_id() == 'firms:BNK'
        assert bank_account_id() == firm_account_id('BNK')


# ===== G. originate_loan 成功路径（4 项）=====

class TestOriginateLoanSuccess:
    """V2.1 §8.1 发起贷款的成功路径。"""

    def test_loan_added_to_loan_book(self, ledger_with_loan_book):
        """阶段 4：成功发起后 LoanBook 包含新贷款。"""
        loan = originate_loan(
            ledger_with_loan_book, ledger_with_loan_book.loan_book,
            loan_id='L1', principal_cents=500_000,
            interest_rate_monthly=0.01, start_month=1, maturity_month=12,
            borrower_account='firms:IND', max_loan_to_borrower_cash_ratio=5.0,
        )
        assert 'L1' in ledger_with_loan_book.loan_book
        assert loan.status == LoanStatus.ACTIVE
        assert loan.original_lender_account == 'firms:BNK'

    def test_cash_transfers_in_ledger(self, ledger_with_loan_book):
        """阶段 4：发起贷款后银行 cash ↓、借款方 cash ↑。"""
        bank_before = ledger_with_loan_book.book['firms:BNK'].cash_cents
        ind_before = ledger_with_loan_book.book['firms:IND'].cash_cents

        originate_loan(
            ledger_with_loan_book, ledger_with_loan_book.loan_book,
            loan_id='L1', principal_cents=300_000,
            interest_rate_monthly=0.01, start_month=1, maturity_month=12,
            borrower_account='firms:IND', max_loan_to_borrower_cash_ratio=5.0,
        )

        bank_after = ledger_with_loan_book.book['firms:BNK'].cash_cents
        ind_after = ledger_with_loan_book.book['firms:IND'].cash_cents
        assert bank_after == bank_before - 300_000
        assert ind_after == ind_before + 300_000

    def test_total_money_conserved(self, ledger_with_loan_book):
        """阶段 4：贷款过账是内部转账，总金额不变（不计入 GDP）。"""
        total_before = sum(a.cash_cents for a in ledger_with_loan_book.book.local_leaves())
        originate_loan(
            ledger_with_loan_book, ledger_with_loan_book.loan_book,
            loan_id='L1', principal_cents=400_000,
            interest_rate_monthly=0.01, start_month=1, maturity_month=12,
            borrower_account='firms:IND', max_loan_to_borrower_cash_ratio=5.0,
        )
        total_after = sum(a.cash_cents for a in ledger_with_loan_book.book.local_leaves())
        assert total_before == total_after

    def test_multiple_loans_accumulate(self, ledger_with_loan_book):
        """阶段 4：多笔贷款在 ledger 中独立过账。"""
        for i in range(3):
            originate_loan(
                ledger_with_loan_book, ledger_with_loan_book.loan_book,
                loan_id=f'L{i}', principal_cents=100_000,
                interest_rate_monthly=0.01, start_month=1, maturity_month=12,
                borrower_account='firms:IND', max_loan_to_borrower_cash_ratio=5.0,
            )
        # LoanBook 没有 __len__，用列表大小判断
        assert len(list(ledger_with_loan_book.loan_book)) == 3
        # 银行 -300,000；IND +300,000
        assert ledger_with_loan_book.book['firms:BNK'].cash_cents == 700_000
        assert ledger_with_loan_book.book['firms:IND'].cash_cents == 800_000


# ===== H. originate_loan 校验失败（8 项）=====

class TestOriginateLoanValidationFailures:
    """V2.1 §8.1 + §8.3 originate_loan 的 8 类校验失败。"""

    def test_zero_principal_raises(self, ledger_with_loan_book):
        """阶段 4：本金 ≤ 0 抛 ValueError。"""
        with pytest.raises(ValueError, match='贷款本金必须为正'):
            originate_loan(
                ledger_with_loan_book, ledger_with_loan_book.loan_book,
                loan_id='L1', principal_cents=0,
                interest_rate_monthly=0.01, start_month=1, maturity_month=12,
                borrower_account='firms:IND',
            )

    def test_negative_interest_raises(self, ledger_with_loan_book):
        """阶段 4：负利率抛 ValueError。"""
        with pytest.raises(ValueError, match='月利率越界'):
            originate_loan(
                ledger_with_loan_book, ledger_with_loan_book.loan_book,
                loan_id='L1', principal_cents=100_000,
                interest_rate_monthly=-0.01, start_month=1, maturity_month=12,
                borrower_account='firms:IND',
            )

    def test_excessive_interest_raises(self, ledger_with_loan_book):
        """阶段 4：利率超 10%/月 抛 ValueError。"""
        with pytest.raises(ValueError, match='月利率越界'):
            originate_loan(
                ledger_with_loan_book, ledger_with_loan_book.loan_book,
                loan_id='L1', principal_cents=100_000,
                interest_rate_monthly=0.15, start_month=1, maturity_month=12,
                borrower_account='firms:IND',
            )

    def test_emergency_relief_purpose_raises(self, ledger_with_loan_book):
        """阶段 4：紧急救济用途被 V2.1 §8.3 禁用。"""
        with pytest.raises(ValueError, match='V2.1 §8.3 禁用'):
            originate_loan(
                ledger_with_loan_book, ledger_with_loan_book.loan_book,
                loan_id='L1', principal_cents=100_000,
                interest_rate_monthly=0.01, start_month=1, maturity_month=12,
                borrower_account='firms:IND',
                purpose=LoanPurpose.EMERGENCY_RELIEF,
            )

    def test_missing_lender_raises(self, ledger_with_loan_book):
        """阶段 4：贷款方账户不存在抛 ValueError。"""
        with pytest.raises(ValueError, match='贷款方账户不存在'):
            originate_loan(
                ledger_with_loan_book, ledger_with_loan_book.loan_book,
                loan_id='L1', principal_cents=100_000,
                interest_rate_monthly=0.01, start_month=1, maturity_month=12,
                borrower_account='firms:IND', lender_account='firms:NOPE',
            )

    def test_missing_borrower_raises(self, ledger_with_loan_book):
        """阶段 4：借款方账户不存在抛 ValueError。"""
        with pytest.raises(ValueError, match='借款方账户不存在'):
            originate_loan(
                ledger_with_loan_book, ledger_with_loan_book.loan_book,
                loan_id='L1', principal_cents=100_000,
                interest_rate_monthly=0.01, start_month=1, maturity_month=12,
                borrower_account='firms:NOPE',
            )

    def test_self_lending_raises(self, ledger_with_loan_book):
        """阶段 4：贷款方与借款方相同抛 ValueError。"""
        with pytest.raises(ValueError, match='不能相同'):
            originate_loan(
                ledger_with_loan_book, ledger_with_loan_book.loan_book,
                loan_id='L1', principal_cents=100_000,
                interest_rate_monthly=0.01, start_month=1, maturity_month=12,
                borrower_account='firms:BNK',
            )

    def test_exceeds_bank_fund_raises_gameerror(self, ledger_with_loan_book):
        """阶段 4：超过银行基金制上限抛 GameError（V2.1 §8.1）。"""
        with pytest.raises(GameError, match='基金制'):
            originate_loan(
                ledger_with_loan_book, ledger_with_loan_book.loan_book,
                loan_id='L1', principal_cents=2_000_000,  # 银行只有 1,000,000
                interest_rate_monthly=0.01, start_month=1, maturity_month=12,
                borrower_account='firms:IND',
            )

    def test_exceeds_borrower_cash_ratio_raises_gameerror(self, ledger_with_loan_book):
        """阶段 4：超过借款方现金 × 比例 抛 GameError（V2.1 §8.3 风险约束）。

        把基金制上限放宽到 10×，让 2,500,000 笔能过基金制；
        但 borrower cash 500,000 × 5.0 = 2,500,000 是上限；超过即触发风险约束。
        """
        # 传 max_loan_to_bank_cash_ratio=10.0 避免基金制先失败
        with pytest.raises(GameError, match='风险约束'):
            originate_loan(
                ledger_with_loan_book, ledger_with_loan_book.loan_book,
                loan_id='L1', principal_cents=3_000_000,
                interest_rate_monthly=0.01, start_month=1, maturity_month=12,
                borrower_account='firms:IND',
                max_loan_to_bank_cash_ratio=10.0,
                max_loan_to_borrower_cash_ratio=5.0,
            )


# ===== I. repay_loan（5 项）=====

class TestRepayLoan:
    """V2.1 §8.1 还本付息。"""

    def test_principal_repayment_transfers_cash(self, ledger_with_loan_book):
        """阶段 4：还本时 borrower → lender 过账。"""
        originate_loan(
            ledger_with_loan_book, ledger_with_loan_book.loan_book,
            loan_id='L1', principal_cents=300_000,
            interest_rate_monthly=0.01, start_month=1, maturity_month=12,
            borrower_account='firms:IND', max_loan_to_borrower_cash_ratio=5.0,
        )
        bank_before = ledger_with_loan_book.book['firms:BNK'].cash_cents
        ind_before = ledger_with_loan_book.book['firms:IND'].cash_cents

        actual_p, actual_i = repay_loan(
            ledger_with_loan_book, ledger_with_loan_book.loan_book,
            'L1', principal_cents=100_000, interest_cents=0,
        )
        assert actual_p == 100_000
        assert actual_i == 0
        # 银行 cash ↑；IND cash ↓
        assert ledger_with_loan_book.book['firms:BNK'].cash_cents == bank_before + 100_000
        assert ledger_with_loan_book.book['firms:IND'].cash_cents == ind_before - 100_000

    def test_interest_repayment_transfers_cash(self, ledger_with_loan_book):
        """阶段 4：付息时 borrower → lender 过账（不影响本金）。"""
        originate_loan(
            ledger_with_loan_book, ledger_with_loan_book.loan_book,
            loan_id='L1', principal_cents=300_000,
            interest_rate_monthly=0.01, start_month=1, maturity_month=12,
            borrower_account='firms:IND', max_loan_to_borrower_cash_ratio=5.0,
        )
        bank_before = ledger_with_loan_book.book['firms:BNK'].cash_cents
        ind_before = ledger_with_loan_book.book['firms:IND'].cash_cents

        repay_loan(
            ledger_with_loan_book, ledger_with_loan_book.loan_book,
            'L1', principal_cents=0, interest_cents=3_000,
        )
        # 银行 +3,000；IND -3,000
        assert ledger_with_loan_book.book['firms:BNK'].cash_cents == bank_before + 3_000
        assert ledger_with_loan_book.book['firms:IND'].cash_cents == ind_before - 3_000
        # 本金 outstanding 不变
        assert ledger_with_loan_book.loan_book.total_outstanding_principal_cents('firms:BNK') == 300_000

    def test_full_repayment_sets_status_repaid(self, ledger_with_loan_book):
        """阶段 4：还清本金后 status=REPAID。"""
        originate_loan(
            ledger_with_loan_book, ledger_with_loan_book.loan_book,
            loan_id='L1', principal_cents=200_000,
            interest_rate_monthly=0.01, start_month=1, maturity_month=12,
            borrower_account='firms:IND', max_loan_to_borrower_cash_ratio=5.0,
        )
        # 还清全部本金
        repay_loan(
            ledger_with_loan_book, ledger_with_loan_book.loan_book,
            'L1', principal_cents=200_000, interest_cents=2_000,
        )
        assert ledger_with_loan_book.loan_book.get('L1').status == LoanStatus.REPAID

    def test_overpayment_capped_to_principal(self, ledger_with_loan_book):
        """阶段 4：超额还本被截断到 principal（不会还出负数 outstanding）。"""
        originate_loan(
            ledger_with_loan_book, ledger_with_loan_book.loan_book,
            loan_id='L1', principal_cents=100_000,
            interest_rate_monthly=0.01, start_month=1, maturity_month=12,
            borrower_account='firms:IND', max_loan_to_borrower_cash_ratio=5.0,
        )
        # 还 200,000 分本金（实际只能扣 100,000）
        actual_p, _ = repay_loan(
            ledger_with_loan_book, ledger_with_loan_book.loan_book,
            'L1', principal_cents=200_000, interest_cents=0,
        )
        assert actual_p == 100_000
        assert ledger_with_loan_book.loan_book.total_outstanding_principal_cents('firms:BNK') == 0

    def test_repay_non_active_loan_raises(self, ledger_with_loan_book):
        """阶段 4：非 ACTIVE 状态还本抛 GameError。"""
        originate_loan(
            ledger_with_loan_book, ledger_with_loan_book.loan_book,
            loan_id='L1', principal_cents=100_000,
            interest_rate_monthly=0.01, start_month=1, maturity_month=12,
            borrower_account='firms:IND', max_loan_to_borrower_cash_ratio=5.0,
        )
        # 先还清让 status=REPAID
        repay_loan(
            ledger_with_loan_book, ledger_with_loan_book.loan_book,
            'L1', principal_cents=100_000, interest_cents=0,
        )
        # 再还一次
        with pytest.raises(GameError, match='不能还本付息'):
            repay_loan(
                ledger_with_loan_book, ledger_with_loan_book.loan_book,
                'L1', principal_cents=10_000, interest_cents=0,
            )

    def test_repay_nonexistent_loan_raises(self, ledger_with_loan_book):
        """阶段 4：还本付息不存在的贷款抛 KeyError。"""
        with pytest.raises(KeyError, match='贷款不存在'):
            repay_loan(
                ledger_with_loan_book, ledger_with_loan_book.loan_book,
                'NOPE', principal_cents=100_000, interest_cents=0,
            )


# ===== J. transfer_loan（5 项）=====

class TestTransferLoan:
    """V2.1 §8.2 债权转让（保留 borrower 义务）。"""

    def test_lender_account_changes(self, ledger_with_loan_book):
        """阶段 4：转让后 lender_account 变更为新持有方。"""
        originate_loan(
            ledger_with_loan_book, ledger_with_loan_book.loan_book,
            loan_id='L1', principal_cents=200_000,
            interest_rate_monthly=0.01, start_month=1, maturity_month=12,
            borrower_account='firms:IND', max_loan_to_borrower_cash_ratio=5.0,
        )
        new_loan = transfer_loan(
            ledger_with_loan_book.loan_book, 'L1',
            new_lender_account='firms:SRV',
        )
        assert new_loan.lender_account == 'firms:SRV'
        assert ledger_with_loan_book.loan_book.get('L1').lender_account == 'firms:SRV'

    def test_borrower_obligations_preserved(self, ledger_with_loan_book):
        """阶段 4：borrower 账户保留，本金和利息义务不变。"""
        originate_loan(
            ledger_with_loan_book, ledger_with_loan_book.loan_book,
            loan_id='L1', principal_cents=200_000,
            interest_rate_monthly=0.01, start_month=1, maturity_month=12,
            borrower_account='firms:IND', max_loan_to_borrower_cash_ratio=5.0,
        )
        new_loan = transfer_loan(
            ledger_with_loan_book.loan_book, 'L1',
            new_lender_account='firms:SRV',
        )
        # borrower、principal、rate、maturity 全部不变
        assert new_loan.borrower_account == 'firms:IND'
        assert new_loan.principal_cents == 200_000
        assert new_loan.interest_rate_monthly == 0.01
        assert new_loan.maturity_month == 12

    def test_original_lender_immutable(self, ledger_with_loan_book):
        """阶段 4：original_lender_account 不可变（V2.1 §8.2 原始债权人）。"""
        originate_loan(
            ledger_with_loan_book, ledger_with_loan_book.loan_book,
            loan_id='L1', principal_cents=200_000,
            interest_rate_monthly=0.01, start_month=1, maturity_month=12,
            borrower_account='firms:IND', max_loan_to_borrower_cash_ratio=5.0,
        )
        transfer_loan(
            ledger_with_loan_book.loan_book, 'L1',
            new_lender_account='firms:SRV',
        )
        # 二次转让：original_lender 仍为 BNK
        new_loan = transfer_loan(
            ledger_with_loan_book.loan_book, 'L1',
            new_lender_account='firms:MAG',
        )
        assert new_loan.original_lender_account == 'firms:BNK'
        assert new_loan.lender_account == 'firms:MAG'

    def test_repayment_after_transfer_goes_to_new_lender(self, ledger_with_loan_book):
        """阶段 4：转让后还本付息走新 lender。"""
        originate_loan(
            ledger_with_loan_book, ledger_with_loan_book.loan_book,
            loan_id='L1', principal_cents=200_000,
            interest_rate_monthly=0.01, start_month=1, maturity_month=12,
            borrower_account='firms:IND', max_loan_to_borrower_cash_ratio=5.0,
        )
        transfer_loan(
            ledger_with_loan_book.loan_book, 'L1',
            new_lender_account='firms:SRV',
        )
        # SRV 接收还款
        srv_before = ledger_with_loan_book.book['firms:SRV'].cash_cents
        repay_loan(
            ledger_with_loan_book, ledger_with_loan_book.loan_book,
            'L1', principal_cents=50_000, interest_cents=0,
        )
        assert ledger_with_loan_book.book['firms:SRV'].cash_cents == srv_before + 50_000

    def test_transfer_non_active_loan_raises(self, ledger_with_loan_book):
        """阶段 4：非 ACTIVE 状态转让抛 GameError。"""
        originate_loan(
            ledger_with_loan_book, ledger_with_loan_book.loan_book,
            loan_id='L1', principal_cents=100_000,
            interest_rate_monthly=0.01, start_month=1, maturity_month=12,
            borrower_account='firms:IND', max_loan_to_borrower_cash_ratio=5.0,
        )
        # 先还清
        repay_loan(
            ledger_with_loan_book, ledger_with_loan_book.loan_book,
            'L1', principal_cents=100_000, interest_cents=0,
        )
        # 再转让
        with pytest.raises(GameError, match='不能转让'):
            transfer_loan(
                ledger_with_loan_book.loan_book, 'L1',
                new_lender_account='firms:SRV',
            )


# ===== K. 集成测试（2 项）=====

class TestBankingIntegration:
    """V2.1 §8 完整业务流程。"""

    def test_full_flow_originate_transfer_repay(self, ledger_with_loan_book):
        """阶段 4：发起 → 转让 → 还本付息 完整流程。"""
        # 1. 银行放贷给 IND
        loan = originate_loan(
            ledger_with_loan_book, ledger_with_loan_book.loan_book,
            loan_id='L1', principal_cents=500_000,
            interest_rate_monthly=0.02, start_month=1, maturity_month=12,
            borrower_account='firms:IND', max_loan_to_borrower_cash_ratio=5.0,
        )
        bank_after_orig = ledger_with_loan_book.book['firms:BNK'].cash_cents

        # 2. BNK 把债权卖给 SRV
        transfer_loan(
            ledger_with_loan_book.loan_book, 'L1',
            new_lender_account='firms:SRV',
        )

        # 3. IND 还本 + 付息给 SRV
        ind_before = ledger_with_loan_book.book['firms:IND'].cash_cents
        srv_before = ledger_with_loan_book.book['firms:SRV'].cash_cents
        actual_p, actual_i = repay_loan(
            ledger_with_loan_book, ledger_with_loan_book.loan_book,
            'L1', principal_cents=500_000,
            interest_cents=loan.monthly_interest_cents(),
        )
        # IND - (500000 + 10000)；SRV + (500000 + 10000)
        assert actual_p == 500_000
        assert actual_i == 10_000
        assert ledger_with_loan_book.book['firms:IND'].cash_cents == ind_before - 510_000
        assert ledger_with_loan_book.book['firms:SRV'].cash_cents == srv_before + 510_000

        # 4. 银行现金不变（卖债权只是改了 lender，没动 bank cash）
        assert ledger_with_loan_book.book['firms:BNK'].cash_cents == bank_after_orig

        # 5. 贷款 status=REPAID
        assert ledger_with_loan_book.loan_book.get('L1').status == LoanStatus.REPAID

    def test_multiple_loans_aggregate_in_bank_fund(self, ledger_with_loan_book):
        """阶段 4：多笔贷款聚合影响银行可贷现金（V2.1 §8.1 基金制）。

        3 笔 100,000 → bank_cash = 700,000, outstanding = 300,000，
        可贷现金 = 700,000 - 300,000 = 400,000。
        还 50,000 本金 → bank_cash 涨回 50,000（750,000），
        outstanding 跌 50,000（250,000）；
        可贷现金 = 750,000 - 250,000 = 500,000。
        """
        for i in range(3):
            originate_loan(
                ledger_with_loan_book, ledger_with_loan_book.loan_book,
                loan_id=f'L{i}', principal_cents=100_000,
                interest_rate_monthly=0.01, start_month=1, maturity_month=12,
                borrower_account='firms:IND', max_loan_to_borrower_cash_ratio=5.0,
            )
        # bank_cash 700,000 - outstanding 300,000 = 400,000 可贷
        assert available_cash_for_lending(ledger_with_loan_book, 'firms:BNK') == 400_000
        # 还 50,000 → bank_cash 750,000 - outstanding 250,000 = 500,000
        repay_loan(
            ledger_with_loan_book, ledger_with_loan_book.loan_book,
            'L0', principal_cents=50_000, interest_cents=0,
        )
        assert available_cash_for_lending(ledger_with_loan_book, 'firms:BNK') == 500_000


# ===== L. JSON 配置（3 项）=====

class TestBankingJsonConfig:
    """V2.1 §8 配置文件结构与默认值。"""

    def test_banking_json_loads(self):
        """阶段 4：banking.json 可成功加载。"""
        config = load_banking_config()
        assert config['_meta']['version'] == '2.1.0'
        assert '§8' in config['_meta']['spec']

    def test_banking_json_default_params(self):
        """阶段 4：默认参数严格匹配 V2.1 §8.1（1.0/5.0/0.01/0.10/12）。"""
        config = load_banking_config()
        params = config['default_params']
        assert params['max_loan_to_bank_cash_ratio'] == 1.0
        assert params['max_loan_to_borrower_cash_ratio'] == 5.0
        assert params['default_interest_rate_monthly'] == 0.01
        assert params['max_interest_rate_monthly'] == 0.10
        assert params['default_grace_period_months'] == 12

    def test_banking_json_prohibited_purposes(self):
        """阶段 4：prohibited_purposes 包含 emergency_relief（V2.1 §8.3）。"""
        config = load_banking_config()
        assert 'emergency_relief' in config['prohibited_purposes']
        # allowed 4 项（不含 emergency_relief）
        assert len(config['allowed_purposes']) == 4
        assert 'emergency_relief' not in config['allowed_purposes']

    def test_default_constants_match_json(self):
        """阶段 4：Python 常量与 JSON 默认值一致（避免双源不一致）。"""
        assert DEFAULT_MAX_LOAN_TO_BANK_CASH_RATIO == 1.0
        assert DEFAULT_MAX_LOAN_TO_BORROWER_CASH_RATIO == 5.0
        assert DEFAULT_INTEREST_RATE_MONTHLY == 0.01
        assert DEFAULT_MAX_INTEREST_RATE_MONTHLY == 0.10


# ===== M. 默认参数边界（额外）=====

class TestDefaultConstants:
    """阶段 4：模块级常量边界。"""

    def test_constants_have_expected_types(self):
        """阶段 4：常量类型正确。"""
        assert isinstance(DEFAULT_MAX_LOAN_TO_BANK_CASH_RATIO, float)
        assert isinstance(DEFAULT_MAX_LOAN_TO_BORROWER_CASH_RATIO, float)
        assert isinstance(DEFAULT_INTEREST_RATE_MONTHLY, float)
        assert isinstance(DEFAULT_MAX_INTEREST_RATE_MONTHLY, float)
        assert isinstance(PROHIBITED_PURPOSES, frozenset)

    def test_prohibited_purposes_is_immutable(self):
        """阶段 4：PROHIBITED_PURPOSES 是 frozenset，不可修改。"""
        with pytest.raises(AttributeError):
            PROHIBITED_PURPOSES.add(LoanPurpose.PERSONAL)
