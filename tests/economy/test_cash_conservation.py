"""P02：全城现金守恒的长期与边界验证。

守恒恒等式：各本地账户现金变化之和，必须等于与非本地账户的实际净收付款的相反数。
两者相加为零，说明全世界的现金既没有被创造也没有被销毁。
"""
import pytest

from backend.config import CALIBRATION
from backend.core.monthly import simulate_month
from backend.core.settlement import InsufficientFunds, Settlement
from backend.model.accounts import AccountBook
from backend.model.economy import ReferenceCity, cr_to_cents
from backend.model.transactions import Transaction
from backend.systems.accounting import check_conservation

CENT = 100
TARGETS = CALIBRATION["derived_targets"]
GOV, CHURCH = ReferenceCity.GOVERNMENT, ReferenceCity.CHURCH
HOUSEHOLDS, LANDLORDS = ReferenceCity.HOUSEHOLDS, ReferenceCity.LANDLORDS
FIRMS, OUTSIDE = ReferenceCity.FIRMS, ReferenceCity.OUTSIDE
LOCAL = (GOV, CHURCH, HOUSEHOLDS, LANDLORDS, FIRMS)


def world_total(book):
    return sum(a.cash_cents for a in book.leaves())


# --- 单月守恒 ---------------------------------------------------------

def test_local_delta_equals_negative_external_delta():
    city = ReferenceCity()
    opening = city.book.snapshot()
    simulate_month(city, month=1)

    report = check_conservation(city.book, opening, city.last_journal)
    assert report.city_cash_delta_cents == cr_to_cents(TARGETS["city_net_cash_change"])
    assert report.external_cash_delta_cents == -cr_to_cents(TARGETS["external_net_cash_change"])
    assert report.world_cash_delta_cents == 0
    assert report.is_balanced and report.violations == []


def test_world_total_cash_never_changes():
    city = ReferenceCity()
    before = world_total(city.book)
    simulate_month(city, month=1)
    assert world_total(city.book) == before, "全城现金总量不得因结算而改变"


def test_each_account_delta_equals_its_own_net_trades():
    """逐账户核对：余额变化必须恰好等于它在交易里的净头寸，不允许任何系统直接改余额。"""
    city = ReferenceCity()
    opening = city.book.snapshot()
    simulate_month(city, month=1)

    expected = {name: 0 for name in LOCAL + (OUTSIDE,)}
    for tx in city.last_journal:
        expected[tx.from_account] -= tx.amount_cents
        expected[tx.to_account] += tx.amount_cents

    for name in LOCAL + (OUTSIDE,):
        assert city.book[name].cash_cents - opening[name] == expected[name], name


def test_resident_cash_is_redistribution_not_creation():
    """居民账户没有任何一笔收入直接来自外部世界边界，居民的钱只能由企业与市政真实转付。"""
    city = ReferenceCity()
    simulate_month(city, month=1)
    for tx in city.last_journal:
        if tx.to_account in (HOUSEHOLDS, LANDLORDS) and tx.amount_cents > 0:
            assert tx.from_account in LOCAL, \
                f"居民收入来自外部账户 {tx.from_account}，说明该笔现金无真实付款方"


def test_internal_transfers_alone_do_not_create_cash():
    """剔除对外交易后，纯内部转移的净额必须为零：本地账户之间只是换个口袋。"""
    city = ReferenceCity()
    simulate_month(city, month=1)
    internal = [tx for tx in city.last_journal if OUTSIDE not in (tx.from_account, tx.to_account)]
    net = 0
    for tx in internal:
        net += tx.amount_cents if tx.to_account != OUTSIDE else 0
        net -= tx.amount_cents if tx.from_account != OUTSIDE else 0
    assert net == 0


# --- 长期稳定性 -------------------------------------------------------

def test_twelve_consecutive_months_stay_balanced():
    """固定配置下连续 12 个月，每月都应闭合且世界现金不变。"""
    city = ReferenceCity()
    opening_total = world_total(city.book)
    for month in range(1, 13):
        before = world_total(city.book)
        report = simulate_month(city, month=month)
        assert world_total(city.book) == before, f"第 {month} 月世界现金发生变化"
        assert report.city_cash_change_cents == cr_to_cents(TARGETS["city_net_cash_change"])
    assert world_total(city.book) == opening_total


def test_fixed_baseline_drains_household_savings_and_then_stalls():
    """已知边界：P02 固定配置下居民每月净流出 12569，第 13 个月将无力支付，整批被拒。

    这不是 bug，而是「消费不随收入变化」的结果——P03 引入预算反馈后才会改变。
    这里把行为钉死，防止将来有人误以为是无穷印钞或莫名破产。
    """
    city = ReferenceCity()
    for month in range(1, 13):
        simulate_month(city, month=month)
    households = city.book[HOUSEHOLDS].cash_cents
    assert households < cr_to_cents(20000), "第 12 月末居民储蓄应已接近耗尽"

    with pytest.raises(InsufficientFunds):
        simulate_month(city, month=13)
    assert city.book[HOUSEHOLDS].cash_cents == households, "清算失败不得扣动居民余额"


def test_failure_leaves_every_account_untouched():
    city = ReferenceCity()
    for month in range(1, 14):
        try:
            simulate_month(city, month=month)
        except InsufficientFunds:
            break
    snapshot = city.book.snapshot()
    with pytest.raises(InsufficientFunds):
        simulate_month(city, month=13)
    assert city.book.snapshot() == snapshot


# --- 存档与恢复 -------------------------------------------------------

def test_book_survives_save_and_reload_without_replay():
    city = ReferenceCity()
    simulate_month(city, month=1)
    opening = city.book.snapshot()
    journal = [Transaction.from_dict(t.to_dict()) for t in city.last_journal]
    balance = city.book.snapshot()

    restored = AccountBook.from_dict(city.book.to_dict())
    assert restored.snapshot() == balance

    replay = simulate_month(ReferenceCity(book=restored), month=1, settled=journal)
    assert restored.snapshot() == balance, "读档重放不得二次付款"
    assert replay.city_cash_change_cents == 0


def test_opening_snapshot_is_taken_before_any_payment():
    city = ReferenceCity()
    opening = city.book.snapshot()
    simulate_month(city, month=1)
    for name, start in opening.items():
        assert city.book[name].cash_cents != start or name == OUTSIDE or start == 0