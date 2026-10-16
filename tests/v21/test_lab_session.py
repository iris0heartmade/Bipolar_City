from copy import deepcopy
import json
import pytest
from tools.lab_session import LabSession


def test_initial_and_incremental_months():
    incremental, continuous = LabSession(), LabSession()
    assert incremental.city.month == 0 and incremental.history == []
    incremental.advance()
    incremental.advance()
    continuous.advance(2)
    assert incremental.to_dict() == continuous.to_dict()


def test_policy_does_not_reset_and_round_trip():
    session = LabSession()
    session.advance(2)
    session.command('limit_layoff', industry='IND', months=1)
    assert session.city.month == 2
    restored = LabSession.from_dict(json.loads(json.dumps(session.to_dict())))
    session.advance()
    restored.advance()
    assert restored.to_dict() == session.to_dict()


def test_parameters_only_change_future_and_sync_derived():
    session = LabSession()
    session.advance()
    old = deepcopy(session.history)
    session.set_parameter('city', 'household_consumption_size', 2.5)
    session.set_parameter('city', 'living_cost_cr', 120)
    assert session.city.month == 1 and session.history == old
    assert session.city.household_size == 2.5
    assert session.city.living_cost_cents == round(12000 * session.city.cpi)
    assert session.scenario == session.city.scenario


@pytest.mark.parametrize('months', [0, -1, True, 1.5, 121])
def test_invalid_advance_is_atomic(months):
    session = LabSession()
    before = session.to_dict()
    with pytest.raises(ValueError):
        session.advance(months)
    assert session.to_dict() == before


def test_invalid_parameter_is_atomic():
    session = LabSession()
    before = session.to_dict()
    with pytest.raises(ValueError):
        session.set_parameter('sector', 'wage_multiplier', 999)
    assert session.to_dict() == before


def test_config_is_not_runtime():
    session = LabSession()
    with pytest.raises(ValueError):
        LabSession.from_dict(session.scenario)


def test_failed_second_month_rolls_back_entire_batch(monkeypatch):
    from backend.systems.v21.industry_economy import IndustryEconomy
    session = LabSession()
    before = session.to_dict()
    original = IndustryEconomy.advance

    def fail_at_second_month(city):
        if city.month == 1:
            raise RuntimeError('injected settlement failure')
        return original(city)

    monkeypatch.setattr(IndustryEconomy, 'advance', fail_at_second_month)
    with pytest.raises(RuntimeError):
        session.advance(2)
    assert session.to_dict() == before


def test_rejected_command_does_not_mutate_city_or_history():
    session = LabSession()
    session.advance()
    before = session.to_dict()
    with pytest.raises(ValueError):
        session.command('subsidy_industry', industry='IND', cash_cr=10**12)
    assert session.to_dict() == before


@pytest.mark.parametrize('corrupt', ['missing_history', 'bad_month', 'missing_city', 'commands', 'baseline_month'])
def test_corrupt_session_rejected(corrupt):
    session = LabSession()
    session.advance(2)
    payload = session.to_dict()
    if corrupt == 'missing_history':
        payload['history'].pop()
    elif corrupt == 'bad_month':
        payload['history'][0]['month'] = 2
    elif corrupt == 'missing_city':
        del payload['city']
    elif corrupt == 'commands':
        payload['commands'] = [{'month': 3, 'kind': 'limit_layoff'}]
    else:
        payload['baseline_city']['month'] = 1
    with pytest.raises(ValueError):
        LabSession.from_dict(payload)


def test_json_encoding_failure_preserves_file(tmp_path):
    from tools.lab_session import write_json_atomic
    destination = tmp_path / 'session.json'
    destination.write_text('original', encoding='utf-8')
    with pytest.raises(ValueError):
        write_json_atomic(destination, {'invalid': float('nan')})
    assert destination.read_text(encoding='utf-8') == 'original'
    assert len(list(tmp_path.iterdir())) == 1


def test_parameter_change_keeps_baseline_and_prior_transactions():
    session = LabSession()
    session.advance(2)
    previous = deepcopy(session.history)
    baseline = deepcopy(session.baseline)
    session.set_parameter('sector', 'wage_multiplier', 1.5)
    session.advance()
    assert session.history[:2] == previous and session.baseline[:2] == baseline
    assert session.history[-1]['wages_cents'] != session.baseline[-1]['wages_cents']
    assert session.baseline_city.scenario != session.city.scenario


def test_civil_hiring_configuration_survives_save_load_and_continue():
    session = LabSession()
    session.advance()
    session.command('hire_civil', people=10)
    assert session.city.employment()['GOV'] == 60
    restored = LabSession.from_dict(json.loads(json.dumps(session.to_dict())))
    assert session.scenario == session.city.scenario
    session.advance()
    restored.advance()
    assert session.to_dict() == restored.to_dict()
    assert restored.city.employment()['GOV'] == 60


@pytest.mark.parametrize('series', ['history', 'baseline'])
@pytest.mark.parametrize('damage', ['missing_summary', 'missing_sector', 'numeric_text', 'bad_bottleneck'])
def test_all_saved_reports_are_complete_and_renderable(series, damage):
    session = LabSession()
    session.advance(2)
    payload = session.to_dict()
    report = payload[series][0]
    if damage == 'missing_summary':
        del report['cash_residual_cents']
    elif damage == 'missing_sector':
        del report['sectors']['IND']['output_units']
    elif damage == 'numeric_text':
        report['sectors']['IND']['attractiveness'] = 'not-a-number'
    else:
        report['sectors']['IND']['capacity_details']['bottlenecks'] = ['invalid-key']
    with pytest.raises(ValueError):
        LabSession.from_dict(payload)


@pytest.mark.parametrize('parameters', [
    {'months': 0}, {'months': -1}, {'months': 121}, {'months': True}, {'months': 1.5},
    {'cash_cr': True}, {'cash_cr': -1}, {'cash_cr': float('inf')}, {'cash_cr': float('nan')},
    {'people': True}, {'people': -1}, {'people': 1.5},
])
def test_invalid_policy_parameters_are_atomic(parameters):
    session = LabSession()
    before = session.to_dict()
    with pytest.raises(ValueError):
        session.command('limit_layoff', industry='IND', **parameters)
    assert session.to_dict() == before


@pytest.mark.parametrize('parameters', [
    {'cash_cents': True}, {'cash_cents': -1}, {'cash_cents': 1.5},
    {'raw_units': -1}, {'raw_units': True}, {'raw_units': float('inf')},
])
def test_invalid_external_purchase_parameters_are_atomic(parameters):
    session = LabSession()
    before = session.to_dict()
    command = {'cash_cents': 0, 'raw_units': 0, **parameters}
    with pytest.raises(ValueError):
        session.command('purchase_external', **command)
    assert session.to_dict() == before
