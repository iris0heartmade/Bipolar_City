"""WorldGame：联动 WorldState + NpcCityEngine + DiplomacySystem + 玩家城市 IndustryEconomy。

参数分层：
- 世界级（world_state）：通胀、参考消费、距离矩阵、节点
- 国家级（diplomacy）：友好度、贸易量、关系事件
- 城市级（每个 IndustryEconomy）：scenario、人口、就业、政策、税率（玩家城市与 NPC 城市各自独立）

月结顺序：
  1. NPC 城市 AI 市长 step（每 NPC 独立）
  2. NPC 城市 advance
  3. 玩家城市 advance
  4. WorldState.month += months
"""
from copy import deepcopy
from dataclasses import replace
from typing import Optional

from backend.systems.v21.diplomacy import DiplomacySystem
from backend.systems.v21.industry_economy import IndustryEconomy
from backend.systems.v21.npc_city_engine import NpcCityEngine
from backend.systems.v21.world_state import WorldState


class WorldGame:
    """世界级 + NPC 城市的统一调度入口。"""
    
    def __init__(self, world: WorldState, npc_city: NpcCityEngine,
                 diplomacy: DiplomacySystem):
        self.world = world
        self.npc_city = npc_city
        self.diplomacy = diplomacy
    
    @classmethod
    def default(cls, seed: int = 20251008) -> 'WorldGame':
        from backend.systems.v21.world_state import default_world
        world = default_world(seed=seed)
        npc_city = NpcCityEngine.from_world(world)
        diplomacy = DiplomacySystem(world)
        return cls(world=world, npc_city=npc_city, diplomacy=diplomacy)
    
    def advance_npc_only(self, months: int = 1) -> 'WorldGame':
        """只推进 NPC 城市（不含玩家城市）。"""
        if type(months) is not int or not 1 <= months <= 120:
            raise ValueError('推进月数须为 1-120 整数')
        new_game = deepcopy(self)
        new_game.world = replace(self.world, month=self.world.month + months)
        new_game.npc_city = self.npc_city.advance(months=months)
        # M27：NPC-NPC 贸易流（按月生成 + 友好度互调）
        new_game._apply_monthly_trade_routes()
        return new_game

    def _apply_monthly_trade_routes(self) -> None:
        """每月推进后调用：生成 NPC-NPC 贸易流并调友好度。"""
        from backend.systems.v21.npc_trade_router import NpcTradeRouter
        router = NpcTradeRouter()
        npc_ids = [c.id for c in self.world.cities if not c.is_player]
        positions = {c.id: (c.x, c.y) for c in self.world.cities}

        def friendly(a: str, b: str) -> int:
            return self.diplomacy.get_relation(a, b).friendliness

        current_month = self.world.month
        flows = router.route_all(npc_ids, positions, friendly, current_month)
        for flow in flows:
            self.diplomacy.record_trade_flow(flow)
    
    def advance_with_player(self, player_city: IndustryEconomy, months: int = 1) -> tuple['WorldGame', IndustryEconomy]:
        """推进 NPC 城市 + 玩家城市。

        返回新的 WorldGame + 推进后的玩家城市 IndustryEconomy。
        """
        if type(months) is not int or not 1 <= months <= 120:
            raise ValueError('推进月数须为 1-120 整数')
        new_game = self.advance_npc_only(months)
        # 玩家城市独立 advance
        new_player = player_city
        for _ in range(months):
            new_player = new_player
            new_player.advance()
        return new_game, new_player
    
    def get_npc_city(self, city_id: str):
        return self.npc_city.get_city(city_id)
    
    def get_player_city_id(self) -> Optional[str]:
        try:
            return self.world.player_city().id
        except ValueError:
            return None

    def get_city_node(self, city_id: str):
        return self.world.get_city(city_id)

    def player_city_proxy(self):
        """玩家城市的代理占位；实际 IndustryEconomy 由 IndustryGame 持有。"""
        return None


# 全局单例（API 层使用）
_world_game_instance: Optional[WorldGame] = None


def get_world_game() -> WorldGame:
    """获取全局 WorldGame 单例；首次调用时构造。"""
    global _world_game_instance
    if _world_game_instance is None:
        _world_game_instance = WorldGame.default()
    return _world_game_instance


def set_world_game(game: WorldGame) -> None:
    """测试或存档恢复时设置全局单例。"""
    global _world_game_instance
    _world_game_instance = game


def reset_world_game_for_tests() -> None:
    global _world_game_instance
    _world_game_instance = None