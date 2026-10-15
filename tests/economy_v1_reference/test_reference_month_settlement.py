"""把 P00 的参考月流量真正跑过 P01 清算引擎。

P00 的 test_reference_config.py 只核对配置算术，本文件证明同一批数字
经由交易引擎清算后依然闭合：每个主体的现金变化与 derived_targets 一致，
且本地现金变化正好等于外部账户的反向变化。

本文件只验证"已定义的流量能够清算并守恒"，不涉及订单分配、价格形成或生产调度，
那些属于 P02，不在这里冒认已完成。
"""
import pytest

from backend.config import CALIBRATION, INPUT_OUTPUT, REFERENCE_CITY
from backend.core.settlement import Settlement
from backend.model.accounts import (CHURCH, EXTERNAL, FIRM, HOUSEHOLD, LANDLORD,
                                   MUNICIPAL, AccountBook)
from backend.model.transactions import (CAPITAL, COMMERCIAL_RENT, DIVIDEND, DONATION,
                                       EXPORT, FEES, GRANT, IMPORT, INCOME_TAX, INTEREST,
                                       MAINTENANCE, PROCUREMENT, PROFIT_TAX, PROPERTY_TAX,
                                       RELIEF, RESIDENTIAL_RENT, SALE, TRANSFER, WAGE,
                                       Transaction)
from backend.systems.accounting import check_conservation

CENT = 100
CITY = "reference_city"
TARGETS = CALIBRATION["derived_targets"]
IO = INPUT_OUTPUT
CITY_CONFIG = REFERENCE_CITY

FIRMS = "firms"
FIRMS_OPENING = CALIBRATION["opening_balances"]


def cr(amount):
    return int(round(amount * CENT))


def build_book():
    book = AccountBook()
    book.open("gov", MUNICIPAL, "city_government", CITY, cr(FIRMS_OPENING["city_government"]))
    book.open("church", CHURCH, "city_church", CITY, cr(FIRMS_OPENING["church"]))
    book.open("households", HOUSEHOLD, "all_households", CITY, cr(FIRMS_OPENING["households_total"]))
    # 房东是家庭的财产身份，挂在主钱包下，不额外增加人口。
    book.open("landlord", LANDLORD, "household_landlords", CITY, 0, parent_account_id="households")
    book.open(FIRMS, FIRM, "all_firms", CITY, cr(sum(f["opening_cash"] for f in FIRMS_OPENING["firms"])))
    book.open("world", EXTERNAL, "rest_of_world", None, 0)
    return book


def reference_month_transactions():
    """把 input_output_v1.json 的每条流向翻译成一笔真实交易，付款方与收款方都显式指定。"""
    revenue = IO["firm_revenue_sources"]
    firm_in = IO["firm_flows"]["inflows"]
    firm_out = IO["firm_flows"]["outflows"]
    resident_out = IO["resident_flows"]["outflows"]
    resident_in = IO["resident_flows"]["inflows"]
    municipal_in = IO["municipal_flows"]["inflows"]
    municipal_out = IO["municipal_flows"]["outflows"]
    trade = IO["trade"]

    def tx(index, sender, receiver, amount, category):
        return Transaction(from_account=sender, to_account=receiver, amount_cents=cr(amount),
                           category=category, source_ref=f"ref-month-{index:02d}", month=1)

    rows = [
        # 企业收入八来源
        tx(1, "households", FIRMS, revenue["household_consumption"], SALE),
        tx(2, "landlord", FIRMS, revenue["owner_repair_spend"], MAINTENANCE),
        tx(3, "gov", FIRMS, revenue["municipal_procurement"], PROCUREMENT),
        tx(4, "gov", FIRMS, revenue["municipal_capital"], CAPITAL),
        tx(5, "world", FIRMS, revenue["exports"], EXPORT),
        tx(6, "world", FIRMS, revenue["tourist_spend"], SALE),
        # 企业间互购是本城内部转移，净额为零，不产生新钱
        tx(7, FIRMS, FIRMS, revenue["firm_intermediate"], TRANSFER),
        # 私人资本投入与企业资本品采购等额进出，因此不改变对外净流量公式，
        # 但企业现金必须真实收到与付出，不能只记支出。
        tx(8, "world", FIRMS, revenue["private_capital"], CAPITAL),
        # 企业支出
        tx(9, FIRMS, "world", IO["firm_input_split"]["imports"], IMPORT),
        tx(10, FIRMS, "households", firm_out["wages"], WAGE),
        tx(11, FIRMS, "landlord", firm_out["commercial_rent"], COMMERCIAL_RENT),
        tx(12, FIRMS, "gov", firm_out["fees"], FEES),
        tx(13, FIRMS, "gov", firm_out["profit_tax"], PROFIT_TAX),
        tx(14, FIRMS, "households", firm_out["dividends"], DIVIDEND),
        tx(15, FIRMS, "world", firm_out["capital_purchase"], CAPITAL),
        # 市政支出
        tx(16, "gov", "households", municipal_out["public_wages"], WAGE),
        tx(17, "gov", "households", municipal_out["relief"], RELIEF),
        # 市政收入
        tx(18, "households", "gov", municipal_in["income_tax"], INCOME_TAX),
        tx(19, "households", "gov", municipal_in["property_tax"], PROPERTY_TAX),
        tx(20, "world", "gov", trade["central_grant"], GRANT),
        tx(21, "gov", "world", trade["external_interest_paid"], INTEREST),
        tx(22, "church", "gov", municipal_in["church_donation"], DONATION),
        # 居民支出
        tx(23, "households", "landlord", resident_out["residential_rent"], RESIDENTIAL_RENT),
        tx(24, "households", "church", resident_out["donation"], DONATION),
    ]
    assert resident_in["relief"] == municipal_out["relief"]
    assert firm_in["revenue"] == sum(revenue[k] for k in revenue if k != "note")
    return rows


@pytest.fixture
def settled_month():
    book = build_book()
    opening = book.snapshot()
    settlement = Settlement(book, month=1, opening=opening)
    for transaction in reference_month_transactions():
        settlement.enqueue(transaction)
    settled = settlement.settle()
    return book, opening, settlement, settled


def test_reference_month_settles_without_rejected_payments(settled_month):
    book, _, settlement, settled = settled_month
    assert len(settled) == 24
    assert settlement.pending_count() == 0
    assert all(tx.status == "settled" for tx in settled)
    assert book["firms"].cash_cents > 0


def test_reference_month_closes_for_every_actor(settled_month):
    book, opening, _, _ = settled_month

    assert book[FIRMS].cash_cents - opening[FIRMS] == cr(TARGETS["firm_cash_change"]) == cr(17312)
    # 居民按家庭身份合并主钱包与房东子钱包后才是文档口径的现金变化
    residents = (book["households"].cash_cents - opening["households"]) + \
                (book["landlord"].cash_cents - opening["landlord"])
    assert residents == cr(TARGETS["resident_cash_change"]) == cr(8431)
    assert book["gov"].cash_cents - opening["gov"] == cr(TARGETS["municipal_surplus"]) == cr(517)
    assert book["church"].cash_cents - opening["church"] == cr(TARGETS["church_reserve_change"]) == cr(840)


def test_reference_month_external_mirror_explains_city_change(settled_month):
    book, opening, settlement, _ = settled_month
    report = check_conservation(book, opening, settlement.journal)

    assert report.city_cash_delta_cents == cr(TARGETS["city_net_cash_change"]) == cr(27100)
    assert report.external_cash_delta_cents == -cr(TARGETS["external_net_cash_change"])
    assert report.world_cash_delta_cents == 0
    assert report.is_balanced, report.violations
    assert report.violations == []


def test_reference_month_produces_no_negative_local_balance(settled_month):
    book, _, _, _ = settled_month
    for account in book.local_leaves():
        assert account.cash_cents >= 0, account.account_id
    # 只有"世界其余部分"这个镜像账户会因进口而为负
    assert book["world"].cash_cents == -cr(27100)
    assert all(a.cash_cents >= 0 for a in book.external_leaves() if a.account_id != "world")


def test_reference_month_rent_and_dividend_reach_the_right_wallets(settled_month):
    book, opening, settlement, _ = settled_month

    # 房东是家庭财产身份：住宅租金与商业租金进子钱包，主钱包不被重复入账
    assert book["landlord"].cash_cents == cr(TARGETS["residential_rent"]) + cr(TARGETS["commercial_rent"]) \
        - cr(TARGETS["owner_repair_spend"]) == cr(21000)
    assert book["households"].cash_cents == opening["households"] - cr(12569)
    # 维修费是业主付给企业的真实付款，企业侧已经记为收入
    assert settlement.journal_by_category()[MAINTENANCE] == -cr(TARGETS["owner_repair_spend"])


def test_reference_month_firm_intermediate_purchase_creates_no_money(settled_month):
    book, opening, settlement, _ = settled_month
    transfer_rows = [tx for tx in settlement.journal if tx.category == TRANSFER]
    assert len(transfer_rows) == 1
    assert transfer_rows[0].from_account == transfer_rows[0].to_account == FIRMS
    # 企业总现金变化 17312 已经包含互购的收与付，不能把 86000 再加一次
    assert book[FIRMS].cash_cents - opening[FIRMS] == cr(17312)


def test_private_capital_inflow_and_capex_outflow_cancel_at_the_border(settled_month):
    """私人资本投入与资本品采购等额进出，不进入对外净流量公式，但两边都要真实记账。"""
    book, opening, settlement, _ = settled_month
    capital_rows = [tx for tx in settlement.journal if tx.category == CAPITAL]

    foreign_inflow = sum(tx.amount_cents for tx in capital_rows
                         if tx.to_account == FIRMS and tx.from_account == "world")
    municipal_inflow = sum(tx.amount_cents for tx in capital_rows
                           if tx.to_account == FIRMS and tx.from_account == "gov")
    outflow = sum(tx.amount_cents for tx in capital_rows if tx.from_account == FIRMS)

    assert foreign_inflow == outflow == cr(15000)
    assert municipal_inflow == cr(1500)
    # 净对外流量只受进出口、游客、拨款与利息影响
    assert book["world"].cash_cents == opening["world"] - cr(27100)


def test_reference_month_categories_reconcile_with_input_output(settled_month):
    _, _, settlement, _ = settled_month
    totals = settlement.journal_by_category()
    revenue_sources = IO["firm_revenue_sources"]

    assert -totals[EXPORT] == cr(TARGETS["exports"])
    assert -totals[IMPORT] == cr(TARGETS["imports"])
    assert -totals[GRANT] == cr(TARGETS["central_grant"])
    assert -totals[INTEREST] == cr(TARGETS["external_interest"])
    assert -totals[PROFIT_TAX] == cr(TARGETS["profit_tax"])
    assert -totals[INCOME_TAX] == cr(TARGETS["income_tax"])
    assert -totals[PROPERTY_TAX] == cr(TARGETS["property_tax"])
    assert -totals[DIVIDEND] == cr(TARGETS["dividends"])
    assert -totals[PROCUREMENT] == cr(revenue_sources["municipal_procurement"])
    assert -totals[SALE] == cr(revenue_sources["household_consumption"] + TARGETS["tourist_spend"])
    assert -totals[COMMERCIAL_RENT] == cr(TARGETS["commercial_rent"])
    assert -totals[RESIDENTIAL_RENT] == cr(TARGETS["residential_rent"])
    # 类别汇总是付款方视角的支出额：互购确实发生 86000 的内部支付，
    # 但它是企业付给企业自己，现金净影响为 0（见 firm_intermediate 那条断言）。
    assert -totals[TRANSFER] == cr(IO["firm_revenue_sources"]["firm_intermediate"])
    transfer_row = next(tx for tx in settlement.journal if tx.category == TRANSFER)
    assert transfer_row.from_account == transfer_row.to_account, "互购不是凭空消失，而是企业内部的资金转移"


def test_reference_month_rejects_duplicate_submission(settled_month):
    book, opening, settlement, _ = settled_month
    after_month = book.snapshot()
    replay = Settlement(book, month=1, opening=opening, settled=settlement.journal)

    for transaction in reference_month_transactions():
        replay.enqueue(transaction)

    assert replay.pending_count() == 0, "同一份月报重复提交不得再次改变任何余额"
    assert book.snapshot() == after_month
    assert check_conservation(book, opening, settlement.journal).is_balanced


def test_reference_month_book_survives_save_and_reload(settled_month):
    book, opening, settlement, settled = settled_month
    reloaded = AccountBook.from_dict(book.to_dict())

    assert reloaded.snapshot() == book.snapshot()
    replay = Settlement(reloaded, month=1, opening=opening,
                        settled=[Transaction.from_dict(tx.to_dict()) for tx in settled])
    for transaction in reference_month_transactions():
        replay.enqueue(transaction)
    replay.settle()

    assert reloaded.snapshot() == book.snapshot(), "存档往返后再次重放不得改动余额"
    assert check_conservation(reloaded, opening, settled).is_balanced


def test_reference_month_public_sector_matches_published_baseline(settled_month):
    """公共工资、采购与市政资本都来自配置，不额外加一遍道路或防御费。"""
    book, opening, settlement, _ = settled_month
    public = REFERENCE_CITY["public_sector"]
    procurement_total = sum(p["spend"] for p in public["procurement"])

    assert public["wage_bill"] == TARGETS["wages_public"] == 14400
    assert procurement_total == IO["firm_revenue_sources"]["municipal_procurement"] == 8600
    assert settlement.journal_by_category()[PROCUREMENT] == -cr(8600)

    wages_paid = sum(tx.amount_cents for tx in settlement.journal
                     if tx.category == WAGE and tx.to_account == "households")
    assert wages_paid == cr(TARGETS["wages_total"]) == cr(136300), "公共工资与私企工资合计须等于全城工资"


def test_resource_flows_are_not_paid_twice(settled_month):
    """粮食与魔材进口金额已含在 68000 内，不得再加一笔战略品付款。"""
    _, _, settlement, _ = settled_month
    trade = IO["trade"]
    # 配置以 CR 计价，此处先按配置单位核对，再换算到引擎的整数分
    strategic_imports = sum(line["value"] for line in trade["imports"] if "qty" in line)
    strategic_exports = sum(line["value"] for line in trade["exports"] if "qty" in line)

    assert strategic_imports == 5500
    assert strategic_exports == 3000
    assert -settlement.journal_by_category()[IMPORT] == cr(TARGETS["imports"]) == cr(68000)
    assert -settlement.journal_by_category()[EXPORT] == cr(TARGETS["exports"]) == cr(88200)