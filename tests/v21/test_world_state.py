"""WorldState 测试：世界级参数与节点。"""
import pytest

from backend.systems.v21.world_state import WorldState, CityNode, default_world


def test_default_world_has_five_cities():
    world = default_world()
    assert len(world.cities) == 5


def test_default_world_has_player_city():
    world = default_world()
    player = [c for c in world.cities if c.is_player]
    assert len(player) == 1
    assert player[0].id == 'industrial_city'


def test_city_node_coordinates_in_range():
    world = default_world()
    for c in world.cities:
        assert 0 <= c.x <= 100
        assert 0 <= c.y <= 100


def test_get_city_returns_correct_node():
    world = default_world()
    c = world.get_city('agricultural_town')
    assert c is not None
    assert c.name_cn == '禾谷镇'
    assert c.archetype == 'agricultural_town'


def test_get_city_returns_none_for_unknown():
    world = default_world()
    assert world.get_city('unknown_city') is None


def test_player_city_returns_industrial_city():
    world = default_world()
    p = world.player_city()
    assert p.id == 'industrial_city'


def test_distance_matrix_lookup():
    world = default_world()
    d = world.distance('agricultural_town', 'industrial_city')
    assert d == 240
    # 排序不影响
    d2 = world.distance('industrial_city', 'agricultural_town')
    assert d2 == 240


def test_distance_returns_zero_when_unknown_pair():
    world = default_world()
    assert world.distance('unknown_a', 'unknown_b') == 0


def test_world_state_is_frozen():
    world = default_world()
    with pytest.raises(Exception):
        world.month = 99  # type: ignore[misc]


def test_world_state_stores_world_level_params():
    world = default_world()
    assert world.inflation_pi == 0.0
    assert world.reference_consumption_cr == 50.0
    assert world.month == 0
    assert world.year == 1