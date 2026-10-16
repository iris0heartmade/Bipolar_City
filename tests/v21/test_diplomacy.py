"""DiplomacySystem 测试。"""
import pytest

from backend.systems.v21.world_state import default_world
from backend.systems.v21.diplomacy import DiplomacySystem, Relation


def test_diplomacy_initializes_with_all_pairs():
    world = default_world()
    dip = DiplomacySystem(world)
    n = len(world.cities)
    expected_pairs = n * (n - 1) // 2
    assert len(dip.relations) == expected_pairs


def test_relation_with_player_returns_existing():
    world = default_world()
    dip = DiplomacySystem(world)
    rel = dip.relation_with_player('agricultural_town')
    # pair_key 字典序：agricultural_town < industrial_city
    assert rel.city_a == 'agricultural_town'
    assert rel.city_b == 'industrial_city'
    assert rel.friendliness == 50


def test_adjust_friendliness_clamps_to_zero():
    world = default_world()
    dip = DiplomacySystem(world)
    dip.adjust_friendliness('industrial_city', 'agricultural_town', -100, '冲突')
    rel = dip.relation_with_player('agricultural_town')
    assert rel.friendliness == 0


def test_adjust_friendliness_clamps_to_hundred():
    world = default_world()
    dip = DiplomacySystem(world)
    dip.adjust_friendliness('industrial_city', 'agricultural_town', 200, '铁盟')
    rel = dip.relation_with_player('agricultural_town')
    assert rel.friendliness == 100


def test_adjust_friendliness_records_reason():
    world = default_world()
    dip = DiplomacySystem(world)
    dip.adjust_friendliness('industrial_city', 'agricultural_town', 10, '建交使团')
    rel = dip.relation_with_player('agricultural_town')
    assert '建交' in rel.last_event


def test_adjust_rejects_non_integer_delta():
    world = default_world()
    dip = DiplomacySystem(world)
    with pytest.raises(ValueError):
        dip.adjust_friendliness('industrial_city', 'agricultural_town', 1.5, '半份')  # type: ignore[arg-type]


def test_adjust_rejects_unknown_city():
    world = default_world()
    dip = DiplomacySystem(world)
    with pytest.raises(ValueError):
        dip.adjust_friendliness('industrial_city', 'unknown', 5, '尝试')


def test_get_visibility_levels():
    world = default_world()
    dip = DiplomacySystem(world)
    # 0：陌生人
    dip.adjust_friendliness('industrial_city', 'mining_border', -100, '敌对')
    assert dip.get_visibility('mining_border') == 0
    # 1：认识
    dip.adjust_friendliness('industrial_city', 'agricultural_town', -25, '一般')
    assert dip.get_visibility('agricultural_town') == 1
    # 2：友好
    dip.adjust_friendliness('industrial_city', 'commercial_port', 5, '友好')
    assert dip.get_visibility('commercial_port') == 2
    # 3：盟友
    dip.adjust_friendliness('industrial_city', 'magic_academy', 30, '盟友')  # 50+30=80 = level 3
    assert dip.get_visibility('magic_academy') == 3
    # 4：完全开放
    dip.adjust_friendliness('industrial_city', 'magic_academy', 100, '完全同盟')  # 80+100=100 = level 4
    assert dip.get_visibility('magic_academy') == 4


def test_relations_snapshot_returns_list():
    world = default_world()
    dip = DiplomacySystem(world)
    snap = dip.relations_snapshot()
    assert isinstance(snap, list)
    assert all('city_a' in r and 'friendliness' in r for r in snap)


def test_relations_for_city_filters():
    world = default_world()
    dip = DiplomacySystem(world)
    rels = dip.relations_for_city('industrial_city')
    assert all('industrial_city' in (r.city_a, r.city_b) for r in rels)
    assert len(rels) == len(world.cities) - 1  # 玩家城市与所有其他城市的关系


def test_trade_volume_positive():
    world = default_world()
    dip = DiplomacySystem(world)
    vol = dip.trade_volume('industrial_city', 'agricultural_town')
    assert vol > 0


def test_diplomacy_with_custom_initial_friendliness():
    world = default_world()
    dip = DiplomacySystem(world, initial_friendliness=70)
    rel = dip.relation_with_player('agricultural_town')
    assert rel.friendliness == 70


def test_diplomacy_invalid_initial_friendliness():
    world = default_world()
    with pytest.raises(ValueError):
        DiplomacySystem(world, initial_friendliness=200)
    with pytest.raises(ValueError):
        DiplomacySystem(world, initial_friendliness=-1)