"""M27 NPC-NPC 贸易网：按友好度+距离生成月度贸易流。

设计要点：
- M27 是 M26 之后的"叙事层扩展"，不引入跨城现金流转账，保留 IndustryEconomy 守恒完整性。
- 友好度 ≥ threshold 的 NPC-NPC 对每月产生 trade volume（CR 叙事）。
- trade volume 由 base_volume × (friendly / 100) × distance_factor 推导。
- 每月推进时调 DiplomacySystem.adjust_friendliness(pair, +0.1, "贸易流月增")（双向）。
- 贸易流存为 list[NpcTradeFlow]，通过 /api/world/state 暴露给前端叠加渲染。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def _default_config_path() -> Path:
    return Path(__file__).resolve().parents[3] / 'data' / 'npc_trade_routes.json'


@dataclass(frozen=True)
class NpcTradeConfig:
    base_volume_per_month_cr: float = 1000.0
    friendliness_threshold: int = 30
    distance_factor_divisor: float = 100.0
    trade_friendliness_delta_per_month: float = 0.1
    min_volume_per_month_cr: float = 100.0
    max_volume_per_month_cr: float = 5000.0


def load_npc_trade_config(config_path: Path | None = None) -> NpcTradeConfig:
    if config_path is None:
        config_path = _default_config_path()
    raw = json.loads(Path(config_path).read_text(encoding='utf-8'))
    cfg = raw.get('trade_route', {})
    return NpcTradeConfig(
        base_volume_per_month_cr=float(cfg.get('base_volume_per_month_cr', 1000)),
        friendliness_threshold=int(cfg.get('friendliness_threshold', 30)),
        distance_factor_divisor=float(cfg.get('distance_factor_divisor', 100)),
        trade_friendliness_delta_per_month=float(cfg.get('trade_friendliness_delta_per_month', 0.1)),
        min_volume_per_month_cr=float(cfg.get('min_volume_per_month_cr', 100)),
        max_volume_per_month_cr=float(cfg.get('max_volume_per_month_cr', 5000)),
    )


@dataclass
class NpcTradeFlow:
    """一对 NPC 城市在某月的贸易流。"""
    city_a: str
    city_b: str
    month: int
    volume_cr: float
    friendliness_delta_per_side: int  # 每端友好度 +0（整数化）

    def to_dict(self) -> dict[str, Any]:
        return {
            'city_a': self.city_a,
            'city_b': self.city_b,
            'month': self.month,
            'volume_cr': self.volume_cr,
            'friendliness_delta_per_side': self.friendliness_delta_per_side,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> 'NpcTradeFlow':
        return cls(
            city_a=str(data['city_a']),
            city_b=str(data['city_b']),
            month=int(data['month']),
            volume_cr=float(data['volume_cr']),
            friendliness_delta_per_side=int(data.get('friendliness_delta_per_side', 0)),
        )


class NpcTradeRouter:
    """NPC-NPC 贸易流路由器。"""

    def __init__(self, config: NpcTradeConfig | None = None):
        self.config = config or load_npc_trade_config()

    def compute_distance_factor(self, distance_units: float) -> float:
        """距离因子：1.0 / (1 + distance / divisor)。距离 0 时为 1.0。"""
        return 1.0 / (1.0 + max(0.0, distance_units) / self.config.distance_factor_divisor)

    def monthly_flow(self, city_a_id: str, city_b_id: str,
                     distance_units: float, friendliness: int) -> NpcTradeFlow | None:
        """计算一对 NPC 的月贸易流。友好度低于阈值返回 None。"""
        if friendliness < self.config.friendliness_threshold:
            return None
        distance_factor = self.compute_distance_factor(distance_units)
        raw_volume = self.config.base_volume_per_month_cr * (friendliness / 100.0) * distance_factor
        volume = max(self.config.min_volume_per_month_cr,
                     min(self.config.max_volume_per_month_cr, raw_volume))
        return NpcTradeFlow(
            city_a=city_a_id,
            city_b=city_b_id,
            month=0,  # 由 caller 填入当月
            volume_cr=round(volume, 2),
            friendliness_delta_per_side=round(self.config.trade_friendliness_delta_per_month * 10)
                                          // 10,  # 保留 1 位小数 -> 0
        )

    def route_all(self, npc_city_ids: list[str], city_positions: dict[str, tuple[float, float]],
                  friendliness_lookup, current_month: int) -> list[NpcTradeFlow]:
        """对所有 NPC 城市对生成月贸易流。

        参数：
        - npc_city_ids: NPC 城市 ID 列表（不含玩家城市）
        - city_positions: {city_id: (x, y)} 坐标字典（viewBox 坐标）
        - friendliness_lookup: callable(city_a, city_b) -> int（友好度 0-100）
        - current_month: 当前世界月
        """
        flows: list[NpcTradeFlow] = []
        for i, a in enumerate(npc_city_ids):
            for b in npc_city_ids[i + 1:]:
                pos_a = city_positions.get(a)
                pos_b = city_positions.get(b)
                if pos_a is None or pos_b is None:
                    continue
                distance = ((pos_a[0] - pos_b[0]) ** 2 + (pos_a[1] - pos_b[1]) ** 2) ** 0.5
                friendliness = friendliness_lookup(a, b)
                flow = self.monthly_flow(a, b, distance, friendliness)
                if flow is None:
                    continue
                flow.month = current_month
                flows.append(flow)
        return flows