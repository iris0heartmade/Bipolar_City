"""国家关系系统：友好度 + 贸易量 + 事件。

参数分层：
- 国家级：与所有城市相关的元数据（友好度、贸易量、关系事件）。
  每对城市一个 Relation 实例，跨 player/NPC 城市与 NPC 城市之间。
- 不修改城市级 scenario。
"""
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class Relation:
    """两座城市之间的关系。"""
    city_a: str
    city_b: str
    friendliness: int        # 0-100；50 = 中立
    last_event: str           # 最近一次友好度调整的原因


class DiplomacySystem:
    """管理所有城市对之间的关系 + 友好度调整。"""
    
    def __init__(self, world_state, player_city_id: str = 'industrial_city',
                 initial_friendliness: int = 50):
        if not (0 <= initial_friendliness <= 100):
            raise ValueError('初始友好度须为 0-100 整数')
        self.world_state = world_state
        self.player_city_id = player_city_id
        self._initial_friendliness = initial_friendliness
        self.relations: dict = {}  # 'cityA|cityB' -> Relation
        # M26：玩家外交行动历史（最近 N 条）
        self._player_actions_history: list = []
        self._init_relations()
    
    def _init_relations(self):
        cities = [c.id for c in self.world_state.cities]
        for i, a in enumerate(cities):
            for b in cities[i + 1:]:
                key = self._pair_key(a, b)
                self.relations[key] = Relation(
                    city_a=a, city_b=b,
                    friendliness=self._initial_friendliness,
                    last_event='初次接触',
                )

    # M26：玩家外交行动
    def player_actions_history(self, limit: int = 50) -> list:
        """最近 N 条玩家行动（按时间倒序）。"""
        return list(reversed(self._player_actions_history[-limit:]))

    def apply_player_action(self, action, current_month: int,
                            player_cash_cr: float = None) -> tuple:
        """应用一次玩家外交行动。

        返回 (success: bool, reject_reason: str, action_record: DiplomacyAction)。
        不修改 cash；现金扣款由 caller 负责（避免引入对 IndustryGame 的循环引用）。
        """
        from backend.systems.v21.diplomacy_action import DiplomacyActionValidator
        if player_cash_cr is None:
            player_cash_cr = 0.0
        validator = DiplomacyActionValidator()
        visibility = self.get_visibility(action.target_city_id)
        ok, reason, delta = validator.validate(
            action=action, current_month=current_month,
            visibility=visibility, history=self._player_actions_history,
            player_cash_cr=player_cash_cr,
        )
        if not ok:
            record = type(action)(  # 同 type 工厂
                action_key=action.action_key,
                target_city_id=action.target_city_id,
                month=current_month,
                cost_cr=action.cost_cr,
                friendliness_delta=0,
                note=action.note,
                success=False,
                reject_reason=reason,
            )
            self._record_action(record)
            return False, reason, record
        # 调友好度
        action_label_map = {
            'trade_agreement': '签订贸易协议',
            'aid': '援助',
            'tariff': '征收关税',
            'immigration_open': '开放移民',
            'statement': '公开声明',
        }
        label = action_label_map.get(action.action_key, action.action_key)
        reason_text = f'{label}（{action.note or "玩家行动"}）'
        self.adjust_friendliness(self.player_city_id, action.target_city_id, delta, reason_text)
        # 记录
        record = type(action)(
            action_key=action.action_key,
            target_city_id=action.target_city_id,
            month=current_month,
            cost_cr=action.cost_cr,
            friendliness_delta=delta,
            note=action.note,
            success=True,
            reject_reason='',
        )
        self._record_action(record)
        return True, '', record

    def _record_action(self, action):
        self._player_actions_history.append(action)
        # 保留最近 200 条（玩家行动频率低，不需要无限保留）
        self._player_actions_history = self._player_actions_history[-200:]
    
    @staticmethod
    def _pair_key(a: str, b: str) -> str:
        return f'{min(a, b)}|{max(a, b)}'
    
    def adjust_friendliness(self, city_a: str, city_b: str, delta: int, reason: str) -> Relation:
        if not isinstance(delta, int):
            raise ValueError(f'友好度调整量须为整数，得 {delta}')
        if not reason or not isinstance(reason, str):
            raise ValueError('友好度调整必须提供原因')
        city_ids = {c.id for c in self.world_state.cities}
        if city_a not in city_ids or city_b not in city_ids:
            raise ValueError(f'未知城市：{city_a} 或 {city_b}')
        key = self._pair_key(city_a, city_b)
        old = self.relations[key]
        new_value = max(0, min(100, old.friendliness + delta))
        new_rel = Relation(
            city_a=old.city_a, city_b=old.city_b,
            friendliness=new_value, last_event=reason,
        )
        self.relations[key] = new_rel
        return new_rel
    
    def get_relation(self, city_a: str, city_b: str) -> Relation:
        return self.relations[self._pair_key(city_a, city_b)]
    
    def relation_with_player(self, city_id: str) -> Relation:
        return self.get_relation(self.player_city_id, city_id)
    
    def get_visibility(self, city_id: str) -> int:
        """根据友好度返回可见等级 0-4。"""
        f = self.relation_with_player(city_id).friendliness
        if f >= 81: return 4
        if f >= 61: return 3
        if f >= 41: return 2
        if f >= 21: return 1
        return 0
    
    def trade_volume(self, city_a: str, city_b: str) -> int:
        """两城之间当月贸易额（分）；默认初始值。"""
        from backend.systems.v21.city_presets import CITY_PRESETS
        a = next((c for c in self.world_state.cities if c.id == city_a), None)
        b = next((c for c in self.world_state.cities if c.id == city_b), None)
        if a is None or b is None:
            return 0
        # 默认按两城 N 乘积 × 0.5 CR/月 × 100 算
        from backend.systems.v21.industry_scenario import default_scenario
        s = default_scenario()
        return int(s['city'].get('reference_consumption_cr', 50) * a.x * b.x)
    
    def relations_snapshot(self) -> list[dict]:
        return [
            {'city_a': r.city_a, 'city_b': r.city_b,
             'friendliness': r.friendliness, 'last_event': r.last_event}
            for r in self.relations.values()
        ]
    
    def relations_for_city(self, city_id: str) -> list[Relation]:
        return [r for r in self.relations.values()
                if r.city_a == city_id or r.city_b == city_id]


def reset_diplomacy_for_tests():
    pass