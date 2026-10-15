"""P01：账户、交易与清算的守恒测试。

验收口径来自设计文档 M02：内部转账双方同额、全城现金不变；外部进出能解释全城变化；
零/负金额、重复交易、余额不足、部分履约、失败回滚、恢复幂等都必须有明确结论。
"""
import pytest

from backend.core.errors import GameError
from backend.core.settlement import InsufficientFunds, Settlement
from backend.model.accounts import (CHURCH, EXTERNAL, FIRM, HOUSEHOLD, LANDLORD,
                                   MUNICIPAL, AccountBook)
from backend.model.transactions import (COMMERCIAL_RENT, IMPORT, PRINCIPAL, WAGE,
                                       Transaction, transaction_id)
from backend.systems.accounting import assert_conserved, check_conservation

CENT = 100                      # 1 CR = 100 分
CITY = "reference_city"


def cr(amount):
    """CR 转整数分。引擎内部不允许出现浮点金额。"""
    return int(round(amount * CENT))


def make_book():
    book = AccountBook()
    book.open("gov", MUNICIPAL, "city_government", CITY, cr(100000))
    book.open("church", CHURCH, "city_church", CITY, cr(20000))
    book.open("households", HOUSEHOLD, "all_households", CITY, cr(157131))
    book.open("firms", FIRM, "all_firms", CITY, cr(265200))
    book.open("world", EXTERNAL, "rest_of_world", None, 0)
    return book


def wage_tx(month=1, amount_cents=10000):
    return Transaction(from_account="firms", to_account="households", amount_cents=amount_cents,
                       category=WAGE, source_ref="payroll-2026-01", month=month)


# --- 内部转账 ---------------------------------------------------------

def test_internal_transfer_moves_cash_without_creating_it():
    book = make_book()
    opening = book.snapshot()
    settlement = Settlement(book, month=1, opening=opening)

    settlement.enqueue(Transaction("firms", "households", cr(121900), WAGE, "payroll", 1))
    settlement.settle()

    assert book["firms"].cash_cents == cr(265200) - cr(121900)
    assert book["households"].cash_cents == cr(157131) + cr(121900)
    assert_conserved(book, opening, settlement.journal)


def test_payer_and_payee_change_by_identical_amount():
    book = make_book()
    settlement = Settlement(book, month=1)
    before_payer, before_payee = book["gov"].cash_cents, book["church"].cash_cents

    settlement.enqueue(Transaction("gov", "church", cr(660), "donation", "church-donation", 1))
    settlement.settle()

    assert before_payer - book["gov"].cash_cents == book["church"].cash_cents - before_payee == cr(660)


def test_settlement_reports_balance_per_transaction():
    book = make_book()
    settlement = Settlement(book, month=1)
    settlement.enqueue(Transaction("gov", "church", cr(660), "donation", "church-donation", 1))
    settled = settlement.settle()

    assert len(settled) == 1
    assert settled[0].status == "settled"
    assert settled[0].tx_id == transaction_id(1, "church-donation", "main")


# --- 外部边界 ---------------------------------------------------------

def test_external_flow_explains_city_change():
    book = make_book()
    opening = book.snapshot()
    settlement = Settlement(book, month=1, opening=opening)

    settlement.enqueue(Transaction("firms", "world", cr(68000), IMPORT, "imports", 1))
    settlement.enqueue(Transaction("firms", "world", cr(15000), PRINCIPAL, "loan-interest", 1))
    settlement.settle()

    report = check_conservation(book, opening, settlement.journal)
    assert report.city_cash_delta_cents == -cr(83000)
    assert report.external_cash_delta_cents == cr(83000)
    assert report.world_cash_delta_cents == 0
    assert report.is_balanced


def test_external_account_may_go_negative_as_world_mirror():
    """外部账户代表世界其余部分，不是本地钱包，允许被贸易倒空。"""
    book = make_book()
    settlement = Settlement(book, month=1)
    settlement.enqueue(Transaction("world", "firms", cr(300000), "foreign_investment", "inbound", 1))
    settlement.enqueue(Transaction("firms", "world", cr(100000), IMPORT, "imports", 1))
    settlement.settle()

    assert book["world"].cash_cents == -cr(200000)
    assert check_conservation(book, settlement.opening, settlement.journal).is_balanced


def test_local_accounts_still_cannot_overdraw_on_external_payments():
    """外部账户可负，不代表本地账户可以透支。"""
    book = make_book()
    settlement = Settlement(book, month=1)
    with pytest.raises(InsufficientFunds):
        settlement.enqueue(Transaction("firms", "world", cr(900000), IMPORT, "over-import", 1))
        settlement.settle()
    assert book["firms"].cash_cents == cr(265200)


def test_loan_principal_is_financing_not_revenue():
    """放款增加现金和本金，但不计入经营收入类别。"""
    book = make_book()
    settlement = Settlement(book, month=1)
    settlement.enqueue(Transaction("world", "gov", cr(60000), PRINCIPAL, "loan-drawdown", 1))
    settlement.settle()

    assert book["gov"].cash_cents == cr(100000) + cr(60000)
    assert settlement.journal_by_category()[PRINCIPAL] == -cr(60000)


# --- 校验与幂等 -------------------------------------------------------

@pytest.mark.parametrize("amount", [0, -1, -cr(10)])
def test_zero_or_negative_amounts_are_rejected(amount):
    book = make_book()
    settlement = Settlement(book, month=1)
    with pytest.raises(GameError):
        settlement.enqueue(wage_tx(amount_cents=amount))


def test_unknown_account_is_rejected():
    book = make_book()
    settlement = Settlement(book, month=1)
    with pytest.raises(GameError):
        settlement.enqueue(Transaction("nobody", "households", 100, WAGE, "ghost", 1))


def test_transaction_from_another_month_is_rejected():
    book = make_book()
    settlement = Settlement(book, month=2)
    with pytest.raises(GameError):
        settlement.enqueue(wage_tx(month=1))


def test_duplicate_source_ref_pays_only_once():
    book = make_book()
    opening = book.snapshot()
    settlement = Settlement(book, month=1, opening=opening)

    settlement.enqueue(wage_tx(amount_cents=cr(121900)))
    settlement.enqueue(wage_tx(amount_cents=cr(121900)))
    settlement.enqueue(wage_tx(amount_cents=cr(121900)))
    assert settlement.pending_count() == 1

    settlement.settle()
    assert book["firms"].cash_cents == cr(265200) - cr(121900)
    assert len(settlement.journal) == 1
    assert_conserved(book, opening, settlement.journal)


def test_duplicate_with_different_amount_is_rejected():
    book = make_book()
    settlement = Settlement(book, month=1)
    settlement.enqueue(wage_tx(amount_cents=cr(121900)))
    with pytest.raises(GameError):
        settlement.enqueue(wage_tx(amount_cents=cr(99999)))


def test_distinct_payment_parts_are_separate_obligations():
    book = make_book()
    settlement = Settlement(book, month=1)
    settlement.enqueue(wage_tx(amount_cents=cr(60000)))
    settlement.enqueue(Transaction("firms", "households", cr(61900), WAGE, "payroll-2026-01", 1,
                                   payment_part="final"))
    settlement.settle()

    assert len(settlement.journal) == 2
    assert book["firms"].cash_cents == cr(265200) - cr(121900)


# --- 余额不足、回滚与部分履约 ------------------------------------------

def test_insufficient_balance_is_rejected():
    book = make_book()
    settlement = Settlement(book, month=1)
    with pytest.raises(InsufficientFunds):
        settlement.enqueue(Transaction("church", "gov", cr(999999), "donation", "impossible", 1))
        settlement.settle()


def test_failed_settlement_leaves_every_account_untouched():
    book = make_book()
    opening = book.snapshot()
    settlement = Settlement(book, month=1, opening=opening)

    settlement.enqueue(Transaction("firms", "households", cr(121900), WAGE, "payroll", 1))
    settlement.enqueue(Transaction("church", "gov", cr(999999), "donation", "impossible", 1))

    with pytest.raises(InsufficientFunds):
        settlement.settle()

    assert book.snapshot() == opening, "整批清算失败时不得有任何一个账户被扣款"
    assert settlement.journal == []
    assert settlement.pending_count() == 2


def test_batch_can_be_discarded_after_failure():
    book = make_book()
    opening = book.snapshot()
    settlement = Settlement(book, month=1, opening=opening)
    settlement.enqueue(Transaction("church", "gov", cr(999999), "donation", "impossible", 1))
    with pytest.raises(InsufficientFunds):
        settlement.settle()
    settlement.discard()

    assert settlement.pending_count() == 0
    assert book.snapshot() == opening


# --- 预留 -------------------------------------------------------------

def test_reserved_cash_stays_in_wallet_but_becomes_unavailable():
    book = make_book()
    settlement = Settlement(book, month=1)
    before = book["gov"].cash_cents

    settlement.reserve("gov", cr(20000), "road-contract")
    assert book["gov"].cash_cents == before, "预留不是另一笔钱，现金总额不变"
    assert book["gov"].reserved_cents == cr(20000)
    assert book["gov"].available_cents == before - cr(20000)


def test_payment_may_not_invade_reserved_cash():
    book = make_book()
    settlement = Settlement(book, month=1)
    settlement.reserve("gov", cr(90000), "road-contract")

    with pytest.raises(InsufficientFunds):
        settlement.enqueue(Transaction("gov", "church", cr(20000), "donation", "greedy", 1))
        settlement.settle()
    assert book["gov"].cash_cents == cr(100000)


def test_reservation_allows_payment_outside_reserved_part():
    book = make_book()
    settlement = Settlement(book, month=1)
    settlement.reserve("gov", cr(90000), "road-contract")
    settlement.enqueue(Transaction("gov", "church", cr(9000), "donation", "affordable", 1))
    settlement.settle()

    assert book["gov"].cash_cents == cr(100000) - cr(9000)
    assert book["gov"].reserved_cents == cr(90000)


def test_release_and_over_release():
    book = make_book()
    settlement = Settlement(book, month=1)
    settlement.reserve("gov", cr(50000), "contract")
    assert settlement.release("gov", cr(20000)) == cr(30000)
    with pytest.raises(GameError):
        settlement.release("gov", cr(30001))


def test_partial_delivery_keeps_unspent_reservation():
    """部分履约：只支付实际交付部分，未付部分释放为可用余额。"""
    book = make_book()
    settlement = Settlement(book, month=1)
    settlement.reserve("gov", cr(10000), "construction")
    settlement.enqueue(Transaction("gov", "firms", cr(6000), "capital", "construction", 1))
    settlement.settle()
    settlement.release("gov", cr(4000))

    assert book["gov"].cash_cents == cr(100000) - cr(6000)
    assert book["gov"].reserved_cents == 0
    assert book["gov"].available_cents == cr(100000) - cr(6000)


# --- 房东子账户与守恒 --------------------------------------------------

def test_landlord_is_a_child_of_household_wallet():
    book = make_book()
    book.open("hh_landlord", LANDLORD, "household_1", CITY, 0, parent_account_id="households")
    book.open("hh_other", HOUSEHOLD, "household_2", CITY, cr(50000))

    # 父账户标记财产身份归属，不是替子账户持钱的容器，家庭共有现金必须计入合计
    assert book.total_cash() == cr(100000) + cr(20000) + cr(157131) + cr(265200) + cr(50000)
    assert {a.account_id for a in book.local_leaves()} == {"gov", "church", "firms", "households",
                                                          "hh_other", "hh_landlord"}
    assert book.mirrored_pairs() == [], "业主账户与家庭主钱包属于不同主体，不是镜像"


def test_mirrored_cash_on_parent_and_child_is_reported():
    book = make_book()
    book.open("hh_mirror", LANDLORD, "all_households", CITY, cr(1000), parent_account_id="households")
    report = check_conservation(book, book.snapshot())

    assert any("重复记账" in v for v in report.violations), report.violations
    assert not report.is_balanced


def test_rent_to_landlord_child_does_not_credit_household_twice():
    book = make_book()
    book.open("hh_landlord", LANDLORD, "household_1", CITY, cr(20000), parent_account_id="households")
    opening = book.snapshot()
    settlement = Settlement(book, month=1, opening=opening)

    settlement.enqueue(Transaction("firms", "hh_landlord", cr(9500), COMMERCIAL_RENT, "shop-rent", 1))
    settlement.enqueue(Transaction("hh_landlord", "firms", cr(16700), "maintenance", "repairs", 1))
    settlement.settle()

    assert book["hh_landlord"].cash_cents == cr(20000) + cr(9500) - cr(16700)
    assert book["households"].cash_cents == cr(157131), "家庭主钱包不得因房东收租再被镜像入账"
    report = check_conservation(book, opening, settlement.journal)
    # 商业租金与维修都是本城内部转账：企业付给本地业主，业主付给本地维修企业，
    # 全城现金净变化为 0。净房产收入 21000 只是统计口径，不是新增现金。
    assert report.city_cash_delta_cents == 0
    assert report.world_cash_delta_cents == 0
    assert sum(report.category_totals.values()) == -cr(26200)


# --- 整数分与诊断 ------------------------------------------------------

def test_remainder_is_zero_when_amounts_are_integer_cents():
    book = make_book()
    opening = book.snapshot()
    settlement = Settlement(book, month=1, opening=opening)
    for index in range(20):
        settlement.enqueue(Transaction("firms", "households", cr(0.07) + index, WAGE, f"wage-{index}", 1))
    settlement.settle()

    report = check_conservation(book, opening, settlement.journal)
    assert report.world_cash_delta_cents == 0
    assert report.is_balanced


def test_category_totals_separate_income_and_expense():
    book = make_book()
    settlement = Settlement(book, month=1)
    settlement.enqueue(Transaction("firms", "households", cr(121900), WAGE, "payroll", 1))
    settlement.enqueue(Transaction("gov", "church", cr(660), "donation", "church-donation", 1))
    settlement.settle()

    totals = settlement.journal_by_category()
    assert totals[WAGE] == -cr(121900)
    assert totals["donation"] == -cr(660)


def test_transaction_can_be_traced_back_to_its_source_contract():
    book = make_book()
    settlement = Settlement(book, month=1)
    settlement.enqueue(Transaction("firms", "households", cr(60000), WAGE, "contract-A", 1, payment_part="advance"))
    settlement.enqueue(Transaction("firms", "households", cr(61900), WAGE, "contract-A", 1, payment_part="final"))
    settlement.settle()

    traced = settlement.trace("contract-A")
    assert [tx.payment_part for tx in traced] == ["advance", "final"]
    assert all(tx.tx_id.startswith("1:contract-A:") for tx in traced)


def test_accounts_reject_duplicate_ids_and_missing_parents():
    book = make_book()
    with pytest.raises(KeyError):
        book.open("gov", MUNICIPAL, "second", CITY, 100)
    with pytest.raises(KeyError):
        book.open("orphan", LANDLORD, "whoever", CITY, 0, parent_account_id="not-there")


def test_opening_snapshot_is_captured_before_first_payment():
    book = make_book()
    settlement = Settlement(book, month=1)
    opening = settlement.opening
    settlement.enqueue(wage_tx(amount_cents=cr(121900)))
    settlement.settle()

    assert opening["firms"] == cr(265200), "开账余额必须在第一笔付款前保存"
    assert book["firms"].cash_cents == cr(265200) - cr(121900)


def test_reloaded_settlement_does_not_pay_the_same_obligation_twice():
    """恢复幂等：读档重开服务后，重发同一工资义务不会再次付款。"""
    book = make_book()
    first = Settlement(book, month=1)
    first.enqueue(wage_tx(amount_cents=cr(121900)))
    first.settle()
    balance = book["firms"].cash_cents

    restored = AccountBook.from_dict(book.to_dict())
    reloaded = Settlement(restored, month=1,
                          settled=[Transaction.from_dict(tx.to_dict()) for tx in first.journal])
    assert [tx.tx_id for tx in reloaded.journal] == ["1:payroll-2026-01:main"]

    reloaded.enqueue(wage_tx(amount_cents=cr(121900)))
    reloaded.settle()

    assert restored["firms"].cash_cents == balance, "重复提交不得造成二次付款"
    assert len(reloaded.journal) == 1


def test_reloaded_settlement_keeps_saving_conserved():
    book = make_book()
    opening = book.snapshot()
    first = Settlement(book, month=1, opening=opening)
    first.enqueue(wage_tx(amount_cents=cr(121900)))
    first.settle()

    restored = AccountBook.from_dict(book.to_dict())
    assert restored.snapshot() == book.snapshot(), "存档往返后每个账户余额必须一致"
    assert_conserved(restored, opening, first.journal)