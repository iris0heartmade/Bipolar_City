"""发展层验收：产能与企业决策是否真的接上了可玩世界。

这一组测试回答三个问题：
1. 产能因子能不能真的改变对外收入（发动机不是摆设）；
2. 满负荷时是否与接入前逐分一致（不破坏已有基准）；
3. 企业扩张与退出能不能改变行业规模（城市真的会动）。
"""
from copy import deepcopy
from fractions import Fraction

import pytest

from backend.config import INPUT_OUTPUT, REFERENCE_CITY
from backend.core.errors import GameError
from backend.systems.capacity import CapacityFactors, with_factors
from backend.systems.development import (apply_capacity_to_input_output,
                                         capacity_factors, firm_history_from_config,
                                         rebuild_config_for_targets,
                                         sector_employee_targets)
from backend.systems.firm_decisions import FirmHistory
from backend.systems.generator import new_world
from backend.config import BALANCE

SOURCES = INPUT_OUTPUT["firm_revenue_sources"]
MONTH = BALANCE["days_per_month"]


# --- 产能确实在起作用 ---------------------------------------------------

def test_full_load_returns_the_table_untouched():
    """满负荷时不改动投入产出表——这是保护既有基准的前提。"""
    factors = CapacityFactors()
    assert apply_capacity_to_input_output(INPUT_OUTPUT, factors) is INPUT_OUTPUT


def test_halved_capacity_halves_exports():
    """产能打五折，出口必须跟着砍半。"""
    factors = with_factors(CapacityFactors(), labour=Fraction(1, 2))
    scaled = apply_capacity_to_input_output(INPUT_OUTPUT, factors)
    original = SOURCES["exports"]
    assert scaled["firm_revenue_sources"]["exports"] == round(original / 2)
    assert scaled["firm_revenue_sources"]["exports"] < original


def test_capacity_does_not_shrink_household_demand():
    """居民消费是付款方的需求，不是企业的产出能力，不能跟着产能缩水。

    产能不足时缺的那部分该由「卖不掉」承担，而不是让居民突然不买东西。
    """
    factors = with_factors(CapacityFactors(), labour=Fraction(1, 4))
    scaled = apply_capacity_to_input_output(INPUT_OUTPUT, factors)
    assert scaled["firm_revenue_sources"]["household_consumption"] \
        == SOURCES["household_consumption"]
    assert scaled["firm_revenue_sources"]["municipal_procurement"] \
        == SOURCES["municipal_procurement"]


def test_scaled_amounts_never_become_zero():
    """缩到 0 会让引擎以为「本月没有这笔义务」，比少收一点更糟。"""
    factors = with_factors(CapacityFactors(), labour=Fraction(1, 100))
    scaled = apply_capacity_to_input_output(INPUT_OUTPUT, factors)
    for key in ("exports", "private_capital", "tourist_spend"):
        assert scaled["firm_revenue_sources"][key] >= 1


def test_original_table_is_never_mutated():
    """缩放必须产生新对象。原地改会污染参考城的基准配置。"""
    snapshot = deepcopy(SOURCES)
    apply_capacity_to_input_output(INPUT_OUTPUT,
                                   with_factors(CapacityFactors(), labour=Fraction(1, 2)))
    assert SOURCES == snapshot


def test_stopped_industry_sells_nothing():
    """原料断供时出口归零，而不是变成负数。"""
    factors = with_factors(CapacityFactors(), resource=Fraction(0))
    scaled = apply_capacity_to_input_output(INPUT_OUTPUT, factors)
    assert scaled["firm_revenue_sources"]["exports"] == 1, "非零项保底 1 单位，不取负"


# --- 信仰影响工时 -------------------------------------------------------

def test_faith_share_moves_the_worktime_factor():
    """玩家能通过传教政策影响工时——这是设计稿 §6.1 坏2效应的落点。"""
    base = capacity_factors(faith_share=Fraction(55, 100))
    high = capacity_factors(faith_share=Fraction(90, 100))
    low = capacity_factors(faith_share=Fraction(20, 100))
    assert base.worktime == 1
    assert high.worktime < 1 < low.worktime


def test_worktime_factor_changes_export_revenue():
    """信仰比例变化必须一路传导到营业额，否则玩家调政策毫无意义。"""
    high = apply_capacity_to_input_output(
        INPUT_OUTPUT, capacity_factors(faith_share=Fraction(90, 100)))
    low = apply_capacity_to_input_output(
        INPUT_OUTPUT, capacity_factors(faith_share=Fraction(20, 100)))
    assert high["firm_revenue_sources"]["exports"] < low["firm_revenue_sources"]["exports"]


# --- 企业规模会变 -------------------------------------------------------

def test_sector_targets_follow_population_growth():
    """城市招人时，行业用工规模跟着长——这是「城市在发展」的第一体现。"""
    small = new_world(BALANCE["starting_seed"])
    big = new_world(BALANCE["starting_seed"])
    big.population = small.population * 3

    small_targets, _ = sector_employee_targets(small, {})
    big_targets, _ = sector_employee_targets(big, {})
    assert sum(big_targets.values()) > sum(small_targets.values())


def test_first_month_has_no_decision_because_there_is_no_history():
    """第一个月没有历史可看，一律维持现状，绝不凭空预测。"""
    world = new_world(BALANCE["starting_seed"])
    targets, notes = sector_employee_targets(world, {})
    assert notes == [], "没有历史时不应产生任何进退理由"
    assert all(v > 0 for v in targets.values())


def test_sustained_high_load_expands_a_sector():
    """连续满负荷三个月后，行业规模应当扩张。"""
    world = new_world(BALANCE["starting_seed"])
    history = firm_history_from_config(REFERENCE_CITY)
    for record in history.values():
        for _ in range(3):
            record.record(Fraction(95, 100), 5000)

    targets, notes = sector_employee_targets(world, history)
    assert notes, "持续满负荷必须给出扩张理由"
    assert sum(targets.values()) > 0


def test_cash_crisis_shrinks_the_city():
    """企业连续三个月付不出现金，规模就该缩小。"""
    world = new_world(BALANCE["starting_seed"])
    history = firm_history_from_config(REFERENCE_CITY)
    for record in history.values():
        for _ in range(3):
            record.record(Fraction(70, 100), 100, True)

    targets, notes = sector_employee_targets(world, history)
    assert notes
    assert any("退出" in note or "缩减" in note for note in notes), \
        "企业破产时必须给出可读的原因"


def test_rebuilding_config_keeps_unit_economics_intact():
    """扩张只让人数与总量同比例涨，人均工资与利润率必须不变。"""
    config = deepcopy(REFERENCE_CITY)
    before = config["firms"]["sectors"][0]
    targets = {s["id"]: s["employees"] * 2 for s in config["firms"]["sectors"]}
    rebuilt = rebuild_config_for_targets(config, targets)
    after = rebuilt["firms"]["sectors"][0]

    assert after["employees"] == before["employees"] * 2
    assert after["wage_per_employee"] == before["wage_per_employee"], "单价不得变动"
    assert after["revenue"] == before["revenue"] * 2


def test_rebuilding_config_never_moves_unit_prices():
    """全行业扫一遍，确认没有任何单价格式化被改动。"""
    config = deepcopy(REFERENCE_CITY)
    targets = {s["id"]: max(1, int(s["employees"] * 1.3)) for s in config["firms"]["sectors"]}
    rebuilt = rebuild_config_for_targets(config, targets)
    for before, after in zip(config["firms"]["sectors"], rebuilt["firms"]["sectors"]):
        assert after["wage_per_employee"] == before["wage_per_employee"]


def test_history_survives_rebuild():
    """跨月延续：重建配置不能把经营历史弄丢，否则判断永远从零开始。"""
    config = deepcopy(REFERENCE_CITY)
    history = firm_history_from_config(config)
    record = history["retail"]
    for _ in range(3):
        record.record(Fraction(95, 100), 5000)
    carried = firm_history_from_config(rebuild_config_for_targets(config, {}), history)
    assert carried["retail"].average_utilisation == Fraction(95, 100)


def test_unknown_sector_target_is_ignored():
    """配置里没有的行业不该让重建崩掉。"""
    config = deepcopy(REFERENCE_CITY)
    rebuilt = rebuild_config_for_targets(config, {"nonexistent": 5})
    assert rebuilt["firms"]["sectors"][0]["employees"] == config["firms"]["sectors"][0]["employees"]


# --- 与接入前口径一致（最重要的一条）------------------------------------

def test_capacity_layer_off_is_identical_to_pre_capacity():
    """关掉产能层后，月结必须与接入前逐分相同。

    接入期踩过的坑：投入产出表在「按人口比缩放」之上又叠了一层「按人数缩放」，
    两套口径打架，账目差出一个数。这条测试专门盯住它。
    """
    from backend.core.simulation import advance_days

    def month_report(flag):
        world = new_world(BALANCE["starting_seed"])
        world.paused = False
        world.development_enabled = flag
        advance_days(world, MONTH, manual=True)
        return world.economy["report"]

    off, on = month_report(False), month_report(True)
    assert on["revenue_cents"] == off["revenue_cents"], "满负荷时产能层不得改变营业额"
    assert on["profit_before_tax_cents"] == off["profit_before_tax_cents"]
    assert on["municipal_surplus_cents"] == off["municipal_surplus_cents"]
    assert on["gva_production_cents"] == off["gva_production_cents"]


def test_switch_is_the_only_difference_between_two_worlds():
    """两座完全相同的世界，只差一个开关，首月账目必须一致。"""
    from backend.core.simulation import advance_days

    def balances(flag):
        world = new_world(BALANCE["starting_seed"])
        world.paused = False
        world.development_enabled = flag
        advance_days(world, MONTH, manual=True)
        return {a["account_id"]: a["cash_cents"] for a in world.economy["accounts"]}

    assert balances(False) == balances(True), "满负荷时开关不应改变任何账户余额"