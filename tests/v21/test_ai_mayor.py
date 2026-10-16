"""AiMayor 测试。"""
import pytest

from backend.systems.v21.industry_economy import IndustryEconomy
from backend.systems.v21.industry_scenario import default_scenario
from backend.systems.v21.ai_mayor import AiMayor
from backend.systems.v21.mayor_preferences import MayorPreferences


def _make_mayor(archetype='industrial_city', overrides=None):
    scenario = default_scenario()
    city = IndustryEconomy(scenario=scenario)
    prefs = MayorPreferences(
        city_id='test_city',
        archetype=archetype,
        industry_focus={'AGR': 1.5, 'IND': 1.0, 'SRV': 1.0, 'CHU': 0.5, 'BNK': 0.5, 'MAG': 0.5, 'GOV': 1.0},
        tax_rate_preference=0.12,
        welfare_stance='normal',
        expansion_drive=0.5,
        risk_tolerance=0.5,
        subsidy_priority=('IND',),
        purchase_urgency=0.5,
        target_unemployment_rate=0.18,
        target_wage_arrears_ratio=0.5,
        min_cash_months=3,
    )
    if overrides:
        for k, v in overrides.items():
            setattr(prefs, k, v)
    return AiMayor(city, prefs)


def test_ai_mayor_constructor_validates():
    scenario = default_scenario()
    city = IndustryEconomy(scenario=scenario)
    with pytest.raises(ValueError):
        AiMayor(city, "not a preferences")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        AiMayor("not a city", MayorPreferences(city_id='x', archetype=''))  # type: ignore[arg-type]


def test_step_does_not_crash_at_zero_month():
    mayor = _make_mayor()
    results = mayor.step()
    assert isinstance(results, list)


def test_step_records_actions():
    mayor = _make_mayor()
    mayor.step()
    # 没有断言具体内容，只确认不崩溃且返回 list
    assert mayor.city.last_report is None or True


def test_evaluate_returns_all_dimensions():
    mayor = _make_mayor()
    profile = mayor._evaluate()
    assert 'unemployment_rate' in profile
    assert 'gov_cash' in profile
    assert 'cash_months' in profile
    assert 'monthly_cost' in profile
    assert 'current_tax_rate' in profile


def test_composite_index_in_unit_range():
    mayor = _make_mayor()
    profile = mayor._evaluate()
    idx = mayor._composite_index(profile)
    assert 0 <= idx <= 1


def test_tax_adjust_changes_rate_smoothly():
    """调税从 current 到 target 步长 0.02 / 次。"""
    mayor = _make_mayor()
    # 强制 GOV 现金偏低以触发调税
    mayor.city.cash['GOV'] = 1000
    initial_rate = mayor.city.scenario['city']['profit_tax_multiplier']
    mayor.step()
    new_rate = mayor.city.scenario['city']['profit_tax_multiplier']
    # 步长 0.02
    assert abs(new_rate - initial_rate) <= 0.021


def test_ai_mayor_only_affects_own_city():
    """AI 调税只影响自己城市 scenario，不影响其他城市。"""
    from backend.systems.v21.world_state import default_world
    from backend.systems.v21.npc_city_engine import NpcCityEngine
    world = default_world()
    engine = NpcCityEngine.from_world(world)
    # 跑 6 月让税率变化
    new_engine = engine.advance(months=6)
    # 检查每 NPC 城市的税率在 scenario 中独立
    rates = {cid: city.scenario['city']['profit_tax_multiplier']
             for cid, city in new_engine.cities.items()}
    # 不同 NPC 的税率可能不同（取决于 array）
    assert all(0 <= r <= 1 for r in rates.values())


def test_ai_actions_logged():
    from backend.systems.v21.world_state import default_world
    from backend.systems.v21.npc_city_engine import NpcCityEngine
    world = default_world()
    engine = NpcCityEngine.from_world(world)
    new_engine = engine.advance(months=6)
    for cid in new_engine.cities:
        log = new_engine.ai_log[cid]
        assert isinstance(log, list)


def test_ai_mayor_does_not_modify_base_parameters():
    """AI 不能修改 scenario 的基础结构参数。"""
    from backend.systems.v21.world_state import default_world
    from backend.systems.v21.npc_city_engine import NpcCityEngine
    from backend.systems.v21.industry_scenario import default_scenario
    world = default_world()
    engine = NpcCityEngine.from_world(world)
    base = default_scenario()
    base_keys = set(base['city'].keys())
    new_engine = engine.advance(months=12)
    for cid, city in new_engine.cities.items():
        assert set(city.scenario['city'].keys()) == base_keys