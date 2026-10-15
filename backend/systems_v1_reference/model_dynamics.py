"""经营反馈：变化有资金和时间门槛，离场不凭空销毁钱包。"""
import math
from backend.config import REFERENCE_CITY
from backend.model.economy import cr_to_cents
from backend.systems.model_ledger import split


def compute_capacity(sim) -> dict:
    """城市K值（Carrying Capacity）：在所有约束生效时，该城市能容纳的总人口上限。

    公式（详见 data/economy/model_parameters_v1.json 的 city.capacity_definition）：
        K = round(min(K_housing, K_jobs) × K_resource × K_safety × K_skill × K_worktime × K_service)

    「瓶颈」只取住房与岗位的较小者：这两个是硬上限，没房没工作就待不下去。
    资源作为调节乘子而非硬瓶颈：单资源耗尽让 K 收缩但不归零，避免城市瞬间崩溃。
    这样游戏内「城市资源紧张、人口规模缩减」是渐进的、可恢复的，而不是"一颗子弹毙命"。

    返回 dict 供 model_sim 写进 metrics；迁徙目标直接取 value。
    各分项中间值一并返回，方便月报展示「是住房先顶到上限还是岗位先顶」。
    """
    p = sim.params
    housing_units = sim.housing_units
    household_size = p.get('housing.household_size')
    k_housing = housing_units * household_size * p.get('city.k_housing_fit')

    private_jobs = sum(s.planned_jobs for s in sim.sectors)
    participation = p.get('population.labour_force') / max(1, p.get('population.start'))
    reference_employment = (REFERENCE_CITY['demographics']['employed']
                            / REFERENCE_CITY['demographics']['labour_force_derived'])
    jobs_derived = max(0.01, participation * reference_employment)
    k_jobs = (private_jobs + sim.public_jobs) / jobs_derived * p.get('city.k_jobs_fit')

    # 资源项：按"自给率"算调节因子，不让资源项进入瓶颈比较。
    # 自给率 = 当月产量+进口 ÷ 当月基准需求。1.0 = 自给自足，<1 = 短缺，>1 = 富余。
    # base_use 是参考城市（3000 人）基准需求；城市规模缩放则用人口比例推算。
    if sim.resources:
        ref_pop = max(1, p.get('population.start'))
        scale = sim.period_population / ref_pop if hasattr(sim, 'period_population') else 1.0
        ratios = []
        for r in sim.resources.values():
            monthly_supply = max(0.0, r.produced + r.imported)
            monthly_need = max(0.001, r.base_use * scale)
            ratios.append(monthly_supply / monthly_need)
        # 平均自给率；clip 到 [0.05, 2.0]，防止单资源耗尽让 K 直接归零。
        # 0.05 是「资源几近断供」的极端下限，等价于 K 被压到 5%。
        # clip 必须在乘法之后：保证乘以 k_resource_fit 后仍不低于 0.05 下限。
        k_resource = max(0.05, min(2.0, sum(ratios) / len(ratios) * p.get('city.k_resource_fit')))
    else:
        k_resource = 1.0

    bottleneck_value = min(k_housing, k_jobs)
    safety_base = max(0.05, 1 - sim.magic_risk * p.get('security.default_downtime_share'))
    k_safety = safety_base ** p.get('city.k_safety_exp')
    k_skill = (0.65 + 0.35 * sim.skill_factor) ** p.get('city.k_skill_exp')
    k_worktime = max(0.05, sim.worktime_factor) ** p.get('city.k_worktime_exp')
    k_service = (0.5 + 0.5 * sim.school_coverage) ** p.get('city.k_service_exp')

    multiplier = k_resource * k_safety * k_skill * k_worktime * k_service
    raw = bottleneck_value * multiplier
    value = int(round(max(0, raw)))

    components = {
        'housing': {'name': '住房承载', 'value': k_housing,
                    'is_bottleneck': bottleneck_value == k_housing},
        'jobs':    {'name': '岗位承载', 'value': k_jobs,
                    'is_bottleneck': bottleneck_value == k_jobs},
        'resource':{'name': '资源承载(乘子)', 'value': k_resource,
                    'is_bottleneck': False},
    }
    return {
        'value': value,
        'raw': raw,
        'bottleneck': bottleneck_value,
        'multiplier': multiplier,
        'k_housing': k_housing,
        'k_jobs': k_jobs,
        'k_resource': k_resource,
        'k_safety': k_safety,
        'k_skill': k_skill,
        'k_worktime': k_worktime,
        'k_service': k_service,
        'components': components,
    }



def match_labour(sim):
    """先裁员再填补岗位，所有人数与群体身份同步。"""
    labour = sum(c.employed + c.unemployed for c in sim.cohorts)
    public = min(sim.public_jobs, labour)
    private_jobs = [max(0, s.planned_jobs) if s.firm_count else 0 for s in sim.sectors]
    slots = min(sum(private_jobs), max(0, labour - public))
    counts = split(slots, private_jobs)
    for s, n in zip(sim.sectors, counts):
        s.employees = n
    employed_total = public + slots
    current = sum(c.employed for c in sim.cohorts)
    if employed_total < current:
        cuts = split(current - employed_total, [c.employed for c in sim.cohorts])
        for c, n in zip(sim.cohorts, cuts):
            c.employed -= n
            c.unemployed += n
    else:
        remaining = employed_total - current
        # 岗位空缺优先匹配；不把失业小于4人的尾数卡住。
        for c in sorted(sim.cohorts, key=lambda c: c.wage_weight / max(1, c.population), reverse=True):
            n = min(c.unemployed, remaining)
            c.unemployed -= n
            c.employed += n
            remaining -= n
    sim.public_staff = public


def scale_sector(s, ratio):
    s.capacity_units = max(0, round(s.capacity_units * ratio))
    s.capital_units = max(0, round(s.capital_units * ratio))
    s.planned_jobs = max(0, round(s.planned_jobs * ratio))
    s.depreciation_cents = round(s.depreciation_cents * ratio)
    s.commercial_rent_cents = round(s.commercial_rent_cents * ratio)
    s.fees_cents = round(s.fees_cents * ratio)


def firms(sim):
    p = sim.params
    sim.bankrupt = sim.relocated = sim.entered = 0
    for s in sim.sectors:
        s.cash_cents = sim.ledger.cash(s.account)
        if not s.firm_count:
            continue
        s.loss_streak = s.loss_streak + 1 if s.profit_before_tax_cents < 0 else 0
        s.cash_streak = s.cash_streak + 1 if s.arrears > 0 else 0
        s.investment_reserve += s.capex_paid
        s.sales_history.append(s.revenue_cents)
        window = p.get('business.forecast_window_months')
        s.sales_history = s.sales_history[-window:]
        mean_sales = sum(s.sales_history) / len(s.sales_history)
        # 现金危机对应破产；有资金但持续亏损对应外逃。
        bankruptcy = s.cash_streak >= p.get('business.exit_cash_streak_months')
        flight = s.loss_streak >= p.get('feedback.relocation_loss_months')
        long_loss = s.loss_streak >= p.get('business.exit_loss_streak_months')
        if bankruptcy or flight or long_loss:
            count = max(1, math.ceil(s.firm_count * p.get('feedback.relocation_share')))
            count = min(count, s.firm_count)
            ratio = (s.firm_count - count) / s.firm_count
            amount = round(sim.ledger.cash(s.account) * (1 - ratio))
            if bankruptcy or long_loss:
                # 清算剩余现金返还本地股东；尚未支付义务显式登记核销。
                sim.distribute(s.account, amount, 'liquidation', 'asset')
                sim.bankrupt += count
                sim.written_off += round(s.arrears * (1 - ratio))
                sim.events.append(f'{s.name} {count}家企业破产清算，连续欠付{s.cash_streak}月/亏损{s.loss_streak}月')
            else:
                # A-01 改造（2026-10-15）：资本外逃收款方 'external' → 'ocean'
                sim.ledger.pay(s.account, 'ocean', amount, 'capital_flight')
                sim.relocated += count
                sim.events.append(f'{s.name} {count}家企业外迁并带走资金')
            for category in ('wage', 'commercial_rent', 'fees'):
                key = (s.sector_id, category)
                sim.obligations[key] = round(sim.obligations.get(key, 0) * ratio)
            s.arrears = sum(sim.obligations.get((s.sector_id, k), 0) for k in ('wage', 'commercial_rent', 'fees'))
            s.firm_count -= count
            scale_sector(s, ratio)
            continue
        margin = s.profit_before_tax_cents / max(1, s.revenue_cents)
        liquidity = s.cash_runway_months
        # 设备投资已经真实付款，才允许将其中的有效部分转成产能。
        if margin >= p.get('business.entry_expected_margin_min') and liquidity >= p.get('business.entry_liquidity_months'):
            addition = round(s.capex_paid * p.get('feedback.investment_productivity'))
            addition = min(addition, round(s.capacity_units * p.get('feedback.growth_monthly_cap')))
            s.capacity_units += addition
            s.capital_units += s.capex_paid
            if s.utilisation >= p.get('business.expansion_utilisation_min') and addition:
                s.planned_jobs += max(1, round(s.employees * addition / max(1, s.capacity_units)))
            # 新开业需支付有对手的设备订单，不能凭空加店。
            fixed_per_firm = (s.wage_bill_cents + s.commercial_rent_cents + s.fees_cents) / s.firm_count
            setup = round(fixed_per_firm * p.get('feedback.entry_setup_months'))
            if s.utilisation >= p.get('business.expansion_utilisation_min') and s.investment_reserve >= setup > 0:
                s.investment_reserve -= setup
                posts = max(1, round(s.base['employees'] / s.base['firms']))
                s.planned_jobs += posts
                s.firm_count += 1
                sim.entered += 1
                sim.events.append(f'{s.name}新增1家企业，已支付开业资本')
        # 持续订单不足才裁员，防止一个月需求噪声引发全城退出。
        target_jobs = round(s.employees * mean_sales / max(1, s.capacity_units * s.price * p.get('capacity.reference_utilisation')))
        if margin < 0 or s.utilisation < p.get('business.contraction_utilisation_max'):
            reduction = max(1, round(max(0, s.planned_jobs - target_jobs) * p.get('feedback.job_adjustment_rate')))
            s.planned_jobs = max(0, s.planned_jobs - reduction)


def wages_prices(sim):
    p = sim.params
    jobs = sum(s.planned_jobs for s in sim.sectors) + sim.public_jobs
    labour = sum(c.employed + c.unemployed for c in sim.cohorts)
    reference_theta = REFERENCE_CITY['demographics']['employed'] / REFERENCE_CITY['demographics']['labour_force_derived']
    theta = jobs / max(1, labour) / reference_theta
    for s in sim.sectors:
        alpha = p.get('religion.wage_reserve_alpha_basic')
        ref_faith = p.get('capacity.reference_faith_share')
        faith_ratio = (1 - alpha * sim.faith_share) / max(0.01, 1 - alpha * ref_faith)
        target = cr_to_cents(s.base['wage_per_employee']) * sim.cpi ** p.get('labour.wage_cpi_exponent')
        target *= max(0.01, theta) ** p.get('labour.wage_tightness_exponent') * faith_ratio
        change = (target - s.wage_per_employee_cents) * p.get('labour.wage_target_adjustment')
        cap = math.floor(s.wage_per_employee_cents * p.get('labour.wage_change_monthly_cap'))
        # 最低工资政策立即生效；普通市场调整受单月上限。
        s.wage_per_employee_cents = max(cr_to_cents(p.get('labour.min_wage')),
                                        s.wage_per_employee_cents + round(max(-cap, min(cap, change))))
        if s.firm_count and s.revenue_cents:
            demand_ratio = max(0.01, (s.revenue_cents + s.unmet) / max(1, s.capacity_units * s.price * p.get('capacity.reference_utilisation')))
            imported_pressure = p.get('trade.import_price_index') - 1
            step = p.get('market.price_adapt_k') * math.log(demand_ratio)
            step += p.get('market.price_adapt_k') * imported_pressure * s.base['input_cost'] / s.base['revenue']
            cap_price = p.get('business.price_change_monthly_cap')
            s.price = max(p.get('feedback.price_floor'), min(p.get('feedback.price_ceiling'), s.price * (1 + max(-cap_price, min(cap_price, step)))))
    sim.cpi = sum(s.price * s.base['revenue'] for s in sim.sectors) / sum(s.base['revenue'] for s in sim.sectors)


def population(sim):
    p = sim.params
    total = sum(c.population for c in sim.cohorts)
    participation = p.get('population.labour_force') / max(1, p.get('population.start'))
    # K 值兜底：迁徙目标交给 compute_capacity()，统一从 city_capacity 取。
    # 不再用 min(住房容量, 岗位数) 这种简单组合，K 值综合了住房、岗位、资源、
    # 安全、技能、工时、服务六个维度，由 model_sim._step() 在调用本函数前先算好。
    # 物理硬上限：K 值可能因乘子略高于住房物理容量(3200)，但人口绝不能超过住房物理容量。
    # 这是"无家可归"语义不允许的硬天花板，测试 test_population_never_exceeds_housing_or_jobs 守护此点。
    physical_cap = sim.housing_units * p.get('housing.household_size')
    target = min(physical_cap,
                 max(p.get('feedback.minimum_population'), sim.city_capacity['value']))
    # 单月迁徙上限：硬上限（=0 表示关闭）+ 比率上限，取更严格者。
    # 设计稿 §5.2 要求「单月净迁徙不超过当前人口 1%」，
    # 原 migration_max_per_month=14 相当于给 3000 人城市硬卡了 0.47%，
    # 现默认值改为 0 后改由比率上限主导，避免悄悄突破设计稿。
    rate_cap = math.floor(total * p.get('population.migration_monthly_cap_rate'))
    hard_cap = p.get('population.migration_max_per_month')
    cap = min(hard_cap, rate_cap) if hard_cap else rate_cap
    delta = max(-cap, min(cap, target - total))
    if delta:
        movements = split(abs(delta), [c.population for c in sim.cohorts])
        for c, count in zip(sim.cohorts, movements):
            if delta > 0:
                n = count
                workers = round(n * participation)
                c.unemployed += workers
                c.non_labour += n - workers
                # A-01 改造（2026-10-15）：迁入付款方 'external' → 'ocean'
                sim.ledger.pay('ocean', c.account, n * cr_to_cents(p.get('population.migration_savings_per_person')), 'migration_in')
            else:
                n = min(count, c.population)
                identities = split(n, [c.employed, c.unemployed, c.non_labour])
                c.employed -= identities[0]
                c.unemployed -= identities[1]
                c.non_labour -= identities[2]
                # A-01 改造（2026-10-15）：迁出收款方 'external' → 'ocean'
                sim.ledger.pay(c.account, 'ocean', round(sim.ledger.cash(c.account) * n / max(1, c.population)), 'migration_out')
            c.population += n if delta > 0 else -n
            c.households = math.ceil(c.population / p.get('population.household_size'))
    # 自然增长使用独立月率，不伪装成迁徙。
    free_capacity = max(0, sim.housing_units * p.get('housing.household_size') - sum(c.population for c in sim.cohorts))
    for c in sim.cohorts:
        births = math.floor(c.population * p.get('population.natural_growth_rate'))
        if births >= 0:
            births = min(births, free_capacity)
            free_capacity -= births
            c.population += births
            c.non_labour += births
        else:
            deaths = min(c.population, -births)
            removed = split(deaths, [c.employed, c.unemployed, c.non_labour])
            c.employed -= removed[0]
            c.unemployed -= removed[1]
            c.non_labour -= removed[2]
            c.population -= deaths
        c.households = math.ceil(c.population / p.get('population.household_size'))
    match_labour(sim)
    return delta


def education_housing(sim):
    p = sim.params
    for completion, units in list(sim.housing_queue):
        if completion <= sim.month:
            sim.housing_units += units
            sim.housing_queue.remove((completion, units))
            sim.events.append(f'私人住宅竣工{units}套')
    occupied = sum(c.households for c in sim.cohorts)
    vacancy = max(0, 1 - occupied / max(1, sim.housing_units))
    change = p.get('feedback.rent_adjustment_speed') * (p.get('housing.target_vacancy_rate') - vacancy)
    cap = p.get('housing.rent_monthly_change_cap')
    sim.rent_cents *= 1 + max(-cap, min(cap, change))
    # 有住房需求且居民自有现金足额才开工；不把预售意愿当钱。
    if occupied + sum(n for _, n in sim.housing_queue) >= sim.housing_units * (1 - p.get('housing.target_vacancy_rate')):
        cost = cr_to_cents(p.get('feedback.housing_build_cost'))
        for c in sim.cohorts:
            invest = round(sim.ledger.cash(c.account) * p.get('feedback.housing_investment_share'))
            if invest >= cost and sim.sectors[1].firm_count:
                if c.account not in sim.housing_orders:
                    sim.housing_orders.append(c.account)
    if sim.month % p.get('education.training_months') == 0:
        seats = sim.school_coverage * p.get('education.school_age_population')
        trained = math.floor(seats * p.get('education.completion_rate') * p.get('education.skill_transition_rate') * sim.public_wage_ratio)
        old_skill = sim.skill_factor
        sim.trained += trained
        sim.skill_factor = 1 + min(p.get('feedback.education_productivity'), sim.trained / max(1, p.get('population.start')) * p.get('feedback.education_productivity'))

        for s in sim.sectors:
            s.capacity_units = round(s.capacity_units * sim.skill_factor / old_skill)
