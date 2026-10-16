"""M28 行业情报：派间谍获取 NPC 完整 last_report。

设计要点：
- 派间谍需友好度 ≥ 3（盟友级，friendly ≥ 61）。
- 成本 2000 CR（一次性，不退还）。
- 风险：被发现概率按友好度分级。friendly ≥ 70 → 10%；friendly < 70 → 30%。
- 成功：返回完整 last_report（不脱敏），含 sectors / transactions / housing 等。
- 失败：被发现 → friendly -20（友好度惩罚），成本不退还，无情报。
- 月限：每城市 1 次；cooldown 1 月。
- 不修改现金（aid 同样设计：M28 阶段只是叙事"派出"），避免破坏 IndustryEconomy 守恒。
"""
from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def _default_config_path() -> Path:
    return Path(__file__).resolve().parents[3] / 'data' / 'espionage_config.json'


@dataclass(frozen=True)
class EspionageConfig:
    cost_cr: float = 2000.0
    min_visibility: int = 3
    discovery_probability_max: float = 0.30
    discovery_probability_low: float = 0.10
    discovery_friendliness_threshold: int = 70
    discovery_penalty_friendliness: int = -20
    cooldown_months: int = 1
    max_per_month: int = 1


def load_espionage_config(config_path: Path | None = None) -> EspionageConfig:
    if config_path is None:
        config_path = _default_config_path()
    raw = json.loads(Path(config_path).read_text(encoding='utf-8'))
    cfg = raw.get('espionage', {})
    return EspionageConfig(
        cost_cr=float(cfg.get('cost_cr', 2000)),
        min_visibility=int(cfg.get('min_visibility', 3)),
        discovery_probability_max=float(cfg.get('discovery_probability_max', 0.30)),
        discovery_probability_low=float(cfg.get('discovery_probability_low', 0.10)),
        discovery_friendliness_threshold=int(cfg.get('discovery_friendliness_threshold', 70)),
        discovery_penalty_friendliness=int(cfg.get('discovery_penalty_friendliness', -20)),
        cooldown_months=int(cfg.get('cooldown_months', 1)),
        max_per_month=int(cfg.get('max_per_month', 1)),
    )


@dataclass
class EspionageAttempt:
    target_city_id: str
    month: int
    cost_cr: float
    success: bool
    discovered: bool = False
    friendliness_delta: int = 0
    note: str = ''

    def to_dict(self) -> dict[str, Any]:
        return {
            'target_city_id': self.target_city_id,
            'month': self.month,
            'cost_cr': self.cost_cr,
            'success': self.success,
            'discovered': self.discovered,
            'friendliness_delta': self.friendliness_delta,
            'note': self.note,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> 'EspionageAttempt':
        return cls(
            target_city_id=str(data['target_city_id']),
            month=int(data['month']),
            cost_cr=float(data.get('cost_cr', 0)),
            success=bool(data.get('success', False)),
            discovered=bool(data.get('discovered', False)),
            friendliness_delta=int(data.get('friendliness_delta', 0)),
            note=str(data.get('note', '')),
        )


def validate_espionage(cfg: EspionageConfig, target_city_id: str,
                        current_month: int, visibility: int,
                        history: list[EspionageAttempt]) -> tuple[bool, str]:
    """校验派遣条件是否满足。"""
    if visibility < cfg.min_visibility:
        return False, f'可见等级不足：需要 {cfg.min_visibility}（盟友级），当前 {visibility}'
    # 月内次数
    same_month = [h for h in history
                  if h.target_city_id == target_city_id
                  and h.month == current_month]
    if len(same_month) >= cfg.max_per_month:
        return False, f'本月已用尽：情报（上限 {cfg.max_per_month} 次/月）'
    # 冷却期：cooldown_months=1 含义为"1 月内不能再触发"
    if cfg.cooldown_months > 0:
        cooldown = [h for h in history
                    if h.target_city_id == target_city_id
                    and current_month - h.month <= cfg.cooldown_months]
        if cooldown:
            earliest = min(h.month for h in cooldown)
            remaining = cfg.cooldown_months - (current_month - earliest)
            return False, f'冷却中：剩 {max(0, remaining)} 月'
    return True, ''


def attempt_espionage(cfg: EspionageConfig, target_city_id: str,
                      current_month: int, friendliness: int,
                      rng: random.Random | None = None) -> EspionageAttempt:
    """执行一次间谍派遣：按友好度决定被发现概率。

    friendly ≥ threshold → 10%；friendly < threshold → 30%。
    - 成功 + 未发现：返回情报，cost 已付
    - 成功 + 发现：友好度 -20
    - 失败：完全没情报
    """
    rng = rng or random.Random()
    if friendliness >= cfg.discovery_friendliness_threshold:
        discovery_prob = cfg.discovery_probability_low
    else:
        discovery_prob = cfg.discovery_probability_max
    discovered = rng.random() < discovery_prob
    # M28 阶段简化：被发现 = 没情报（即使没被发现也不一定拿到完整 last_report，但本次实现：100% 拿到）
    if discovered:
        return EspionageAttempt(
            target_city_id=target_city_id, month=current_month,
            cost_cr=cfg.cost_cr, success=False, discovered=True,
            friendliness_delta=cfg.discovery_penalty_friendliness,
            note=f'被发现（{discovery_prob:.0%} 概率触发），友好度惩罚',
        )
    return EspionageAttempt(
        target_city_id=target_city_id, month=current_month,
        cost_cr=cfg.cost_cr, success=True, discovered=False,
        friendliness_delta=0,
        note=f'情报成功（被发现概率 {discovery_prob:.0%}）',
    )