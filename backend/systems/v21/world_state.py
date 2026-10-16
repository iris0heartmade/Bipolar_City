"""世界地图状态：5 城市节点 + 距离矩阵 + 世界级参数。

参数分层（2026-10-16 用户明确）：
- 世界级：本模块持有。所有城市共享。例：通胀率、参考消费、距离矩阵。
- 国家级：派生自世界级参数与玩家/NPC 城市的关系。
- 城市级：每座城市各自独立（scenario、人口、就业、政策等）。

玩家城市与 NPC 城市各自持有独立的 IndustryEconomy 与 scenario；
本模块只同步世界级参数。
"""
from dataclasses import dataclass, field
import json
from typing import Optional

from backend.config import ROOT, read_json


@dataclass(frozen=True)
class CityNode:
    """世界地图上的城市节点（不含城市级参数）。"""
    id: str
    name_cn: str
    archetype: str
    x: float
    y: float
    is_player: bool = False


@dataclass(frozen=True)
class WorldState:
    """世界级参数：所有城市共享。

    不包含任何城市级数据（人口、就业、scenario）。
    """
    seed: int
    month: int
    year: int
    inflation_pi: float
    reference_consumption_cr: float
    cities: list = field(default_factory=list)
    distance_matrix: dict = field(default_factory=dict)

    def get_city(self, city_id: str) -> Optional[CityNode]:
        for c in self.cities:
            if c.id == city_id:
                return c
        return None

    def player_city(self) -> CityNode:
        for c in self.cities:
            if c.is_player:
                return c
        raise ValueError('未配置玩家城市')

    def distance(self, city_a: str, city_b: str) -> int:
        key = '|'.join(sorted([city_a, city_b]))
        return self.distance_matrix.get(key, 0)


def _load_world_map() -> dict:
    path = ROOT / 'data' / 'world_map.json'
    return json.loads(path.read_text(encoding='utf-8'))


def default_world(seed: int = 20251008) -> WorldState:
    """从 data/world_map.json 构造默认世界（5 城固定）。"""
    if type(seed) is not int or not 0 <= seed <= 2**32 - 1:
        raise ValueError('seed 须为 32 位无符号整数')
    config = _load_world_map()
    cities = [
        CityNode(
            id=c['id'],
            name_cn=c['name_cn'],
            archetype=c['archetype'],
            x=float(c['x']),
            y=float(c['y']),
            is_player=bool(c.get('is_player_default', False)),
        )
        for c in config['cities']
    ]
    return WorldState(
        seed=seed,
        month=0,
        year=1,
        inflation_pi=0.0,
        reference_consumption_cr=50.0,
        cities=cities,
        distance_matrix=config['distance_matrix'],
    )