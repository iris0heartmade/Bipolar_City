"""P02：参考月由月度引擎真实清算的结果。

这些数字必须由引擎算出，不得从配置或期望表读回来断言自己。
基准来源为设计文档 §3.2 会计硬门槛。
"""
import pytest

from backend.config import CALIBRATION, INPUT_OUTPUT, REFERENCE_CITY
from backend.core.monthly import simulate_month
from backend.core.settlement import Settlement
from backend.model.economy import ReferenceCity, apply_rate, cr_to_cents, to_rate
from backend.model.transactions import Transaction
from backend.systems.fiscal import FiscalPolicy
from backend.systems.markets import market_flows, trade_flows
from backend.systems.production import corporate_ledger
from backend.systems.property_income import housing_capacity_check, property_flows
from backend.systems.reports import gva_production_method

CENT = 100
TARGETS = CALIBRATION["derived_targets"]


def cr(cents):
    return cents // CENT


@pytest.fixture
def city():
    return ReferenceCity()


@pytest.fixture
def report(city):
    opening = city.book.snapshot()
    month = simulate_month(city, month=1)
    return month


# --- 企业侧 ---------------------------------------------------------

def test_sector_aggregates_sum_to_published_totals(city):
    assert sum(s.firm_count for s in city.sectors) == 86
    assert sum(s.employees for s in city.sectors) == 1380
    assert city.private_wage_bill_cents == cr_to_cents(TARGETS["wages_private"])
    assert city.revenue_cents == cr_to_cents(TARGETS["firm_revenue"])
    assert city.input_cost_cents == cr_to_cents(TARGETS["firm_input_cost"])
    assert city.profit_before_tax_cents == cr_to_cents(TARGETS["profit_before_tax"])


def test_sector_profit_is_recomputed_from_raw_inputs(city):
    for sector in city.sectors:
        expected = (sector.revenue_cents - sector.input_cost_cents - sector.wage_bill_cents
                    - sector.commercial_rent_cents - sector.depreciation_cents - sector.fees_cents)
        assert sector.profit_before_tax_cents == expected, sector.sector_id


def test_corporate_ledger_keeps_depreciation_out_of_cash(city):
    ledger = corporate_ledger(city, FiscalPolicy(city.config), to_rate(0.55))
    assert ledger.profit_before_tax_cents == cr_to_cents(TARGETS["profit_before_tax"])
    assert ledger.profit_tax_cents == cr_to_cents(TARGETS["profit_tax"])
    assert ledger.profit_after_tax_cents == cr_to_cents(TARGETS["profit_after_tax"])
    assert ledger.dividend_cents == cr_to_cents(TARGETS["dividends"])
    assert ledger.retained_profit_cents == cr_to_cents(TARGETS["retained_profit"])
    assert ledger.depreciation_cents not in (ledger.cash_outflow_cents,)
    assert city.revenue_cents - ledger.cash_outflow_cents == cr_to_cents(TARGETS["firm_cash_change"])


def test_loss_making_sector_is_not_taxed_and_pays_no_dividend():
    config = {**REFERENCE_CITY, "firms": {**REFERENCE_CITY["firms"],
                                          "sectors": [dict(REFERENCE_CITY["firms"]["sectors"][0])]}}
    config["firms"]["sectors"][0]["revenue"] = 1        # 人为制造亏损
    city = ReferenceCity(config=config)
    policy = FiscalPolicy(config)
    ledger = corporate_ledger(city, policy, to_rate(0.55))

    assert ledger.profit_before_tax_cents < 0
    assert ledger.profit_tax_cents == 0, "亏损不得产生负税"
    assert ledger.dividend_cents == 0, "亏损不得分红"


# --- 居民与房产 -----------------------------------------------------

def test_resident_income_and_disposable_income(city, report):
    assert report.resident_gross_income_cents == cr_to_cents(TARGETS["resident_gross_income"])
    assert report.resident_disposable_income_cents == cr_to_cents(TARGETS["resident_disposable_income"])
    assert report.resident_cash_change_cents == cr_to_cents(TARGETS["resident_cash_change"])
    assert report.income_tax_cents == cr_to_cents(TARGETS["income_tax"])
    assert report.property_tax_cents == cr_to_cents(TARGETS["property_tax"])


def test_average_wage_uses_employed_not_population(city, report):
    employed = REFERENCE_CITY["demographics"]["employed"]
    population = REFERENCE_CITY["demographics"]["population"]
    assert report.average_wage_cents == city.total_wage_bill_cents // employed
    assert report.average_wage_cents > city.total_wage_bill_cents // population


def test_property_flows_and_net_income(city):
    flows = property_flows(city)
    assert flows.residential_rent_cents == cr_to_cents(TARGETS["residential_rent"])
    assert flows.commercial_rent_cents == cr_to_cents(TARGETS["commercial_rent"])
    assert flows.owner_repair_cents == cr_to_cents(TARGETS["owner_repair_spend"])
    assert flows.net_property_income_cents == 21000 * CENT


def test_housing_capacity_adds_up(city):
    check = housing_capacity_check(city)
    assert check["balanced"] is True
    assert check["total"] == 800 and check["occupied"] == 750 and check["vacant"] == 50


# --- 市政与教会 -----------------------------------------------------

def test_municipal_budget_closes_on_actual_payments(report):
    assert report.municipal_income_cents == cr_to_cents(TARGETS["municipal_income"])
    assert report.municipal_spending_cents == cr_to_cents(TARGETS["municipal_spending"])
    assert report.municipal_surplus_cents == cr_to_cents(TARGETS["municipal_surplus"])


def test_neutral_religion_pays_no_official_subsidy(city, report):
    neutral = next(t for t in REFERENCE_CITY["religion"]["policy_tiers"] if t["tier"] == 0)
    policy = FiscalPolicy(city.config)
    assert policy.official_subsidy(0) == cr_to_cents(neutral["subsidy"]) == 0
    assert report.church_reserve_change_cents == cr_to_cents(TARGETS["church_reserve_change"])


def test_church_donation_is_capped_by_available_cash():
    policy = FiscalPolicy()
    faith = to_rate(0.55)
    expected = apply_rate(cr_to_cents(1200), faith)
    assert policy.church_donation(cr_to_cents(100000), faith) == expected
    assert policy.church_donation(100, faith) == 100, "教会没钱不能硬捐"


# --- 口径交叉验证 -----------------------------------------------------

def test_four_headline_figures_are_not_interchangeable(report):
    """营业额、利润、现金变化、GVA 互不等价，混用就会掩盖经济错误。"""
    assert report.revenue_cents == 340000 * CENT
    assert report.profit_before_tax_cents == 42400 * CENT
    assert report.city_cash_change_cents == 27100 * CENT
    assert report.gva_production_cents == 211900 * CENT
    assert len({report.revenue_cents, report.profit_before_tax_cents,
                report.city_cash_change_cents, report.gva_production_cents}) == 4


def test_gva_breakdown_sums_to_total(city, report):
    ledger = corporate_ledger(city, FiscalPolicy(city.config), to_rate(0.55))
    gva = gva_production_method(city, ledger, property_flows(city))
    breakdown = report.gva_production_breakdown
    assert breakdown["private_firms_cents"] == 176500 * CENT
    assert breakdown["property_and_landlords_cents"] == 21000 * CENT
    assert breakdown["government_non_market_cents"] == 14400 * CENT
    assert breakdown["total_cents"] == gva.total_cents == cr_to_cents(TARGETS["gva"])


def test_revenue_has_eight_funded_sources():
    flows = market_flows()
    assert flows.total_revenue_cents == cr_to_cents(TARGETS["firm_revenue"])
    assert flows.household_consumption_cents == cr_to_cents(119000)
    assert flows.firm_intermediate_cents == cr_to_cents(86000)
    assert flows.external_inflow_cents == cr_to_cents(88200 + 5000 + 15000)


def test_strategic_goods_are_not_paid_twice():
    trade = trade_flows()
    assert trade["imports_detail"] == cr_to_cents(68000)
    assert trade["imports"] == trade["imports_detail"], "战略品金额已含在进口总额内"
    assert trade["exports"] == cr_to_cents(88200)


# --- 引擎行为 ---------------------------------------------------------

def test_wages_are_paid_by_real_employers_not_created(city, report):
    journal = city.last_journal
    wage_rows = [tx for tx in journal if tx.category == "wage"]
    payers = {tx.from_account for tx in wage_rows}
    assert payers == {ReferenceCity.FIRMS, ReferenceCity.GOVERNMENT}
    total_wages = sum(tx.amount_cents for tx in wage_rows)
    assert total_wages == city.total_wage_bill_cents == cr_to_cents(TARGETS["wages_total"])


def test_no_income_is_created_without_a_payer(city, report):
    """每一笔流入本城的现金都必须有对应的外部或本地付款方。"""
    incoming = {}
    for tx in city.last_journal:
        if tx.to_account != ReferenceCity.OUTSIDE:
            incoming[tx.from_account] = incoming.get(tx.from_account, 0) + tx.amount_cents
    assert set(incoming) <= {ReferenceCity.HOUSEHOLDS, ReferenceCity.LANDLORDS,
                             ReferenceCity.GOVERNMENT, ReferenceCity.CHURCH,
                             ReferenceCity.FIRMS, ReferenceCity.OUTSIDE}
    assert all(value > 0 for value in incoming.values())


def test_same_month_replay_does_not_pay_twice(city):
    first = simulate_month(city, month=1)
    assert first.city_cash_change_cents == cr_to_cents(TARGETS["city_net_cash_change"])
    after_first = city.book.snapshot()
    journal = city.last_journal

    replay = simulate_month(city, month=1,
                            settled=[Transaction.from_dict(t.to_dict()) for t in journal])

    assert city.book.snapshot() == after_first, "重复提交同一月份不得二次付款"
    assert replay.city_cash_change_cents == 0, "被幂等跳过的月份不再产生新的现金变动"
    assert len(journal) == 24, "本月恰好 24 笔交易，重放不新增"


def test_world_is_external_and_may_go_negative(city):
    simulate_month(city, month=1)
    world = city.book[ReferenceCity.OUTSIDE]
    assert world.is_external is True
    assert world.cash_cents == -cr_to_cents(TARGETS["city_net_cash_change"])


def test_landlord_is_household_property_not_extra_population(city):
    simulate_month(city, month=1)
    landlords = city.book[ReferenceCity.LANDLORDS]
    assert landlords.owner_kind == "landlord"
    assert landlords.parent_account_id == ReferenceCity.HOUSEHOLDS
    assert city.book.mirrored_pairs() == [], "房东与家庭主钱包不是同一主体的镜像"
    assert landlords.cash_cents == 21000 * CENT