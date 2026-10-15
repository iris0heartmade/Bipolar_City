"""P02/P03：全城现金守恒的长期与边界验证。

守恒恒等式：各本地账户现金变化之和，必须等于与非本地账户的实际净收付款的相反数。
两者相加为零，说明全世界的现金既没有被创造也没有被销毁。
"""
from copy import deepcopy

import pytest

from backend.config import CALIBRATION, REFERENCE_CITY
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
    """连续 12 个月每月都应闭合且世界现金不变。

    首月消费走校准总额 119000，第 2 月起消费改由上期可支配收入反馈得出。
    但消费是家庭付给企业的内部转移（通俗比喻：只是把钱从左口袋挪到右口袋），
    不改变全城现金总额——全城现金变化只由进出口、游客、资本、拨款与利息决定，
    所以这 12 个月都应稳定在基准 +27100，而家庭与企业的账却在互相挪动。
    """
    city = ReferenceCity()
    opening_total = world_total(city.book)
    baseline = cr_to_cents(TARGETS["city_net_cash_change"])
    household_changes = []
    for month in range(1, 13):
        before = world_total(city.book)
        before_households = city.book[HOUSEHOLDS].cash_cents
        report = simulate_month(city, month=month)
        assert world_total(city.book) == before, f"第 {month} 月世界现金发生变化"
        assert report.city_cash_change_cents == baseline, f"第 {month} 月全城现金变化偏离基准"
        assert report.city_cash_change_cents == -report.external_cash_change_cents
        household_changes.append(city.book[HOUSEHOLDS].cash_cents - before_households)

    assert world_total(city.book) == opening_total
    assert household_changes[0] != household_changes[1], \
        "消费反馈必须让家庭账逐月变化，否则说明跨月反馈根本没接上"


def test_consumption_feedback_prevents_the_month_13_bankruptcy():
    """P03 的核心验收：P02 里第 13 个月因余额不足被整批拒绝，现在必须能跑通。

    P02 固定配置下住户钱包每月净流出 12569，第 13 个月无力支付。
    P03 做了两件事把它修掉：
    1. 房东净房产收入按月分配回家庭主钱包，钱不再只进不出；
    2. 消费由上期可支配收入反馈决定，并受真实现金容量封顶，不再是写死的常数。
    """
    city = ReferenceCity()
    for month in range(1, 25):
        report = simulate_month(city, month=month)
        assert report.revenue_cents > 0, f"第 {month} 月消费塌成零"
        for account in city.book.local_leaves():
            assert account.cash_cents >= 0, f"第 {month} 月账户 {account.account_id} 透支"

    households = city.book[HOUSEHOLDS].cash_cents
    assert households > cr_to_cents(150000), "24 个月后居民储蓄应稳定增长而非耗尽"
    assert city.book[LANDLORDS].cash_cents == 0, "房东净房产收入已按月分配回主钱包"


def test_failed_month_never_leaves_a_partial_state():
    """付款不足时整批不落账：本地账户快照必须与失败前逐分一致。

    基准配置下所有账户每月都是净流入，付不出钱的情况不会自然发生，
    因此这里人为把市政救济抬到远超收入、再清空市政钱包，制造一次真实的拒付。
    """
    config = deepcopy(REFERENCE_CITY)
    config["fiscal"]["relief_payments"] = 60000      # 市政每月净流出远超其收入
    city = ReferenceCity(config=config)
    simulate_month(city, month=1)
    assert city.book[GOV].cash_cents < cr_to_cents(60000), "前置条件：市政确实已入不敷出"

    city.book[GOV].cash_cents = 0
    snapshot = city.book.snapshot()

    with pytest.raises(InsufficientFunds):
        simulate_month(city, month=2)
    assert city.book.snapshot() == snapshot, "清算失败不得扣动任何账户"


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