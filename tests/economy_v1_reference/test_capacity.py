"""产能引擎验收：设计稿 §4.1 的 Qcap 公式与 §4.2 的基准反推。

这一组测试回答四个问题：
1. 反推出的基准产能能否精确重现原始营业额（P02 基准不漂移）；
2. 每个因子的方向与区间是否符合设计稿原文；
3. 因子之间是否真的相乘（缺一台发动机就红）；
4. 停产、缺料、缺人这些极端情况会不会算出荒谬的负产能。
"""
from fractions import Fraction

import pytest

from backend.config import REFERENCE_CITY
from backend.core.errors import GameError
from backend.model.economy import cr_to_cents
from backend.systems.capacity import (CapacityFactors, baseline_capacity,
                                      labour_factor, resource_factor, safety_factor,
                                      sector_capacity, skill_factor, uniform_factors,
                                      worktime_factor, with_factors)
from backend.systems.live_economy import REFERENCE_POPULATION

SECTORS = REFERENCE_CITY["firms"]["sectors"]
BASE_REVENUE = sum(cr_to_cents(s["revenue"]) for s in SECTORS)


# --- 基准产能反推 -------------------------------------------------------

def test_baseline_capacity_reproduces_original_revenue_exactly():
    """一切因子为 1 时，营业额必须精确回到原始值。

    这是引入产能层后 P02 十四项基准不漂移的根本保证。
    原始基准是 340000 **CR**，账本口径是分，所以此处比对的是 34,000,000 分。
    """
    neutral = CapacityFactors()
    assert neutral.product == 1
    total = sum(sector_capacity(s["id"], cr_to_cents(s["revenue"]), neutral).revenue_cents
                for s in SECTORS)
    assert total == BASE_REVENUE == cr_to_cents(340000)


def test_baseline_capacity_is_revenue_over_eighty_percent():
    """§4.2 的反推公式：K = 营业额 / 0.80。"""
    assert baseline_capacity(1000) == 1250
    with pytest.raises(GameError):
        baseline_capacity(0)
    with pytest.raises(GameError):
        baseline_capacity(-100)


def test_neutral_factors_do_not_change_any_sector():
    neutral = CapacityFactors()
    for sector in SECTORS:
        before = cr_to_cents(sector["revenue"])
        after = sector_capacity(sector["id"], before, neutral).revenue_cents
        assert after == before


# --- F_labor ------------------------------------------------------------

def test_labour_factor_is_full_when_workers_meet_requirement():
    assert labour_factor(240, 240) == 1


def test_labour_factor_follows_point_eight_exponent():
    """招一半人时，产能应是 0.5^0.80，不是 0.5。"""
    expected = Fraction(1, 2) ** Fraction(80, 100)
    assert labour_factor(120, 240) == pytest.approx(float(expected))
    assert labour_factor(120, 240) > Fraction(1, 2), "招一半人产能应高于一半"


def test_labour_factor_never_exceeds_one():
    assert labour_factor(500, 240) == 1


def test_zero_workers_gives_zero_output():
    assert labour_factor(0, 240) == 0


def test_zero_requirement_does_not_divide_by_zero():
    assert labour_factor(0, 0) == 1


# --- F_skill ------------------------------------------------------------

def test_skill_factor_has_a_floor_of_sixty_five_percent():
    """设计稿明确下限 0.65：技能再差也只打六五折，不归零。"""
    assert skill_factor(0, 100) == Fraction(65, 100)


def test_skill_factor_is_full_when_skill_meets_requirement():
    assert skill_factor(100, 100) == 1


def test_skill_factor_follows_point_three_exponent():
    expected = Fraction(1, 2) ** Fraction(30, 100)
    assert skill_factor(50, 100) == pytest.approx(float(expected))
    assert skill_factor(50, 100) > Fraction(1, 2)


def test_overskilled_workers_do_not_boost_output():
    assert skill_factor(300, 100) == 1


# --- F_resource ---------------------------------------------------------

def test_resource_factor_scales_with_available_stock():
    assert resource_factor(100, 100) == 1
    assert resource_factor(50, 100) == Fraction(1, 2)
    assert resource_factor(200, 100) == 1


def test_resource_shortage_stops_output_instead_of_going_negative():
    """煤断供时产线停产，但绝不允许出现负产能。"""
    assert resource_factor(0, 100) == 0
    with pytest.raises(GameError):
        resource_factor(-50, 100)


def test_no_resource_requirement_is_unconstrained():
    assert resource_factor(0, 0) == 1


# --- F_safety -----------------------------------------------------------

def test_safety_factor_is_full_in_a_safe_city():
    assert safety_factor(Fraction(0), Fraction(1)) == 1


def test_safety_factor_has_a_floor_of_sixty_five_percent():
    assert safety_factor(Fraction(10), Fraction(1)) == Fraction(65, 100)


def test_more_mages_cannot_create_infinite_profit():
    """安全城市堆魔法师不会无限赚钱——袭击为 0 时本因子封顶 1。"""
    safe = safety_factor(Fraction(0), Fraction(1))
    super_safe = safety_factor(Fraction(0), Fraction(0))
    assert safe == super_safe == 1


# --- F_worktime ---------------------------------------------------------

def test_worktime_is_neutral_at_the_reference_faith():
    """信仰比例等于基准 0.55 时必须恰好为 1，否则 P02 基准会整体下移。"""
    assert worktime_factor(Fraction(55, 100)) == 1


def test_higher_faith_shortens_worktime():
    assert worktime_factor(Fraction(80, 100)) < 1


def test_lower_faith_lengthens_worktime():
    assert worktime_factor(Fraction(30, 100)) > 1


# --- 因子相乘 -----------------------------------------------------------

def test_factors_actually_multiply():
    """缺一台发动机就红：产能必须是六个因子的乘积，不是取最小值。"""
    neutral = CapacityFactors()
    combined = with_factors(neutral, labour=Fraction(1, 2), skill=Fraction(1, 2))
    assert combined.product == Fraction(1, 4)

    reference = 1000
    capacity = sector_capacity("agriculture", reference, combined)
    assert capacity.revenue_cents == round(reference / 4)


def test_one_dead_factor_drags_the_whole_sector_down():
    """资源断供时，即便人手充足、技能再高，产能也是 0。"""
    factors = CapacityFactors(resource=Fraction(0))
    assert factors.product == 0
    assert sector_capacity("mining", 5000, factors).revenue_cents == 0


def test_revenue_never_goes_negative():
    factors = with_factors(CapacityFactors(), labour=Fraction(0))
    for sector in SECTORS:
        assert sector_capacity(sector["id"], cr_to_cents(sector["revenue"]), factors).revenue_cents == 0


# --- 聚合与工具 ---------------------------------------------------------

def test_uniform_factors_covers_every_sector():
    factors = uniform_factors(6, CapacityFactors())
    assert len(factors) == 6 and all(f.product == 1 for f in factors)


def test_with_factors_rejects_unknown_names():
    with pytest.raises(GameError, match="未知"):
        with_factors(CapacityFactors(), nonexistent=1)


def test_factor_snapshot_is_human_readable():
    """月报要给人看，小数点后四位足够诊断，不能只吐裸分数。"""
    snapshot = with_factors(CapacityFactors(), labour=Fraction(1, 2)).to_dict()
    assert set(snapshot) == {"labour", "skill", "resource", "service", "safety", "worktime"}
    assert snapshot["labour"] == "0.5000"


def test_capacity_tracks_city_scale():
    """产能层必须跟着城市规模走：小城的产出按同比例缩小，满负荷口径不变。"""
    neutral = CapacityFactors()
    scaled = cr_to_cents(REFERENCE_CITY["firms"]["sectors"][0]["revenue"]
                         * 168 / REFERENCE_POPULATION)
    capacity = sector_capacity("agriculture", scaled, neutral)
    assert capacity.revenue_cents == scaled, "满负荷时精确回到该规模的参考营业额"
    assert capacity.baseline_units == baseline_capacity(scaled)


def test_half_capacity_halves_output():
    """产能打五折，营业额就必须是原来的一半——这是整台发动机的意义。"""
    full = sector_capacity("agriculture", 1000, CapacityFactors())
    half = sector_capacity("agriculture", 1000,
                           with_factors(CapacityFactors(), labour=Fraction(1, 2)))
    assert half.revenue_cents == pytest.approx(full.revenue_cents / 2, abs=1)
    assert half.baseline_units == full.baseline_units, "基准产能不随利用率变"