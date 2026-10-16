import json
from copy import deepcopy
import pytest
from backend.systems.v21.industry_economy import IndustryEconomy


def test_policy_json_roundtrip():
    city = IndustryEconomy()
    city.mayor_command(kind='limit_layoff', industry='IND', months=1)
    restored = IndustryEconomy.from_dict(json.loads(json.dumps(city.to_dict())))
    assert restored.advance() == city.advance()


def test_layoff_limit_and_expiry():
    city = IndustryEconomy()
    city.mayor_command(kind='limit_layoff', industry='IND', months=1)
    assert city.advance()['layoffs']['IND'] == 0
    assert not city._active_policies


def test_invariant_check_never_repairs():
    city = IndustryEconomy()
    city.unemployed_by_group['H0'] += 1
    before = deepcopy(city.__dict__)
    with pytest.raises(AssertionError):
        city.assert_invariants()
    assert city.__dict__ == before


def test_missing_runtime_field_rejected():
    state = IndustryEconomy().to_dict()
    state.pop('raw_used')
    with pytest.raises(ValueError):
        IndustryEconomy.from_dict(state)


def test_month_between_actions_recorded():
    city = IndustryEconomy()
    city.mayor_command(kind='subsidy_industry', industry='IND', cash_cr=1)
    report = city.advance()
    assert any(t['kind'] == 'subsidy' for t in report['transactions'])
    assert report['recent_mayor_actions']


def test_ext_accepts_payment_without_cash():
    city = IndustryEconomy()
    city.advance()
    available = city.cash['EXT']
    city.cash['GOV'] += available
    city.cash['EXT'] = 0
    assert city.pay('GOV', 'EXT', 100, 'debt_interest') == 100

def test_tax_refund_is_atomic_and_keeps_tax_history():
    city = IndustryEconomy()
    city.advance()
    city.transactions.append({'from': 'IND', 'to': 'GOV', 'kind': 'industry_tax', 'amount_cents': 123})
    # Move treasury cash to a local account; total cash remains unchanged.
    city.cash['IND'] += city.cash['GOV']
    city.cash['GOV'] = 0
    before = deepcopy(city.__dict__)
    assert city.mayor_command(kind='tax_amnesty', industry='IND')[0] is False
    assert city.__dict__ == before
    city.cash['IND'] -= 123
    city.cash['GOV'] += 123
    assert city.mayor_command(kind='tax_amnesty', industry='IND') == (True, None, 123)
    assert any(t['kind'] == 'industry_tax' for t in city.transactions)
    assert city.pending_transactions[-1]['kind'] == 'tax_refund'
    assert city.mayor_command(kind='tax_amnesty', industry='IND')[2] == 0


def test_negative_population_reconciles_workers_exactly():
    city = IndustryEconomy()
    original = sum(city.population.values())
    city.change_population(-2000)
    assert sum(city.population.values()) == original - 2000
    city.assert_invariants()
    city.advance()
    city.assert_invariants()


def test_arrears_are_debts_and_are_repaid_to_original_group():
    city = IndustryEconomy()
    city.cash['GOV'] += city.cash['IND']
    city.cash['IND'] = 0
    city.mayor_command(kind='limit_layoff', industry='IND', months=2)
    report = city.advance()
    debt = deepcopy(city.arrears['IND'])
    assert sum(debt.values()) > 0
    assert report['wage_arrears_cents'] >= sum(debt.values())
    budget = sum(debt.values())
    city.cash['GOV'] -= budget
    city.cash['IND'] += budget
    city._settling = True
    city.repay_arrears('IND')
    city._settling = False
    assert sum(city.arrears['IND'].values()) == 0
    repayments = [t for t in city.transactions if t['kind'] == 'wage_arrears_repayment']
    assert {t['to']: t['amount_cents'] for t in repayments} == {h: n for h, n in debt.items() if n}
    city.assert_invariants()


def test_monthly_demography_converts_annual_rate_and_carries_fraction():
    from backend.systems.v21.demography import load_params, natural_change
    city = IndustryEconomy()
    city.transactions = []
    params = load_params(city.scenario)
    delta, birth, death, _, _ = natural_change(city, params, 0)
    assert birth * 12000 <= params.max_cbr_per_thousand
    assert death * 12000 <= params.max_cdr_per_thousand
    assert delta + city.population_carry == pytest.approx(sum(city.population.values()) * (birth - death))


def test_full_resource_conservation_and_pending_save_after_months():
    city = IndustryEconomy()
    for _ in range(24):
        city.advance()
    city.mayor_command(kind='limit_hire', industry='IND', months=3)
    restored = IndustryEconomy.from_dict(json.loads(json.dumps(city.to_dict())))
    assert city.advance() == restored.advance()
    for name in ('stock', 'raw'):
        broken = deepcopy(city)
        getattr(broken, name)['IND'] += 0.25
        with pytest.raises(AssertionError):
            broken.assert_invariants()


@pytest.mark.parametrize('field,value', [('receipts_history', {'IND': []}), ('cpi', float('nan')), ('prices', {})])
def test_corrupt_runtime_rejected(field, value):
    state = IndustryEconomy().to_dict()
    state[field] = value
    with pytest.raises(ValueError):
        IndustryEconomy.from_dict(state)


def test_labor_hiring_freeze_preserves_valid_outgoing_switch():
    from backend.systems.v21.industry_model import labor_month, load_parameters
    params = load_parameters()
    params['labor_flow']['monthly_hire_rate'] = 1
    params['labor_flow']['monthly_switch_rate'] = 1
    flow = labor_month(employed={'A': 10, 'B': 0}, unemployed=0,
                       capacities={'A': 20, 'B': 20}, hard_limits={'A': 20, 'B': 20},
                       targets={'A': 20, 'B': 20}, scores={'A': 10, 'B': 100},
                       params=params, limit_hire={'A'})
    assert flow['switches']['A', 'B'] == 10
    assert flow['employed'] == {'A': 0, 'B': 10}

def test_constant_demand_does_not_grow_when_history_fills():
    city = IndustryEconomy()
    city.advance()
    for i in city.keys:
        city.demand_history[i] = [10000]
    longer = deepcopy(city)
    for i in longer.keys:
        longer.demand_history[i] *= 3
    assert city.evaluate_jobs()[1] == longer.evaluate_jobs()[1]

def test_inventory_above_actual_month_sales_does_not_raise_real_price():
    city = IndustryEconomy()
    city.advance()
    city.scenario['city']['inflation'] = 0
    for i in city.keys:
        city.sales[i] = 10000
        city.stock[i] = 2 * city.sales[i] / city.prices[i]
    previous = dict(city.prices)
    city.update_prices_and_wages()
    assert all(city.prices[i] <= previous[i] for i in city.keys)

def test_pending_policy_cash_agrees_with_fiscal_report():
    city = IndustryEconomy()
    city.mayor_command(kind='subsidy_industry', industry='IND', cash_cr=1000)
    report = city.advance()
    net = sum((t['amount_cents'] if t['to'] == 'GOV' else 0)
              - (t['amount_cents'] if t['from'] == 'GOV' else 0) for t in report['transactions'])
    assert report['fiscal_balance_cents'] == net


def test_mortality_month_report_has_consistent_ending_employment():
    city = IndustryEconomy()
    city.scenario['demographics'].update(reference_cbr_per_thousand=0, min_cbr_per_thousand=0,
                                         max_cbr_per_thousand=0, reference_cdr_per_thousand=1000,
                                         min_cdr_per_thousand=1000, max_cdr_per_thousand=1000)
    for _ in range(2):
        report = city.advance()
        assert all(report['sectors'][i]['employed'] == report['employment'][i] for i in city.keys)
        assert sum(report['employment'].values()) + report['unemployed'] == report['labor_force']
        assert 'payroll_employment' in report


@pytest.mark.parametrize('field,value', [('household_size', -5), ('tax_refunded', {'1|IND': -9999999}),
                                          ('last_report', {'month': 1, 'employment': {'IND': -55}})])
def test_cache_and_refund_tampering_rejected(field, value):
    city = IndustryEconomy()
    city.advance()
    state = city.to_dict()
    state[field] = value
    with pytest.raises(ValueError):
        IndustryEconomy.from_dict(state)

def test_report_history_checks_full_structure_and_runtime():
    city = IndustryEconomy()
    history = [city.advance(), city.advance()]
    state = city.to_dict()
    state['report_history'] = history
    assert IndustryEconomy.from_dict(state).month == 2
    broken = deepcopy(state)
    broken['report_history'][0]['employment'] = {'IND': -5}
    with pytest.raises(ValueError):
        IndustryEconomy.from_dict(broken)
    broken = deepcopy(state)
    broken['last_report']['government_cash_cents'] += 1
    broken['report_history'][-1] = deepcopy(broken['last_report'])
    with pytest.raises(ValueError):
        IndustryEconomy.from_dict(broken)
    city.mayor_command(kind='subsidy_industry', industry='IND', cash_cr=10)
    city.mayor_command(kind='hire_civil', people=1)
    city.report_history = history
    assert IndustryEconomy.from_dict(json.loads(json.dumps(city.to_dict()))).month == 2

def test_public_works_pays_only_unemployed_cohorts():
    city = IndustryEconomy()
    city.unemployed_by_group = {h: 0 for h in city.population}
    city.unemployed_by_group['H0'] = 10
    before = dict(city.cash)
    assert city.mayor_command(kind='public_works', cash_cr=100)[0]
    assert city.cash['H0'] - before['H0'] == 10000
    assert all(city.cash[h] == before[h] for h in city.population if h != 'H0')


def test_civil_hiring_authorizes_continuing_positions():
    city = IndustryEconomy()
    initial_jobs = city.jobs('GOV')
    assert city.mayor_command(kind='hire_civil', people=100)[0]
    assert city.jobs('GOV') == initial_jobs + 100
    assert city.funded_civil_staff_plan == 100
    report = city.advance()
    assert report['layoffs']['GOV'] == 0
    assert report['employment']['GOV'] >= 150
    restored = IndustryEconomy.from_dict(json.loads(json.dumps(city.to_dict())))
    assert restored.funded_civil_staff_plan == 100
    assert restored.advance() == city.advance()


def test_wages_catch_up_to_inflated_reference():
    city = IndustryEconomy()
    city.advance()
    city.cpi = 2
    city.scenario['city']['inflation'] = 0
    city.nominal_wages = dict(city.base_wages)
    previous = dict(city.nominal_wages)
    city.update_prices_and_wages()
    assert all(city.nominal_wages[i] > previous[i] for i in city.keys)

def test_prepaid_raw_is_resold_to_firms_and_survives_save():
    city = IndustryEconomy()
    assert city.purchase_external(cash_cents=0, raw_units=100)[2] is None
    assert city.government_raw_units == 100
    restored = IndustryEconomy.from_dict(json.loads(json.dumps(city.to_dict())))
    report = restored.advance()
    resale = [t for t in report['transactions'] if t['kind'] == 'raw_resale_payment']
    assert sum(t['amount_cents'] for t in resale) > 0
    assert all(t['to'] == 'GOV' for t in resale)
    assert restored.cumulative_government_raw_resale_units > 0
    assert restored.government_raw_units + restored.cumulative_government_raw_resale_units == pytest.approx(100)
    restored.assert_invariants()


def test_prepaid_raw_never_overdelivers_across_owners():
    city = IndustryEconomy()
    city.external_raw = .005
    city.initial_external_raw = .005
    city.initial_raw = .005 + sum(city.raw.values())
    assert city.purchase_external(cash_cents=0, raw_units=.015)[2] is None
    report = city.advance()
    assert city.external_raw >= 0
    assert city.government_raw_units >= 0
    assert city.cumulative_raw_input_qty <= .02 + 1e-9
    assert city.cumulative_government_raw_resale_units <= .015 + 1e-9
    city.assert_invariants()

def test_optional_capital_spending_requires_profit_and_protects_working_cash():
    city = IndustryEconomy()
    first = city.advance()
    assert not any(t['kind'] == 'capital_purchase' and t['from'] not in ('GOV',) for t in first['transactions'])
    city.last_report['sectors']['SRV']['profit_cents'] = -1
    report = city.advance()
    assert not any(t['kind'] == 'capital_purchase' and t['from'] == 'SRV' for t in report['transactions'])


def test_raw_ownership_does_not_deliver_without_payment():
    city = IndustryEconomy()
    city.purchase_external(cash_cents=0, raw_units=10)
    city.cash['GOV'] += city.cash['IND']
    city.cash['IND'] = 0
    before = city.government_raw_units
    assert city.purchase_industry_raw('IND', 10, 1000000) == 0
    assert city.government_raw_units == before
    city.assert_invariants()

def test_optional_capital_budgets_never_spend_reserved_cash():
    from backend.systems.v21.industry_settle import private_capital_budgets
    city = IndustryEconomy()
    city.advance()
    for i in city.keys:
        city.last_report['sectors'][i]['profit_cents'] = 1
    budgets, reserves = private_capital_budgets(city)
    assert sum(budgets.values()) <= city.cr(city.scenario['city']['private_capex_cr'])
    for buyer, budget in budgets.items():
        assert budget <= max(0, city.cash[buyer] - reserves[buyer])

@pytest.mark.parametrize('kwargs', [{'months':0}, {'months':-1}, {'months':True}, {'months':1.2},
                                    {'people':True}, {'people':-1}, {'cash_cr':float('inf')},
                                    {'cash_cr':float('nan')}, {'cash_cr':-1}, {'cash_cr':True}])
def test_engine_policy_input_rejects_without_mutation(kwargs):
    city = IndustryEconomy()
    before = deepcopy(city.__dict__)
    with pytest.raises(ValueError):
        city.mayor_command(kind='limit_hire', industry='IND', **kwargs)
    assert city.__dict__ == before


@pytest.mark.parametrize('cash,raw', [(-1,0), (True,0), (1.2,0), (0,-1), (0,float('inf')), (0,float('nan')), (0,True)])
def test_engine_procurement_input_rejects_without_mutation(cash,raw):
    city = IndustryEconomy()
    before = deepcopy(city.__dict__)
    with pytest.raises(ValueError):
        city.purchase_external(cash_cents=cash, raw_units=raw)
    assert city.__dict__ == before
