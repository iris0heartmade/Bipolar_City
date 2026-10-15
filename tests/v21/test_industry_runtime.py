"""行业模型实际运行、干预与游戏接口验收，避免固定True式测试。"""
from copy import deepcopy

import pytest

from backend.systems.v21.industry_economy import IndustryEconomy
from backend.systems.v21.industry_scenario import default_scenario, validate_scenario


def test_month_really_pays_produces_and_conserves():
    city = IndustryEconomy()
    before = sum(city.cash.values())
    report = city.advance()
    assert report['month'] == 1
    assert report['wages_cents'] > 0
    assert report['sales_cents'] > 0
    assert report['consumer_spending_cents'] > 0
    assert sum(city.cash.values()) == before
    assert sum(report['employment'].values()) + report['unemployed'] == city.labor_force
    assert all(v >= 0 for v in city.cash.values())
    assert report['cash_residual_cents'] == 0
    assert report['transactions']


def test_wage_and_input_controls_have_real_effect():
    base = IndustryEconomy()
    altered = deepcopy(base)
    altered.scenario['sectors']['IND']['wage_multiplier'] = 1.5
    a, b = base.advance(), altered.advance()
    assert a['sectors']['IND']['wage_cents'] != b['sectors']['IND']['wage_cents']
    assert a['wages_cents'] != b['wages_cents']
    blocked = IndustryEconomy()
    blocked.scenario['sectors']['IND']['input_supply_ratio'] = 0
    # 先用完已有原料缓冲；断供不能凭空删除期初库存。
    blocked.advance()
    c = blocked.advance()
    assert c['sectors']['IND']['output_units'] == 0
    assert c['sectors']['IND']['capacity'] == 0


def test_invalid_parameters_are_rejected():
    scenario = default_scenario()
    scenario['sectors']['IND']['wage_multiplier'] = float('nan')
    with pytest.raises(ValueError):
        validate_scenario(scenario)


def test_save_restore_preserves_next_month():
    city = IndustryEconomy()
    city.advance()
    restored = IndustryEconomy.from_dict(city.to_dict())
    assert city.advance() == restored.advance()


def test_cash_injection_is_rejected_and_month_not_committed():
    city = IndustryEconomy()
    city.cash['GOV'] += 1
    before = city.to_dict()
    with pytest.raises(AssertionError, match='现金不守恒'):
        city.advance()
    assert city.to_dict() == before


def test_property_channel_distributes_without_destroying_cash():
    city = IndustryEconomy()
    r = city.advance()
    received = sum(t['amount_cents'] for t in r['transactions'] if t['to'] == 'PROP')
    distributed = sum(t['amount_cents'] for t in r['transactions'] if t['from'] == 'PROP')
    assert received > 0 and received == distributed and city.cash['PROP'] == 0


def test_tax_uses_real_positive_profit_and_government_not_taxed():
    city = IndustryEconomy()
    r = city.advance()
    for i, s in r['sectors'].items():
        if i == 'GOV':
            assert s['profit_cents'] is None and s['tax_cents'] == 0
        elif s['profit_cents'] < 0:
            assert s['tax_cents'] == 0
    assert sum(t['amount_cents'] for t in r['transactions'] if t['kind'] == 'industry_tax') == sum(s['tax_cents'] for s in r['sectors'].values())


def test_external_buyer_cannot_spend_more_than_real_cash():
    city = IndustryEconomy()
    city.cash['GOV'] += city.cash['EXT']
    city.cash['EXT'] = 0
    report = city.advance()
    balance = 0
    for t in report['transactions']:
        if t['to'] == 'EXT':
            balance += t['amount_cents']
        if t['from'] == 'EXT':
            balance -= t['amount_cents']
        assert balance >= 0
    assert balance == city.cash['EXT']


def test_weight_linkage_and_invalid_change_are_atomic():
    from backend.systems.v21.industry_scenario import change_control
    original = default_scenario()
    changed = change_control(original, 'weights', 'earnings', 1)
    assert sum(changed['model']['attractiveness']['weights'].values()) == 1
    assert original['model']['attractiveness']['weights']['earnings'] == .6
    changed = change_control(changed, 'weights', 'security', .8)
    assert sum(changed['model']['attractiveness']['weights'].values()) == pytest.approx(1)
    with pytest.raises(ValueError):
        change_control(original, 'sector', 'wage_multiplier', 100)


def test_120_months_real_transactions_keep_all_invariants():
    city = IndustryEconomy()
    for month in range(1, 121):
        report = city.advance()
        assert report['month'] == month
        assert report['cash_residual_cents'] == 0
        city.assert_invariants()
    # 没有把经营收缩误判为模型已经数值平衡。
    assert sum(report['employment'].values()) < city.labor_force


def test_game_and_desktop_share_identical_results(tmp_path):
    from backend.industry_api import IndustryGame
    from tools.economy_lab import compute_comparison
    from backend.systems.v21.industry_scenario import change_control
    scenario = change_control(default_scenario(), 'sector', 'wage_multiplier', 1.25, 'IND')
    _, desktop = compute_comparison(scenario, 3)
    game = IndustryGame(tmp_path)
    game.command({'type': 'set_parameter', 'section': 'sector', 'key': 'wage_multiplier', 'value': 1.25, 'industry': 'IND'})
    for expected in desktop:
        actual = game.command({'type': 'advance'})['latest']
        assert actual == expected


def test_game_idempotence_and_json_save_load(tmp_path):
    from backend.industry_api import IndustryGame
    game = IndustryGame(tmp_path)
    once = game.command({'type': 'advance', 'request_id': 'unique'})
    twice = game.command({'type': 'advance', 'request_id': 'unique'})
    assert once['month'] == twice['month'] == 1
    game.command({'type': 'save'})
    expected = game.command({'type': 'advance'})['latest']
    game.command({'type': 'load'})
    assert game.command({'type': 'advance'})['latest'] == expected


def test_game_api_and_page_are_available(tmp_path):
    from backend.app import app
    from backend.industry_api import IndustryGame
    from fastapi.testclient import TestClient
    with TestClient(app) as client:
        app.state.industry_game = IndustryGame(tmp_path)
        assert client.get('/').status_code == 200
        invalid = client.post('/api/industry/commands', json={'type': 'advance', 'months': True})
        assert invalid.status_code == 422
        result = client.post('/api/industry/commands', json={'type': 'advance', 'request_id': 'api-first'})
        assert result.status_code == 200 and result.json()['month'] == 1
        assert result.json()['latest']['consumer_spending_cents'] > 0


def test_every_exposed_control_can_really_simulate():
    from backend.systems.v21.industry_scenario import change_control, simulate
    for meta in default_scenario()['controls']:
        value = (meta['min']+meta['max'])/2
        scenario = change_control(default_scenario(), meta['section'], meta['key'], value, 'IND')
        report = simulate(scenario, 3)[-1]
        assert report['cash_residual_cents'] == 0


def test_removing_exports_does_not_delete_local_demand():
    city = IndustryEconomy()
    city.scenario['sectors']['AGR']['demand_multiplier'] = 0
    r = city.advance()
    assert r['sectors']['AGR']['target'] > 0
    assert any(t['to'] == 'AGR' and t['kind'] == 'consumer_purchase' for t in r['transactions'])
