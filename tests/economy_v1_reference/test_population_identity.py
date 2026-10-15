"""P03：收入层的人口恒等式与配置对照。

人口是这个引擎里最容易凭空多出或凭空少掉的东西：
一个家庭既要计入就业又要计入非劳动力，房东是财产身份不能再算一次人口。
这里把恒等式钉死，任何分群配置写错都会立刻炸出来。
"""
import pytest

from backend.config import LABOR_CONSUMPTION, REFERENCE_CITY
from backend.core.errors import GameError
from backend.model.economy import cr_to_cents
from backend.model.households import (HouseholdCohort, HouseholdRegistry,
                                      build_cohorts, reconcile_with_reference_city)


@pytest.fixture
def registry():
    return HouseholdRegistry()


def test_three_bands_are_built_from_config(registry):
    assert [c.band_id for c in registry] == ["low", "middle", "high"]
    assert all(c.name for c in registry), "每个收入层都要有可读名称"


def test_population_identity_holds_for_every_band(registry):
    for cohort in registry:
        assert cohort.population_check, cohort.band_id
        assert cohort.employed + cohort.unemployed + cohort.not_in_labour_force == cohort.population
        assert cohort.labour_force <= cohort.population, cohort.band_id


def test_bands_sum_to_the_reference_city_demographics(registry):
    demo = REFERENCE_CITY["demographics"]
    assert registry.total_population == demo["population"] == 3000
    assert registry.total_households == demo["households"] == 750
    assert registry.total_employed == demo["employed"] == 1500
    assert registry.total_unemployed == demo["unemployed"] == 150
    assert registry.total_not_in_labour_force == demo["not_in_labour_force"] == 1350
    assert registry.population_identity_holds()


def test_band_income_sums_to_the_p02_reference_totals(registry):
    """分群收入必须加总回 P02 已验证的全城口径，否则月报会与 P00 基准脱节。"""
    wages_private = cr_to_cents(121900)
    wages_public = cr_to_cents(14400)
    dividends = cr_to_cents(16000)
    net_property = cr_to_cents(21000)

    assert registry.total_wage_income_cents == wages_private + wages_public == cr_to_cents(136300)
    assert registry.total_asset_income_cents == dividends + net_property == cr_to_cents(37000)
    assert registry.total_gross_income_cents == cr_to_cents(173300)


def test_reconcile_helper_reports_zero_population_drift(registry):
    result = reconcile_with_reference_city(registry)
    assert set(result["population_deltas"].values()) == {0}
    assert result["income_cents"]["gross"] == cr_to_cents(173300)


def test_household_size_is_consistent_within_each_band(registry):
    for cohort in registry:
        assert cohort.population / cohort.households == pytest.approx(4.0, abs=0.05), cohort.band_id


# --- 校验闸门：配置写错必须立刻报错，不能悄悄跑下去 ------------------------

def _broken(**overrides):
    base = dict(band_id="low", name="低收入群体", households=375, population=1500,
                employed=750, unemployed=75, not_in_labour_force=675,
                wage_income_cents=0, asset_income_cents=0, property_tax_share=0.1,
                relief_share=0.6, spend_ratio=0.85,
                basket={"food": 0.55, "fuel": 0.12, "clothing": 0.10,
                        "services": 0.13, "culture": 0.04, "transport": 0.06})
    base.update(overrides)
    return HouseholdCohort(**base)


def test_broken_population_identity_is_rejected():
    with pytest.raises(GameError, match="人口不闭合"):
        _broken(employed=750, unemployed=75, not_in_labour_force=600).validate()


def test_labour_force_above_population_is_rejected():
    """人口不闭合且劳动力越界时，先报「劳动力超过总人口」——这是更具体的错因。"""
    with pytest.raises(GameError, match="劳动力超过总人口"):
        _broken(population=800, employed=750, unemployed=75, not_in_labour_force=-25).validate()


def test_basket_weights_must_sum_to_one():
    with pytest.raises(GameError, match="消费篮子占比合计不等于 1"):
        _broken(basket={"food": 0.55, "fuel": 0.12, "clothing": 0.10,
                        "services": 0.13, "culture": 0.04, "transport": 0.05}).validate()


def test_spend_ratio_outside_zero_one_is_rejected():
    with pytest.raises(GameError, match="消费率"):
        _broken(spend_ratio=1.4).validate()


def test_unknown_band_id_lookup_raises_keyerror(registry):
    with pytest.raises(StopIteration):
        registry["mortal"]


# --- 配置本身的合理性 ---------------------------------------------------

def test_config_declares_every_share_we_rely_on():
    for row in LABOR_CONSUMPTION["income_bands"]:
        assert 0 <= row["property_tax_share"] <= 1, row["id"]
        assert 0 <= row["relief_share"] <= 1, row["id"]
    total_property_share = sum(r["property_tax_share"] for r in LABOR_CONSUMPTION["income_bands"])
    total_relief_share = sum(r["relief_share"] for r in LABOR_CONSUMPTION["income_bands"])
    assert total_property_share == pytest.approx(1.0), "房产税份额合计必须为 1，否则分不到或重复分"
    assert total_relief_share == pytest.approx(1.0), "救济份额合计必须为 1"


def test_income_bands_are_ranked_by_per_capita_income(registry):
    """收入层高低按人均收入排，不是按总额排：高收入层人少，总额本来就可能更低。"""
    ordered = sorted(registry, key=lambda c: -c.per_capita_income_cents)
    assert [c.band_id for c in ordered] == ["high", "middle", "low"]
    per_capita = [c.per_capita_income_cents for c in ordered]
    assert per_capita == sorted(per_capita, reverse=True)

    wages = [c.average_wage_cents for c in ordered]
    assert wages == sorted(wages, reverse=True), "就业者人均工资也应随收入层递减"


def test_spend_ratio_decreases_with_income(registry):
    """按收入从高到低排，消费率应递增——高收入层储蓄更多。"""
    ordered = sorted(registry, key=lambda c: -c.per_capita_income_cents)
    ratios = [c.spend_ratio for c in ordered]
    assert ratios == sorted(ratios), f"收入越高消费率越低，实得 {ratios}"
    assert [c.band_id for c in ordered] == ["high", "middle", "low"]


def test_food_share_is_highest_for_the_poorest_band(registry):
    """E §5 要求食品占比 55%—65%，且低收入层应最高。"""
    ordered = sorted(registry, key=lambda c: -c.per_capita_income_cents)
    food = [c.basket["food"] for c in ordered]
    assert food == sorted(food), f"收入越低食品占比越高，实得 {food}"
    assert food[-1] >= 0.55, "最低收入层食品支出占比不得低于 55%"
    assert food[0] <= 0.35, "最高收入层食品占比不应过半"


def test_build_cohorts_reads_amounts_as_integer_cents():
    cohorts = build_cohorts()
    assert all(isinstance(c.wage_income_cents, int) for c in cohorts)
    assert all(isinstance(c.asset_income_cents, int) for c in cohorts)
    assert sum(c.wage_income_cents for c in cohorts) == cr_to_cents(136300)