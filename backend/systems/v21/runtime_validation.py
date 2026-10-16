"""用正式月结实跑评估稳定性；门槛公开配置，不修改被评估城市。"""
from copy import deepcopy

from backend.systems.v21.industry_economy import IndustryEconomy
from backend.systems.v21.industry_scenario import default_scenario, read_config


def summarize(city, report):
    employed = sum(city.employment().values())
    return {
        'month': city.month, 'population': sum(city.population.values()),
        'labor_force': city.labor_force, 'employment': employed,
        'unemployment_rate': report['unemployed'] / max(1, city.labor_force),
        'government_cash_cents': city.cash['GOV'],
        'sales_cents': report['sales_cents'],
        'consumer_spending_cents': report['consumer_spending_cents'],
        'wages_due_cents': report['wages_cents'],
        'wages_paid_cents': sum(t['amount_cents'] for t in report['transactions']
                                if t['kind'] in ('wage', 'wage_arrears_repayment')),
        'wage_arrears_cents': report['wage_arrears_cents'],
        'cash_residual_cents': report['cash_residual_cents'],
        'external_raw_units': city.external_raw,
        'industries': {i: {'employed': s['employed'], 'sales_cents': s['sales_cents'],
                           'cash_cents': s['cash_cents']} for i, s in report['sectors'].items()},
    }


def validate_runtime(months, scenario=None, before_month=None):
    """每月执行真实结算及守恒；记录每次干预的明确成本与结果。"""
    if type(months) is not int or not 1 <= months <= 120:
        raise ValueError('验收月数须为1—120整数')
    city = IndustryEconomy(deepcopy(scenario or default_scenario()))
    initial_employment = sum(city.employment().values())
    thresholds = read_config('runtime_validation.json')
    rows, interventions, errors = [], [], []
    for month in range(1, months + 1):
        if before_month:
            actions = before_month(city, month)
            interventions.extend(actions or [])
        try:
            report = city.advance()
            city.assert_invariants()
        except (AssertionError, ValueError) as exc:
            errors.append({'month': month, 'reason': str(exc)})
            break
        rows.append(summarize(city, report))
    final = rows[-1] if rows else None
    failures = []
    if len(rows) != months:
        failures.append('未完成全部请求月份')
    if final:
        if final['employment'] < initial_employment * thresholds['minimum_employment_retention']:
            failures.append('就业保留率低于门槛')
        if final['unemployment_rate'] > thresholds['maximum_unemployment_rate']:
            failures.append('失业率超过门槛')
        if final['wage_arrears_cents'] > thresholds['maximum_wage_arrears_cr'] * 100:
            failures.append('存在未偿工资欠款')
        if thresholds['required_positive_consumer_spending'] and final['consumer_spending_cents'] <= 0:
            failures.append('实际居民消费已停止')
        if thresholds['required_positive_sales'] and final['sales_cents'] <= 0:
            failures.append('行业成交已停止')
    return {'engine': 'IndustryEconomy', 'scope': thresholds['scope'],
            'months_requested': months, 'months_executed': len(rows),
            'thresholds': thresholds, 'initial_employment': initial_employment,
            'passed': not errors and not failures, 'failures': failures, 'errors': errors,
            'history': rows, 'interventions': interventions}
