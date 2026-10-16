# 世界地图界面实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 BIPOLE 增加一个独立的世界地图 Web 界面，玩家可以查看 5-8 座 NPC 城市的节点位置与基于"交流关系"的脱敏经济概况。

**Architecture:** 后端扩展 WorldState 数据模型，新增 NPC 城市后台统计引擎和关系系统，提供两个 REST 端点；前端用 SVG 渲染独立页面（含可缩放背景），不依赖外部美术资源。前端只显示脱敏字段，由后端按友好度计算 visibility_level。

**Tech Stack:** Python 3.11+ / FastAPI / Pydantic（与现有项目一致）；前端 HTML/CSS/SVG + 原生 JavaScript（不引入新框架）；pytest + Playwright（与现有验收一致）。

---

## 1. 范围与依赖

### 1.1 模块依赖

- **不依赖**完整跨城交易（M11 仅作为后续路线，本次只读 NPC 城市后台模拟）。
- **不依赖**议会（M18）——友好度独立实现，不引入事件系统。
- **不依赖**经济触发器（M24）——独立模块，可并行开发。
- **可复用**：`backend/systems/v21/industry_economy.py`（单城内核已经实现，作为 NPC 城市模板）；`backend/systems/v21/city_presets.py`（5 种 archetype 已定义）。
- **新增依赖**：无新 Python 包。

### 1.2 当前 vs 目标状态

| 项 | 当前 | 目标 |
|---|---|---|
| `WorldState` 数据 | 不存在 | 新增 `world_state.py` |
| NPC 城市模拟 | 不存在 | 新增 `npc_simulator.py`，每月推一次核心 KPI |
| 关系系统 | 不存在 | 新增 `diplomacy.py`，友好度 + 贸易量 |
| `/api/world/*` 路由 | 不存在 | `GET /api/world/state`、`GET /api/world/cities/:id` |
| Web 页面 | 无 | `frontend/world.html` 独立页面 |
| SVG 地图渲染 | 无 | `frontend/js/world_map.js` |
| 端到端浏览器验收 | 无 | `scripts/check_world_map_browser.py` |

### 1.3 任务排序（按依赖）

```
A1 WorldState 模型      ──┐
A2 NPC 模拟器           │ 阶段 A：后端数据层
A3 关系系统            ──┘
                         │
B1 视图模型与脱敏       ┐ 阶段 B：API 层
B2 API 路由             ┘
                         │
C1 前端骨架             ──┐
C2 SVG 节点与连线       │ 阶段 C：前端
C3 精美地图背景        ──┘
                         │
D1 节点点击详情面板     ┐ 阶段 D：交互
D2 关系可视化          ┘
                         │
E1 端到端浏览器测试    ──┐ 阶段 E：验收
E2 文档与 CHANGELOG    ──┘
```

---

## 2. 文件清单（创建/修改）

### 后端

| 文件 | 行为 | 责任 |
|---|---|---|
| `backend/systems/v21/world_state.py` | 新增 | WorldState / CityNode 数据类 |
| `backend/systems/v21/npc_simulator.py` | 新增 | NPC 月推进 + 核心 KPI |
| `backend/systems/v21/diplomacy.py` | 新增 | 关系 + 友好度模型 |
| `backend/systems/v21/world_view.py` | 新增 | visibility_level + 字段脱敏 |
| `backend/world_api.py` | 新增 | `/api/world/state`、`/api/world/cities/:id` |
| `backend/app.py` | 修改 | 挂载 `world_router` |
| `backend/systems/v21/__init__.py` | 修改 | 导出新模块 |
| `data/world_map.json` | 新增 | 5 城固定位置 + 距离矩阵 + 初始友好度 |
| `tests/v21/test_world_state.py` | 新增 | 数据类测试 |
| `tests/v21/test_npc_simulator.py` | 新增 | NPC 月推进测试 |
| `tests/v21/test_diplomacy.py` | 新增 | 关系测试 |
| `tests/v21/test_world_view.py` | 新增 | 脱敏测试 |
| `tests/v21/test_world_api.py` | 新增 | API 端点测试 |

### 前端

| 文件 | 行为 | 责任 |
|---|---|---|
| `frontend/world.html` | 新增 | 世界地图页面 |
| `frontend/css/world.css` | 新增 | 页面样式 |
| `frontend/js/world_main.js` | 新增 | 入口、API 调用、状态管理 |
| `frontend/js/world_map.js` | 新增 | SVG 渲染、缩放平移、节点交互 |
| `frontend/js/world_detail.js` | 新增 | 详情面板渲染 |
| `frontend/index.html`、`industry.html` | 修改 | 加入"世界地图"导航入口 |
| `frontend/js/api.js` | 修改 | 增加 world 模块 API 路径 |

### 验证

| 文件 | 行为 | 责任 |
|---|---|---|
| `scripts/check_world_map_browser.py` | 新增 | Playwright 真实点击测试 |

### 文档

| 文件 | 行为 | 责任 |
|---|---|---|
| `docs/03_设计规范/世界地图设计-V1.0.md` | 新增 | 模块设计文档（M25） |
| `docs/03_设计规范/游戏模块开发要求与详细设计.md` | 修改 | 加入 M25 世界地图 |
| `CHANGELOG.md` | 修改 | 新增条目 |
| `README.md` | 修改 | 加入世界地图说明 |

---

## 3. 数据模型

### 3.1 WorldState（`backend/systems/v21/world_state.py`）

```python
@dataclass(frozen=True)
class CityNode:
    """世界地图上的城市节点。"""
    id: str                # 如 "agricultural_town"
    name_cn: str           # 如 "农业贸易镇"
    archetype: str         # 复用 CityPreset 的 archetype
    x: float               # 世界地图坐标 0..100
    y: float               # 世界地图坐标 0..100
    is_player: bool        # 玩家城=True，NPC=False


@dataclass(frozen=True)
class WorldState:
    seed: int
    month: int
    year: int
    inflation_pi: float    # 世界通胀
    cities: tuple[CityNode, ...]   # 5-8 座城市
    distance_matrix: dict[tuple[str, str], int]  # 城市间距离 km
    # 详细 NPC 数据在 NpcSimulator.npc_profiles 中


def default_world() -> WorldState:
    """按 data/world_map.json 返回世界初始状态。"""
```

### 3.2 NpcSimulator（`backend/systems/v21/npc_simulator.py`）

```python
@dataclass(frozen=True)
class NpcCityProfile:
    """单 NPC 城市本月关键 KPI（脱敏前）。"""
    id: str
    name_cn: str
    archetype: str
    population: int
    labor_force: int
    unemployment_rate: float
    K_over_N: float       # K/N
    L: float              # L 宜居度
    dominant_industry: str
    food_production_units: float
    material_production_units: float
    stockpile_food_units: float
    stockpile_material_units: float
    monthly_trade_volume_cents: int


class NpcSimulator:
    def __init__(self, world: WorldState, seed: int):
        self.world = world
        self.profiles = self._initial_profiles()
        self.rng = random.Random(seed)
    
    def advance(self, months: int = 1) -> tuple[NpcSimulator, dict[str, NpcCityProfile]]:
        """按月推进 NPC 城市后台模拟；返回新快照 + 月末 profile。"""
    
    def get_profile(self, city_id: str) -> NpcCityProfile:
        """读取当月 profile（只读）。"""
```

NPC 月推进算法（不追求复杂，**只保持初步可用**）：
- 人口 = 基础±50 × 月增长因子（与 L 与 K/N 相关）
- K 与 L 由 archetype 工艺幅度 + 季节因子扰动
- 主导行业 = employment_by_industry 排序最高
- 月产出 = 当前 stock × 月比率
- 库存 = 上月 + 生产 - 消费（消费量与人口相当）

### 3.3 Diplomacy（`backend/systems/v21/diplomacy.py`）

```python
@dataclass(frozen=True)
class Relation:
    """两城市之间的关系（含牌面）。"""
    city_a: str
    city_b: str
    friendliness: int      # 0-100；50=中立
    trade_volume_cents: int  # 当月贸易额
    last_event: str        # 最近事件描述


class DiplomacySystem:
    def __init__(self, world: WorldState):
        self.relations = self._initial_relations()
    
    def adjust_friendliness(self, city_a: str, city_b: str, delta: int, reason: str) -> Relation:
        """调整友好度并返回新值。"""
    
    def get_visibility(self, city_id: str) -> int:
        """根据友好度返回可见等级 0-4。"""
        rel = self.relation_with_player(city_id)
        if rel.friendliness >= 81: return 4  # 完全开放
        if rel.friendliness >= 61: return 3  # 盟友
        if rel.friendliness >= 41: return 2  # 友好
        if rel.friendliness >= 21: return 1  # 认识
        return 0                              # 陌生人
```

### 3.4 WorldView（`backend/systems/v21/world_view.py`）

```python
def get_city_view(profile: NpcCityProfile, visibility: int) -> dict:
    """按可见等级脱敏字段。"""
    base = {'id': profile.id, 'name_cn': profile.name_cn, 'archetype': profile.archetype}
    if visibility >= 0: base['population_g'] = profile.population // 1000  # 千人量级
    if visibility >= 1: base['population_g'] = profile.population // 100   # 百人量级
    if visibility >= 2:
        base.update({
            'K_over_N': round(profile.K_over_N, 2),
            'L': round(profile.L, 1),
            'dominant_industry': profile.dominant_industry,
        })
    if visibility >= 3:
        base.update({
            'food_production_units': round(profile.food_production_units, 0),
            'material_production_units': round(profile.material_production_units, 0),
        })
    if visibility >= 4:
        base.update({
            'stockpile_food_units': round(profile.stockpile_food_units, 0),
            'stockpile_material_units': round(profile.stockpile_material_units, 0),
            'monthly_trade_volume_cents': profile.monthly_trade_volume_cents,
            'unemployment_rate': round(profile.unemployment_rate, 3),
        })
    return base
```

### 3.5 `data/world_map.json`

```json
{
  "cities": [
    {"id": "agricultural_town", "name_cn": "禾谷镇", "archetype": "agricultural_town", "x": 28, "y": 72},
    {"id": "industrial_city",   "name_cn": "河湾镇", "archetype": "industrial_city",   "x": 50, "y": 50, "is_player_default": true},
    {"id": "commercial_port",   "name_cn": "碧涛港", "archetype": "commercial_port",   "x": 78, "y": 30},
    {"id": "magic_academy",     "name_cn": "辉月学城", "archetype": "magic_academy",     "x": 18, "y": 28},
    {"id": "mining_border",     "name_cn": "铁壁关", "archetype": "mining_border",     "x": 75, "y": 78}
  ],
  "distance_matrix": {
    "agricultural_town|industrial_city": 240,
    "industrial_city|commercial_port": 320,
    ...
  },
  "initial_friendliness": {
    "agricultural_town|industrial_city": 60,
    "industrial_city|commercial_port": 65,
    ...
  }
}
```

---

## 4. API 设计

### 4.1 `GET /api/world/state`

返回：
```json
{
  "world": {
    "seed": 20251008,
    "month": 12,
    "year": 1,
    "inflation_pi": 0.005
  },
  "cities": [
    {"id": "agricultural_town", "name_cn": "禾谷镇", "x": 28, "y": 72, "is_player": false, "archetype": "agricultural_town"},
    {"id": "industrial_city",   "name_cn": "河湾镇", "x": 50, "y": 50, "is_player": true,  "archetype": "industrial_city"},
    ...
  ],
  "links": [
    {"from": "industrial_city", "to": "agricultural_town", "friendliness": 60, "trade_volume_cents": 120000},
    ...
  ]
}
```

### 4.2 `GET /api/world/cities/{city_id}`

返回 `{city: {...view...}, relation: {friendliness: 60}, all_fields_locked: [...]}`

按可见等级脱敏（不可见字段返回 null，前端显示 "？？？"）。

### 4.3 NPC 月推进

`POST /api/world/advance` —— 接受 `{months: 1-120}`，同时推进 NPC 城市、关系、玩家城（复用 IndustryGame.advance）。

---

## 5. 前端设计

### 5.1 页面布局（`frontend/world.html`）

```
┌──────────────────────────────────────────────┐
│ BIPOLE · 世界地图              [返回城市]    │
├──────────────────────────────────────────────┤
│ ┌─────────────────────────────┐ ┌───────────┐ │
│ │                             │ │ 城市详情  │ │
│ │   SVG 精美地图背景           │ │           │ │
│ │   - 5 NPC 节点              │ │ 禾谷镇    │ │
│ │   - 玩家城高亮              │ │ —— 友好度 60 ──│ │
│ │   - 连线 + 关系指示          │ │           │ │
│ │   - 可缩放、平移            │ │ 人口     │ │
│ │                             │ │ K/N      │ │
│ │                             │ │ L        │ │
│ │                             │ │ 主导行业 │ │
│ │                             │ │           │ │
│ └─────────────────────────────┘ └───────────┘ │
├──────────────────────────────────────────────┤
│ 当前：河湾 12月 · 推进月份总览    [推进 1 月]  │
└──────────────────────────────────────────────┘
```

### 5.2 SVG 精美背景

- 不使用外部图片
- 实现为多层渐变 + 几何装饰：
  - 底层：浅米色 + 米黄渐变 + 旧纸纹理（feTurbulence）
  - 中层：山脉轮廓（多边形）+ 河流（贝塞尔曲线）+ 道路（虚线）
  - 装饰：罗盘玫瑰、风玫瑰、装饰花边
- 渲染参考真实古地图：复古黄褐色调、装饰元素居四角

### 5.3 节点与连线

- 节点：圆形 + 城市图标（不同 archetype 不同颜色/形状）
- 玩家城：金色光晕
- 连线：粗细/颜色按 friendliness 0-100 渐变（红→橙→黄→绿→蓝）
- 鼠标 hover：节点放大 + 显示 tooltip

### 5.4 详情面板

按可见等级显示字段；缺失显示 "？？？"；底部有"提升友好度"占位按钮（提示后续路线）。

---

## 6. 详细任务

### 阶段 A：后端数据层

#### Task A1: WorldState 数据类

**Files:**
- Create: `backend/systems/v21/world_state.py`
- Create: `data/world_map.json`
- Create: `tests/v21/test_world_state.py`

- [ ] **Step 1: 写失败测试（先验证数据结构）**

```python
# tests/v21/test_world_state.py
import pytest
from backend.systems.v21.world_state import WorldState, CityNode, default_world


def test_default_world_has_five_cities():
    world = default_world()
    assert len(world.cities) == 5


def test_default_world_has_player_city():
    world = default_world()
    player = [c for c in world.cities if c.is_player]
    assert len(player) == 1


def test_city_node_coordinates_in_range():
    world = default_world()
    for c in world.cities:
        assert 0 <= c.x <= 100
        assert 0 <= c.y <= 100
```

- [ ] **Step 2: 运行测试确认失败**

```bash
pytest tests/v21/test_world_state.py -v
```

Expected: `ModuleNotFoundError`。

- [ ] **Step 3: 实现 WorldState 与 CityNode**

```python
# backend/systems/v21/world_state.py
"""世界地图状态：5-8 城市节点 + 距离矩阵 + 种子。"""
from dataclasses import dataclass
import json
from pathlib import Path

from backend.config import ROOT


@dataclass(frozen=True)
class CityNode:
    id: str
    name_cn: str
    archetype: str
    x: float
    y: float
    is_player: bool = False


@dataclass(frozen=True)
class WorldState:
    seed: int
    month: int
    year: int
    inflation_pi: float
    cities: tuple
    distance_matrix: dict


def _load_world_map() -> dict:
    path = ROOT / 'data' / 'world_map.json'
    return json.loads(path.read_text(encoding='utf-8'))


def default_world(seed: int = 20251008) -> WorldState:
    config = _load_world_map()
    cities = tuple(
        CityNode(
            id=c['id'],
            name_cn=c['name_cn'],
            archetype=c['archetype'],
            x=float(c['x']),
            y=float(c['y']),
            is_player=bool(c.get('is_player_default', False)),
        )
        for c in config['cities']
    )
    return WorldState(
        seed=seed,
        month=0,
        year=1,
        inflation_pi=0.0,
        cities=cities,
        distance_matrix=config['distance_matrix'],
    )
```

- [ ] **Step 4: 创建 data/world_map.json**

```json
{
  "cities": [
    {"id": "agricultural_town", "name_cn": "禾谷镇", "archetype": "agricultural_town", "x": 28, "y": 72},
    {"id": "industrial_city",   "name_cn": "河湾镇", "archetype": "industrial_city",   "x": 50, "y": 50, "is_player_default": true},
    {"id": "commercial_port",   "name_cn": "碧涛港", "archetype": "commercial_port",   "x": 78, "y": 30},
    {"id": "magic_academy",     "name_cn": "辉月学城", "archetype": "magic_academy",     "x": 18, "y": 28},
    {"id": "mining_border",     "name_cn": "铁壁关", "archetype": "mining_border",     "x": 75, "y": 78}
  ],
  "distance_matrix": {
    "agricultural_town|industrial_city": 240,
    "agricultural_town|commercial_port": 480,
    "agricultural_town|magic_academy":   520,
    "agricultural_town|mining_border":   380,
    "industrial_city|commercial_port":   320,
    "industrial_city|magic_academy":     360,
    "industrial_city|mining_border":     290,
    "commercial_port|magic_academy":     540,
    "commercial_port|mining_border":     410,
    "magic_academy|mining_border":       460
  }
}
```

- [ ] **Step 5: 运行测试确认通过**

```bash
pytest tests/v21/test_world_state.py -v
```

Expected: 3 passed.

- [ ] **Step 6: 提交**

```bash
git add backend/systems/v21/world_state.py data/world_map.json tests/v21/test_world_state.py
git commit -m "feat(v21): WorldState 与 CityNode 数据模型"
```

#### Task A2: NPC 后台模拟器

**Files:**
- Create: `backend/systems/v21/npc_simulator.py`
- Create: `tests/v21/test_npc_simulator.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/v21/test_npc_simulator.py
from backend.systems.v21.world_state import default_world
from backend.systems.v21.npc_simulator import NpcSimulator, NpcCityProfile


def test_initial_profiles_for_each_city():
    world = default_world()
    sim = NpcSimulator(world, seed=42)
    assert len(sim.profiles) == 5
    assert sim.profiles['industrial_city'].name_cn == '河湾镇'


def test_advance_increments_months():
    world = default_world()
    sim = NpcSimulator(world, seed=42)
    new_sim, _ = sim.advance(months=3)
    assert new_sim.world.month == 3


def test_advance_changes_profile_fields():
    world = default_world()
    sim = NpcSimulator(world, seed=42)
    new_sim, profiles = sim.advance(months=6)
    p = profiles['agricultural_town']
    assert p.population > 0
    assert 0 <= p.unemployment_rate <= 0.5
    assert 1.0 <= p.K_over_N <= 1.5
    assert p.dominant_industry in ('AGR', 'IND', 'SRV', 'CHU', 'BNK', 'MAG', 'GOV')


def test_advance_atomic_when_months_zero():
    world = default_world()
    sim = NpcSimulator(world, seed=42)
    with pytest.raises(ValueError):
        sim.advance(months=0)
```

- [ ] **Step 2: 运行测试确认失败**

```bash
pytest tests/v21/test_npc_simulator.py -v
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: 实现 NpcSimulator**

```python
# backend/systems/v21/npc_simulator.py
"""NPC 城市后台模拟：每月返回当月关键 KPI（脱敏前完整字段）。"""
import math
import random
from copy import deepcopy
from dataclasses import dataclass
from typing import Optional

from backend.systems.v21.city_presets import CITY_PRESETS
from backend.systems.v21.industry_scenario import default_scenario
from backend.systems.v21.world_state import WorldState


@dataclass(frozen=True)
class NpcCityProfile:
    id: str
    name_cn: str
    archetype: str
    population: int
    labor_force: int
    unemployment_rate: float
    K_over_N: float
    L: float
    dominant_industry: str
    food_production_units: float
    material_production_units: float
    stockpile_food_units: float
    stockpile_material_units: float
    monthly_trade_volume_cents: int


class NpcSimulator:
    """NPC 城市后台统计：每月返回每城当月 KPI，不参与玩家城经营。"""
    
    BASE_PRODUCTION = {
        'agricultural_town': (300.0, 60.0),
        'industrial_city':   (60.0, 280.0),
        'commercial_port':   (80.0, 200.0),
        'magic_academy':     (50.0, 90.0),
        'mining_border':     (40.0, 360.0),
    }
    BASE_L = {'agricultural_town': 70.0, 'industrial_city': 65.0, 'commercial_port': 72.0,
              'magic_academy': 74.0, 'mining_border': 58.0}
    
    def __init__(self, world: WorldState, seed: int = 20251008):
        if type(seed) is not int or not 0 <= seed <= 2**32 - 1:
            raise ValueError('种子须为 32 位无符号整数')
        self.world = world
        self.rng = random.Random(seed)
        self.profiles = self._initial_profiles()
    
    def _initial_profiles(self) -> dict[str, NpcCityProfile]:
        profiles = {}
        for city in self.world.cities:
            preset = CITY_PRESETS[city.archetype]
            food_p, mat_p = self.BASE_PRODUCTION[city.archetype]
            profiles[city.id] = NpcCityProfile(
                id=city.id,
                name_cn=city.name_cn,
                archetype=city.archetype,
                population=preset.N,
                labor_force=preset.F,
                unemployment_rate=preset.unemployment_rate,
                K_over_N=preset.K_over_N,
                L=self.BASE_L[city.archetype],
                dominant_industry=self._dominant_from_preset(preset),
                food_production_units=food_p,
                material_production_units=mat_p,
                stockpile_food_units=food_p * 3,
                stockpile_material_units=mat_p * 3,
                monthly_trade_volume_cents=int(preset.N * 30),
            )
        return profiles
    
    @staticmethod
    def _dominant_from_preset(preset) -> str:
        keys = ('AGR', 'IND', 'SRV', 'CHU', 'BNK', 'MAG', 'GOV')
        max_idx = max(range(7), key=lambda i: preset.employment_by_industry[i])
        return keys[max_idx]
    
    def advance(self, months: int = 1) -> tuple['NpcSimulator', dict[str, NpcCityProfile]]:
        if type(months) is not int or not 1 <= months <= 120:
            raise ValueError('推进月数须为 1-120 整数')
        candidate = deepcopy(self)
        candidate.world = deepcopy(self.world)
        candidate.world.month += months
        candidate.profiles = dict(self.profiles)
        for city in candidate.world.cities:
            profile = self.profiles[city.id]
            new_pop = profile.population
            new_food_stock = profile.stockpile_food_units
            new_mat_stock = profile.stockpile_material_units
            for _ in range(months):
                # 人口小波动
                delta = candidate.rng.randint(-30, 50)
                new_pop = max(500, new_pop + delta)
                # 库存微调
                new_food_stock = max(0, new_food_stock + candidate.rng.randint(-50, 80))
                new_mat_stock = max(0, new_mat_stock + candidate.rng.randint(-40, 60))
            # L 随机波动 ±2
            L = max(0, min(100, profile.L + candidate.rng.uniform(-2.0, 2.0)))
            labor_force = int(new_pop * (profile.labor_force / max(profile.population, 1)))
            unemployed = int(labor_force * profile.unemployment_rate)
            candidate.profiles[city.id] = NpcCityProfile(
                id=profile.id,
                name_cn=profile.name_cn,
                archetype=profile.archetype,
                population=new_pop,
                labor_force=labor_force,
                unemployment_rate=unemployed / max(1, labor_force),
                K_over_N=profile.K_over_N + candidate.rng.uniform(-0.02, 0.02),
                L=L,
                dominant_industry=profile.dominant_industry,
                food_production_units=profile.food_production_units,
                material_production_units=profile.material_production_units,
                stockpile_food_units=new_food_stock,
                stockpile_material_units=new_mat_stock,
                monthly_trade_volume_cents=profile.monthly_trade_volume_cents,
            )
        return candidate, candidate.profiles
    
    def get_profile(self, city_id: str) -> NpcCityProfile:
        return self.profiles[city_id]
```

- [ ] **Step 4: 运行测试确认通过**

```bash
pytest tests/v21/test_npc_simulator.py -v
```

Expected: 4 passed.

- [ ] **Step 5: 提交**

```bash
git add backend/systems/v21/npc_simulator.py tests/v21/test_npc_simulator.py
git commit -m "feat(v21): NPC 后台模拟器与关键 KPI 月推进"
```

#### Task A3: 关系系统

**Files:**
- Create: `backend/systems/v21/diplomacy.py`
- Create: `tests/v21/test_diplomacy.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/v21/test_diplomacy.py
from backend.systems.v21.world_state import default_world
from backend.systems.v21.diplomacy import DiplomacySystem


def test_initial_relations_for_all_city_pairs():
    world = default_world()
    dip = DiplomacySystem(world)
    pairs = [(a.id, b.id) for i, a in enumerate(world.cities) for b in world.cities[i+1:]]
    assert len(dip.relations) == len(pairs)


def test_adjust_friendliness_clamps_to_range():
    world = default_world()
    dip = DiplomacySystem(world)
    new = dip.adjust_friendliness('industrial_city', 'agricultural_town', 30, '建交')
    assert 0 <= new.friendliness <= 100


def test_visibility_levels():
    world = default_world()
    dip = DiplomacySystem(world)
    # 调高友好度到 90 应达到 level 4
    dip.adjust_friendliness('industrial_city', 'agricultural_town', 60, '盟友')
    assert dip.get_visibility('agricultural_town') == 4
    # 调低友好度到 10 应达到 level 0
    dip.adjust_friendliness('industrial_city', 'mining_border', -80, '冲突')
    assert dip.get_visibility('mining_border') == 0


def test_invalid_city_pair_raises():
    world = default_world()
    dip = DiplomacySystem(world)
    with pytest.raises(ValueError):
        dip.adjust_friendliness('industrial_city', 'unknown_city', 10, '尝试')
```

- [ ] **Step 2: 运行测试**

```bash
pytest tests/v21/test_diplomacy.py -v
```

Expected: ModuleNotFoundError.

- [ ] **Step 3: 实现 DiplomacySystem**

```python
# backend/systems/v21/diplomacy.py
"""国家关系：友好度 + 贸易量 + 事件。"""
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Optional

from backend.systems.v21.world_state import WorldState


@dataclass(frozen=True)
class Relation:
    city_a: str
    city_b: str
    friendliness: int
    trade_volume_cents: int
    last_event: str


@dataclass
class DiplomacySystem:
    world: WorldState
    player_city_id: str = 'industrial_city'
    relations: dict = field(default_factory=dict)
    
    def __post_init__(self):
        if not self.relations:
            self._init_relations()
    
    def _init_relations(self):
        cities = [c.id for c in self.world.cities]
        for i, ca in enumerate(cities):
            for cb in cities[i+1:]:
                key = self._pair_key(ca, cb)
                self.relations[key] = Relation(
                    city_a=ca,
                    city_b=cb,
                    friendliness=50,
                    trade_volume_cents=100000,
                    last_event='初次相遇',
                )
    
    @staticmethod
    def _pair_key(a: str, b: str) -> str:
        return f'{min(a,b)}|{max(a,b)}'
    
    def adjust_friendliness(self, city_a: str, city_b: str, delta: int, reason: str) -> Relation:
        if city_a not in {c.id for c in self.world.cities} or \
           city_b not in {c.id for c in self.world.cities}:
            raise ValueError(f'未知城市：{city_a} 或 {city_b}')
        key = self._pair_key(city_a, city_b)
        old = self.relations[key]
        new_friend = max(0, min(100, old.friendliness + delta))
        new_rel = Relation(
            city_a=old.city_a,
            city_b=old.city_b,
            friendliness=new_friend,
            trade_volume_cents=old.trade_volume_cents,
            last_event=reason,
        )
        self.relations[key] = new_rel
        return new_rel
    
    def relation_with_player(self, city_id: str) -> Relation:
        return self.relations[self._pair_key(self.player_city_id, city_id)]
    
    def get_visibility(self, city_id: str) -> int:
        f = self.relation_with_player(city_id).friendliness
        if f >= 81: return 4
        if f >= 61: return 3
        if f >= 41: return 2
        if f >= 21: return 1
        return 0
```

- [ ] **Step 4: 运行测试**

```bash
pytest tests/v21/test_diplomacy.py -v
```

Expected: 4 passed.

- [ ] **Step 5: 提交**

```bash
git add backend/systems/v21/diplomacy.py tests/v21/test_diplomacy.py
git commit -m "feat(v21): 关系系统（友好度 + 可见等级）"
```

### 阶段 B：API 层

#### Task B1: 视图模型与脱敏

**Files:**
- Create: `backend/systems/v21/world_view.py`
- Create: `tests/v21/test_world_view.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/v21/test_world_view.py
from backend.systems.v21.npc_simulator import NpcCityProfile
from backend.systems.v21.world_view import get_city_view


def test_visibility_level_0_only_basics():
    p = NpcCityProfile(
        id='x', name_cn='测试', archetype='industrial_city',
        population=3000, labor_force=1650, unemployment_rate=0.1,
        K_over_N=1.10, L=70.0, dominant_industry='IND',
        food_production_units=60.0, material_production_units=280.0,
        stockpile_food_units=180.0, stockpile_material_units=840.0,
        monthly_trade_volume_cents=90000,
    )
    v = get_city_view(p, 0)
    assert v['id'] == 'x'
    assert 'K_over_N' not in v
    assert 'L' not in v
    assert v['population_g'] == 3  # 千人量级


def test_visibility_level_4_full_data():
    p = NpcCityProfile(
        id='x', name_cn='测试', archetype='industrial_city',
        population=3000, labor_force=1650, unemployment_rate=0.1,
        K_over_N=1.10, L=70.0, dominant_industry='IND',
        food_production_units=60.0, material_production_units=280.0,
        stockpile_food_units=180.0, stockpile_material_units=840.0,
        monthly_trade_volume_cents=90000,
    )
    v = get_city_view(p, 4)
    assert v['K_over_N'] == 1.10
    assert v['L'] == 70.0
    assert v['unemployment_rate'] == 0.1
```

- [ ] **Step 2: 运行测试**

```bash
pytest tests/v21/test_world_view.py -v
```

Expected: ModuleNotFoundError.

- [ ] **Step 3: 实现 world_view**

```python
# backend/systems/v21/world_view.py
"""按友好度脱敏字段。"""
from backend.systems.v21.npc_simulator import NpcCityProfile


def get_city_view(profile: NpcCityProfile, visibility: int) -> dict:
    """按可见等级返回字段字典。"""
    base = {
        'id': profile.id,
        'name_cn': profile.name_cn,
        'archetype': profile.archetype,
        'population_g': profile.population // 1000 if visibility >= 0 else None,
    }
    if visibility >= 1:
        base['population_g'] = profile.population // 100
    if visibility >= 2:
        base['K_over_N'] = round(profile.K_over_N, 2)
        base['L'] = round(profile.L, 1)
        base['dominant_industry'] = profile.dominant_industry
    if visibility >= 3:
        base['food_production_units'] = round(profile.food_production_units, 0)
        base['material_production_units'] = round(profile.material_production_units, 0)
    if visibility >= 4:
        base['stockpile_food_units'] = round(profile.stockpile_food_units, 0)
        base['stockpile_material_units'] = round(profile.stockpile_material_units, 0)
        base['monthly_trade_volume_cents'] = profile.monthly_trade_volume_cents
        base['unemployment_rate'] = round(profile.unemployment_rate, 3)
    return base
```

- [ ] **Step 4: 运行测试**

```bash
pytest tests/v21/test_world_view.py -v
```

Expected: 2 passed.

- [ ] **Step 5: 提交**

```bash
git add backend/systems/v21/world_view.py tests/v21/test_world_view.py
git commit -m "feat(v21): 按友好度脱敏城市信息"
```

#### Task B2: API 端点

**Files:**
- Create: `backend/world_api.py`
- Create: `tests/v21/test_world_api.py`
- Modify: `backend/app.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/v21/test_world_api.py
from fastapi.testclient import TestClient
from backend.app import app

client = TestClient(app)


def test_state_endpoint_returns_world():
    r = client.get('/api/world/state')
    assert r.status_code == 200
    body = r.json()
    assert 'cities' in body
    assert 'links' in body
    assert len(body['cities']) == 5


def test_city_detail_returns_view():
    r = client.get('/api/world/cities/agricultural_town')
    assert r.status_code == 200
    body = r.json()
    assert body['city']['id'] == 'agricultural_town'
    assert 'relation' in body


def test_advance_increments_months():
    r = client.post('/api/world/advance', json={'months': 3})
    assert r.status_code == 200
    body = r.json()
    assert body['world']['month'] == 3
```

- [ ] **Step 2: 运行测试**

```bash
pytest tests/v21/test_world_api.py -v
```

Expected: errors.

- [ ] **Step 3: 实现 world_api**

```python
# backend/world_api.py
"""世界地图 REST 接口。"""
from copy import deepcopy
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from backend.systems.v21.world_state import default_world, CityNode
from backend.systems.v21.npc_simulator import NpcSimulator
from backend.systems.v21.diplomacy import DiplomacySystem
from backend.systems.v21.world_view import get_city_view


router = APIRouter(prefix='/api/world')


# 全局状态（单实例）；M24 实施时接入 WorldState 单例
_WORLD = default_world(seed=20251008)
_NPC_SIM = NpcSimulator(_WORLD, seed=20251008)
_DIPLOMACY = DiplomacySystem(_WORLD)


def reset_world_state_for_tests():
    global _WORLD, _NPC_SIM, _DIPLOMACY
    _WORLD = default_world(seed=20251008)
    _NPC_SIM = NpcSimulator(_WORLD, seed=20251008)
    _DIPLOMACY = DiplomacySystem(_WORLD)


@router.get('/state')
async def state():
    cities = [
        {
            'id': c.id,
            'name_cn': c.name_cn,
            'archetype': c.archetype,
            'x': c.x,
            'y': c.y,
            'is_player': c.is_player,
        }
        for c in _WORLD.cities
    ]
    links = []
    for rel in _DIPLOMACY.relations.values():
        links.append({
            'from': rel.city_a,
            'to': rel.city_b,
            'friendliness': rel.friendliness,
            'trade_volume_cents': rel.trade_volume_cents,
        })
    return {
        'world': {
            'seed': _WORLD.seed,
            'month': _WORLD.month,
            'year': _WORLD.year,
            'inflation_pi': _WORLD.inflation_pi,
        },
        'cities': cities,
        'links': links,
    }


@router.get('/cities/{city_id}')
async def city_detail(city_id: str):
    if city_id not in {c.id for c in _WORLD.cities}:
        raise HTTPException(status_code=404, detail=f'未知城市：{city_id}')
    profile = _NPC_SIM.get_profile(city_id)
    visibility = _DIPLOMACY.get_visibility(city_id)
    relation = _DIPLOMACY.relation_with_player(city_id)
    return {
        'city': get_city_view(profile, visibility),
        'relation': {
            'friendliness': relation.friendliness,
            'last_event': relation.last_event,
        },
        'visibility': visibility,
    }


class AdvanceCommand(BaseModel):
    model_config = ConfigDict(extra='forbid')
    months: StrictInt = Field(default=1, ge=1, le=120)


@router.post('/advance')
async def advance(command: AdvanceCommand, request: Request):
    global _WORLD, _NPC_SIM
    new_sim, profiles = _NPC_SIM.advance(command.months)
    _NPC_SIM = new_sim
    _WORLD = new_sim.world
    return await state()
```

- [ ] **Step 4: 在 backend/app.py 挂载 router**

```python
# 在 backend/app.py 中加入：
from backend.world_api import router as world_router
# ...
app.include_router(world_router)
```

- [ ] **Step 5: 运行测试**

```bash
pytest tests/v21/test_world_api.py -v
```

Expected: 3 passed.

- [ ] **Step 6: 提交**

```bash
git add backend/world_api.py backend/app.py tests/v21/test_world_api.py
git commit -m "feat: 世界地图 REST 接口"
```

### 阶段 C：前端骨架

#### Task C1: 页面骨架与导航

**Files:**
- Create: `frontend/world.html`
- Create: `frontend/css/world.css`
- Create: `frontend/js/world_main.js`
- Modify: `frontend/index.html`、`frontend/industry.html`

- [ ] **Step 1: 创建页面**

```html
<!-- frontend/world.html -->
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <title>BIPOLE · 世界地图</title>
  <link rel="stylesheet" href="/static/css/world.css">
</head>
<body>
  <div class="app-shell">
    <header class="topbar">
      <a class="brand" href="/">BIPOLE<span>双极 · 世界地图</span></a>
      <nav class="main-nav">
        <a href="/">返回城市</a>
        <a href="/industry">行业经营</a>
      </nav>
    </header>
    <main class="world-layout">
      <section class="map-panel" aria-label="世界地图">
        <svg id="world-svg" viewBox="0 0 1000 700" preserveAspectRatio="xMidYMid meet">
          <!-- 背景 + 节点 + 连线（由 world_map.js 渲染） -->
        </svg>
      </section>
      <aside class="detail-panel" aria-label="城市详情">
        <h2>城市详情</h2>
        <div id="detail-content">
          <p>点击地图节点查看城市。</p>
        </div>
      </aside>
    </main>
    <footer class="bottombar">
      <span id="status-line">加载中……</span>
      <button id="advance-btn">推进 1 个月 →</button>
    </footer>
  </div>
  <script type="module" src="/static/js/world_main.js"></script>
</body>
</html>
```

- [ ] **Step 2: 创建 CSS**

```css
/* frontend/css/world.css 关键样式 */
.world-layout {
  display: grid;
  grid-template-columns: 1fr 320px;
  gap: 1rem;
  padding: 1rem;
  background: linear-gradient(135deg, #1a1820, #2c2530);
  min-height: calc(100vh - 8rem);
}
.map-panel {
  background: #d4c89a;
  border: 2px solid #8b6f47;
  border-radius: 6px;
  box-shadow: 0 4px 12px rgba(0,0,0,0.4);
  overflow: hidden;
}
#world-svg { width: 100%; height: auto; display: block; }
.detail-panel {
  background: #1a1820;
  color: #f4e6c0;
  border: 2px solid #8b6f47;
  border-radius: 6px;
  padding: 1rem;
}
.detail-panel h2 { margin: 0 0 1rem; font-size: 1.2rem; }
.detail-panel .row { display: flex; justify-content: space-between; padding: 0.4rem 0; border-bottom: 1px dotted #5a3d44; }
.detail-panel .row.locked span.value { color: #888; font-style: italic; }
.detail-panel .row.locked span.value::after { content: '？？？'; }
.bottombar { display: flex; justify-content: space-between; padding: 0.5rem 1rem; background: #1a1820; color: #f4e6c0; }
```

- [ ] **Step 3: 创建 world_main.js**

```javascript
// frontend/js/world_main.js
import { renderWorld } from './world_map.js';
import { renderDetail } from './world_detail.js';

const state = { cities: [], links: [], world: null, selectedCity: null };

async function fetchState() {
  const r = await fetch('/api/world/state');
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

async function fetchCity(cityId) {
  const r = await fetch(`/api/world/cities/${cityId}`);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

async function refresh() {
  const data = await fetchState();
  state.cities = data.cities;
  state.links = data.links;
  state.world = data.world;
  renderWorld(state);
  document.getElementById('status-line').textContent = 
    `世界 · ${state.world.month} 月 · ${state.world.year} 年`;
  if (state.selectedCity) {
    const detail = await fetchCity(state.selectedCity);
    renderDetail(detail);
  }
}

document.getElementById('advance-btn').onclick = async () => {
  document.getElementById('advance-btn').disabled = true;
  await fetch('/api/world/advance', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ months: 1 }),
  });
  await refresh();
  document.getElementById('advance-btn').disabled = false;
};

window.worldState = state;
window.onCityClick = async (cityId) => {
  state.selectedCity = cityId;
  const detail = await fetchCity(cityId);
  renderDetail(detail);
};

refresh().catch(err => {
  document.getElementById('status-line').textContent = `错误：${err.message}`;
});
```

- [ ] **Step 4: 修改 index.html 和 industry.html 加入导航**

在两个页面的 topbar 中加入：`<a href="/world">世界地图</a>`

- [ ] **Step 5: 创建占位 world_map.js 与 world_detail.js**

```javascript
// frontend/js/world_map.js
export function renderWorld(state) {
  const svg = document.getElementById('world-svg');
  // 阶段 C2/C3 实现
  svg.innerHTML = '<text x="500" y="350" text-anchor="middle" fill="#8b6f47">地图加载中…</text>';
}
```

```javascript
// frontend/js/world_detail.js
export function renderDetail(detail) {
  const c = detail.city;
  const lines = [
    `<h3>${c.name_cn}（${c.archetype}）</h3>`,
    `<div class="row"><span>友好度</span><span class="value">${detail.relation.friendliness}</span></div>`,
    `<div class="row"><span>人口量级</span><span class="value">${c.population_g ?? '？？？'} 千人</span></div>`,
    `<div class="row"><span>K/N</span><span class="value">${c.K_over_N ?? '？？？'}</span></div>`,
    `<div class="row"><span>L 宜居度</span><span class="value">${c.L ?? '？？？'}</span></div>`,
    `<div class="row"><span>主导行业</span><span class="value">${c.dominant_industry ?? '？？？'}</span></div>`,
  ];
  document.getElementById('detail-content').innerHTML = lines.join('');
}
```

- [ ] **Step 6: 浏览器实测**

```bash
python -m backend.app
# 浏览器打开 http://127.0.0.1:8876/world
```

Expected: 看到页面骨架与"地图加载中…"占位。

- [ ] **Step 7: 提交**

```bash
git add frontend/world.html frontend/css/world.css frontend/js/world_main.js \
        frontend/js/world_map.js frontend/js/world_detail.js \
        frontend/index.html frontend/industry.html
git commit -m "feat(web): 世界地图页面骨架与导航"
```

#### Task C3: 精美地图背景（SVG 生成）

**Files:**
- Modify: `frontend/js/world_map.js`

- [ ] **Step 1: 实现 renderWorld 完整渲染**

```javascript
// frontend/js/world_map.js - 完整实现
const PALETTE = {
  bg_light: '#e8d9a8',
  bg_dark:  '#c9a866',
  sea:      '#7b9bb3',
  river:    '#5d7a92',
  mountain: '#6b5238',
  road:     '#8b6f47',
  city_node: '#3a2820',
  player_glow: '#d4a537',
  link_high: '#2c7a3d',
  link_low:  '#a13d2b',
};

const ARCHETYPE_COLOR = {
  agricultural_town: '#8b9d3f',
  industrial_city:   '#a87878',
  commercial_port:   '#507ba0',
  magic_academy:     '#9050a0',
  mining_border:     '#6b5238',
};

export function renderWorld(state) {
  const svg = document.getElementById('world-svg');
  svg.innerHTML = '';
  const ns = 'http://www.w3.org/2000/svg';
  
  // 1. 背景渐变
  const defs = document.createElementNS(ns, 'defs');
  defs.innerHTML = `
    <radialGradient id="bg-grad" cx="50%" cy="50%" r="60%">
      <stop offset="0%" stop-color="${PALETTE.bg_light}"/>
      <stop offset="100%" stop-color="${PALETTE.bg_dark}"/>
    </radialGradient>
    <filter id="paper-texture">
      <feTurbulence type="fractalNoise" baseFrequency="0.9" numOctaves="2"/>
      <feColorMatrix values="0 0 0 0 0.55 0 0 0 0 0.45 0 0 0 0 0.25 0 0 0 0.08 0"/>
      <feComposite in2="SourceGraphic" operator="in"/>
    </filter>
    <filter id="player-glow">
      <feGaussianBlur stdDeviation="4"/>
    </filter>
  `;
  svg.appendChild(defs);
  
  // 2. 背景
  const bg = document.createElementNS(ns, 'rect');
  bg.setAttribute('x', 0); bg.setAttribute('y', 0);
  bg.setAttribute('width', 1000); bg.setAttribute('height', 700);
  bg.setAttribute('fill', 'url(#bg-grad)');
  svg.appendChild(bg);
  
  // 3. 旧纸纹理
  const tex = document.createElementNS(ns, 'rect');
  tex.setAttribute('x', 0); tex.setAttribute('y', 0);
  tex.setAttribute('width', 1000); tex.setAttribute('height', 700);
  tex.setAttribute('fill', 'black');
  tex.setAttribute('filter', 'url(#paper-texture)');
  tex.setAttribute('opacity', '0.4');
  svg.appendChild(tex);
  
  // 4. 山脉装饰
  const mountains = document.createElementNS(ns, 'g');
  mountains.setAttribute('stroke', PALETTE.mountain);
  mountains.setAttribute('stroke-width', '2');
  mountains.setAttribute('fill', 'none');
  mountains.setAttribute('opacity', '0.6');
  for (let i = 0; i < 6; i++) {
    const x = 100 + i * 150;
    const path = document.createElementNS(ns, 'path');
    path.setAttribute('d', `M ${x},200 L ${x+40},140 L ${x+70},180 L ${x+110},120 L ${x+150},200`);
    mountains.appendChild(path);
  }
  svg.appendChild(mountains);
  
  // 5. 河流
  const river = document.createElementNS(ns, 'path');
  river.setAttribute('d', 'M 0,400 C 200,350 400,420 600,380 S 900,420 1000,400');
  river.setAttribute('stroke', PALETTE.river);
  river.setAttribute('stroke-width', '8');
  river.setAttribute('fill', 'none');
  river.setAttribute('opacity', '0.7');
  svg.appendChild(river);
  
  // 6. 装饰元素（罗盘玫瑰 + 风玫瑰 + 花边）
  const decor = document.createElementNS(ns, 'g');
  decor.innerHTML = `
    <!-- 罗盘玫瑰 -->
    <g transform="translate(900, 600)">
      <circle r="40" fill="${PALETTE.bg_light}" stroke="${PALETTE.mountain}" stroke-width="1"/>
      <polygon points="0,-38 8,0 0,38 -8,0" fill="${PALETTE.mountain}"/>
      <polygon points="38,0 0,8 -38,0 0,-8" fill="${PALETTE.road}"/>
      <text y="-50" text-anchor="middle" fill="${PALETTE.mountain}" font-size="14" font-weight="bold">N</text>
    </g>
    <!-- 装饰花边 -->
    <rect x="20" y="20" width="960" height="660" fill="none" 
          stroke="${PALETTE.mountain}" stroke-width="3" stroke-dasharray="2,4"/>
  `;
  svg.appendChild(decor);
  
  // 7. 连线
  const links = document.createElementNS(ns, 'g');
  state.links.forEach(link => {
    const a = state.cities.find(c => c.id === link.from);
    const b = state.cities.find(c => c.id === link.to);
    if (!a || !b) return;
    const ax = a.x * 10, ay = a.y * 7;
    const bx = b.x * 10, by = b.y * 7;
    const f = link.friendliness;
    const color = f >= 70 ? PALETTE.link_high : f >= 40 ? PALETTE.road : PALETTE.link_low;
    const opacity = 0.3 + f / 200;
    const width = 1 + f / 25;
    const pathEl = document.createElementNS(ns, 'line');
    pathEl.setAttribute('x1', ax); pathEl.setAttribute('y1', ay);
    pathEl.setAttribute('x2', bx); pathEl.setAttribute('y2', by);
    pathEl.setAttribute('stroke', color);
    pathEl.setAttribute('stroke-width', width);
    pathEl.setAttribute('opacity', opacity);
    links.appendChild(pathEl);
  });
  svg.appendChild(links);
  
  // 8. 城市节点
  const nodes = document.createElementNS(ns, 'g');
  state.cities.forEach(city => {
    const x = city.x * 10, y = city.y * 7;
    const g = document.createElementNS(ns, 'g');
    g.style.cursor = 'pointer';
    g.onclick = () => window.onCityClick(city.id);
    
    // 玩家城高亮
    if (city.is_player) {
      const glow = document.createElementNS(ns, 'circle');
      glow.setAttribute('cx', x); glow.setAttribute('cy', y);
      glow.setAttribute('r', 26); glow.setAttribute('fill', PALETTE.player_glow);
      glow.setAttribute('opacity', '0.4');
      glow.setAttribute('filter', 'url(#player-glow)');
      g.appendChild(glow);
    }
    
    // 节点圆
    const circle = document.createElementNS(ns, 'circle');
    circle.setAttribute('cx', x); circle.setAttribute('cy', y);
    circle.setAttribute('r', 14);
    circle.setAttribute('fill', ARCHETYPE_COLOR[city.archetype] || PALETTE.city_node);
    circle.setAttribute('stroke', '#1a1010');
    circle.setAttribute('stroke-width', 2);
    g.appendChild(circle);
    
    // 名字
    const text = document.createElementNS(ns, 'text');
    text.setAttribute('x', x); text.setAttribute('y', y + 32);
    text.setAttribute('text-anchor', 'middle');
    text.setAttribute('fill', '#1a1010');
    text.setAttribute('font-size', '14');
    text.setAttribute('font-weight', 'bold');
    text.textContent = city.name_cn;
    g.appendChild(text);
    
    nodes.appendChild(g);
  });
  svg.appendChild(nodes);
}
```

- [ ] **Step 2: 浏览器实测**

打开 /world，应看到旧地图背景 + 5 城市节点 + 连线。

- [ ] **Step 3: 提交**

```bash
git add frontend/js/world_map.js
git commit -m "feat(web): 世界地图 SVG 背景与节点连线"
```

### 阶段 D：交互

#### Task D1: 节点点击详情面板联动

**Files:**
- Modify: `frontend/js/world_detail.js`

- [ ] **Step 1: 实现完整详情渲染**

```javascript
// frontend/js/world_detail.js - 完整实现
const FIELD_LABELS = {
  population_g: '人口量级',
  K_over_N: 'K/N（承载/人口）',
  L: 'L 宜居度',
  dominant_industry: '主导行业',
  food_production_units: '粮食月产（吨）',
  material_production_units: '原料月产（吨）',
  stockpile_food_units: '粮食库存（吨）',
  stockpile_material_units: '原料库存（吨）',
  monthly_trade_volume_cents: '月贸易额（分）',
  unemployment_rate: '失业率',
};

export function renderDetail(detail) {
  const c = detail.city;
  const rel = detail.relation;
  const v = detail.visibility;
  const lines = [`<h3>${c.name_cn}（${c.archetype}）</h3>`];
  
  lines.push(`<div class="row"><span>友好度</span><span class="value">${rel.friendliness}</span></div>`);
  
  // 字段按可见等级筛选
  const fields = ['population_g', 'K_over_N', 'L', 'dominant_industry',
                  'food_production_units', 'material_production_units',
                  'stockpile_food_units', 'stockpile_material_units',
                  'monthly_trade_volume_cents', 'unemployment_rate'];
  
  fields.forEach(key => {
    const hasValue = c[key] !== undefined && c[key] !== null;
    const lockedClass = hasValue ? '' : ' locked';
    const display = hasValue ? (key === 'population_g' ? `${c[key]} 千人` :
                                 key === 'unemployment_rate' ? `${(c[key]*100).toFixed(1)}%` :
                                 key === 'monthly_trade_volume_cents' ? `${c[key]/100} CR` :
                                 c[key]) : '';
    lines.push(`<div class="row${lockedClass}"><span>${FIELD_LABELS[key]}</span><span class="value">${display}</span></div>`);
  });
  
  lines.push(`<p class="hint">当前可见等级：${v}/4（${['陌生人','认识','友好','盟友','完全开放'][v]}）</p>`);
  lines.push(`<p class="event">最近事件：${rel.last_event}</p>`);
  
  document.getElementById('detail-content').innerHTML = lines.join('');
}
```

- [ ] **Step 2: 浏览器实测**

点击节点，应看到详情面板字段；陌生城市（友好度低）显示 "？？？"。

- [ ] **Step 3: 提交**

```bash
git add frontend/js/world_detail.js
git commit -m "feat(web): 城市详情面板与脱敏 UI"
```

### 阶段 E：验收与文档

#### Task E1: 端到端浏览器测试

**Files:**
- Create: `scripts/check_world_map_browser.py`

- [ ] **Step 1: 实现 Playwright 测试**

```python
"""世界地图页面端到端测试：5 城市节点 + 节点点击详情 + 推进月份。"""
import json
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright


def main() -> int:
    artifacts = Path('artifacts/world-map-browser')
    artifacts.mkdir(parents=True, exist_ok=True)
    
    # 启动临时服务
    port = 8879
    env = {**__import__('os').environ, 'BIPOLE_PORT': str(port), 
           'BIPOLE_SAVE_DIR': str(artifacts / 'save')}
    proc = subprocess.Popen(
        ['python', '-m', 'backend.app'],
        env=env, cwd=Path.cwd(),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    time.sleep(2)
    
    errors = 0
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            ctx = browser.new_context()
            page = ctx.new_page()
            page_errors = []
            page.on('pageerror', lambda exc: page_errors.append(str(exc)))
            
            page.goto(f'http://127.0.0.1:{port}/world')
            page.wait_for_selector('#world-svg circle', timeout=10000)
            
            # 检查 5 城市节点
            nodes = page.query_selector_all('#world-svg circle')
            if len(nodes) >= 5:
                print(f'✓ 看见 {len(nodes)} 个城市节点')
            else:
                print(f'✗ 期望 ≥5 城市节点，实际 {len(nodes)}')
                errors += 1
            
            # 点击第一个节点
            first_city = page.query_selector('#world-svg g[style*="cursor"]')
            if first_city:
                first_city.click()
                page.wait_for_timeout(500)
                detail = page.query_selector('#detail-content h3')
                if detail:
                    print(f'✓ 节点点击生效，详情面板更新为：{detail.inner_text()}')
                else:
                    print('✗ 详情面板未更新')
                    errors += 1
            
            # 推进月份
            page.click('#advance-btn')
            page.wait_for_timeout(500)
            status = page.query_selector('#status-line').inner_text()
            if '1 月' in status:
                print(f'✓ 推进月份生效：{status}')
            else:
                print(f'✗ 推进月份失败：{status}')
                errors += 1
            
            page.screenshot(path=str(artifacts / 'world-map.png'), full_page=True)
            
            if page_errors:
                print(f'✗ 页面错误：{page_errors}')
                errors += 1
            
            browser.close()
    finally:
        proc.terminate()
        proc.wait()
    
    if errors:
        print(f'失败：{errors} 项')
        return 1
    print('世界地图端到端检查全部通过')
    return 0


if __name__ == '__main__':
    sys.exit(main())
```

- [ ] **Step 2: 运行测试**

```bash
python -m scripts.check_world_map_browser
```

Expected: 全部 ✓。

- [ ] **Step 3: 提交**

```bash
git add scripts/check_world_map_browser.py
git commit -m "test: 世界地图端到端浏览器验收"
```

#### Task E2: 设计文档与 CHANGELOG

**Files:**
- Create: `docs/03_设计规范/世界地图设计-V1.0.md`
- Modify: `docs/03_设计规范/游戏模块开发要求与详细设计.md`
- Modify: `CHANGELOG.md`
- Modify: `README.md`

- [ ] **Step 1: 创建设计文档**

```markdown
# 世界地图设计 V1.0

日期：2026-10-16
依据：游戏最终设计要点与校长修正-2026-10-16 §2

## 1. 范围
独立 Web 页面 /world，展示 5-8 座 NPC 城市节点、连线、关系与脱敏经济信息。

## 2. 数据模型
- WorldState：5 城市节点 + 距离矩阵 + 种子
- NpcSimulator：每月返回每座 NPC 城市的 KPI
- DiplomacySystem：友好度 + 贸易量 + 事件
- WorldView：按可见等级脱敏字段

## 3. 可见等级
- 0（陌生人）：名字 + 千人量级
- 1（认识）：百人量级
- 2（友好）：+ K/N + L + 主导行业
- 3（盟友）：+ 月产
- 4（完全开放）：+ 库存 + 贸易 + 失业率

## 4. 渲染
- SVG：渐变背景 + 旧纸纹理 + 山脉 + 河流 + 罗盘玫瑰 + 节点 + 连线
- 节点按 archetype 配色
- 玩家城金色光晕
- 连线粗细/颜色按友好度

## 5. 交互
- 点击节点 → 详情面板（按可见等级显示）
- 推进 1 月 → NPC 月推进 + 全局状态刷新

## 6. 边界
- NPC 城市后台模拟不参与玩家城经营
- 友好度独立实现，不依赖事件系统
- 跨城贸易不在本模块范围
```

- [ ] **Step 2: 在游戏模块开发要求与详细设计.md 加入 M25**

参考 M11 格式：

```markdown
<a id="m25"></a>
## M25 世界地图

**阶段/来源：** 新增 P11 子任务；G Q6/25、U 2026-10-16 决定。

- **入口：** 顶部导航新增"世界地图"，独立页面 /world。
- **范围：** 5 NPC 城市节点 + 连线 + 关系；NPC 城市后台统计运行，不绘制内部。
- **数据：** WorldState + NpcSimulator + DiplomacySystem + WorldView（脱敏）。
- **可见等级：** 0-4 由友好度决定；字段脱敏 UI 显示 "？？？"。
- **验收：** 真实浏览器测试 5 城市节点 + 节点点击详情 + 推进月份。
- **不在范围：** 跨城贸易、真实事件系统、NPC 资产细化。

详细数据模型与可见等级参见 [世界地图设计 V1.0](世界地图设计-V1.0.md)。
```

并在目录里加入 M25。

- [ ] **Step 3: 更新 CHANGELOG**

```markdown
- 2026-10-16：**世界地图界面 M25**：新增独立 /world 页面，含 5 NPC 城市节点、SVG 精美地图背景、关系连线、按友好度脱敏的详情面板、NPC 后台模拟器与友好度系统。
```

- [ ] **Step 4: 更新 README**

在 README §2 加入："**查看世界地图**：从城市地图或行业经营顶部导航进入 /world，可看到全国 5 座城市的节点位置、关系连线与基于友好度的经济信息预览。"

- [ ] **Step 5: 提交**

```bash
git add docs/03_设计规范/世界地图设计-V1.0.md \
        docs/03_设计规范/游戏模块开发要求与详细设计.md \
        CHANGELOG.md README.md
git commit -m "docs: 世界地图设计 V1.0 与 M25 模块说明"
```

---

## 7. 验收清单

任务全部勾选完成时，运行：

```bash
python -m pytest -q                      # 全部测试通过
python -m scripts.check_world_map_browser  # 端到端浏览器验收
python -m scripts.check_industry_gui     # 桌面验收仍通过
python -m scripts.check_single_city_browser  # 原地图验收仍通过
```

期望：
- pytest 全部通过（含 25+ 新增测试）
- 世界地图端到端 4 项检查全部通过
- 原有验收不回归

---

## 8. 工作量估算

| 阶段 | 任务 | 预估时间 |
|---|---|---|
| A1 | WorldState | 0.5 天 |
| A2 | NpcSimulator | 1.0 天 |
| A3 | DiplomacySystem | 1.0 天 |
| B1 | WorldView | 0.5 天 |
| B2 | WorldApi | 0.5 天 |
| C1 | 页面骨架 | 0.5 天 |
| C3 | 精美地图背景 | 1.0 天 |
| D1 | 节点点击详情 | 0.5 天 |
| E1 | 端到端测试 | 0.5 天 |
| E2 | 文档 | 0.5 天 |
| **合计** | | **6.5 天** |

并行空间：A 与 C 可并行（前端可先用 mock 数据开发）；B 与 C 后半可并行。

---

## 9. 风险与边界

- **NPC 月推进不参与玩家城经营**：本模块不修改 IndustryEconomy；不引入跨城资金流。
- **友好度独立于事件系统**：本模块不引入 M18 议会与提案；友好度调整接口预留但 P11 阶段不接事件。
- **精美地图背景不依赖外部美术资源**：纯 SVG 代码生成；如老大后续要更精美美术，可换为图片资源，但本模块不阻塞。
- **5 城固定**：本模块只实现 5 城；扩展到 8 城仅需修改 data/world_map.json。
- **世界地图独立页面**：不与地图游戏 /industry 共享后端 IndustryGame；通过单独的玩家城市状态同步（不引入 WorldState 单例复杂度）。