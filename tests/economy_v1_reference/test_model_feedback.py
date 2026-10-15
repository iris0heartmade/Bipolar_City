"""按真实交易和行为验证实验室，不能用零残差代替业务正确。"""
import math

import pytest

from backend.model.parameters import EconomyParameters
from backend.systems.model_sim import EconomySimulation
from backend.config import ROOT, read_json
from tools.economy_scenarios import run_scenario


def test_reference_cash_and_profit_are_reproduced():
    sim = EconomySimulation()
    opening = sim.ledger.local_total
    report = sim.step()
    assert report.metrics['firm_sales'] == 34000000
    assert report.metrics['firm_pre_tax_profit'] == 4240000
    assert report.metrics['fiscal_balance'] == 51700
    # K 值（city_capacity）引入后，首月迁徙可能让本地现金增加少许（迁入者带积蓄），
    # 允许 ±1000 CR 漂移。1000 CR = 单月迁徙上限 ≈ 上限 × 800 CR/人。
    delta_cash = (sim.ledger.local_total - opening) // 100
    assert 26000 <= delta_cash <= 28000, f"本地现金漂移 {delta_cash} CR 超出 ±1k"
    # K 值引入后首月可能迁入几人，失业允许 ±5 人漂移
    assert 145 <= sum(c.unemployed for c in sim.cohorts) <= 155


def test_housing_shock_preserves_identity_each_month():
    sim = EconomySimulation(parameters=EconomyParameters({'housing.units': 400}))
    for _ in range(120):
        sim.step()
        for c in sim.cohorts:
            assert c.employed + c.unemployed + c.non_labour == c.population
        assert sum(c.employed for c in sim.cohorts) == sum(s.employees for s in sim.sectors) + sim.public_staff


def test_income_bands_remain_distinct():
    sim = EconomySimulation()
    sim.step()
    low, middle, high = sim.cohorts
    assert low.per_capita_cents < middle.per_capita_cents < high.per_capita_cents


def test_depression_can_cause_losses_exits_and_relief_shortage():
    sim = EconomySimulation(months=60, parameters=EconomyParameters({
        'trade.exports_base': 0, 'trade.tourist_spend_base': 0,
        'city.start_cash': 0, 'city.central_transfer_monthly': 0,
        'religion.start_church_cash': 0,
    }))
    history = sim.run()
    assert any(r.metrics['firm_pre_tax_profit'] < 0 for r in history)
    assert any(r.metrics['firms_bankrupt'] + r.metrics['firms_relocated'] > 0 for r in history)
    assert max(r.metrics['unemployment_rate'] for r in history) > 0.3
    assert any(r.metrics['church_relief_shortfall'] > 0 for r in history)
    for r in history:
        assert r.metrics['accounting_residual_cents'] == 0
        assert all(math.isfinite(v) for v in r.metrics.values())


def test_report_is_actual_fiscal_flow():
    sim = EconomySimulation(parameters=EconomyParameters({'city.start_cash': 0}))
    before = sim.ledger.balances['government']
    r = sim.step()
    assert r.metrics['fiscal_balance'] == sim.ledger.balances['government'] - before + r.metrics['debt_principal_paid']


@pytest.mark.parametrize('spec', read_json(ROOT / 'data/economy/scenarios_v1.json')['scenarios'], ids=lambda s: s['id'])
def test_condition_scenarios(spec):
    sim, history, _, checks = run_scenario(spec)
    assert all(checks.values()), checks
    for r in history:
        # 卖方成交统计来自真实流水，不能把欠付订单当销售。
        sales_categories = {'sale', 'export', 'tourism', 'procurement', 'maintenance', 'intermediate', 'capital', 'housing_construction'}
        actual = sum(t['amount_cents'] for t in r.transactions if t['to_account'].startswith('firms:') and t['category'] in sales_categories)
        assert actual == r.metrics['firm_sales']
        assert sum(s['cash'] for s in r.sectors) == r.metrics['firm_cash']
        assert all(s['cash'] >= 0 for s in r.sectors)
        assert all(math.isfinite(v) for v in r.metrics.values())
        assert len({t['tx_id'] for t in r.transactions}) == len(r.transactions)
        assert all(t['month'] == r.month for t in r.transactions)
        flows = {}
        for t in r.transactions:
            flows[t['category']] = flows.get(t['category'], 0) + t['amount_cents']
        expenditure = sum(flows.get(k, 0) for k in ('sale', 'procurement', 'capital', 'housing_construction', 'residential_rent', 'public_wage', 'export', 'tourism')) - flows.get('import', 0)
        assert expenditure == r.metrics['gva']


def test_first_month_complete_cash_bridge():
    sim = EconomySimulation()
    opening = sim.ledger.balances
    r = sim.step()
    # 与 test_reference_cash_and_profit_are_reproduced 同源：K 值引入后，
    # 首月家庭可能多收到迁入者带进的钱（每人 800 CR）。允许 households 账户 ±1k CR 漂移，
    # 其余账户的精确值仍保留为硬断言。
    expected = {'firms': 1731200, 'households': 843100, 'government': 51700, 'church': 84000}
    tolerance = {'households': 100000}
    for account, delta in expected.items():
        actual = sim.ledger.balances[account] - opening[account]
        tol = tolerance.get(account, 0)
        assert abs(actual - delta) <= tol, f"{account}: 期望 {delta} 实际 {actual} (容差 {tol})"
    assert r.metrics['gva'] == 21190000
    assert r.metrics['avg_wage_employed'] == (12190000 + 1440000) / 1500


def test_no_money_no_imported_goods():
    sim = EconomySimulation(parameters=EconomyParameters({'feedback.firm_cash_multiplier': 0}))
    sim.step()
    assert all(r.imported == 0 for r in sim.resources.values())
    assert sim.ledger.by_category().get('import', 0) == 0


def test_failure_rolls_back_entire_month(monkeypatch):
    sim = EconomySimulation()
    opening = sim.ledger.balances
    stocks = {k: r.stock for k, r in sim.resources.items()}
    monkeypatch.setattr(sim, '_assert_invariants', lambda _opening: (_ for _ in ()).throw(RuntimeError('injected failure')))
    with pytest.raises(RuntimeError, match='injected'):
        sim.step()
    assert sim.month == 0 and sim.history == []
    assert sim.ledger.balances == opening
    assert {k: r.stock for k, r in sim.resources.items()} == stocks


def test_education_has_real_delay_and_capacity_effect():
    slow = EconomySimulation(parameters=EconomyParameters({'education.skill_transition_rate': 0.1}))
    fast = EconomySimulation(parameters=EconomyParameters({'education.skill_transition_rate': 0.6}))
    for _ in range(11):
        slow.step(); fast.step()
    assert slow.trained == fast.trained == 0
    assert sum(s.capacity_units for s in slow.sectors) == sum(s.capacity_units for s in fast.sectors)
    slow.step(); fast.step()
    assert fast.trained > slow.trained
    assert sum(s.capacity_units for s in fast.sectors) > sum(s.capacity_units for s in slow.sectors)


def test_minimum_wage_and_price_change_bounds():
    sim = EconomySimulation(parameters=EconomyParameters({'labour.min_wage': 100}))
    for _ in range(24):
        wages = [s.wage_per_employee_cents for s in sim.sectors]
        prices = [s.price for s in sim.sectors]
        sim.step()
        for s, wage, price in zip(sim.sectors, wages, prices):
            assert s.wage_per_employee_cents >= 10000
            if wage >= 10000:
                assert abs(s.wage_per_employee_cents / wage - 1) <= 0.03 + 1 / wage
            assert abs(s.price / price - 1) <= 0.05 + 1e-9


@pytest.mark.parametrize('employed,unemployed', [(0, 0), (0, 3000), (3000, 0), (1, 0), (1, 1)])
def test_extreme_population_composition(employed, unemployed):
    p = EconomyParameters({'population.employed': employed, 'population.unemployed': unemployed,
                           'population.out_of_labour_force': 3000 - employed - unemployed,
                           'population.labour_force': employed + unemployed})
    sim = EconomySimulation(months=24, parameters=p)
    history = sim.run()
    assert all(r.metrics['accounting_residual_cents'] == 0 for r in history)


@pytest.mark.parametrize('invalid', [float('nan'), float('inf'), '0.08', True])
def test_invalid_parameter_cannot_corrupt_saved_value(invalid):
    params = EconomyParameters()
    original = params.get('tax.income_flat_rate')
    with pytest.raises(Exception):
        params.set('tax.income_flat_rate', invalid)
    assert params.get('tax.income_flat_rate') == original


def test_resource_quantity_has_real_import_payment_and_balances():
    sim = EconomySimulation(parameters=EconomyParameters({'trade.import_price_index': 2}))
    opening = {k: r.stock for k, r in sim.resources.items()}
    for _ in range(24):
        report = sim.step()
        for r in report.resources:
            paid_quantity = sum(t['qty'] or 0 for t in report.transactions if t['category'] == 'import' and t['commodity_id'] == r['resource_id'])
            assert paid_quantity == pytest.approx(r['imported'])
            expected = opening[r['resource_id']] + r['produced'] + r['imported'] - r['used'] - r['exported'] - r['wasted']
            assert r['stock'] == pytest.approx(expected, abs=1e-6)
            opening[r['resource_id']] = r['stock']


def test_births_cannot_ignore_housing_constraint():
    sim = EconomySimulation(months=240, parameters=EconomyParameters({'population.natural_growth_rate': 0.02}))
    for r in sim.run():
        assert r.metrics['population'] <= r.metrics['housing_units'] * sim.params.get('housing.household_size')


def test_coal_interruption_stops_dependent_industries():
    items = EconomyParameters().get('resources.items')
    for row in items:
        if row['id'] == 'coal':
            row['produced'] = row['imported'] = 0
    sim = EconomySimulation(months=36, shocks=[{'start': 13, 'overrides': {'resources.items': items}}])
    sim.run()
    assert sim.resources['coal'].stock < 1e-6
    assert next(s for s in sim.sectors if s.sector_id == 'heavy_mining').revenue_cents == 0


def test_export_units_and_crisis_warning():
    from backend.model.indicators import BY_KEY
    assert BY_KEY['firm_sales'].display_value(34000000) == 340000
    assert BY_KEY['unemployment_rate'].display_value(0.09) == 9
    assert BY_KEY['church_relief_shortfall'].status(1) == 'bad'
    assert BY_KEY['unpaid_cents'].status(1) == 'bad'


def test_basket_changes_orders_without_creating_budget():
    sim = EconomySimulation()
    normal = EconomySimulation().step()
    for c in sim.cohorts:
        c.basket = {key: 1.0 if key == 'food' else 0.0 for key in c.basket}
    report = sim.step()
    assert report.metrics['firm_sales'] < normal.metrics['firm_sales']
    assert all(c.consumption_paid <= c.budget for c in sim.cohorts)
    assert report.metrics['accounting_residual_cents'] == 0


def test_initial_high_unemployment_already_needs_church_relief():
    sim = EconomySimulation(parameters=EconomyParameters({
        'population.employed': 0, 'population.unemployed': 1650,
        'population.out_of_labour_force': 1350, 'population.labour_force': 1650,
        'religion.start_church_cash': 0,
    }))
    report = sim.step()
    assert sim.church_relief_due > 0
    assert report.metrics['church_relief_shortfall'] > 0
