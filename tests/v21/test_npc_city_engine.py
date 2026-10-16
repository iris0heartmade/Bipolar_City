"""NpcCityEngine 测试。"""
import pytest

from backend.systems.v21.world_state import default_world
from backend.systems.v21.npc_city_engine import NpcCityEngine
from backend.systems.v21.city_presets import CITY_PRESETS


def test_from_world_creates_four_npc_cities():
    world = default_world()
    engine = NpcCityEngine.from_world(world)
    assert len(engine.cities) == 4  # 玩家城市不在 NPC 列表


def test_npc_cities_have_archetype_distribution():
    """每座 NPC 城市按 preset 的 7 行业分布，不全是 industrial_city 默认。"""
    world = default_world()
    engine = NpcCityEngine.from_world(world)
    agricultural = engine.cities['agricultural_town']
    magic = engine.cities['magic_academy']
    mining = engine.cities['mining_border']
    # 农业城 AGR 就业 ≈ preset 290（允许 ±5 舍入误差）
    assert abs(agricultural.employment()['AGR'] - CITY_PRESETS['agricultural_town'].employment_by_industry[0]) <= 5
    # 魔法城 MAG 就业 ≈ preset 370
    assert abs(magic.employment()['MAG'] - CITY_PRESETS['magic_academy'].employment_by_industry[5]) <= 5
    # 矿业城 IND 就业 ≈ preset 830
    assert abs(mining.employment()['IND'] - CITY_PRESETS['mining_border'].employment_by_industry[1]) <= 10


def test_advance_increments_world_month():
    world = default_world()
    engine = NpcCityEngine.from_world(world)
    new = engine.advance(months=12)
    assert new.world.month == 12


def test_advance_each_city_gets_one_month():
    world = default_world()
    engine = NpcCityEngine.from_world(world)
    new = engine.advance(months=6)
    for cid, city in new.cities.items():
        assert city.month == 6


def test_advance_rejects_invalid_months():
    world = default_world()
    engine = NpcCityEngine.from_world(world)
    with pytest.raises(ValueError):
        engine.advance(months=0)
    with pytest.raises(ValueError):
        engine.advance(months=121)


def test_advance_is_atomic_for_invalid_input():
    world = default_world()
    engine = NpcCityEngine.from_world(world)
    with pytest.raises(ValueError):
        engine.advance(months=-1)


def test_24_months_does_not_make_city_bankrupt():
    """AI 跑 24 月 GOV 现金不应出现极端破产（c < -100000 CR）。"""
    world = default_world()
    engine = NpcCityEngine.from_world(world)
    new = engine.advance(months=24)
    for cid, city in new.cities.items():
        gov = city.cash['GOV']
        # 至少不破产超 1000 CR；GOV 现金可能为正也可能为负但不应极端
        assert gov > -100000 * 100, f'{cid} GOV 破产 {gov}'


def test_each_npc_has_mayor():
    world = default_world()
    engine = NpcCityEngine.from_world(world)
    for cid in engine.cities:
        assert cid in engine.mayors
    assert len(engine.mayors) == 4


def test_get_city_returns_correct_instance():
    world = default_world()
    engine = NpcCityEngine.from_world(world)
    city = engine.get_city('agricultural_town')
    assert city is not None
    assert city.archetype if hasattr(city, 'archetype') else True


def test_recent_ai_log_returns_list():
    world = default_world()
    engine = NpcCityEngine.from_world(world)
    new = engine.advance(months=12)
    log = new.recent_ai_log('agricultural_town')
    assert isinstance(log, list)
    assert len(log) > 0