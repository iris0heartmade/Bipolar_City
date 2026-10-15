"""行业实验真实交易：每笔钱都有付款者，标准交付包只售出一次。

阶段 14 改：
- 家庭口径：每位就业者背后有 household_consumption_size 口家庭；
  生活需求按「家庭人口」而不是「可劳动人口」计算，倍数由配置驱动。
- 外部节点可持续：每月注入 fixed_cash 和 fixed_raw（按 external_replenish_cap_cents 上限）；
  原料按 replenish_price 计价购买；EXT 不会再耗尽。
- 外部订单按比例分给本地和外部（external_local_split），本地订单不会因外部竞争被吃光。
"""
import math
from backend.systems.v21.industry_scenario import split_integer


def settle_markets(city, opening, wages):
    c = city.scenario['city']
    city.actual_domestic, city.actual_rent = {}, {}
    # 阶段 14 改：外部节点不再自动补给。EXT 现金 / 原料都靠玩家通过
    # `purchase_external` 命令一次性购入（govern_api.purchase_external），
    # 这条函数只负责行业间市场清算。
    # 自动补给参数 external_cash_replenish_cr / external_raw_replenish_units
    # 保留为 0，命名改为"基准市政采购量"，便于实验场景开局一次性注入。
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
    # 生活篮子：家庭口径。名义需求 = 可劳动人口 × 每户人口 × 生活成本；
    # 实际预算仍受「储蓄提款上限 + 当期可支配收入」封顶。
    household_size = float(c.get('household_consumption_size', 1.0))
    for h, population in city.population.items():
        nominal = population * household_size * city.living_cost_cents
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
    # 同一个有限外部贸易节点：外部订单按 external_local_split 切给本地一份后，
    # 剩余走外部；本地订单不因外部竞争被完全吃光。
    ext_split = c.get('external_local_split', 0.5)
    for seller in city.keys:
        sector = city.sector(seller)
        demand_external = city.cr((sector['external_demand_cr']+sector['tourism_cr'])
                                  *sector['demand_multiplier']*city.cpi)
        local_portion = round(demand_external * ext_split)
        if local_portion > 0:
            # 本地居民把部分外部订单"消化"为本地购买
            for h, p in city.population.items():
                share = split_integer(local_portion, city.scenario['household_seller_weights'])
                share.pop(seller, None)
                amount = share.get(seller, 0)
                city.buy(h, seller, amount, 'consumer_purchase')
        ext_remainder = demand_external - local_portion
        city.buy('EXT', seller, ext_remainder, 'external_purchase', external=True)
