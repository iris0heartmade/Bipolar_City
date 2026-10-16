"""NPC 城市多城调度器：每座 NPC 城市持独立 IndustryEconomy 实例 + AiMayor。

参数分层（2026-10-16 用户明确）：
- 每座 NPC 城市有自己的 scenario、行业、就业、人口、政策、税率；
- AI 市长调税率只影响自己城市；
- 世界级参数（inflation_pi、reference_consumption_cr）通过 advance 共享推进；
- NPC 城市的经营状态不与玩家城市相互影响（独立 IndustryEconomy 实例）。
"""
import math
import random
from copy import deepcopy
from dataclasses import dataclass, field, replace
from typing import Optional

from backend.systems.v21.city_presets import CITY_PRESETS
from backend.systems.v21.industry_economy import IndustryEconomy
from backend.systems.v21.industry_scenario import default_scenario, split_integer
from backend.systems.v21.mayor_preferences import (
    MayorPreferences, default_preferences, get_preferences, reset_cache_for_tests,
)
from backend.systems.v21.ai_mayor import AiMayor
from backend.systems.v21.world_state import WorldState


_EMP_KEYS = ('AGR', 'IND', 'SRV', 'CHU', 'BNK', 'MAG', 'GOV')


@dataclass
class NpcCityEngine:
    """多城调度器：持有 NPC 城市的 IndustryEconomy 与 AiMayor。"""
    world: WorldState
    cities: dict = field(default_factory=dict)
    mayors: dict = field(default_factory=dict)
    ai_log: dict = field(default_factory=dict)
    rng: random.Random = field(default=None)
    
    def __post_init__(self):
        if self.rng is None:
            self.rng = random.Random(self.world.seed)
    
    @classmethod
    def from_world(cls, world: WorldState) -> 'NpcCityEngine':
        """从 WorldState 构造：每座 NPC 城市用其 archetype 初始化独立 IndustryEconomy。"""
        engine = cls(world=world)
        for node in world.cities:
            if node.is_player:
                continue
            city_engine = _build_npc_economy(node.archetype)
            engine.cities[node.id] = city_engine
            prefs = get_preferences(node.id)
            engine.mayors[node.id] = AiMayor(city_engine, prefs)
            engine.ai_log[node.id] = []
        return engine
    
    def advance(self, months: int = 1) -> 'NpcCityEngine':
        """推进 NPC 城市 monthly。

        月结顺序（每 NPC 城市独立）：
          1. AI 市长 step()（月结前）
          2. city.advance()（正式月结）
        """
        if type(months) is not int or not 1 <= months <= 120:
            raise ValueError('推进月数须为 1-120 整数')
        new_engine = deepcopy(self)
        new_engine.world = deepcopy(self.world)
        new_engine.cities = dict(self.cities)
        new_engine.mayors = dict(self.mayors)
        new_engine.ai_log = {k: list(v) for k, v in self.ai_log.items()}
        for _ in range(months):
            for cid, city in new_engine.cities.items():
                mayor = new_engine.mayors.get(cid)
                if mayor is not None:
                    results = mayor.step()
                    if results:
                        new_engine.ai_log[cid].extend(results)
                city.advance()
        new_engine.world = replace(new_engine.world, month=new_engine.world.month + months)
        return new_engine
    
    def get_city(self, city_id: str) -> Optional[IndustryEconomy]:
        return self.cities.get(city_id)
    
    def recent_ai_log(self, city_id: str, limit: int = 12) -> list[dict]:
        log = self.ai_log.get(city_id, [])
        return list(log[-limit:])


def _build_npc_economy(archetype: str) -> IndustryEconomy:
    """为指定 archetype 构造 IndustryEconomy，并按 preset 重分配 7 行业就业。

    返回的城市**只**用于 NPC；scenario 不与玩家城市共享。

    守恒保证：
    - 总就业 ≤ labor_force（保留失业池）
    - 每个群体就业 ≤ population
    - 总就业 + 总失业 = labor_force
    """
    scenario = default_scenario()
    city = IndustryEconomy(scenario=scenario)
    preset = CITY_PRESETS.get(archetype)
    if preset is None:
        return city

    target_emp = dict(zip(_EMP_KEYS, preset.employment_by_industry))
    target_total = sum(target_emp.values())
    if target_total == 0:
        return city

    labor_force = city.labor_force
    # 缩 target_emp 保证不超过 labor_force（至少留 5% 失业池）
    max_target_total = max(1, int(labor_force * 0.95))
    if target_total > max_target_total:
        scale = max_target_total / target_total
        for i in target_emp:
            target_emp[i] = max(1, int(target_emp[i] * scale))

    total_pop = sum(city.population.values())
    if total_pop == 0:
        return city

    # 按 population 比例分配每个行业就业到各群体
    for i, target in target_emp.items():
        for h in city.population:
            share = city.population[h] / total_pop
            city.workers[h][i] = int(round(target * share))

    # 每个群体就业 ≤ population；超额按群体比例缩
    for h in city.population:
        row_total = sum(city.workers[h].values())
        pop = city.population[h]
        if row_total > pop:
            scale_factor = pop / row_total
            for i in _EMP_KEYS:
                city.workers[h][i] = int(city.workers[h][i] * scale_factor)
            new_total = sum(city.workers[h].values())
            big = max(_EMP_KEYS, key=lambda k: city.workers[h][k])
            city.workers[h][big] += pop - new_total

    new_employed_total = sum(sum(row.values()) for row in city.workers.values())
    new_unemployed_total = labor_force - new_employed_total

    if new_unemployed_total < 0:
        # 仍超：按比例缩到 labor_force
        scale_factor = labor_force / new_employed_total
        for h in city.workers:
            for i in _EMP_KEYS:
                city.workers[h][i] = int(city.workers[h][i] * scale_factor)
        new_employed_total = sum(sum(row.values()) for row in city.workers.values())
        new_unemployed_total = labor_force - new_employed_total

    spare = {h: city.population[h] - sum(row.values()) for h, row in city.workers.items()}
    city.unemployed_by_group = split_integer(new_unemployed_total, spare)

    actual = sum(sum(r.values()) for r in city.workers.values()) + sum(city.unemployed_by_group.values())
    if actual < labor_force:
        # 差值补到 H7 失业池
        city.unemployed_by_group['H7'] += labor_force - actual
    elif actual > labor_force:
        # 超值从 H7 失业池扣
        diff = actual - labor_force
        reduce = min(city.unemployed_by_group['H7'], diff)
        city.unemployed_by_group['H7'] -= reduce
        # 仍超则继续缩 H7 的最大行业就业
        residual = diff - reduce
        if residual > 0:
            big = max(_EMP_KEYS, key=lambda k: city.workers['H7'][k])
            city.workers['H7'][big] -= residual

    city.assert_invariants()
    return city


def reset_preferences_cache_for_tests():
    reset_cache_for_tests()