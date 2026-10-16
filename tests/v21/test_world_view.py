"""WorldView 脱敏测试。"""
import pytest

from backend.systems.v21.world_state import default_world
from backend.systems.v21.world_game import WorldGame
from backend.systems.v21.world_view import get_city_view, full_city_payload, VISIBILITY_LABELS


@pytest.fixture
def game():
    return WorldGame.default()


def test_visibility_level_0_only_basics(game):
    game = game.advance_npc_only(3)
    city = game.get_npc_city('agricultural_town')
    node = game.get_city_node('agricultural_town')
    rel = game.diplomacy.relation_with_player('agricultural_town')
    view = get_city_view(city, 0, rel)
    assert 'id' not in view or view.get('id') == ''
    assert view['month'] == 3
    # 千人量级
    assert 'population_g' in view
    # 字段屏蔽
    assert 'labor_force' not in view
    assert 'unemployment_rate' not in view
    assert 'employment' not in view


def test_visibility_level_2_includes_employment(game):
    game = game.advance_npc_only(3)
    city = game.get_npc_city('commercial_port')
    node = game.get_city_node('commercial_port')
    rel = game.diplomacy.relation_with_player('commercial_port')
    view = get_city_view(city, 2, rel)
    assert 'labor_force' in view
    assert 'employment' in view
    assert len(view['employment']) == 7


def test_visibility_level_4_includes_all_fields(game):
    game = game.advance_npc_only(6)
    # 提升友好度到 100
    game.diplomacy.adjust_friendliness('industrial_city', 'commercial_port', 100, '完全同盟')
    city = game.get_npc_city('commercial_port')
    node = game.get_city_node('commercial_port')
    rel = game.diplomacy.relation_with_player('commercial_port')
    view = get_city_view(city, 4, rel)
    assert view['stockpile_food_units'] is not None or 'stockpile_food_units' in view
    assert view['monthly_trade_volume_cents'] is not None or 'monthly_trade_volume_cents' in view


def test_full_city_payload_includes_metadata(game):
    game = game.advance_npc_only(2)
    node = game.get_city_node('magic_academy')
    city = game.get_npc_city('magic_academy')
    rel = game.diplomacy.relation_with_player('magic_academy')
    payload = full_city_payload(node, city, game.diplomacy.get_visibility('magic_academy'), rel)
    assert payload['id'] == 'magic_academy'
    assert payload['name_cn'] == '辉月学城'
    assert payload['archetype'] == 'magic_academy'
    assert 'visibility_label' in payload


def test_full_city_payload_unknown_city(game):
    payload = full_city_payload(None, None, 0, None)
    assert 'error' in payload


def test_visibility_labels_all_levels():
    assert len(VISIBILITY_LABELS) == 5
    for i in range(5):
        assert i in VISIBILITY_LABELS