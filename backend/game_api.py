"""等距地图与行业经济共用的原子命令入口；建筑配置、付款及月结能力真实持久化。"""
from __future__ import annotations

import json as _stdlib_json
import logging
import random
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import Field, StrictInt

from backend.config import CATALOG, ROOT, SAVE_DIR, TEMPLATE, read_json
from backend.industry_api import IndustryGame, Command as IndustryCommand

logger = logging.getLogger(__name__)

router = APIRouter(prefix='/api/game')

from backend.systems.v21.map_geometry import _generate_terrain, validate_placement as _validate_geometry
from backend.systems.v21.map_economy import BUILDINGS as _BUILDING_INDUSTRY_MAP, housing_capacity, change_capacity



def _initial_buildings(template: dict, terrain: list[list[str]], seed: int = 20251008) -> list[dict]:
    """生成 v1 模板里的 initial_houses 起始建筑列表。"""
    width, height = template['width'], template['height']
    initial_houses = template.get('initial_houses', 0)
    candidates = list(template.get('house_candidates', []))
    random.Random(seed).shuffle(candidates)
    buildings = []
    for i, (x, y) in enumerate(candidates[:initial_houses]):
        # 校验地块：必须是 grass 且不与已有建筑重叠
        if 0 <= x < width and 0 <= y < height and terrain[y][x] == 'grass':
            buildings.append({
                'id': f'h{i+1}',
                'definition_id': 'row_house',
                'x': x, 'y': y, 'rotation': 0,
                'status': 'completed',
                'housing_mode': 'public',
                'occupancy': 0,
                'sold_units': 0,
                'progress': 14,
                'started_day': -14,
            })
    # 市政厅放在地图中央
    hall = next(item for item in template['landmarks'] if item['definition_id'] == 'town_hall')
    cx, cy = hall['x'], hall['y']
    buildings.insert(0, {
        'id': 'h0', 'definition_id': 'town_hall', 'x': cx, 'y': cy, 'rotation': hall.get('rotation', 0),
        'status': 'completed', 'housing_mode': 'public',
        'occupancy': 0, 'sold_units': 0, 'progress': 0, 'started_day': -30,
    })
    return buildings


def _next_building_id(buildings: list[dict]) -> str:
    used = {int(b['id'][1:]) for b in buildings if b['id'].startswith('h') and b['id'][1:].isdigit()}
    n = 1
    while n in used:
        n += 1
    return f'h{n}'


def build_catalog(industry_game: IndustryGame) -> list[dict]:
    """构造建筑目录：外置投资、维护、住房与行业能力。"""
    catalog = []
    # CATALOG 在 config.py 中是 {id: def} 的 dict，需要迭代 value
    raw_items = CATALOG.values() if isinstance(CATALOG, dict) else CATALOG
    for raw in raw_items:
        item = dict(raw)
        item.setdefault('buildable', True)
        mapping = _BUILDING_INDUSTRY_MAP.get(item['id'], {})
        industry = mapping.get('industry')
        item['industry'] = industry
        item['jobs_added'] = mapping.get('jobs_added', 0)
        item['housing_added'] = mapping.get('housing_added', 0)
        item['cost'] = mapping.get('cost_cents', 0)
        item['maintenance'] = mapping.get('maintenance_cents', 0)
        item['duration'] = 1
        item['description'] = ('下一月完工；增加真实住房容量。' if item['housing_added'] else '下一月完工；增加岗位和生产设施，实际雇佣取决于资金、投入与需求。' if industry else '公共设施；维护从市政预算支付。')
        # 把当前行业的 jobs 字段填充（用于 UI 显示"建一座工坊可让 IND 行业 +40 岗位"）
        if industry:
            try:
                item['industry_jobs'] = industry_game.city.jobs(industry)
            except Exception:  # noqa: BLE001
                item['industry_jobs'] = None
        catalog.append(item)
    return catalog


def build_industry_snapshot(industry_game: IndustryGame) -> dict:
    """从 IndustryEconomy 派生前端可见的 industries / cash / ocean / policies 等。"""
    city = industry_game.city
    cash = city.cash
    employment = city.employment()
    jobs = {i: city.jobs(i) for i in city.keys}
    industries = []
    for i in city.keys:
        sector = city.scenario['sectors'][i]
        wage_cents = city.current_wages.get(i, 0) if hasattr(city, 'current_wages') else 0
        industries.append({
            'id': i,
            'name_cn': sector.get('name_cn', i),
            'employment': employment[i],
            'capacity': jobs[i],
            'wage_cents': wage_cents,
            'cash_cents': cash.get(i, 0),
        })
    last = city.last_report or {}
    return {
        'industries': industries,
        'cash_by_industry': {k: v for k, v in cash.items() if k != 'EXT'},
        'government_cash_cents': cash.get('GOV', 0),
        'ocean_cash_cents': getattr(city, 'ocean_cash', 0),
        'external_cash_cents': cash.get('EXT', 0),
        'external_raw_units': getattr(city, 'external_raw', 0),
        'cumulative_ext_in_cents': getattr(city, 'cumulative_ext_in_cents', 0),
        'cumulative_ext_out_cents': getattr(city, 'cumulative_ext_out_cents', 0),
        'cumulative_raw_purchased_units': getattr(city, 'cumulative_raw_purchased_units', 0),
        'month': city.month,
        'active_policies': {f'{t}|{k}': v for (t, k), v in getattr(city, '_active_policies', {}).items()},
        'recent_mayor_actions': list(getattr(city, '_last_policy_actions', [])),
        'last_report': last,
        'last_demography': getattr(city, 'last_demography', {}),
    }


def build_state(industry_game: IndustryGame) -> dict:
    """构造完整 game state（前端 index.html 期望的 schema）。"""
    terrain = _generate_terrain(TEMPLATE)
    city = industry_game.city
    buildings = list(city.scenario.get('_map_buildings', _initial_buildings(TEMPLATE, terrain, city.scenario.get('_seed', 20251008))))
    catalog = build_catalog(industry_game)
    snapshot = build_industry_snapshot(industry_game)

    total_jobs = sum(ind['capacity'] for ind in snapshot['industries'])
    total_employment = sum(ind['employment'] for ind in snapshot['industries'])
    population = sum(city_population := industry_game.city.population.values()) if hasattr(industry_game.city, 'population') else 0

    return {
        'calendar': {'days_per_month': 30, 'months_per_year': 12},
        'world': {
            'day': snapshot['month'] * 30,
            'seed': city.scenario.get('_seed', 20251008),
            'terrain': terrain,
            'buildings': buildings,
            'paused': True,  # 地图游戏默认暂停，玩家点月进触发
            'speed': 1,
        },
        'catalog': catalog,
        'summary': {
            'capacity': total_jobs,
            'jobs': total_employment,
            'blueprints': 0,
            'blueprint_cost': 0,
            'construction': sum(b['status'] == 'constructing' for b in buildings),
            'net': 0,
        },
        'housing': {
            'capacity': housing_capacity(city),
            'residents': sum(city.population.values()),
            'unsold_units': 0,
            'unsold_asset_value': 0,
            'public_residents': 0,
            'buyer_demand': 0,
        },
        'commerce': {'merchants': 0, 'slots': 0, 'demand': 0},
        'industries': snapshot['industries'],
        'government_cash_cents': snapshot['government_cash_cents'],
        'ocean_cash_cents': snapshot['ocean_cash_cents'],
        'external_cash_cents': snapshot['external_cash_cents'],
        'external_raw_units': snapshot['external_raw_units'],
        'cash_by_industry': snapshot['cash_by_industry'],
        'month': snapshot['month'],
        'active_policies': snapshot['active_policies'],
        'recent_mayor_actions': snapshot['recent_mayor_actions'],
        'last_report': snapshot['last_report'],
        'map_month_report': getattr(city, 'map_month_report', []),
        'maintenance_due_cents': sum(b.get('maintenance_due_cents', 0) for b in buildings),
        'last_demography': snapshot['last_demography'],
        'save_exists': (industry_game.save_dir / 'industry-slot.json').exists(),
        'cumulative_ext_in_cents': snapshot['cumulative_ext_in_cents'],
        'cumulative_ext_out_cents': snapshot['cumulative_ext_out_cents'],
        'cumulative_raw_purchased_units': snapshot['cumulative_raw_purchased_units'],
    }


class Command(IndustryCommand):
    type: Literal['build', 'cancel', 'remove', 'relocate', 'advance', 'save', 'load', 'new_game',
                  'mayor_command', 'purchase_external', 'set_parameter']
    # build / remove / relocate 命令字段
    definition_id: str | None = None
    building_id: str | None = None
    x: StrictInt | None = Field(default=None, ge=0, le=63)
    y: StrictInt | None = Field(default=None, ge=0, le=63)
    new_x: StrictInt | None = Field(default=None, ge=0, le=63)
    new_y: StrictInt | None = Field(default=None, ge=0, le=63)
    rotation: StrictInt | None = Field(default=None, ge=0, le=3)


@router.get('/state')
async def state(request: Request):
    async with request.app.state.lock:
        return build_state(request.app.state.industry_game)


@router.get('/placement')
async def placement(request: Request, definition_id: str, x: int, y: int, rotation: int = 0):
    """预览与实际建造共用完整占地、地形和道路入口校验。"""
    try:
        validate_placement(request.app.state.industry_game.city, definition_id, x, y, rotation)
        return {'valid': True, 'reason': '可建造：点击即付款，下一月完工'}
    except (ValueError, KeyError, TypeError) as exc:
        return {'valid': False, 'reason': str(exc)}


@router.post('/commands')
async def commands(command: Command, request: Request):
    async with request.app.state.lock:
        industry_game: IndustryGame = request.app.state.industry_game
        try:
            payload = command.model_dump(exclude_none=True)
            kind = payload.pop('type')
            if kind == 'build':
                _handle_build(payload, industry_game)
            elif kind == 'cancel':
                # 蓝图取消不需要后端操作，仅前端 UI 行为
                pass
            elif kind == 'remove':
                _handle_remove(payload, industry_game)
            elif kind == 'relocate':
                _handle_relocate(payload, industry_game)
            else:
                # 其余命令（advance / save / load / new_game / mayor_command /
                # purchase_external / set_parameter）复用 industry_api 的 IndustryGame
                # 先执行真正的命令（可能改变 IndustryGame 状态）。
                from backend.industry_api import Command as IndustryCommand
                allowed = set(IndustryCommand.model_fields)
                industry_game.command({k: v for k, v in {'type': kind, **payload}.items() if k in allowed})
            # 任何命令结束后都返回 game 视图的 state（含 industries / map）
            return build_state(industry_game)
        except (ValueError, KeyError, TypeError) as exc:
            raise HTTPException(status_code=400, detail=f'操作未生效：{exc}') from exc
        except OSError as exc:
            logger.error('地图存档读写失败 operation=%s error=%s', kind, exc)
            raise HTTPException(status_code=500, detail='无法读写存档，原城市未被替换。') from exc
        except AssertionError as exc:
            logger.exception('经济结算断言失败，未提交状态 operation=%s', kind)
            raise HTTPException(status_code=500, detail='经济核对失败，本次推进未生效。请检查日志。') from exc



def _buildings(city):
    return city.scenario.get('_map_buildings', _initial_buildings(TEMPLATE, _generate_terrain(TEMPLATE), city.scenario.get('_seed', 20251008)))


def validate_placement(city, definition_id, x, y, rotation=0, exclude_id=None):
    return _validate_geometry(_buildings(city), definition_id, x, y, rotation, exclude_id)


def _handle_build(payload, industry_game):
    def operation(city):
        definition_id = payload.get('definition_id')
        x, y, rotation = payload.get('x'), payload.get('y'), payload.get('rotation', 0) or 0
        validate_placement(city, definition_id, x, y, rotation)
        effect = _BUILDING_INDUSTRY_MAP[definition_id]
        cost = effect['cost_cents']
        if city.cash['GOV'] < cost:
            raise ValueError(f'市政现金不足：需要 {cost / 100:g} CR')
        buildings = list(_buildings(city))
        city.scenario.setdefault('_map_capacity_base', {i: {'jobs': city.jobs(i), 'qbar': city.qbar[i], 'full_raw': city.full_raw[i]} for i in city.keys})
        next_id = max(city.scenario.get('_map_next_id', 1),
                      max((int(b['id'][1:]) for b in buildings if b['id'].startswith('h') and b['id'][1:].isdigit()), default=0) + 1)
        building_id = f'h{next_id}'
        city.scenario['_map_next_id'] = next_id + 1
        city.cash['GOV'] -= cost
        city.cash['IND'] += cost
        city._record_tx({'from': 'GOV', 'to': 'IND', 'amount_cents': cost,
                         'kind': 'building_investment', 'building_id': building_id})
        buildings.append({'id': building_id, 'definition_id': definition_id, 'x': x, 'y': y,
                          'rotation': rotation, 'status': 'constructing', 'player_built': True,
                          'started_month': city.month, 'progress': 0, 'housing_mode': 'public',
                          'occupancy': 0, 'sold_units': 0, 'maintenance_due_cents': 0})
        city.scenario['_map_buildings'] = buildings
        city.housing_capacity = housing_capacity(city)
    return industry_game.execute_once({'type': 'build', **payload}, operation)


def _target(city, building_id):
    target = next((b for b in _buildings(city) if b['id'] == building_id), None)
    if target is None:
        raise ValueError('找不到建筑')
    if target['definition_id'] == 'town_hall':
        raise ValueError('市政厅是城市起点，不能拆除或迁址')
    return target


def _handle_remove(payload, industry_game):
    def operation(city):
        target = _target(city, payload.get('building_id'))
        effect = _BUILDING_INDUSTRY_MAP[target['definition_id']]
        if target.get('maintenance_due_cents', 0):
            raise ValueError('建筑仍有维护欠款，不能拆除')
        if target.get('player_built') and target['status'] == 'completed':
            if housing_capacity(city) - effect['housing_added'] < sum(city.population.values()):
                raise ValueError('拆除后住房不足，需先建设替代住房')
            if effect['industry'] and city.jobs(effect['industry']) - effect['jobs_added'] < city.employment()[effect['industry']]:
                raise ValueError('拆除后岗位不足，需先提供替代就业容量')
            change_capacity(city, target, -1)
        city.scenario['_map_buildings'] = [b for b in _buildings(city) if b['id'] != target['id']]
        city.housing_capacity = housing_capacity(city)
    return industry_game.execute_once({'type': 'remove', **payload}, operation)


def _handle_relocate(payload, industry_game):
    def operation(city):
        buildings = list(_buildings(city))
        city.scenario['_map_buildings'] = buildings
        target = _target(city, payload.get('building_id'))
        x, y = payload.get('new_x'), payload.get('new_y')
        rotation = payload.get('rotation', target['rotation'])
        validate_placement(city, target['definition_id'], x, y, rotation, target['id'])
        target.update(x=x, y=y, rotation=rotation)
    return industry_game.execute_once({'type': 'relocate', **payload}, operation)
