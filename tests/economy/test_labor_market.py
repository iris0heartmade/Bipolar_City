"""P03：劳动力市场。岗位必须由雇主真实提供，招聘只能填补空缺。

这一组测试防的是「岗位凭空生成」和「同一个人被两家企业重复录用」两类错误。
招聘不是把失业人口填进表格的装饰动作，它必须改变某个群体的可用劳动力。
"""
import pytest

from backend.config import LABOR_CONSUMPTION, REFERENCE_CITY
from backend.core.errors import GameError
from backend.model.economy import ReferenceCity, cr_to_cents
from backend.model.households import HouseholdRegistry
from backend.systems.labor import (JobPost, build_posts, fill_vacancies, unemployment_rate,
                                   validate_posts)


@pytest.fixture
def city():
    return ReferenceCity()


@pytest.fixture
def registry():
    return HouseholdRegistry()


@pytest.fixture
def posts(city):
    return build_posts(city)


def test_posts_come_from_real_employers(city, posts):
    private = [p for p in posts if p.employer_kind == "private"]
    public = [p for p in posts if p.employer_kind == "public"]

    assert [p.employer_id for p in private] == [s.sector_id for s in city.sectors]
    assert [p.employer_id for p in public] == [r["id"] for r in city.config["public_sector"]["posts"]]
    assert sum(p.posts for p in private) == 1380 == sum(s.employees for s in city.sectors)
    assert sum(p.posts for p in public) == 120
    assert sum(p.posts for p in posts) == 1500


def test_post_wages_match_the_configured_payroll(posts):
    """岗位工资必须与工资总额对得上，否则劳动模块会自己造出一份工资。"""
    total = sum(p.posts * p.wage_cents for p in posts)
    assert total == cr_to_cents(REFERENCE_CITY["public_sector"]["wage_bill"]) \
        + cr_to_cents(121900)


def test_validate_posts_accepts_the_reference_configuration(posts, registry):
    validate_posts(posts, registry)


def test_baseline_has_no_vacancies_so_nobody_is_hired(posts, registry):
    """基准配置岗位已坐满：新招必须为 0，150 名失业者保持待业，不得凭空被录用。"""
    assignment = fill_vacancies(posts, registry)

    assert assignment.total_newly_hired == 0
    assert assignment.vacant_posts == 0
    assert assignment.total_unemployed == registry.total_unemployed == 150
    assert assignment.total_employed == registry.total_employed == 1500
    assert sum(assignment.unemployment_by_band.values()) == 150


def test_expansion_hires_from_unemployed_and_never_double_counts(posts, registry):
    """零售业扩张 20 个岗位：只能从失业者里招，且招完失业人数相应减少。"""
    assignment = fill_vacancies(posts, registry, {"retail": 20})

    assert assignment.total_newly_hired == 20
    assert assignment.vacant_posts == 20
    assert assignment.total_unemployed == 130
    # 录用发生在有剩余求职者的群体上，被录用者从失业池里消失
    for band_id, hired in assignment.newly_hired_by_band.items():
        assert assignment.unemployment_by_band[band_id] == \
            registry[band_id].unemployed - hired
    assert sum(assignment.unemployment_by_band.values()) + assignment.total_newly_hired == 150


def test_hiring_cost_is_paid_at_the_posted_wage(posts, registry):
    """填补空缺不改变工资总额：空缺岗位原本就不发工资，填上后补回同一价。"""
    baseline = fill_vacancies(posts, registry)
    filled = fill_vacancies(posts, registry, {"retail": 20})

    assert baseline.wage_bill_cents == cr_to_cents(136300)
    assert filled.wage_bill_cents == cr_to_cents(136300)
    retail = next(p for p in posts if p.employer_id == "retail")
    assert retail.posts * retail.wage_cents == cr_to_cents(280 * 82)


def test_high_wage_posts_are_filled_by_the_highest_earning_band_first(posts, registry):
    """工资降序匹配：高工资岗位先由高收入层填补，避免低技能劳动力挤走高技能岗位。"""
    assignment = fill_vacancies(posts, registry, {"mages": 20})

    assert assignment.newly_hired_by_band.get("high") == 20, \
        "魔法师岗位工资 220，应优先由高收入层填补"
    assert assignment.unemployment_by_band["high"] == registry["high"].unemployed - 20


def test_matching_order_is_stable_and_reproducible(posts, registry):
    first = fill_vacancies(posts, registry, {"light_industry": 30})
    second = fill_vacancies(posts, registry, {"light_industry": 30})
    assert first.newly_hired_by_band == second.newly_hired_by_band
    assert first.wage_bill_cents == second.wage_bill_cents


def test_vacancies_beyond_the_unemployed_pool_are_rejected(posts, registry):
    """想一口气招 200 人但只有 150 名失业者：必须报错，不许凭空变出劳动力。"""
    with pytest.raises(GameError, match="无可用劳动力"):
        fill_vacancies(posts, registry, {"light_industry": 200})


def test_negative_vacancy_is_rejected(posts, registry):
    with pytest.raises(GameError, match="不能为负"):
        fill_vacancies(posts, registry, {"retail": -1})


def test_vacancy_beyond_post_count_is_rejected(posts, registry):
    retail_posts = next(p.posts for p in posts if p.employer_id == "retail")
    with pytest.raises(GameError, match="超过岗位总数"):
        fill_vacancies(posts, registry, {"retail": retail_posts + 1})


def test_posts_above_employment_are_rejected(posts, registry):
    """岗位数超过就业人数＝凭空创造工作，直接拒绝，且这条检查必须排在配置对不上之前。"""
    with pytest.raises(GameError, match="不得凭空创造岗位"):
        validate_posts(posts + [JobPost("private", "ghost", 50, cr_to_cents(100))], registry)


def test_post_total_must_match_the_labour_market_config(posts, registry):
    with pytest.raises(GameError, match="与配置的岗位总量不符"):
        validate_posts(posts[:-1], registry)


def test_non_positive_wage_is_rejected(city, registry):
    posts = build_posts(city)
    posts[0].wage_cents = 0
    with pytest.raises(GameError, match="数量或工资非正"):
        validate_posts(posts, registry)


def test_unemployment_rate_is_share_of_labour_force(registry, posts):
    assignment = fill_vacancies(posts, registry)
    expected = 150 / (1500 + 150)
    assert unemployment_rate(registry, assignment) == pytest.approx(expected)


def test_config_declares_the_fill_rule_and_wage_convergence():
    labour = LABOR_CONSUMPTION["labour_market"]
    assert labour["private_post_total"] + labour["public_post_total"] == 1500
    assert labour["unemployed_total"] == 150
    assert "不得" in labour["fill_rule"]
    assert 0 < labour["wage_convergence"]["monthly_limit"] <= labour["wage_convergence"]["target_rate"]