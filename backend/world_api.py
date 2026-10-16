"""世界地图 REST 接口（独立 /api/world/*）。

WorldGame 与玩家城市 IndustryGame 各自独立；
前端从 /api/world/state 读 NPC 城市节点 + 关系 + 月报脱敏视图。
"""
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from backend.systems.v21.world_game import WorldGame, get_world_game, reset_world_game_for_tests
from backend.systems.v21.world_view import full_city_payload


router = APIRouter(prefix='/api/world')


def _build_world_payload(game: WorldGame) -> dict:
    nodes = []
    for city_node in game.world.cities:
        nodes.append({
            'id': city_node.id,
            'name_cn': city_node.name_cn,
            'archetype': city_node.archetype,
            'x': city_node.x,
            'y': city_node.y,
            'is_player': city_node.is_player,
        })
    links = []
    for r in game.diplomacy.relations.values():
        links.append({
            'from': r.city_a,
            'to': r.city_b,
            'friendliness': r.friendliness,
            'last_event': r.last_event,
        })
    return {
        'world': {
            'seed': game.world.seed,
            'month': game.world.month,
            'year': game.world.year,
            'inflation_pi': game.world.inflation_pi,
            'reference_consumption_cr': game.world.reference_consumption_cr,
        },
        'cities': nodes,
        'links': links,
    }


@router.get('/state')
async def state():
    return _build_world_payload(get_world_game())


@router.get('/cities/{city_id}')
async def city_detail(city_id: str):
    game = get_world_game()
    node = game.get_city_node(city_id)
    if node is None:
        raise HTTPException(status_code=404, detail=f'未知城市：{city_id}')
    if node.is_player:
        city = game.player_city_proxy() if hasattr(game, 'player_city_proxy') else None
        if city is None:
            raise HTTPException(status_code=409, detail='玩家城市由 IndustryGame 管理，请访问 /api/industry/state')
    else:
        city = game.get_npc_city(city_id)
    rel = game.diplomacy.relation_with_player(city_id)
    visibility = game.diplomacy.get_visibility(city_id)
    payload = full_city_payload(node, city, visibility, rel)
    ai_log = game.npc_city.recent_ai_log(city_id, limit=12) if city_id in game.npc_city.ai_log else []
    payload['ai_recent_actions'] = ai_log
    return payload


class AdvanceCommand(BaseModel):
    model_config = ConfigDict(extra='forbid')
    months: StrictInt = Field(default=1, ge=1, le=120)


@router.post('/advance')
async def advance(command: AdvanceCommand, request: Request):
    game = get_world_game()
    new_game = game.advance_npc_only(command.months)
    set_world_game_for_request(new_game)
    return _build_world_payload(new_game)


def set_world_game_for_request(game: WorldGame):
    """测试时使用；运行时通过 app.state 持有。"""
    from backend.systems.v21.world_game import set_world_game
    set_world_game(game)


def reset_world_for_tests():
    reset_world_game_for_tests()