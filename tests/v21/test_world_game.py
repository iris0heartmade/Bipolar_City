"""WorldGame 测试。"""
import pytest

from backend.systems.v21.world_state import default_world
from backend.systems.v21.world_game import WorldGame
from backend.systems.v21.npc_city_engine import NpcCityEngine
from backend.systems.v21.diplomacy import DiplomacySystem


def test_default_world_game_has_5_cities():
    game = WorldGame.default()
    cities = game.world.cities
    assert len(cities) == 5


def test_default_world_game_has_npc_engine():
    game = WorldGame.default()
    assert len(game.npc_city.cities) == 4


def test_default_world_game_has_diplomacy():
    game = WorldGame.default()
    assert len(game.diplomacy.relations) == 10  # C(5,2)


def test_get_player_city_id():
    game = WorldGame.default()
    assert game.get_player_city_id() == 'industrial_city'


def test_advance_npc_only_increments_world():
    game = WorldGame.default()
    new = game.advance_npc_only(6)
    assert new.world.month == 6


def test_advance_npc_only_advances_npc_cities():
    game = WorldGame.default()
    new = game.advance_npc_only(6)
    for cid, city in new.npc_city.cities.items():
        assert city.month == 6


def test_advance_npc_only_rejects_invalid():
    game = WorldGame.default()
    with pytest.raises(ValueError):
        game.advance_npc_only(0)
    with pytest.raises(ValueError):
        game.advance_npc_only(121)


def test_get_npc_city_returns_correct_instance():
    game = WorldGame.default()
    city = game.get_npc_city('agricultural_town')
    assert city is not None


def test_get_city_node_returns_correct_node():
    game = WorldGame.default()
    node = game.get_city_node('commercial_port')
    assert node is not None
    assert node.name_cn == '碧涛港'


def test_advance_with_player_advances_both():
    from backend.systems.v21.industry_economy import IndustryEconomy
    game = WorldGame.default()
    player = IndustryEconomy()
    new_game, new_player = game.advance_with_player(player, months=6)
    assert new_game.world.month == 6
    assert new_player.month == 6


def test_get_world_game_returns_singleton():
    from backend.systems.v21.world_game import (
        get_world_game, reset_world_game_for_tests, set_world_game,
    )
    reset_world_game_for_tests()
    game1 = get_world_game()
    game2 = get_world_game()
    assert game1 is game2
    # 替换
    new_game = WorldGame.default(seed=42)
    set_world_game(new_game)
    assert get_world_game() is new_game
    reset_world_game_for_tests()