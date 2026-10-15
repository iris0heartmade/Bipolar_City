"""订单与实物供给。货款实际到账后才增加卖方收入。"""
import math
from backend.config import REFERENCE_CITY, INPUT_OUTPUT
from backend.model.economy import cr_to_cents
from backend.systems.model_ledger import split


def weights(sim):
    beta = sim.params.get('market.choice_beta_price')
    return [s.base['revenue'] * s.firm_count / max(1, s.base['firms'])
            * math.exp(max(-50, min(50, -beta * (s.price - 1)))) for s in sim.sectors]


def buy(sim, source, amount, category, supplies, preferred=None):
    """有供给且付得起才成交；最多按配置轮数重新分配。"""
    remaining = max(0, int(amount))
    paid_total = 0
    choices = weights(sim) if preferred is None else preferred
    for _ in range(1 + sim.params.get('market.max_budget_redistribution_rounds')):
        active = [w if supplies[i] > 0 and s.firm_count else 0 for i, (s, w) in enumerate(zip(sim.sectors, choices))]
        if not sum(active) or remaining <= 0:
            break
        allocations = split(remaining, active)
        progress = 0
        for i, (s, requested) in enumerate(zip(sim.sectors, allocations)):
            if active[i] == 0:
                continue
            accepted = min(requested, supplies[i])
            paid = sim.ledger.pay(source, s.account, accepted, category)
            s.revenue_cents += paid
            supplies[i] -= paid
            remaining -= paid
            progress += paid
        paid_total += progress
        if not progress:
            break
    return paid_total


def household_budgets(sim):
    housing = sim.params.section('housing')
    renters = min(housing['renter_households'], sum(c.households for c in sim.cohorts))
    rental_dues = split(round(renters * sim.rent_cents), [c.households for c in sim.cohorts])
    raw_budgets = []
    for c, rent in zip(sim.cohorts, rental_dues):
        c.rental_due = rent
        mandatory = rent + round(c.last_disposable_cents * sim.params.get('feedback.donation_income_share'))
        affordable = max(0, sim.ledger.cash(c.account) - mandatory)
        willingness = max(0, c.last_disposable_cents - mandatory) * c.consumption_ratio * sim.consumption_normalisation
        raw_budgets.append(min(affordable, willingness * sim.params.get('feedback.demand_multiplier')))
        c.consumption_paid = 0
    for c, value in zip(sim.cohorts, split(round(sum(raw_budgets)), raw_budgets)):
        c.budget = value
    return sum(c.budget for c in sim.cohorts)


def resource_ratio(sim, resource_id, actual=False):
    dependencies = sim.params.get('resources.sector_dependency')
    weights_ = [dependencies.get(s.sector_id, {}).get(resource_id, 0) for s in sim.sectors]
    if not sum(weights_):
        weights_ = [1] * len(sim.sectors)
    amount = sum((s.revenue_cents / s.price if actual else s.planned_output) * w for s, w in zip(sim.sectors, weights_))
    reference = sum(cr_to_cents(s.base['revenue']) * w for s, w in zip(sim.sectors, weights_))
    return amount / max(1, reference)


def prepare_resources(sim, output_ratio, buyer_weights):
    """战略订单从总进口拆出；付多少得到多少，不提供免费补库。"""
    factors = {}
    p = sim.params
    for r in sim.resources.values():
        producer = p.get('feedback.resource_producers')[r.resource_id]
        sector = next(s for s in sim.sectors if s.sector_id == producer)
        producer_scale = min(sector.planned_output / max(1, cr_to_cents(sector.base['revenue'])), sector.employees / sector.base['employees'] / p.get('capacity.reference_utilisation'))
        r.produced = r.base_production * producer_scale * p.get('feedback.resource_supply_multiplier')
        r.used = r.exported = r.wasted = 0
        ratio = resource_ratio(sim, r.resource_id)
        planned = r.base_use * ratio
        # 补库上限为期初库存与本月计划；不是无限储备。
        wanted = max(r.base_import * ratio, planned + r.base_export * ratio + sim.resource_targets[r.resource_id] - r.stock - r.produced)
        import_cap = r.base_import * max(1, output_ratio) * p.get('feedback.resource_import_capacity_multiplier')
        wanted = min(max(0, wanted), import_cap) * p.get('feedback.import_availability')
        unit_price = r.unit_price_cents * p.get('trade.import_price_index')
        requested = math.floor(wanted * unit_price)
        paid_total = 0
        for s, amount in zip(sim.sectors, split(requested, buyer_weights)):
            # A-01 改造（2026-10-15）：战略品进口付款方 'external' → 'ocean'
            paid = sim.ledger.pay(s.account, 'ocean', amount, 'import', commodity_id=r.resource_id, qty=amount / max(1, unit_price))
            s.imports_paid += paid
            s.input_cost_cents += paid
            paid_total += paid
        r.imported = paid_total / max(1, unit_price)
        supply = r.stock + r.produced + r.imported
        factors[r.resource_id] = min(1.0, supply / planned) if planned else 1.0
    return factors


def execute_market(sim):
    p = sim.params
    household = sum(c.budget for c in sim.cohorts)
    public = cr_to_cents(sum(r['spend'] for r in REFERENCE_CITY['public_sector']['procurement'])) * sim.scale
    public += cr_to_cents(p.get('city.public_capex_monthly_initial'))
    repair = cr_to_cents(REFERENCE_CITY['housing']['owner_repair_spend']) * sum(c.households for c in sim.cohorts) / REFERENCE_CITY['demographics']['households']
    exports = cr_to_cents(p.get('trade.exports_base')) * p.get('feedback.export_demand_multiplier')
    tourism = cr_to_cents(p.get('trade.tourist_spend_base')) * p.get('feedback.export_demand_multiplier')
    capex = cr_to_cents(p.get('business.firm_capex_monthly')) * sim.scale
    if sim.month > 1:
        capex *= max(0, min(1, sim.last_margin / max(p.get('business.entry_expected_margin_min'), 0.001)))
    intermediate_share = p.get('business.intermediate_sales_share')
    housing_cost = cr_to_cents(p.get('feedback.housing_build_cost'))
    housing_buyers = [a for a in sim.housing_orders if sim.ledger.cash(a) >= housing_cost]
    final = household + public + repair + exports + tourism + capex + housing_cost * len(housing_buyers)
    desired = final / max(0.01, 1 - intermediate_share)
    demand_weights = weights(sim)
    planned_values = split(round(desired), demand_weights)
    import_requested = []
    raw_imports = []
    strategic_base = sum(r.base_import * r.unit_price_cents for r in sim.resources.values())
    foreign_base = cr_to_cents(INPUT_OUTPUT['firm_input_split']['imports'])
    ordinary_share = max(0, 1 - strategic_base / max(1, foreign_base))
    for s, planned in zip(sim.sectors, planned_values):
        s.revenue_cents = s.input_cost_cents = s.capex_paid = s.imports_paid = 0
        s.dividend_cents = s.wage_paid = s.rent_paid = s.fees_paid = 0
        labour_factor = min(1, (s.employees / max(1, s.base['employees'] * s.capacity_units / max(1, cr_to_cents(s.base['revenue']) / p.get('capacity.reference_utilisation')))) ** p.get('capacity.labour_exponent'))
        units = min(planned / max(s.price, 0.01), s.capacity_units * labour_factor)
        s.planned_output = units
        cost = units * s.base['input_cost'] / s.base['revenue']
        foreign_share = INPUT_OUTPUT['firm_input_split']['imports'] / sum(INPUT_OUTPUT['firm_input_split'][k] for k in ('imports', 'local_intermediate'))
        requested = cost * foreign_share * p.get('trade.imports_base') / INPUT_OUTPUT['firm_input_split']['imports']
        raw_imports.append(requested * ordinary_share * p.get('trade.import_price_index') * p.get('feedback.import_availability'))
    requests = split(round(sum(raw_imports)), raw_imports)
    for s, requested in zip(sim.sectors, requests):
        # A-01 改造（2026-10-15）：行业进口付款方 'external' → 'ocean'
        s.imports_paid = sim.ledger.pay(s.account, 'ocean', requested, 'import')
        s.input_cost_cents = s.imports_paid
        import_requested.append(requested)
    output_ratio = sum(s.planned_output for s in sim.sectors) / cr_to_cents(sum(s.base['revenue'] for s in sim.sectors))
    buyer_weights = [s.planned_output * s.base['input_cost'] / s.base['revenue'] for s in sim.sectors]
    ordinary_paid = [s.imports_paid for s in sim.sectors]
    resource_factors = prepare_resources(sim, output_ratio, buyer_weights)
    supplies = []
    for s, requested, paid_ordinary in zip(sim.sectors, import_requested, ordinary_paid):
        deps = p.get('resources.sector_dependency').get(s.sector_id, {})
        resource = min((resource_factors[k] for k in deps), default=1)
        input_factor = (paid_ordinary / requested if requested else 1) * p.get('feedback.import_availability')
        s.readiness = min(1.0, resource * input_factor * sim.safety_factor * sim.worktime_factor * sim.skill_factor)
        supply_units = min(s.planned_output, s.capacity_units * s.readiness)
        supplies.append(round(supply_units * s.price))
    sim.market_supplies = list(supplies)
    sim.market_desired = desired
    sim.owner_repair_paid = 0
    # 外需独立且受真实供给约束；没有无限外界兜底购买。
    strategic_export_base = sum(r.base_export * r.unit_price_cents for r in sim.resources.values())
    strategic_export_available = sum(min(r.base_export * p.get('feedback.export_demand_multiplier'), max(0, r.stock + r.produced + r.imported - r.base_use * resource_ratio(sim, r.resource_id))) * r.unit_price_cents for r in sim.resources.values())
    exports = max(0, exports - max(0, strategic_export_base * p.get('feedback.export_demand_multiplier') - strategic_export_available))
    # A-01 改造（2026-10-15）：出口/游客付款方 'external' → 'ocean'
    sim.exports_paid = buy(sim, 'ocean', round(exports * p.get('trade.export_price_index')), 'export', supplies)
    sim.tourism_paid = buy(sim, 'ocean', round(tourism), 'tourism', supplies)
    for index, c in enumerate(sim.cohorts):
        mapping = p.get('feedback.basket_sector_map')
        choices = [w * c.basket.get(mapping[s.sector_id], 0) * sim.basket_calibration[index][j]
                   for j, (s, w) in enumerate(zip(sim.sectors, demand_weights))]
        c.consumption_paid = buy(sim, c.account, c.budget, 'sale', supplies, choices)
        unmet = c.budget - c.consumption_paid
        if unmet > 0 and p.get('market.external_substitution'):
            # A-01 改造（2026-10-15）：居民外购补足付款方 'external' → 'ocean'
            c.consumption_paid += sim.ledger.pay(c.account, 'ocean', unmet, 'external_purchase')
    sim.public_procurement_paid = buy(sim, 'government', round(public), 'procurement', supplies)
    # 房租先到房东钱包，随后维修；净收益分给实际产权群体。
    for c in sim.cohorts:
        sim.ledger.pay(c.account, 'landlords', c.rental_due, 'residential_rent')
    sim.owner_repair_paid = buy(sim, 'landlords', round(repair), 'maintenance', supplies)
    local_cost_total = round(desired * intermediate_share)
    buyer_weights = [s.planned_output * s.base['input_cost'] / s.base['revenue'] for s in sim.sectors]
    for s, amount, investment in zip(sim.sectors, split(local_cost_total, buyer_weights), split(round(capex), buyer_weights)):
        s.input_cost_cents += buy(sim, s.account, amount, 'intermediate', supplies)
        if s.firm_count:
            s.capex_paid = buy(sim, s.account, investment, 'capital', supplies)
    for account in housing_buyers:
        if sum(supplies) >= housing_cost and sim.ledger.cash(account) >= housing_cost:
            paid = buy(sim, account, housing_cost, 'housing_construction', supplies)
            if paid == housing_cost:
                sim.housing_queue.append((sim.month + p.get('feedback.housing_build_months'), 1))
                sim.housing_orders.remove(account)
    # 实物消耗随真实产销量；同一吨只消费/出口一次。
    actual_ratio = sum(s.revenue_cents / s.price for s in sim.sectors) / cr_to_cents(sum(s.base['revenue'] for s in sim.sectors))
    for r in sim.resources.values():
        available = r.stock + r.produced + r.imported
        r.used = min(available, r.base_use * resource_ratio(sim, r.resource_id, actual=True))
        available -= r.used
        # 出口包含在 export 货款中，仅按已成交量分配资源出口。
        r.exported = min(available, r.base_export * sim.exports_paid / max(1, cr_to_cents(INPUT_OUTPUT['firm_revenue_sources']['exports'])))
        available -= r.exported
        r.wasted = available * p.get('resources.wastage_rate')
        r.stock = max(0.0, available - r.wasted)
    for s, supply_left in zip(sim.sectors, supplies):
        s.utilisation = min(1.0, s.revenue_cents / max(1, s.capacity_units * s.price))
        s.unmet = max(0, planned_values[sim.sectors.index(s)] - s.revenue_cents)
