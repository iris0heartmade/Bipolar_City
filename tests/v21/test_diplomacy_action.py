"""M26 玩家外交行动单元测试。"""
import pytest

from backend.systems.v21.diplomacy import DiplomacySystem
from backend.systems.v21.diplomacy_action import (
    ActionConfig,
    DiplomacyAction,
    DiplomacyActionValidator,
    load_diplomacy_actions,
)
from backend.systems.v21.world_state import default_world


@pytest.fixture
def world():
    return default_world(seed=42)


@pytest.fixture
def diplomacy(world):
    return DiplomacySystem(world)


def test_load_actions():
    actions = load_diplomacy_actions()
    assert 'trade_agreement' in actions
    assert 'aid' in actions
    assert 'tariff' in actions
    assert 'immigration_open' in actions
    assert 'statement' in actions
    assert actions['tariff'].friendliness_delta_per_month == -10


def test_trade_agreement_increases_friendliness(diplomacy):
    action = DiplomacyAction(
        action_key='trade_agreement', target_city_id='agricultural_town',
        month=1, cost_cr=0, friendliness_delta=1, note='测试',
    )
    ok, reason, record = diplomacy.apply_player_action(action, current_month=1,
                                                       player_cash_cr=100_000)
    assert ok, reason
    assert record.friendliness_delta == 1
    rel = diplomacy.relation_with_player('agricultural_town')
    assert rel.friendliness == 51  # 初始 50 + 1
    assert '贸易' in rel.last_event


def test_tariff_decreases_friendliness(diplomacy):
    action = DiplomacyAction(
        action_key='tariff', target_city_id='commercial_port',
        month=1, cost_cr=0, friendliness_delta=-10, note='测试',
    )
    ok, reason, record = diplomacy.apply_player_action(action, current_month=1,
                                                       player_cash_cr=0)
    assert ok, reason
    assert record.friendliness_delta == -10
    rel = diplomacy.relation_with_player('commercial_port')
    assert rel.friendliness == 40  # 50 - 10


def test_aid_amount_scales_friendliness(diplomacy):
    action = DiplomacyAction(
        action_key='aid', target_city_id='commercial_port',
        month=1, cost_cr=500, friendliness_delta=5, note='紧急援助',
    )
    ok, reason, record = diplomacy.apply_player_action(action, current_month=1,
                                                       player_cash_cr=0)  # 不校验现金
    assert ok, reason
    assert record.friendliness_delta == 5  # ceil(500/100) = 5
    rel = diplomacy.relation_with_player('commercial_port')
    assert rel.friendliness == 55


def test_aid_100_cr_gives_1_friendliness(diplomacy):
    action = DiplomacyAction(action_key='aid', target_city_id='agricultural_town',
                             month=1, cost_cr=100, friendliness_delta=0, note='')
    ok, _, record = diplomacy.apply_player_action(action, current_month=1,
                                                  player_cash_cr=0)
    assert record.friendliness_delta == 1


def test_friendliness_clamped_0_100(diplomacy):
    """tariff 多次累积友好度不能降到 0 以下。"""
    # 对 5 个 NPC 城市各调一次 tariff
    cities = ['agricultural_town', 'commercial_port', 'magic_academy',
              'mining_border', 'industrial_city']  # industrial_city 不是玩家城市时
    # 实际上 industrial_city 是玩家城市，不能 self-tariff。改用 4 个 NPC + 不同月份。
    cities = ['agricultural_town', 'commercial_port', 'magic_academy', 'mining_border']
    # 每个城市用 cooldown 间隔，每 3 月触发 1 次，目标是降到 0
    target_city = cities[0]  # agricultural_town
    for month in (1, 4, 7, 10, 13):  # cooldown=3 隔 3 月
        action = DiplomacyAction(action_key='tariff', target_city_id=target_city,
                                 month=month, cost_cr=0, friendliness_delta=-10, note='')
        diplomacy.apply_player_action(action, current_month=month, player_cash_cr=0)
    rel = diplomacy.relation_with_player(target_city)
    assert rel.friendliness == 0  # 50-10*5 = 0


def test_friendliness_clamped_0_underflow(diplomacy):
    """友好度降到 0 以下被钳到 0。"""
    target_city = 'agricultural_town'
    # 6 次：50-60=-10 但被钳到 0
    for month in (1, 4, 7, 10, 13, 16):
        action = DiplomacyAction(action_key='tariff', target_city_id=target_city,
                                 month=month, cost_cr=0, friendliness_delta=-10, note='')
        diplomacy.apply_player_action(action, current_month=month, player_cash_cr=0)
    rel = diplomacy.relation_with_player(target_city)
    assert rel.friendliness == 0


def test_max_per_month_limit(diplomacy):
    """trade_agreement 每月上限 3 次，第 4 次被拒。"""
    for i in range(1, 4):
        action = DiplomacyAction(action_key='trade_agreement',
                                 target_city_id='agricultural_town',
                                 month=1, cost_cr=0, friendliness_delta=1, note='')
        ok, _, _ = diplomacy.apply_player_action(action, current_month=1,
                                                 player_cash_cr=100_000)
        assert ok
    # 第 4 次
    action = DiplomacyAction(action_key='trade_agreement',
                             target_city_id='agricultural_town',
                             month=1, cost_cr=0, friendliness_delta=1, note='')
    ok, reason, record = diplomacy.apply_player_action(action, current_month=1,
                                                       player_cash_cr=100_000)
    assert not ok
    assert '本月已用尽' in reason
    assert record.success is False


def test_cooldown_months(diplomacy):
    """tariff cooldown_months=3，第 1 月触发后第 2 月再次被拒。"""
    action = DiplomacyAction(action_key='tariff', target_city_id='agricultural_town',
                             month=1, cost_cr=0, friendliness_delta=-10, note='')
    ok, _, _ = diplomacy.apply_player_action(action, current_month=1, player_cash_cr=0)
    assert ok
    # 第 2 月再试
    action2 = DiplomacyAction(action_key='tariff', target_city_id='agricultural_town',
                              month=2, cost_cr=0, friendliness_delta=-10, note='')
    ok, reason, _ = diplomacy.apply_player_action(action2, current_month=2,
                                                  player_cash_cr=0)
    assert not ok
    assert '冷却中' in reason


def test_visibility_requirement(diplomacy):
    """trade_agreement requires_visibility=1（认识级，友好度 > 20）；当前 50 应通过。"""
    # 当前默认 50，get_visibility=2（友好），满足 requires_visibility=1
    action = DiplomacyAction(action_key='trade_agreement',
                             target_city_id='agricultural_town',
                             month=1, cost_cr=0, friendliness_delta=1, note='')
    ok, _, _ = diplomacy.apply_player_action(action, current_month=1,
                                             player_cash_cr=100_000)
    assert ok


def test_visibility_blocks_when_too_low(diplomacy, monkeypatch):
    """把友好度调到 0（陌生人），trade_agreement 需 visible=1 应被拒。"""
    # monkey-patch：禁用 cooldown 以便快速把友好度拉到 0
    from dataclasses import replace
    from backend.systems.v21 import diplomacy_action as da
    orig = da.load_diplomacy_actions

    def patched():
        cfg = orig()
        cfg['tariff'] = replace(cfg['tariff'], cooldown_months=0)
        return cfg

    monkeypatch.setattr(da, 'load_diplomacy_actions', patched)
    # 调 5 次 tariff 把友好度拉到 0
    for month in range(1, 6):
        action = DiplomacyAction(action_key='tariff', target_city_id='mining_border',
                                 month=month, cost_cr=0, friendliness_delta=-10, note='')
        diplomacy.apply_player_action(action, current_month=month, player_cash_cr=0)
    # 现在友好度=0，可见等级=0，trade_agreement 需要 visible=1
    action = DiplomacyAction(action_key='trade_agreement',
                             target_city_id='mining_border',
                             month=7, cost_cr=0, friendliness_delta=1, note='')
    ok, reason, _ = diplomacy.apply_player_action(action, current_month=7,
                                                  player_cash_cr=100_000)
    assert not ok
    assert '可见等级不足' in reason


def test_player_actions_history(diplomacy):
    """history 记录所有行动（含 rejected）。"""
    action = DiplomacyAction(action_key='aid', target_city_id='agricultural_town',
                             month=1, cost_cr=-1, friendliness_delta=0, note='')
    ok, _, _ = diplomacy.apply_player_action(action, current_month=1, player_cash_cr=0)
    assert not ok
    history = diplomacy.player_actions_history()
    assert len(history) == 1
    assert history[0].success is False


def test_to_dict_from_dict_roundtrip():
    a = DiplomacyAction(action_key='aid', target_city_id='x', month=3,
                        cost_cr=500.0, friendliness_delta=5, note='test note',
                        success=True)
    d = a.to_dict()
    a2 = DiplomacyAction.from_dict(d)
    assert a2.action_key == a.action_key
    assert a2.cost_cr == a.cost_cr
    assert a2.friendliness_delta == a.friendliness_delta
    assert a2.note == a.note


def test_statement_low_cost_quick_change(diplomacy):
    action = DiplomacyAction(action_key='statement', target_city_id='agricultural_town',
                             month=1, cost_cr=0, friendliness_delta=2, note='友好宣言')
    ok, _, record = diplomacy.apply_player_action(action, current_month=1,
                                                  player_cash_cr=0)
    assert ok
    assert record.friendliness_delta == 2
    rel = diplomacy.relation_with_player('agricultural_town')
    assert rel.friendliness == 52
    assert '声明' in rel.last_event


def test_immigration_open_increases_friendliness(diplomacy):
    action = DiplomacyAction(action_key='immigration_open',
                             target_city_id='magic_academy',
                             month=1, cost_cr=0, friendliness_delta=3, note='开放边境')
    ok, _, _ = diplomacy.apply_player_action(action, current_month=1, player_cash_cr=0)
    assert ok
    assert diplomacy.relation_with_player('magic_academy').friendliness == 53