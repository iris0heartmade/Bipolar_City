"""生成可重复的独立模型验证证据：python -m tools.model_design.validate_industry_labor。"""
from dataclasses import asdict
import itertools
import json
import math
from pathlib import Path
import random

from tools.model_design.industry_labor import (
    attractiveness, capacity, employment_target, labor_month, load_parameters, match,
)


def run_validation():
    params = load_parameters()
    cases = []

    def check(name, condition, evidence):
        if not condition:
            raise AssertionError(f'{name}: {evidence}')
        cases.append({'name': name, 'passed': True, 'evidence': evidence})

    for key in ('weights', 'security_weights', 'environment_weights'):
        weights = params['attractiveness'][key]
        assert all(value >= 0 for value in weights.values())
        assert abs(sum(weights.values()) - 1) < 1e-12
    check('configured_weights_are_valid', True,
          {key: params['attractiveness'][key] for key in ('weights', 'security_weights', 'environment_weights')})

    cap_args = dict(jobs=100, wage_cents=9000, payroll_budget_cents=900000,
                    recurring_receipts_cents=1120000, recurring_nonpayroll_cents=400000,
                    reserve_cents=300000, full_load_inputs={'ORE': 100},
                    available_inputs={'ORE': 100}, nonlabor_factor=1.0, params=params)
    baseline = capacity(**cap_args)
    check('capacity_baseline', baseline.limit == 91 and baseline.hard_limit == 100, asdict(baseline))
    cash_bad = capacity(**{**cap_args, 'payroll_budget_cents': 270000, 'reserve_cents': 0})
    check('current_cash_is_hard_bound', cash_bad.limit == 30 and cash_bad.hard_limit == 30, asdict(cash_bad))
    inputs_bad = capacity(**{**cap_args, 'available_inputs': {'ORE': 50}})
    check('input_inverse_matches_exponent', inputs_bad.limit == 42, asdict(inputs_bad))
    zero = capacity(**{**cap_args, 'available_inputs': {'ORE': 0}})
    check('missing_input_stops_operational_capacity', zero.limit == 0, asdict(zero))
    no_recipe = capacity(**{**cap_args, 'full_load_inputs': {}, 'available_inputs': {}})
    check('no_material_recipe_does_not_mean_zero_jobs', no_recipe.inputs == 100, asdict(no_recipe))
    no_activity = capacity(**{**cap_args, 'nonlabor_factor': 0.0})
    check('zero_operating_factor_stops_capacity', no_activity.limit == 0, asdict(no_activity))
    narrow_jobs = capacity(**{**cap_args, 'jobs': 20})
    check('cash_cannot_create_building_jobs', narrow_jobs.limit <= 20, asdict(narrow_jobs))
    rich_no_orders = capacity(**{**cap_args, 'recurring_receipts_cents': 0, 'reserve_cents': 0})
    check('no_support_means_no_sustainable_capacity', rich_no_orders.limit == 0, asdict(rich_no_orders))
    inflated = capacity(**{**cap_args, 'wage_cents': 18000, 'payroll_budget_cents': 1800000,
                           'recurring_receipts_cents': 2240000, 'recurring_nonpayroll_cents': 800000,
                           'reserve_cents': 600000})
    check('nominal_scaling_preserves_capacity', inflated.limit == baseline.limit, asdict(inflated))

    def score(wage=8280, benefit=0, living=5000, pay=1, runway=3, previous=None, safety=None):
        return attractiveness(net_paid_wage_cents=wage, delivered_benefit_cents=benefit,
                              living_cost_cents=living, timely_pay_ratio=pay,
                              cash_runway_months=runway, previous_score=previous,
                              workplace_safety=safety, params=params)

    root = Path(__file__).resolve().parents[2]
    industry_config = json.loads((root / 'data/economy_v2/industries.json').read_text(encoding='utf-8'))
    cohort_config = json.loads((root / 'data/economy_v2/cohorts.json').read_text(encoding='utf-8'))
    tiers = industry_config['_meta']['wage_tier_cr']
    pit = cohort_config['pit_rules']['personal_income_tax_rate']
    net_wages = {i: round(tiers[d['wage_tier']] * 100 * (1 if i == 'AGR' else 1-pit))
                 for i, d in industry_config['industries'].items()}
    scores = {i: score(wage=w) for i, w in net_wages.items()}
    check('existing_industry_configuration_reused', net_wages['IND'] == 8280 and len(net_wages) == 7, net_wages)
    check('existing_taxed_wage_ranking', scores['SRV']['score'] > scores['IND']['score'] > scores['AGR']['score'], scores)
    check('no_invented_government_or_church_preference', scores['GOV']['score'] == scores['CHU']['score'], scores['GOV'])
    check('delivered_benefit_can_improve_low_wage_job', score(wage=6000, benefit=2000)['score'] > score(wage=6000)['score'], score(wage=6000, benefit=2000))
    check('unpaid_high_offer_not_high_quality', score(wage=0, pay=0, previous=90)['score'] == 0, score(wage=0, pay=0, previous=90))
    check('nominal_scaling_preserves_attractiveness', abs(score(wage=16560, living=10000)['score'] - score()['score']) < 1e-9, score(wage=16560, living=10000))
    check('higher_local_cost_reduces_attractiveness', score(living=7500)['score'] < score()['score'], score(living=7500))
    check('working_environment_has_auditable_effect', score(safety=1)['score'] > score(safety=0)['score'], {'safe': score(safety=1), 'unsafe': score(safety=0)})
    target = employment_target(jobs=100, deliverable_activity=0.6**0.8,
                               nonlabor_factor=1, limit=91, params=params)
    check('demand_inverse_recovers_employment', target == 60, {'target': target, 'K': 91})
    actual_activity = min((60/100)**0.8, 50/100)
    feasible_at_limit = (inputs_bad.limit/100)**0.8
    infeasible_next = ((inputs_bad.limit+1)/100)**0.8
    check('input_and_labor_constraint_is_applied_once', actual_activity == 0.5 and feasible_at_limit <= 0.5 < infeasible_next,
          {'activity_with_60_workers': actual_activity, 'activity_at_capacity': feasible_at_limit,
           'activity_one_worker_above_capacity': infeasible_next})

    def flow(e, u, k, h, t, a, carries=None):
        return labor_month(employed=e, unemployed=u, capacities=k, hard_limits=h,
                           targets=t, scores=a, params=params, carries=carries)

    closed = flow({'AGR': 100, 'SRV': 100}, 0, {'AGR': 100, 'SRV': 100},
                  {'AGR': 100, 'SRV': 100}, {'AGR': 100, 'SRV': 100}, {'AGR': 20, 'SRV': 95})
    check('high_score_without_vacancy_cannot_pull_workers', not closed['switches'], closed['employed'])
    switching = flow({'AGR': 100, 'SRV': 50}, 0, {'AGR': 100, 'SRV': 100},
                     {'AGR': 100, 'SRV': 100}, {'AGR': 100, 'SRV': 100}, {'AGR': 20, 'SRV': 95})
    check('offered_switch_is_limited_and_conserved', switching['switches'].get(('AGR', 'SRV')) == 5 and switching['unemployed'] == 0,
          {'employed': switching['employed'], 'switches': [[*k, v] for k, v in switching['switches'].items()]})
    seekers_first = flow({'AGR': 100, 'SRV': 50}, 10, {'AGR': 100, 'SRV': 100},
                         {'AGR': 100, 'SRV': 100}, {'AGR': 100, 'SRV': 100}, {'AGR': 20, 'SRV': 95})
    check('unemployed_receive_offer_before_incumbent_switch', not seekers_first['switches'] and seekers_first['hires_from_unemployment']['SRV'] == 5,
          seekers_first['hires_from_unemployment'])
    little_gap = flow({'AGR': 100, 'SRV': 50}, 0, {'AGR': 100, 'SRV': 100},
                     {'AGR': 100, 'SRV': 100}, {'AGR': 100, 'SRV': 100}, {'AGR': 60, 'SRV': 65})
    check('switch_deadband_stops_churn', not little_gap['switches'], little_gap['employed'])
    hard = flow({'IND': 100}, 0, {'IND': 30}, {'IND': 30}, {'IND': 30}, {'IND': 60})
    check('hard_cash_loss_cannot_be_hidden_by_soft_exit', hard['employed']['IND'] == 30 and hard['unemployed'] == 70, hard['employed'])
    soft = flow({'IND': 100}, 0, {'IND': 100}, {'IND': 100}, {'IND': 0}, {'IND': 60})
    check('demand_drop_is_gradual_when_payroll_is_funded', soft['employed']['IND'] == 90, soft['employed'])
    startup = flow({'MAG': 0}, 20, {'MAG': 10}, {'MAG': 10}, {'MAG': 10}, {'MAG': 80})
    check('new_industry_can_recruit_without_incumbents', startup['employed']['MAG'] == 2, startup['employed'])
    reroute = match({'flexible': 1, 'restricted': 1}, {'A': 1, 'B': 1},
                    {('flexible', 'A'): 100, ('flexible', 'B'): 50, ('restricted', 'A'): 90})
    check('matching_does_not_waste_reachable_jobs', sum(reroute.values()) == 2,
          [[*k, v] for k, v in reroute.items()])

    # 对2x2匹配进行穷举比对：验证最大录用数和最大收益，不仅检查程序自身标志。
    rng = random.Random(20261015)
    for _ in range(100):
        supply = {'x': rng.randrange(3), 'y': rng.randrange(3)}
        vacancy = {'A': rng.randrange(3), 'B': rng.randrange(3)}
        pairs = list(itertools.product(supply, vacancy))
        benefit = {pair: rng.randrange(1, 10) for pair in pairs if rng.random() > 0.25}
        actual = match(supply, vacancy, benefit)
        best = (0, 0)
        for counts in itertools.product(range(3), repeat=4):
            trial = dict(zip(pairs, counts))
            if any(trial[pair] and pair not in benefit for pair in pairs):
                continue
            if any(sum(trial[x, y] for y in vacancy) > supply[x] for x in supply):
                continue
            if any(sum(trial[x, y] for x in supply) > vacancy[y] for y in vacancy):
                continue
            best = max(best, (sum(counts), sum(n * benefit.get(pair, 0) for pair, n in trial.items())))
        assert (sum(actual.values()), sum(n * benefit[pair] for pair, n in actual.items())) == best
    check('matching_100_cases_vs_exhaustive_optimum', True, {'cases': 100, 'seed': 20261015})

    for _ in range(500):
        keys = ['AGR', 'IND', 'SRV', 'CHU', 'BNK', 'MAG', 'GOV']
        e = {i: rng.randrange(0, 301) for i in keys}
        h = {i: rng.randrange(0, 401) for i in keys}
        k = {i: rng.randrange(h[i] + 1) for i in keys}
        t = {i: rng.randrange(k[i] + 1) for i in keys}
        a = {i: rng.random() * 100 for i in keys}
        u = rng.randrange(200)
        actual = flow(e, u, k, h, t, a)
        reverse = lambda x: dict(reversed(list(x.items())))
        reordered = flow(reverse(e), u, reverse(k), reverse(h), reverse(t), reverse(a))
        assert actual == reordered
        assert sum(actual['employed'].values()) + actual['unemployed'] == sum(e.values()) + u
        assert all(0 <= actual['employed'][i] <= h[i] for i in keys)
        for (origin, dest), count in actual['switches'].items():
            assert a[dest] - a[origin] > params['labor_flow']['switch_score_gap'] and count > 0
    check('labor_500_random_cases', True, {'cases': 500, 'seed': 20261015,
          'checked': ['labor_conservation', 'hard_limits', 'strict_switch_improvement', 'order_independence']})

    e, u, carry, trace = {'AGR': 100, 'SRV': 50}, 0, None, []
    for month in range(1, 121):
        result = flow(e, u, {'AGR': 100, 'SRV': 100}, {'AGR': 100, 'SRV': 100},
                      {'AGR': 100, 'SRV': 100}, {'AGR': 20, 'SRV': 95}, carry)
        e, u, carry = result['employed'], result['unemployed'], result['carries']
        trace.append({'month': month, 'employed': e, 'unemployed': u})
    check('120_steps_static_opportunity_converges_without_labor_loss', e == {'AGR': 50, 'SRV': 100} and u == 0,
          {'first': trace[0], 'last': trace[-1], 'scope': '固定评分/容量/目标的劳动力子模型，不是世界经济模拟'})
    e, u, carry = {'BNK': 1, 'SRV': 10}, 0, None
    for _ in range(20):
        result = flow(e, u, {'BNK': 1, 'SRV': 20}, {'BNK': 1, 'SRV': 20},
                      {'BNK': 1, 'SRV': 20}, {'BNK': 20, 'SRV': 90}, carry)
        e, u, carry = result['employed'], result['unemployed'], result['carries']
    check('fractional_carry_allows_small_sector_mobility', e['BNK'] == 0 and e['SRV'] == 11, e)

    for wage, cost, pay in [(-1, 5000, 1), (8280, 0, 1), (8280, 5000, math.nan)]:
        try:
            score(wage=wage, living=cost, pay=pay)
        except ValueError:
            continue
        raise AssertionError('非法评分输入未被拒绝')
    check('invalid_inputs_rejected', True, ['negative_money', 'zero_living_cost', 'NaN_pay_ratio'])
    return {'model_version': params['model_version'], 'date': '2026-10-15',
            'scope': '独立模型参考验证，不是现有游戏内核验收',
            'passed_cases': len(cases), 'cases': cases, 'labor_trace_120_steps': trace}


def main():
    result = run_validation()
    root = Path(__file__).resolve().parents[2]
    path = root / 'docs/04_验收记录/行业双指标模型验证-2026-10-15.json'
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'passed_cases': result['passed_cases'], 'report': str(path)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
