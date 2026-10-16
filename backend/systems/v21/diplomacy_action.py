"""M26 玩家外交行动：5 类行动 + 限制 + 友好度变化。

设计要点：
- M26 是 M25/M24 之外的设计：让玩家主动改变 NPC 城市友好度，把 NPC 从旁观者变成可交互对手。
- 外交行动只修改 DiplomacySystem.relations，不修改玩家城市现金流/库存。
  现金与"援助"行动的成本校验：玩家先调 /api/world/diplomacy_action，
  服务端校验 cash['GOV'] ≥ cost_cr，调一次 confirm_cash_payment(city, cost_cr)，
  IndustryGame._apply_purchase_external 类似路径（避免破坏守恒）。
- 友好度变化：单次行动最多 +/-2 友好度（除 tariff 是 -10）。
- 限制：每月 max_per_month 次；连续 cooldown_months 月不可重复。
- 月内 + cooldown 历史存为 DiplomacyAction（写入 DiplomacySystem.player_actions_history）。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


def _default_config_path() -> Path:
    return Path(__file__).resolve().parents[3] / 'data' / 'diplomacy_actions.json'


@dataclass(frozen=True)
class ActionConfig:
    """单类外交行动配置。"""
    key: str
    label: str
    description: str
    cost_cr: float | str  # 数字 = 固定成本；"user_supplied" = 由玩家传入 cost_cr
    monthly_cost_cr: float = 0.0
    friendliness_delta_per_month: int = 0
    cooldown_months: int = 0
    max_per_month: int = 1
    requires_visibility: int = 0  # 最小可见等级要求


def load_diplomacy_actions(config_path: Path | None = None) -> dict[str, ActionConfig]:
    if config_path is None:
        config_path = _default_config_path()
    raw = json.loads(Path(config_path).read_text(encoding='utf-8'))
    actions: dict[str, ActionConfig] = {}
    for key, cfg in raw['actions'].items():
        actions[key] = ActionConfig(
            key=key,
            label=cfg.get('label', key),
            description=cfg.get('description', ''),
            cost_cr=cfg.get('cost_cr', 0),
            monthly_cost_cr=cfg.get('monthly_cost_cr', 0),
            friendliness_delta_per_month=cfg.get('friendliness_delta_per_month', 0),
            cooldown_months=cfg.get('cooldown_months', 0),
            max_per_month=cfg.get('max_per_month', 1),
            requires_visibility=cfg.get('requires_visibility', 0),
        )
    return actions


@dataclass
class DiplomacyAction:
    """一次玩家外交行动记录。"""
    action_key: str
    target_city_id: str
    month: int
    cost_cr: float
    friendliness_delta: int
    note: str = ''
    success: bool = True
    reject_reason: str = ''

    def to_dict(self) -> dict[str, Any]:
        return {
            'action_key': self.action_key,
            'target_city_id': self.target_city_id,
            'month': self.month,
            'cost_cr': self.cost_cr,
            'friendliness_delta': self.friendliness_delta,
            'note': self.note,
            'success': self.success,
            'reject_reason': self.reject_reason,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> 'DiplomacyAction':
        return cls(
            action_key=data['action_key'],
            target_city_id=data['target_city_id'],
            month=int(data['month']),
            cost_cr=float(data.get('cost_cr', 0)),
            friendliness_delta=int(data.get('friendliness_delta', 0)),
            note=str(data.get('note', '')),
            success=bool(data.get('success', True)),
            reject_reason=str(data.get('reject_reason', '')),
        )


class DiplomacyActionValidator:
    """校验玩家外交行动合法性。"""

    def __init__(self, actions: dict[str, ActionConfig] | None = None):
        self.actions = actions or load_diplomacy_actions()

    def validate(self, action: DiplomacyAction, current_month: int,
                 visibility: int, history: list[DiplomacyAction],
                 player_cash_cr: float) -> tuple[bool, str, int]:
        """返回 (success, reject_reason, computed_delta)。"""
        if action.action_key not in self.actions:
            return False, f'未知行动类型：{action.action_key}', 0
        cfg = self.actions[action.action_key]
        # 可见等级校验
        if visibility < cfg.requires_visibility:
            return False, f'可见等级不足：需要 {cfg.requires_visibility}，当前 {visibility}', 0
        # 月内次数限制
        same_month = [h for h in history
                      if h.action_key == action.action_key
                      and h.month == current_month
                      and h.success]
        if len(same_month) >= cfg.max_per_month:
            return False, f'本月已用尽：{cfg.label}（上限 {cfg.max_per_month} 次/月）', 0
        # 冷却期
        if cfg.cooldown_months > 0:
            cooldown = [h for h in history
                        if h.action_key == action.action_key
                        and h.success
                        and current_month - h.month < cfg.cooldown_months]
            if cooldown:
                earliest = min(h.month for h in cooldown)
                return False, f'冷却中：{cfg.label}（剩 {cfg.cooldown_months - (current_month - earliest)} 月）', 0
        # 现金成本
        cost = action.cost_cr
        if isinstance(cfg.cost_cr, (int, float)):
            cost = float(cfg.cost_cr)
        elif cfg.cost_cr == 'user_supplied_narrative':
            # 叙事援助：不实际扣款，仅校验 amount 合法
            cost = action.cost_cr
        if cost < 0:
            return False, '金额不能为负', 0
        # aid 是叙事动作，不校验玩家现金（避免破坏经济守恒）
        if action.action_key != 'aid' and isinstance(cfg.cost_cr, (int, float)):
            if cost > player_cash_cr:
                return False, f'政府现金不足：需要 {cost:.0f} CR，当前 {player_cash_cr:.0f} CR', 0
        # 计算友好度变化（aid 按 100 CR → +1 上取整；其他按配置 delta）
        if action.action_key == 'aid':
            delta = max(1, int(-(-action.cost_cr // 100)))  # ceil(amount / 100)
        else:
            delta = cfg.friendliness_delta_per_month
        return True, '', delta