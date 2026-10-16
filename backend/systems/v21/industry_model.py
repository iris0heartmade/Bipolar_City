"""行业双指标模型的共用公式实现；GUI、游戏和参考验证均调用本模块。

所有货币输入为整数分；人数为非负整数。验证程序明确提供合成场景，
不把它们当作已经校准的游戏城市。公式说明见行业双指标模型规范。
"""
from dataclasses import dataclass
import json
import math
from pathlib import Path


def load_parameters():
    root = Path(__file__).resolve().parents[3]
    path = root / 'data/economy_v2/industry_labor.json'
    params = json.loads(path.read_text(encoding='utf-8'))
    validate_parameters(params)
    return params


def validate_parameters(params):
    """外部参数先验校验，防止负权重、零除和非法人数速度进入公式。"""
    a, c, f = params['attractiveness'], params['capacity'], params['labor_flow']
    for key in ('weights', 'security_weights', 'environment_weights'):
        fraction(**a[key])
        if abs(sum(a[key].values()) - 1) > 1e-9:
            raise ValueError(f'{key} 权重总和必须为1')
    if not a['history_weights_newest_first']:
        raise ValueError('历史权重不能为空')
    nonnegative(**{str(i): v for i, v in enumerate(a['history_weights_newest_first'])})
    if abs(sum(a['history_weights_newest_first']) - 1) > 1e-9:
        raise ValueError('历史权重总和必须为1')
    for key in ('reference_gross_wage_cr', 'reference_living_cost_cr',
                'earnings_log_scale', 'secure_runway_months'):
        nonnegative(**{key: a[key]})
        if a[key] == 0:
            raise ValueError(f'{key} 必须为正')
    for key in ('reference_wage_pit_rate', 'reference_earnings_score',
                'score_update_weight', 'neutral_environment', 'new_employer_prior_pay_ratio'):
        fraction(**{key: a[key]})
    if a['reference_wage_pit_rate'] == 1:
        raise ValueError('参考税率必须小于1')
    for key in ('reserve_horizon_months', 'labor_output_exponent'):
        nonnegative(**{key: c[key]})
        if c[key] == 0:
            raise ValueError(f'{key} 必须为正')
    for key in ('monthly_hire_rate', 'monthly_soft_exit_rate', 'monthly_switch_rate'):
        fraction(**{key: f[key]})
    integer_count(startup_hiring_reference_people=f['startup_hiring_reference_people'])
    nonnegative(switch_score_gap=f['switch_score_gap'])


def nonnegative(**values):
    for name, value in values.items():
        if not math.isfinite(value) or value < 0:
            raise ValueError(f'{name} 必须是有限非负数')


def fraction(**values):
    nonnegative(**values)
    if any(v > 1 for v in values.values()):
        raise ValueError('比例必须在0到1之间')


def integer_count(**values):
    nonnegative(**values)
    if any(type(v) is not int for v in values.values()):
        raise ValueError('人数和货币分必须为整数')


def clipped(value, low=0.0, high=1.0):
    return min(high, max(low, value))


@dataclass(frozen=True)
class Capacity:
    structural: int
    current_cash: int
    recurring_finance: int
    inputs: int
    hard_limit: int
    limit: int
    bottlenecks: tuple


def capacity(*, jobs, wage_cents, payroll_budget_cents,
             recurring_receipts_cents, recurring_nonpayroll_cents,
             reserve_cents, full_load_inputs, available_inputs,
             nonlabor_factor, params):
    """已分配给本行业的投入和工资预算进入此函数；没有资产副作用。"""
    integer_count(jobs=jobs, wage=wage_cents, payroll=payroll_budget_cents,
                  receipts=recurring_receipts_cents,
                  nonpayroll=recurring_nonpayroll_cents, reserve=reserve_cents)
    fraction(nonlabor_factor=nonlabor_factor)
    if wage_cents == 0:
        raise ValueError('有薪行业工资必须大于0')
    nonnegative(**full_load_inputs)
    nonnegative(**available_inputs)
    if reserve_cents > payroll_budget_cents:
        raise ValueError('可动用储备不得大于当月真实工资资金上界')
    p = params['capacity']
    cash = payroll_budget_cents // wage_cents
    support = max(0, recurring_receipts_cents - recurring_nonpayroll_cents)
    draw = math.floor(reserve_cents / p['reserve_horizon_months'])
    recurring = (support + draw) // wage_cents
    activity = nonlabor_factor
    for good, needed in full_load_inputs.items():
        if needed > 0:
            activity = min(activity, available_inputs.get(good, 0) / needed)
    if nonlabor_factor == 0:
        inputs = 0
    else:
        labor_ratio = clipped(activity / nonlabor_factor) ** (1 / p['labor_output_exponent'])
        inputs = min(jobs, math.floor(jobs * labor_ratio + 1e-10))
    parts = {'building': jobs, 'cash': cash, 'recurring_finance': recurring, 'inputs': inputs}
    limit = min(parts.values())
    return Capacity(jobs, cash, recurring, inputs, min(jobs, cash), limit,
                    tuple(k for k, v in parts.items() if v == limit))


def employment_target(*, jobs, deliverable_activity, nonlabor_factor, limit, params):
    """市场部门的计划产出活动量反推用工；公共部门直接提供有资金的目标。"""
    integer_count(jobs=jobs, limit=limit)
    nonnegative(deliverable_activity=deliverable_activity)
    fraction(nonlabor_factor=nonlabor_factor)
    if nonlabor_factor == 0:
        return 0
    exponent = params['capacity']['labor_output_exponent']
    required = jobs * clipped(deliverable_activity / nonlabor_factor) ** (1 / exponent)
    return min(limit, math.ceil(required - 1e-10))


def attractiveness(*, net_paid_wage_cents, delivered_benefit_cents,
                   living_cost_cents, timely_pay_ratio, cash_runway_months,
                   workplace_safety=None, working_time_quality=None,
                   previous_score=None, params):
    """接收已按历史权重聚合的净实发工资/按时率；无证据的环境用中性值。"""
    integer_count(wage=net_paid_wage_cents, benefit=delivered_benefit_cents,
                  living_cost=living_cost_cents)
    fraction(timely_pay_ratio=timely_pay_ratio)
    nonnegative(cash_runway=cash_runway_months)
    if living_cost_cents == 0:
        raise ValueError('生活成本必须大于0')
    p = params['attractiveness']
    anchor = (p['reference_gross_wage_cr'] * (1 - p['reference_wage_pit_rate'])
              / p['reference_living_cost_cr'])
    real = (net_paid_wage_cents + delivered_benefit_cents) / living_cost_cents
    wage = 0.0 if real == 0 else clipped(
        p['reference_earnings_score'] + p['earnings_log_scale'] * math.log2(real / anchor))
    sw = p['security_weights']
    security = (sw['timely_pay'] * timely_pay_ratio
                + sw['cash_runway'] * clipped(cash_runway_months / p['secure_runway_months']))
    parts = []
    for key, value in [('safety', workplace_safety), ('working_time', working_time_quality)]:
        if value is not None:
            fraction(**{key: value})
        parts.append(p['environment_weights'][key] *
                     (p['neutral_environment'] if value is None else value))
    environment = sum(parts)
    w = p['weights']
    raw = 100 * (w['earnings'] * wage + w['security'] * security
                 + w['environment'] * environment)
    if previous_score is not None:
        nonnegative(previous_score=previous_score)
        if previous_score > 100:
            raise ValueError('上一期吸引力不得超过100')
        alpha = p['score_update_weight']
        raw = alpha * raw + (1 - alpha) * previous_score
    score = min(raw, 100 * timely_pay_ratio)
    return {'score': score, 'earnings': wage, 'security': security,
            'environment': environment, 'environment_is_assumed':
            workplace_safety is None or working_time_quality is None}


def quota(rate, base, room, carry):
    """积累不足一人的小数；无机会时清空，不能储存多年招聘额度。"""
    if room <= 0:
        return 0, 0.0
    raw = rate * base + carry
    count = min(room, math.floor(raw + 1e-10))
    return count, max(0.0, min(1-1e-12, raw - math.floor(raw + 1e-10)))


def match(supplies, vacancies, benefits):
    """小规模整数最小费用最大流：先最大化录用人数，再最大化评分收益。

    supplies/目的行业排序保证输入字典顺序不影响结果。残余反向边允许
    撤回早先选择，避免一个有多种选择的申请人占掉别人的唯一岗位。
    """
    origins = sorted(k for k, v in supplies.items() if v > 0)
    destinations = sorted(k for k, v in vacancies.items() if v > 0)
    start, end = 0, 1 + len(origins) + len(destinations)
    graph = [[] for _ in range(end + 1)]

    def edge(a, b, cap, cost):
        forward = [b, len(graph[b]), cap, cost]
        graph[a].append(forward)
        graph[b].append([a, len(graph[a]) - 1, 0, -cost])
        return forward

    references = {}
    for a, origin in enumerate(origins, 1):
        edge(start, a, supplies[origin], 0)
        for b, dest in enumerate(destinations, 1 + len(origins)):
            if (origin, dest) in benefits:
                cap = min(supplies[origin], vacancies[dest])
                e = edge(a, b, cap, -round(benefits[origin, dest] * 1000))
                references[origin, dest] = (e, cap)
    for b, dest in enumerate(destinations, 1 + len(origins)):
        edge(b, end, vacancies[dest], 0)
    while True:
        distance, previous = [math.inf] * len(graph), [None] * len(graph)
        distance[start] = 0
        for _ in range(len(graph) - 1):
            changed = False
            for a, edges in enumerate(graph):
                for n, e in enumerate(edges):
                    b, _, available, cost = e
                    if available > 0 and distance[a] + cost < distance[b]:
                        distance[b], previous[b] = distance[a] + cost, (a, n)
                        changed = True
            if not changed:
                break
        if previous[end] is None:
            break
        amount, b = math.inf, end
        while b != start:
            a, n = previous[b]
            amount, b = min(amount, graph[a][n][2]), a
        b = end
        while b != start:
            a, n = previous[b]
            e = graph[a][n]
            e[2] -= amount
            graph[b][e[1]][2] += amount
            b = a
    return {pair: initial - e[2] for pair, (e, initial) in references.items()
            if initial - e[2] > 0}


def labor_month(*, employed, unemployed, capacities, hard_limits, targets,
                scores, params, carries=None, limit_hire=(), limit_layoff=(), layoff_cap_share=None):
    """冻结输入，退出/待业招聘/在职转行依次计算，最后同时写出人数。

    这里只验证按行业总人数的流动。生产接口应把返回的流动按居民群体
    细分至X[h,i]，工资才由真实雇主付给相应群体。
    """
    keys = sorted(employed)
    integer_count(unemployed=unemployed, **employed)
    for mapping in (capacities, hard_limits, targets, scores):
        if set(mapping) != set(keys):
            raise ValueError('行业输入键必须完全相同')
    for i in keys:
        integer_count(capacity=capacities[i], hard=hard_limits[i], target=targets[i])
        if not 0 <= targets[i] <= capacities[i] <= hard_limits[i]:
            raise ValueError('必须满足0<=目标<=容纳限度<=硬限度')
        if not math.isfinite(scores[i]) or not 0 <= scores[i] <= 100:
            raise ValueError('吸引力必须为0到100有限数')
    f = params['labor_flow']
    state = dict(carries or {})
    working, layoffs, vacancy = {}, {}, {}
    for i in keys:
        forced = max(0, employed[i] - hard_limits[i])
        if layoff_cap_share is not None:
            forced = min(forced, math.ceil(employed[i] * layoff_cap_share))
        if i in limit_layoff:
            forced = 0
        retained = employed[i] - forced
        gap = max(0, retained - targets[i])
        soft, state[i, 'exit'] = quota(f['monthly_soft_exit_rate'], retained,
                                      gap, state.get((i, 'exit'), 0.0))
        if layoff_cap_share is not None:
            soft = min(soft, max(0, math.ceil(employed[i] * layoff_cap_share) - forced))
        if i in limit_layoff:
            soft = 0
            state[i, 'exit'] = 0.0
        layoffs[i], working[i] = forced + soft, retained - soft
        room = max(0, targets[i] - working[i])
        vacancy[i], state[i, 'hire'] = quota(
            f['monthly_hire_rate'], max(working[i], f['startup_hiring_reference_people']),
            room, state.get((i, 'hire'), 0.0))
            # Hiring freezes apply before matching, including transfers.
    for i in limit_hire:
        vacancy[i] = 0
        state[i, 'hire'] = 0.0
    seekers = unemployed + sum(layoffs.values())
    hired_flows = match({'U': seekers}, vacancy, {('U', i): scores[i] for i in keys})
    hired = {i: hired_flows.get(('U', i), 0) for i in keys}
    vacancy = {i: vacancy[i] - hired[i] for i in keys}
    switch_supply, benefit = {}, {}
    for i in keys:
        eligible = [j for j in keys if vacancy[j] > 0 and scores[j] - scores[i] > f['switch_score_gap']]
        switch_supply[i], state[i, 'switch'] = quota(
            f['monthly_switch_rate'], working[i], working[i] if eligible else 0,
            state.get((i, 'switch'), 0.0))
        for j in eligible:
            benefit[i, j] = scores[j] - scores[i] - f['switch_score_gap']
    switches = match(switch_supply, vacancy, benefit)
    result = {i: working[i] + hired[i] for i in keys}
    for (origin, destination), count in switches.items():
        result[origin] -= count
        result[destination] += count
    final_unemployed = seekers - sum(hired.values())
    if sum(result.values()) + final_unemployed != sum(employed.values()) + unemployed:
        raise AssertionError('劳动力不守恒')
    if any(result[i] < 0 or result[i] > max(hard_limits[i], employed[i] if i in limit_layoff or layoff_cap_share is not None else 0) for i in keys):
        raise AssertionError('人数越过硬边界')
    return {'employed': result, 'unemployed': final_unemployed,
            'layoffs': layoffs, 'hires_from_unemployment': hired,
            'switches': switches, 'carries': state}
