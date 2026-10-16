"""地图游戏 REST 接口。

把 `frontend/index.html` 的等距地图 + 房屋建造视觉接上 V2.1 行业经济内核：
- 地图视觉（terrain/seed/初始建筑）来自 v1 模板（`data/templates/river_town.json`
  与 `data/buildings.json`），仅做表面渲染
- 玩家在地图上建造建筑被翻译为对 `IndustryEconomy` 的能力扩展
  （如 workshop = IND 工业 +N 个岗位），真正驱动经济状态的是新内核
- 月进 / 存读档 / 市政指令 / 调参 全部委托给 `IndustryGame`
- 状态字段统一映射到 industry_economy 派生值

阶段衔接（2026-10-16）：从阶段 14+15 的「行业经营 webui」回退到原
`frontend/index.html` 真游戏界面，并把游戏逻辑接入新经济内核。
"""
from __future__ import annotations

import json as _stdlib_json
import logging
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from backend.config import CATALOG, ROOT, SAVE_DIR, TEMPLATE, read_json
from backend.industry_api import IndustryGame

logger = logging.getLogger(__name__)

router = APIRouter(prefix='/api/game')

# v1 建筑 → V2.1 行业 的翻译表。
# market/workshop/row_house/apartment 都被映射到对应行业的 jobs_added。
# park 仅装饰（jobs_added=0），town_hall 不可建造。
_BUILDING_INDUSTRY_MAP = {
    'market':     {'industry': 'SRV', 'jobs_added': 30, 'note': '河湾集市：服务业 +30 岗位'},
    'workshop':   {'industry': 'IND', 'jobs_added': 40, 'note': '蒸汽工坊：工业 +40 岗位'},
    'row_house':  {'industry': 'AGR', 'jobs_added': 12, 'note': '河岸联排住宅：农业 +12 岗位（住房带动周边需求）'},
    'apartment':  {'industry': 'AGR', 'jobs_added': 24, 'note': '工人公寓：农业 +24 岗位（更多住房与家庭消费）'},
    'park':       {'industry': None,  'jobs_added': 0,  'note': '街角花园：环境装饰，不增加岗位'},
    'town_hall':  {'industry': None,  'jobs_added': 0,  'note': '市政厅：城市起点，不可建造'},
}


def _generate_terrain(template: dict, seed: int = 20251008) -> list[list[str]]:
    """按 river_town.json 生成 28x28 terrain：河岸、桥梁、道路、草原。"""
    width, height = template['width'], template['height']
    river_start_x = template['river_start_x']
    river_bend_y = template['river_bend_y']
    river_bend_offset = template['river_bend_offset']
    road_columns = template['road_columns']
    road_rows = template['road_rows']
    bridge_rows = template['bridge_rows']

    terrain = [['grass'] * width for _ in range(height)]
    # 河流：river_start_x 开始，到 river_bend_y 处弯 river_bend_offset 个格
    river_x = river_start_x
    for y in range(height):
        terrain[y][river_x] = 'water'
        if y == river_bend_y:
            river_x += river_bend_offset
            river_x = max(0, min(width - 1, river_x))
            terrain[y][river_x] = 'water'
    # 道路网格
    for rx in road_columns:
        for y in range(height):
            if terrain[y][rx] != 'water':
                terrain[y][rx] = 'road'
    for ry in road_rows:
        for x in range(width):
            if terrain[ry][x] != 'water':
                terrain[ry][x] = 'road'
    # 桥梁：道路与河流交汇处
    for ry in bridge_rows:
        for x in range(width):
            if terrain[ry][x] == 'road':
                # 与河流相邻的 road 改成 bridge
                for dx in (-1, 0, 1):
                    nx = x + dx
                    if 0 <= nx < width and terrain[ry][nx] == 'water':
                        terrain[ry][nx] = 'bridge'
                        terrain[ry][x] = 'bridge'
    return terrain


def _initial_buildings(template: dict, terrain: list[list[str]]) -> list[dict]:
    """生成 v1 模板里的 initial_houses 起始建筑列表。"""
    width, height = template['width'], template['height']
    initial_houses = template.get('initial_houses', 0)
    candidates = template.get('house_candidates', [])
    buildings = []
    for i, (x, y) in enumerate(candidates[:initial_houses]):
        # 校验地块：必须是 grass 且不与已有建筑重叠
        if 0 <= x < width and 0 <= y < height and terrain[y][x] == 'grass':
            buildings.append({
                'id': f'h{i}',
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
    cx, cy = width // 2 - 1, height // 2 - 1
    buildings.insert(0, {
        'id': 'h0', 'definition_id': 'town_hall', 'x': cx, 'y': cy, 'rotation': 0,
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
    """构造前端 catalog：v1 6 种建筑 + jobs_added/industry 注解 + 当前 capacity。"""
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
    # 优先从运行时内存（_map_buildings）读取玩家真实建造的建筑，
    # 否则从 scenario['_map_buildings']（存档）还原，再否则用模板默认建筑。
    city = industry_game.city
    if hasattr(city, '_map_buildings') and city._map_buildings is not None:
        buildings = list(city._map_buildings)
    else:
        scenario_buildings = city.scenario.get('_map_buildings')
        if scenario_buildings:
            buildings = list(scenario_buildings)
            city._map_buildings = buildings
        else:
            buildings = _initial_buildings(TEMPLATE, terrain)
            city._map_buildings = buildings
            city.scenario['_map_buildings'] = buildings
    catalog = build_catalog(industry_game)
    snapshot = build_industry_snapshot(industry_game)

    total_jobs = sum(ind['capacity'] for ind in snapshot['industries'])
    total_employment = sum(ind['employment'] for ind in snapshot['industries'])
    population = sum(city_population := industry_game.city.population.values()) if hasattr(industry_game.city, 'population') else 0

    return {
        'calendar': {'days_per_month': 30, 'months_per_year': 12},
        'world': {
            'day': snapshot['month'] * 30,
            'seed': 20251008,
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
            'construction': 0,
            'net': 0,
        },
        'housing': {
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
        'last_demography': snapshot['last_demography'],
        'save_exists': (SAVE_DIR / 'industry-slot.json').exists(),
        'cumulative_ext_in_cents': snapshot['cumulative_ext_in_cents'],
        'cumulative_ext_out_cents': snapshot['cumulative_ext_out_cents'],
        'cumulative_raw_purchased_units': snapshot['cumulative_raw_purchased_units'],
    }


class Command(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    type: Literal['build', 'cancel', 'remove', 'relocate', 'advance', 'save', 'load', 'new_game',
                  'mayor_command', 'purchase_external', 'set_parameter']
    request_id: str | None = Field(default=None, max_length=80)
    # build / remove / relocate 命令字段
    definition_id: str | None = None
    building_id: str | None = None
    x: StrictInt | None = Field(default=None, ge=0, le=63)
    y: StrictInt | None = Field(default=None, ge=0, le=63)
    new_x: StrictInt | None = Field(default=None, ge=0, le=63)
    new_y: StrictInt | None = Field(default=None, ge=0, le=63)
    rotation: StrictInt | None = Field(default=None, ge=0, le=3)
    # advance 字段
    months: StrictInt = Field(default=1, ge=1, le=120)
    # set_parameter 字段
    section: str | None = None
    key: str | None = None
    value: float | None = None
    industry: str = 'IND'
    # purchase_external 字段
    cash_cr: float = 0
    raw_units: float = 0
    from_account: str = 'GOV'
    note: str | None = None
    # mayor_command 字段
    mayor_kind: str | None = None
    people: StrictInt = Field(default=0, ge=0, le=10000)
    policy_months: StrictInt = Field(default=1, ge=1, le=120)


@router.get('/state')
async def state(request: Request):
    async with request.app.state.lock:
        return build_state(request.app.state.industry_game)


@router.get('/placement')
async def placement(request: Request, definition_id: str, x: int, y: int, rotation: int = 0):
    """简化版地块预览：仅校验地块在地图范围内且不是水/桥。"""
    terrain = _generate_terrain(TEMPLATE)
    if not (0 <= x < len(terrain[0]) and 0 <= y < len(terrain)):
        return {'valid': False, 'reason': '超出地图范围'}
    cell = terrain[y][x]
    if cell in ('water', 'bridge'):
        return {'valid': False, 'reason': '河流/桥梁不可建造'}
    # 检查 definition_id 是否合法
    if not any(d['id'] == definition_id for d in CATALOG):
        return {'valid': False, 'reason': '未知建筑类型'}
    defn = next(d for d in CATALOG if d['id'] == definition_id)
    if not defn.get('buildable', True):
        return {'valid': False, 'reason': '此建筑不可建造'}
    return {'valid': True, 'reason': '可建造'}


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
                industry_game.command({'type': kind, **payload})
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


def _handle_build(payload: dict, industry_game: IndustryGame) -> dict:
    """地图建造 = 给对应行业 +N jobs + 在地图上放置视觉建筑。"""
    definition_id = payload.get('definition_id')
    if not definition_id:
        raise ValueError('缺少 definition_id')
    x = payload.get('x')
    y = payload.get('y')
    if x is None or y is None:
        raise ValueError('缺少坐标 x / y')
    mapping = _BUILDING_INDUSTRY_MAP.get(definition_id)
    if not mapping:
        raise ValueError(f'未知建筑类型：{definition_id}')
    industry = mapping.get('industry')
    jobs_added = mapping.get('jobs_added', 0)
    rotation = payload.get('rotation', 0) or 0
    # 地块校验
    terrain = _generate_terrain(TEMPLATE)
    if not (0 <= y < len(terrain) and 0 <= x < len(terrain[0])):
        raise ValueError(f'坐标 ({x},{y}) 超出地图')
    cell = terrain[y][x]
    if cell in ('water', 'bridge'):
        raise ValueError(f'({x},{y}) 是 {cell}，不可建造')
    # 同 occupation 内是否已被占用（与现有建筑重叠）
    for b in industry_game.city.state.get('_map_buildings', []) if hasattr(industry_game.city, 'state') else []:
        # 简化：直接看 build_state 输出
        pass
    # 真正的经济动作：扩展行业 jobs
    if industry and jobs_added > 0:
        new_capacity = industry_game.city.add_industry_capacity(industry, jobs_added)
        logger.info('建造 %s @ (%d,%d) → %s 行业 jobs +%d（当前 %d）',
                    definition_id, x, y, industry, jobs_added, new_capacity)
    # 视觉：在地图上记录该建筑（game_api 不持有持久地图，直接在
    # industry_game.city 上挂一个 _map_buildings 列表）。
    if not hasattr(industry_game.city, '_map_buildings') or industry_game.city._map_buildings is None:
        # 用内存 dict 替代首次访问（state 是从 build_state 派生，不是持久的）
        # 这里改成把建筑信息直接序列化进 save（save 时序列化 _map_buildings）
        industry_game.city._map_buildings = []
        # 初次访问时，populate from current city to_dict()
        existing_buildings = industry_game.city.scenario.get('_map_buildings', [])
        if existing_buildings:
            industry_game.city._map_buildings = list(existing_buildings)
        else:
            industry_game.city._map_buildings = _initial_buildings(TEMPLATE, terrain)
    building_id = _next_building_id(industry_game.city._map_buildings)
    industry_game.city._map_buildings.append({
        'id': building_id,
        'definition_id': definition_id,
        'x': x, 'y': y, 'rotation': rotation,
        'status': 'completed', 'housing_mode': 'public',
        'occupancy': 0, 'sold_units': 0, 'progress': 0, 'started_day': 0,
    })
    industry_game.city.scenario['_map_buildings'] = industry_game.city._map_buildings


def _handle_remove(payload: dict, industry_game: IndustryGame) -> dict:
    """拆除指定建筑 = 地图移除 + 对应行业 jobs -N。"""
    building_id = payload.get('building_id')
    if not building_id:
        raise ValueError('缺少 building_id')
    # 先确保 _map_buildings 已加载
    if not hasattr(industry_game.city, '_map_buildings') or industry_game.city._map_buildings is None:
        terrain = _generate_terrain(TEMPLATE)
        industry_game.city._map_buildings = _initial_buildings(TEMPLATE, terrain)
    buildings = industry_game.city._map_buildings
    target = next((b for b in buildings if b['id'] == building_id), None)
    if not target:
        raise ValueError(f'找不到建筑 {building_id}')
    definition_id = target['definition_id']
    mapping = _BUILDING_INDUSTRY_MAP.get(definition_id, {})
    industry = mapping.get('industry')
    jobs_added = mapping.get('jobs_added', 0)
    # 经济动作：行业 jobs 减少
    if industry and jobs_added > 0:
        new_capacity = industry_game.city.add_industry_capacity(industry, -jobs_added)
        logger.info('拆除 %s @ (%d,%d) → %s 行业 jobs -%d（当前 %d）',
                    definition_id, target['x'], target['y'], industry, jobs_added, new_capacity)
    # 视觉：从地图移除
    industry_game.city._map_buildings = [b for b in buildings if b['id'] != building_id]
    industry_game.city.scenario['_map_buildings'] = industry_game.city._map_buildings
    return build_state(industry_game)


def _handle_relocate(payload: dict, industry_game: IndustryGame) -> dict:
    """迁移建筑到新位置：仅视觉移动，不改变 jobs。"""
    building_id = payload.get('building_id')
    new_x = payload.get('new_x')
    new_y = payload.get('new_y')
    if not building_id:
        raise ValueError('缺少 building_id')
    if new_x is None or new_y is None:
        raise ValueError('缺少 new_x / new_y')
    # 先确保 _map_buildings 已加载
    if not hasattr(industry_game.city, '_map_buildings') or industry_game.city._map_buildings is None:
        terrain = _generate_terrain(TEMPLATE)
        industry_game.city._map_buildings = _initial_buildings(TEMPLATE, terrain)
    buildings = industry_game.city._map_buildings
    target = next((b for b in buildings if b['id'] == building_id), None)
    if not target:
        raise ValueError(f'找不到建筑 {building_id}')
    # 校验新坐标
    terrain = _generate_terrain(TEMPLATE)
    if not (0 <= new_y < len(terrain) and 0 <= new_x < len(terrain[0])):
        raise ValueError(f'新坐标 ({new_x},{new_y}) 超出地图')
    cell = terrain[new_y][new_x]
    if cell in ('water', 'bridge'):
        raise ValueError(f'({new_x},{new_y}) 是 {cell}，不可建造')
    # 不允许覆盖已有建筑
    occupied = {(b['x'], b['y']) for b in buildings if b['id'] != building_id}
    if (new_x, new_y) in occupied:
        raise ValueError(f'({new_x},{new_y}) 已被其他建筑占用')
    # 仅更新坐标
    old = (target['x'], target['y'])
    target['x'], target['y'] = new_x, new_y
    industry_game.city.scenario['_map_buildings'] = industry_game.city._map_buildings
    logger.info('迁移 %s 从 (%d,%d) → (%d,%d)', building_id, old[0], old[1], new_x, new_y)
    return build_state(industry_game)