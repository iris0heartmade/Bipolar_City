"""世界地图 REST 接口（独立 /api/world/*）。

WorldGame 与玩家城市 IndustryGame 各自独立；
前端从 /api/world/state 读 NPC 城市节点 + 关系 + 月报脱敏视图。
"""
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator

from backend.systems.v21.world_game import WorldGame, get_world_game, reset_world_game_for_tests
from backend.systems.v21.world_view import full_city_payload
from backend.systems.v21.diplomacy_action import DiplomacyAction


router = APIRouter(prefix='/api/world')


def _build_world_payload(game: WorldGame, industry_game=None) -> dict:
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
    payload = {
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
    # M24：暴露玩家下台状态，让前端可据此灰化玩家城节点
    if industry_game is not None:
        trigger = getattr(industry_game.city, '_trigger', None)
        payload['player_game_over'] = {
            'is_game_over': trigger.is_game_over if trigger else False,
            'reasons': trigger.reasons if trigger else [],
        }
    else:
        payload['player_game_over'] = {'is_game_over': False, 'reasons': []}
    return payload


@router.get('/state')
async def state(request: Request):
    industry_game = getattr(request.app.state, 'industry_game', None) if hasattr(request, 'app') else None
    return _build_world_payload(get_world_game(), industry_game=industry_game)


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
    # M24：玩家下台 → 世界冻结，NPC AI 也停摆
    industry_game = getattr(request.app.state, 'industry_game', None)
    if industry_game is not None:
        trigger = getattr(industry_game.city, '_trigger', None)
        if trigger and trigger.is_game_over:
            raise HTTPException(status_code=409, detail={
                'error': 'game_over',
                'reasons': trigger.reasons,
                'message': '市长已下台，世界地图已冻结。',
            })
    game = get_world_game()
    new_game = game.advance_npc_only(command.months)
    set_world_game_for_request(new_game)
    return _build_world_payload(new_game, industry_game=industry_game)


class DiplomacyActionCommand(BaseModel):
    """M26 玩家外交行动。"""
    model_config = ConfigDict(extra='forbid')
    action_key: str = Field(..., max_length=40)
    target_city_id: str = Field(..., max_length=40)
    amount_cr: float = Field(default=0.0, ge=0, le=1_000_000)
    note: str = Field(default='', max_length=200)

    @field_validator('action_key')
    @classmethod
    def known_action(cls, v):
        from backend.systems.v21.diplomacy_action import load_diplomacy_actions
        if v not in load_diplomacy_actions():
            raise ValueError(f'未知外交行动：{v}')
        return v


@router.post('/diplomacy_action')
async def diplomacy_action(command: DiplomacyActionCommand, request: Request):
    """M26 玩家外交行动入口。

    玩家城市 IndustryGame 持 cash['GOV']；aid 是叙事动作不扣款。
    trade_agreement / tariff / immigration_open / statement 也不扣款（M26 阶段）。
    """
    industry_game = getattr(request.app.state, 'industry_game', None)
    if industry_game is not None:
        trigger = getattr(industry_game.city, '_trigger', None)
        if trigger and trigger.is_game_over:
            raise HTTPException(status_code=409, detail={
                'error': 'game_over',
                'message': '市长已下台，无法发起外交行动。',
            })
    game = get_world_game()
    # 校验目标城市存在
    target_node = game.get_city_node(command.target_city_id)
    if target_node is None:
        raise HTTPException(status_code=404, detail=f'未知城市：{command.target_city_id}')
    if target_node.is_player:
        raise HTTPException(status_code=400, detail='不能对自己发起外交行动')
    current_month = game.world.month
    player_cash_cr = (industry_game.city.cash['GOV'] / 100) if industry_game else 0.0
    action = DiplomacyAction(
        action_key=command.action_key,
        target_city_id=command.target_city_id,
        month=current_month,
        cost_cr=command.amount_cr,
        friendliness_delta=0,
        note=command.note,
    )
    ok, reason, record = game.diplomacy.apply_player_action(
        action, current_month=current_month, player_cash_cr=player_cash_cr)
    # 把更新后的 world_game 写回（diplomacy 是 mutable 对象所以无需 replace，但保险起见 set 一次）
    set_world_game_for_request(game)
    return {
        'success': ok,
        'reason': reason,
        'record': record.to_dict(),
        'relation': {
            'friendliness': game.diplomacy.relation_with_player(command.target_city_id).friendliness,
            'visibility': game.diplomacy.get_visibility(command.target_city_id),
        },
    }


@router.get('/diplomacy_actions/{city_id}')
async def diplomacy_actions(city_id: str):
    """返回该城市最近 50 条玩家行动（按时间倒序）。"""
    game = get_world_game()
    history = game.diplomacy.player_actions_history(limit=50)
    city_actions = [a.to_dict() for a in history if a.target_city_id == city_id]
    return city_actions


def set_world_game_for_request(game: WorldGame):
    """测试时使用；运行时通过 app.state 持有。"""
    from backend.systems.v21.world_game import set_world_game
    set_world_game(game)


def reset_world_for_tests():
    reset_world_game_for_tests()