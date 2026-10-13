"""P03：消费预算。这是把「写死的 119000」换成「收入反馈」的那一层。

三条纪律在这里被钉死：
- 消费只能来自上期已实现的收入与实际储蓄，不能预支没到账的钱；
- 消费必须落在真实现金容量内，付不出就是付不出；
- 首月总额必须精确回到 P00 校准值，否则 P02 已验证的基准就废了。
"""
import pytest

from backend.config import LABOR_CONSUMPTION
from backend.core.errors import GameError
from backend.core.monthly import _band_taxes, simulate_month
from backend.model.economy import ReferenceCity, cr_to_cents
from backend.model.households import HouseholdRegistry
from backend.systems.consumption import (RESERVED_NEEDS, allocate_by_share, build_plan,
                                         sync_cohort_cash)
from backend.systems.fiscal import FiscalPolicy
from backend.systems.property_income import property_flows


@pytest.fixture
def city():
    return ReferenceCity()


@pytest.fixture
def registry():
    return HouseholdRegistry()


def _context(city, **overrides):
    """把市政与房产口径一次性备好，供直接调用 build_plan。"""
    policy = FiscalPolicy(city.config)
    prop = property_flows(city)
    assessed = cr_to_cents(city.config["housing"]["assessed_value"])
    band_tax, income_tax, property_tax = _band_taxes(
        city.household_registry, policy, assessed, policy.opening_faith)
    ctx = {
        "household_cash_cents": city.book[ReferenceCity.HOUSEHOLDS].cash_cents,
        "relief_cents": policy.relief_payments_cents,
        "residential_rent_cents": prop.residential_rent_cents,
        "donation_cents": cr_to_cents(city.config["religion"]["resident_donation"]),
        "band_tax": band_tax,
    }
    ctx.update(overrides)
    ctx["_income_tax_total"] = income_tax
    ctx["_property_tax_total"] = property_tax
    return ctx


@pytest.fixture
def ctx(city):
    return _context(city)


PLAN_FIELDS = ("household_cash_cents", "relief_cents", "residential_rent_cents",
               "donation_cents", "band_tax")


def make_plan(registry, month, ctx):
    return build_plan(registry, month=month,
                      **{key: ctx[key] for key in PLAN_FIELDS})


# --- 分摊工具 -----------------------------------------------------------

def test_allocate_by_share_preserves_the_total_for_fraction_weights():
    parts = allocate_by_share(3200000, [0.6, 0.3, 0.1])
    assert parts == [1920000, 960000, 320000]
    assert sum(parts) == 3200000


def test_allocate_by_share_normalises_raw_counts():
    """权重可以是户数而不是份额：375/262/113 户必须按权重和归一化，不能读成 375 倍。"""
    parts = allocate_by_share(2820000, [375, 262, 113])
    assert sum(parts) == 2820000
    assert parts[0] > parts[1] > parts[2]
    assert parts[0] / 2820000 == pytest.approx(375 / 750, abs=1e-3)


def test_allocate_by_share_rejects_degenerate_weights():
    with pytest.raises(ValueError, match="权重之和必须为正"):
        allocate_by_share(1000, [0, 0, 0])
    with pytest.raises(ValueError, match="权重不能为负"):
        allocate_by_share(1000, [1, -1, 0])


def test_allocate_by_share_on_empty_or_zero_input():
    assert allocate_by_share(1000, []) == []
    assert allocate_by_share(0, [1, 2]) == [0, 0]


# --- 首月校准 -----------------------------------------------------------

def test_first_month_hits_the_p00_calibrated_total(city, ctx):
    plan = make_plan(city.household_registry, 1, ctx)
    assert plan.calibrated is True
    assert plan.total_spend_cents == cr_to_cents(119000)
    assert plan.total_spend_cents == cr_to_cents(
        LABOR_CONSUMPTION["consumption"]["month_1_reference_total"])


def test_first_month_is_funded_by_real_cash_not_future_wages(city, ctx):
    """首月校准总额必须真的付得起，不能靠「下个月会发工资」凑数。"""
    plan = make_plan(city.household_registry, 1, ctx)
    assert plan.total_capacity_cents > plan.total_spend_cents
    for band in plan.bands:
        assert band.spend_cents <= band.capacity_cents, band.band_id


def test_first_month_refuses_to_spend_more_than_it_has(city):
    """房租被抬到付不起时，首月宁可报错也不许透支——校准总额不是可以牺牲的目标。"""
    ctx = _context(city, residential_rent_cents=cr_to_cents(250000))
    with pytest.raises(ValueError, match="超过全城可动用容量"):
        make_plan(city.household_registry, 1, ctx)


def test_basket_split_sums_to_each_band_spend(city, ctx):
    categories = LABOR_CONSUMPTION["basket_categories"]
    plan = make_plan(city.household_registry, 1, ctx)
    for band in plan.bands:
        assert sum(band.basket_split.values()) == band.spend_cents, band.band_id
        assert set(band.basket_split) == set(categories)


def test_food_and_fuel_are_reserved_before_anything_else(city, ctx):
    plan = make_plan(city.household_registry, 1, ctx)
    assert set(RESERVED_NEEDS) == {"rent", "food", "fuel"}
    for band in plan.bands:
        assert 0 <= band.reserved_cents <= band.disposable_cents


# --- 税收与必付支出 -----------------------------------------------------

def test_band_taxes_sum_exactly_to_the_city_wide_tax(city):
    """分层所得税合计必须精确等于全城税额，不能只算到高收入层（这曾是真 bug）。"""
    registry = city.household_registry
    policy = FiscalPolicy(city.config)
    assessed = cr_to_cents(city.config["housing"]["assessed_value"])
    band_tax, income_tax, property_tax = _band_taxes(registry, policy, assessed,
                                                      policy.opening_faith)

    assert income_tax == cr_to_cents(13864) == policy.income_tax(registry.total_gross_income_cents)
    assert property_tax == cr_to_cents(5505)
    assert sum(band_tax["income_tax"].values()) == income_tax
    assert sum(band_tax["property_tax"].values()) == property_tax
    assert all(v >= 0 for v in band_tax["income_tax"].values()), "税额不允许为负"
    assert all(v >= 0 for v in band_tax["property_tax"].values())


def test_mandatory_outflow_covers_rent_donation_and_both_taxes(city, ctx):
    plan = make_plan(city.household_registry, 1, ctx)
    expected = (ctx["residential_rent_cents"] + ctx["donation_cents"]
                + ctx["_income_tax_total"] + ctx["_property_tax_total"])
    assert sum(b.mandatory_cents for b in plan.bands) == expected


def test_inflow_sums_to_what_households_actually_receive(city, ctx):
    """家庭到手现金＝工资＋股息＋净房产收入＋救济，必须与房东分配后的口径一致。"""
    plan = make_plan(city.household_registry, 1, ctx)
    assert plan.total_inflow_cents == cr_to_cents(136300 + 16000 + 21000 + 3200)


# --- 跨月反馈 -----------------------------------------------------------

def test_second_month_is_driven_by_last_month_income_not_a_constant(city):
    first = simulate_month(city, month=1)
    second = simulate_month(city, month=2)

    assert first.revenue_cents == cr_to_cents(340000), "首月消费须精确等于校准值"
    assert second.revenue_cents != first.revenue_cents, "第 2 月起消费必须随收入变化"
    assert second.revenue_cents > first.revenue_cents, \
        "上期有储蓄时消费应略高于校准值，但仍由可支配收入决定"


def test_income_drop_shrinks_consumption(city):
    """收入归零、储蓄花光后消费必须跟着塌——这正是「按人口自动加钱」被换掉的地方。

    注意次序：储蓄还在的时候消费是平滑下降的（钱包里确实有钱），
    储蓄耗尽后才断崖式下跌，这才符合现实。
    """
    simulate_month(city, month=1)
    baseline = simulate_month(city, month=2).revenue_cents

    for cohort in city.household_registry:
        cohort.wage_income_cents = 0
        cohort.asset_income_cents = 0
    city.book[ReferenceCity.HOUSEHOLDS].cash_cents = cr_to_cents(60000)
    for cohort in city.household_registry:
        cohort.last_disposable_cents = cr_to_cents(1_000_000)   # 名义收入还在，但钱只剩 60000

    starved = simulate_month(city, month=3).revenue_cents
    assert starved < baseline, "收入归零后消费不得原地不动"


def test_consumption_is_capped_when_cash_runs_short(city):
    """上期可支配收入再高，钱不够也只能花到容量为止。"""
    for cohort in city.household_registry:
        cohort.wage_income_cents = 0
        cohort.asset_income_cents = 0
        cohort.last_disposable_cents = cr_to_cents(1_000_000)

    broke = _context(city, household_cash_cents=cr_to_cents(60000), relief_cents=0)
    plan = make_plan(city.household_registry, 2, broke)

    assert plan.total_spend_cents == plan.total_capacity_cents
    assert plan.total_capacity_cents < cr_to_cents(60000), "前置条件：容量确实被必付支出压住了"
    for band in plan.bands:
        assert band.spend_cents == band.capacity_cents, band.band_id
        assert band.spend_cents < band.disposable_cents, band.band_id


def test_unspent_money_accumulates_in_the_wallet(city):
    """没花的钱留在钱包里，下月钱包比上月末厚——储蓄不是被冻结的数字。"""
    simulate_month(city, month=1)
    after_first = city.book[ReferenceCity.HOUSEHOLDS].cash_cents
    simulate_month(city, month=2)
    after_second = city.book[ReferenceCity.HOUSEHOLDS].cash_cents

    assert after_second > after_first, "每月的可支配收入大于当期消费，储蓄应持续累积"


def test_unspent_budget_stays_with_households_not_with_firms(city, ctx):
    """未花预算留在家庭钱包：结余＝容量−消费，不能凭空变成商户收入。"""
    plan = make_plan(city.household_registry, 1, ctx)
    for band in plan.bands:
        assert band.saving_cents == band.capacity_cents - band.spend_cents
    assert plan.total_saving_cents > 0


def test_month_zero_consumption_is_reported_clearly(city):
    """空登记表导致消费为零时，要给出人话报错，而不是「交易金额必须为正：0」。"""
    simulate_month(city, month=1)
    city.book[ReferenceCity.HOUSEHOLDS].cash_cents = 0
    for cohort in city.household_registry:
        cohort.last_disposable_cents = 0
    with pytest.raises(GameError, match="消费预算为零"):
        simulate_month(city, month=2)


def test_a_broke_household_spends_nothing_and_fails_loudly(city):
    """全城无收入且无储蓄时，各层流入、必付与消费都应为 0，不得冒出天文数字。"""
    for cohort in city.household_registry:
        cohort.wage_income_cents = 0
        cohort.asset_income_cents = 0
        cohort.last_disposable_cents = 0

    broke = _context(city, household_cash_cents=0, relief_cents=0,
                     residential_rent_cents=0, donation_cents=0,
                     band_tax={"income_tax": {"low": 0, "middle": 0, "high": 0},
                               "property_tax": {"low": 0, "middle": 0, "high": 0}})
    plan = make_plan(city.household_registry, 2, broke)
    assert plan.total_inflow_cents == 0
    assert plan.total_mandatory_cents == 0
    assert plan.total_capacity_cents == 0
    assert plan.total_spend_cents == 0


def test_sync_cohort_cash_is_a_split_not_a_transfer(city):
    """按毛收入比例切分钱包只是预算视角，全城现金必须一分不变。"""
    before = city.book.snapshot()
    balance = city.book[ReferenceCity.HOUSEHOLDS].cash_cents
    sync_cohort_cash(city.household_registry, balance)

    assert city.book.snapshot() == before
    assert sum(c.cash_cents for c in city.household_registry) == balance
    assert all(c.cash_cents >= 0 for c in city.household_registry)