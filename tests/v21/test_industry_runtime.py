"""行业模型实际运行、干预与游戏接口验收，避免固定True式测试。"""
from copy import deepcopy

import pytest

from backend.systems.v21.industry_economy import IndustryEconomy
from backend.systems.v21.industry_scenario import default_scenario, validate_scenario


def test_month_really_pays_produces_and_conserves():
    city = IndustryEconomy()
    before = sum(city.cash.values()) + city.ocean_cash
    report = city.advance()
    assert report['month'] == 1
    assert report['wages_cents'] > 0
    assert report['sales_cents'] > 0
    assert report['consumer_spending_cents'] > 0
    # 阶段 14 改：现金守恒包含 OCEAN 虚拟池（外部补给来源）
    assert sum(city.cash.values()) + city.ocean_cash == before
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
    with pytest.raises(AssertionError, match='守恒'):
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
    # 阶段 14 改：OCEAN 池同样清空；EXT 期初也同步下调，让守恒自洽
    city.initial_total_cash -= city.ocean_cash
    city.total_replenish_cash = 0
    city.ocean_cash = 0
    city.initial_external_cash = 0
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


def test_purchase_external_moves_cash_and_raw():
    """市政采购：GOV 现金扣减 → EXT 现金增 → 原料池增；现金不足整笔被拒。"""
    city = IndustryEconomy()
    gov0, ext_cash0, ext_raw0 = city.cash['GOV'], city.cash['EXT'], city.external_raw
    paid_cash, paid_raw, reason = city.purchase_external(cash_cents=10_000_00, raw_units=2000, from_account='GOV')
    assert reason is None
    assert paid_cash == 10_000_00
    assert paid_raw == 2000
    # GOV 减少 = 现金 + 原料折算
    raw_cash = int(round(2000 * city.scenario['city']['raw_price_cr'] * city.cpi * 100))
    assert gov0 - city.cash['GOV'] == 10_000_00 + raw_cash
    assert city.cash['EXT'] - ext_cash0 == 10_000_00 + raw_cash
    assert city.external_raw - ext_raw0 == 2000
    # 守恒仍成立
    city.assert_invariants()


def test_purchase_external_rejects_when_cash_short():
    city = IndustryEconomy()
    delta = 100 - city.cash['GOV']
    city.cash['GOV'] = 100
    city.initial_total_cash += delta
    before_cash = dict(city.cash)
    before_raw = city.external_raw
    paid_cash, paid_raw, reason = city.purchase_external(cash_cents=50_000_00, raw_units=0, from_account='GOV')
    assert reason and 'GOV 现金不足' in reason
    assert paid_cash == 0 and paid_raw == 0
    assert city.cash == before_cash
    assert city.external_raw == before_raw
    city.assert_invariants()


def test_no_external_purchase_consumes_only_finite_real_resources():
    """不采购时资源按真实投入减少；不预设城市必须在指定月份崩溃。"""
    city = IndustryEconomy()
    opening_raw = city.external_raw
    r = None
    for _ in range(120):
        r = city.advance()
    assert 0 <= city.external_raw < opening_raw
    assert city.external_raw == pytest.approx(
        city.initial_external_raw - city.cumulative_raw_input_qty, abs=1e-3)
    assert city.cumulative_raw_purchased_units == 0
    assert r['cash_residual_cents'] == 0


def test_mayor_purchase_keeps_industry_alive():
    """市政连续采购：m=1 注入 50,000 CR 现金 + 100,000 单位原料，并每月追投小笔现金；
    120 月后 EXT 原料应远高于零操作基线。"""
    city = IndustryEconomy()
    city.purchase_external(cash_cents=50_000_00, raw_units=100_000,
                          from_account='GOV', note='开局势能补给')
    successful = 0
    for m in range(1, 121):
        if m % 6 == 0 and city.cash['GOV'] > 5_000_00:
            paid_cash, paid_raw, reason = city.purchase_external(
                cash_cents=3_000_00, raw_units=8_000, from_account='GOV', note='定期补给')
            if reason is None:
                successful += 1
        r = city.advance()
    # 至少要成功补给 1 次（证明 purchase 路径接通）
    assert successful >= 1
    # 多次补给让 ext_raw 比零操作基线（~0）高
    assert city.external_raw > 0
    # IND 现金应保持能持续经营
    assert r['sectors']['IND']['cash_cents'] > 0


def test_mayor_subsidy_moves_cash_and_conserves():
    city = IndustryEconomy()
    gov0, ind0 = city.cash['GOV'], city.cash['IND']
    ok, reason, _ = city.mayor_command(kind='subsidy_industry', industry='IND', cash_cr=1000)
    assert ok and reason is None
    assert city.cash['GOV'] == gov0 - 100_000
    assert city.cash['IND'] == ind0 + 100_000
    city.assert_invariants()


def test_mayor_limit_hire_freezes_recruitment():
    city = IndustryEconomy()
    # 让一个行业先大量空缺（增容量），然后限招；下月不应被新进。
    city.advance()
    city._active_policies[('AGR', 'limit_hire')] = city.month + 2
    e0 = city.employment().get('AGR', 0)
    # 接下来 2 月 AGR 就业不应增加（因为限招把 hires_from_unemployment 压 0）
    for _ in range(2):
        r = city.advance()
    e2 = city.employment().get('AGR', 0)
    assert e2 <= e0  # 不能因政策加进新人


def test_mayor_hire_civil_moves_workers_and_pays_wages():
    city = IndustryEconomy()
    gov0 = city.cash['GOV']
    gov_emp = city.employment().get('GOV', 0)
    ok, reason, payroll = city.mayor_command(kind='hire_civil', people=5)
    assert ok and reason is None
    assert payroll > 0
    assert city.employment().get('GOV', 0) == gov_emp + 5
    assert city.cash['GOV'] == gov0 - payroll
    city.assert_invariants()


def test_mayor_public_works_pays_unemployed_and_conserves():
    city = IndustryEconomy()
    gov0 = city.cash['GOV']
    hh0 = sum(city.cash[h] for h in city.population)
    ok, reason, _ = city.mayor_command(kind='public_works', cash_cr=500)
    assert ok and reason is None
    assert city.cash['GOV'] == gov0 - 50_000
    assert sum(city.cash[h] for h in city.population) == hh0 + 50_000
    city.assert_invariants()


def test_mayor_rejects_when_cash_short():
    city = IndustryEconomy()
    # 把 GOV 砍到几乎没钱；同步下调守恒基准
    gov0 = city.cash['GOV']
    city.cash['GOV'] = 100
    city.initial_total_cash -= (gov0 - 100)
    before_cash = dict(city.cash)
    before_ind = city.cash['IND']
    ok, reason, _ = city.mayor_command(kind='subsidy_industry', industry='IND', cash_cr=99999)
    assert not ok and 'GOV 现金不足' in reason
    assert city.cash == before_cash
    assert city.cash['IND'] == before_ind
    city.assert_invariants()
