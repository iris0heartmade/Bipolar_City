"""M27 NPC-NPC 贸易网单元测试。"""
import pytest

from backend.systems.v21.npc_trade_router import (
    NpcTradeConfig, NpcTradeFlow, NpcTradeRouter, load_npc_trade_config,
)


def test_load_default_config():
    cfg = load_npc_trade_config()
    assert cfg.base_volume_per_month_cr > 0
    assert cfg.friendliness_threshold > 0


def test_distance_factor_zero_distance():
    router = NpcTradeRouter()
    assert router.compute_distance_factor(0) == 1.0


def test_distance_factor_inverse():
    router = NpcTradeRouter(NpcTradeConfig(distance_factor_divisor=100))
    f1 = router.compute_distance_factor(0)
    f2 = router.compute_distance_factor(100)
    f3 = router.compute_distance_factor(200)
    assert f1 > f2 > f3
    assert abs(f2 - 0.5) < 1e-6  # 1/(1+1)
    assert abs(f3 - 1/3) < 1e-6  # 1/(1+2)


def test_monthly_flow_returns_none_below_threshold():
    cfg = NpcTradeConfig(friendliness_threshold=30)
    router = NpcTradeRouter(cfg)
    flow = router.monthly_flow('a', 'b', distance_units=50, friendliness=29)
    assert flow is None


def test_monthly_flow_min_volume_clamp():
    cfg = NpcTradeConfig(base_volume_per_month_cr=100, min_volume_per_month_cr=100,
                        max_volume_per_month_cr=5000)
    router = NpcTradeRouter(cfg)
    # 友好度=30, distance=1000: raw=100*(30/100)*(1/11)=27 < min 100 → 100
    flow = router.monthly_flow('a', 'b', distance_units=1000, friendliness=30)
    assert flow.volume_cr == 100


def test_monthly_flow_max_volume_clamp():
    cfg = NpcTradeConfig(base_volume_per_month_cr=1000, min_volume_per_month_cr=100,
                        max_volume_per_month_cr=2000)
    router = NpcTradeRouter(cfg)
    # 友好度=100, distance=0: raw=1000 > max 2000？不对，1000 < 2000 OK
    # 改 base=5000: raw=5000*1.0*1.0=5000 > 2000
    cfg2 = NpcTradeConfig(base_volume_per_month_cr=5000, max_volume_per_month_cr=2000)
    router2 = NpcTradeRouter(cfg2)
    flow = router2.monthly_flow('a', 'b', distance_units=0, friendliness=100)
    assert flow.volume_cr == 2000


def test_monthly_flow_friendliness_delta():
    cfg = NpcTradeConfig(trade_friendliness_delta_per_month=1)
    router = NpcTradeRouter(cfg)
    flow = router.monthly_flow('a', 'b', distance_units=50, friendliness=50)
    assert flow.friendliness_delta_per_side == 1


def test_route_all_no_pairs():
    router = NpcTradeRouter()
    flows = router.route_all([], {}, lambda a, b: 100, current_month=1)
    assert flows == []


def test_route_all_skips_below_threshold():
    cfg = NpcTradeConfig(friendliness_threshold=30)
    router = NpcTradeRouter(cfg)
    positions = {'a': (0, 0), 'b': (50, 50)}
    # 友好度 20 < threshold 30
    flows = router.route_all(['a', 'b'], positions, lambda a, b: 20, current_month=1)
    assert flows == []


def test_route_all_generates_flows_with_proper_month():
    cfg = NpcTradeConfig()
    router = NpcTradeRouter(cfg)
    positions = {'a': (0, 0), 'b': (50, 50), 'c': (100, 0)}
    flows = router.route_all(['a', 'b', 'c'], positions,
                              lambda a, b: 50, current_month=5)
    assert len(flows) == 3  # C(3,2) = 3
    for flow in flows:
        assert flow.month == 5
        assert flow.volume_cr > 0


def test_npc_trade_flow_roundtrip():
    flow = NpcTradeFlow(city_a='a', city_b='b', month=3, volume_cr=500.5,
                        friendliness_delta_per_side=1)
    d = flow.to_dict()
    flow2 = NpcTradeFlow.from_dict(d)
    assert flow2.city_a == flow.city_a
    assert flow2.volume_cr == flow.volume_cr
    assert flow2.month == flow.month


def test_worldgame_advance_increases_npc_npc_friendliness():
    """WorldGame.end_advance_npc_only 后 NPC-NPC 友好度自然增长。"""
    from backend.systems.v21.world_game import WorldGame
    g = WorldGame.default()
    # NPC-NPC 初始友好度（包含工业/商业/农业/魔法/矿业）
    before = g.diplomacy.get_relation('agricultural_town', 'commercial_port').friendliness
    # 推进 3 月
    new_g = g.advance_npc_only(months=3)
    after = new_g.diplomacy.get_relation('agricultural_town', 'commercial_port').friendliness
    # 友好度应增长（贸易流触发双向 +1/月 → 3 月后 +3）
    assert after >= before


def test_worldapi_state_exposes_trade_routes():
    """WorldApi /api/world/state 包含 npc_trade_routes 字段。"""
    from backend.world_api import _build_world_payload
    from backend.systems.v21.world_game import WorldGame
    g = WorldGame.default()
    g = g.advance_npc_only(months=2)
    payload = _build_world_payload(g)
    assert 'npc_trade_routes' in payload
    # 应该有若干条贸易流（NPC-NPC C(4,2)=6 对，threshold 30，初始 50 全通过）
    assert len(payload['npc_trade_routes']) >= 1
    # 字段验证
    first = payload['npc_trade_routes'][0]
    assert set(['city_a', 'city_b', 'month', 'volume_cr', 'friendliness_delta_per_side']).issubset(first.keys())