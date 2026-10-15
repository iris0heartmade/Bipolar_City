"""行业实验真实交易：每笔钱都有付款者，标准交付包只售出一次。"""
from backend.systems.v21.industry_scenario import split_integer


def settle_markets(city, opening, wages):
    c = city.scenario['city']
    city.actual_domestic, city.actual_rent = {}, {}
    # 产业实际运行采购。聚合行业不能向自己转账伪造营业额。
    for i in city.keys:
        sector = city.sector(i)
        scale = city.actual_activity[i]/max(city.initial_activity(i), 1e-9)
        budget = city.cr(sector['operating_cr']*c['domestic_input_share']*scale*city.cpi)
        weights = {seller: w for seller, w in city.scenario['domestic_supplier_weights'].items() if seller != i}
        total = 0
        for seller, amount in split_integer(min(budget, city.cash[i]), weights).items():
            total += city.buy(i, seller, amount, 'domestic_purchase')
        city.actual_domestic[i] = total
        city.actual_rent[i] = city.pay(i, 'PROP', city.cr(sector['rent_cr']*city.cpi), 'commercial_rent')
    # 生活篮子：先买基本食品和住房，再买其他交付包；预算只用实际到账与有限储蓄。
    for h, population in city.population.items():
        nominal = population*city.living_cost_cents
        budget = min(city.cash[h], max(0, round(c['savings_draw_limit']*opening[h])+city.income[h]), nominal)
        rent_budget = round(budget*city.scenario['household_rent_share'])
        goods = split_integer(budget-rent_budget, city.scenario['household_seller_weights'])
        city.buy(h, 'AGR', goods.pop('AGR', 0), 'consumer_purchase')
        city.pay(h, 'PROP', rent_budget, 'residential_rent')
        for seller, amount in goods.items():
            city.buy(h, seller, amount, 'consumer_purchase')
    # 房产通道真实买维护服务，余款在月末分给产权持有群体。
    city.buy('PROP', 'SRV', city.cr(c['property_maintenance_cr']), 'maintenance_purchase')
    for seller, amount in split_integer(city.cr(c['government_purchases_cr']), city.scenario['government_supplier_weights']).items():
        city.buy('GOV', seller, amount, 'public_purchase')
    city.buy('GOV', 'IND', city.cr(c['public_investment_cr']), 'capital_purchase')
    capex_weights = {i: city.cash[i] for i in city.keys if i not in ('CHU', 'GOV', 'IND')}
    for buyer, amount in split_integer(city.cr(c['private_capex_cr']), capex_weights).items():
        city.buy(buyer, 'IND', amount, 'capital_purchase')
    for h, amount in split_integer(city.cr(c['offerings_cr']), city.population).items():
        city.pay(h, 'CHU', amount, 'offering')
    # 同一个有限外部贸易节点：缺现金时减少成交，收到的包进入外部实际库存。
    for seller in city.keys:
        sector = city.sector(seller)
        demand = city.cr((sector['external_demand_cr']+sector['tourism_cr'])
                         *sector['demand_multiplier']*city.cpi)
        city.buy('EXT', seller, demand, 'external_purchase', external=True)
