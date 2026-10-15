"""七行业可运行实验内核：真实整数现金、标准交付包库存和汇总劳动力。

共用双指标公式。所有者/居民群体与实际雇主分别核算；外部买家有限。
标准包是本次行业实验粒度，不声称替代V2.1全部商品运输及建筑系统。
"""
from copy import deepcopy
from dataclasses import asdict
import math
from statistics import median

from backend.systems.v21.industry_model import attractiveness, capacity, employment_target, labor_month
from backend.systems.v21.industry_scenario import default_scenario, read_config, split_integer, validate_scenario


class IndustryEconomy:
    def __init__(self, scenario=None):
        self.scenario = deepcopy(scenario or default_scenario())
        validate_scenario(self.scenario)
        self.definitions = read_config('industries.json')['industries']
        self.tiers = read_config('industries.json')['_meta']['wage_tier_cr']
        self.keys = list(self.definitions)
        wallets = read_config('wallets.json')['openings']
        self.cash = {**wallets['firms'], 'GOV': wallets['government'],
                     'CHU': wallets['church'], 'PROP': 0,
                     'EXT': self.cr(self.scenario['city']['external_cash_cr'])}
        self.cash.update({h: wallets['cohorts'][h] for h in wallets['cohorts']})
        cohorts = list(read_config('cohorts.json')['cohorts'].values())
        self.population = {f'H{h}': d['population_P_h'] for h, d in enumerate(cohorts)}
        self.labor_force = math.floor(self.scenario['city']['population'] * self.scenario['city']['labor_participation'])
        self.workers = {h: {i: 0 for i in self.keys} for h in self.population}
        for n, i in enumerate(self.keys, 1):
            self.workers[f'H{n}'][i] = self.definitions[i]['initial_employees_industrial_city']
        spare = {h: self.population[h] - sum(row.values()) for h, row in self.workers.items()}
        self.unemployed_by_group = split_integer(self.labor_force - sum(self.employment().values()), spare)
        self.month, self.cpi = 0, 1.0
        self.living_cost_cents = self.cr(self.scenario['city']['living_cost_cr'])
        self.base_wages = {i: self.cr(self.tiers[d['wage_tier']]) for i, d in self.definitions.items()}
        self.nominal_wages = dict(self.base_wages)
        self.prices = {i: self.cr(self.sector(i)['output_price_cr']) for i in self.keys}
        self.qbar, self.full_raw = {}, {}
        exp = self.scenario['model']['capacity']['labor_output_exponent']
        self.raw, self.stock = {}, {}
        self.receipts_history, self.wage_history, self.pay_history = {}, {}, {}
        self.demand_history, self.previous_demand_factor = {}, {}
        for i in self.keys:
            e, j = self.employment()[i], self.jobs(i)
            activity = self.scenario['city']['security'] * (e/j)**exp
            s = self.sector(i)
            self.qbar[i] = (s['reference_sales_cr']/s['output_price_cr']/activity) if activity else 0
            if i == 'GOV':
                self.qbar[i] = e / activity
            raw_month = s['operating_cr'] * (1-self.scenario['city']['domestic_input_share'])
            self.full_raw[i] = raw_month / activity
            self.raw[i] = raw_month * self.scenario['city']['input_buffer_months']
            self.stock[i] = self.qbar[i] * activity * self.scenario['city']['opening_output_months']
            support = self.cr(s['reference_sales_cr'])
            if i == 'CHU':
                support += self.cr(self.scenario['city']['offerings_cr'])
            if i == 'GOV':
                support = self.base_wages[i] * e
            self.receipts_history[i] = [support]
            self.wage_history[i] = [round(self.base_wages[i]*(1-self.pit(i)))]
            self.pay_history[i] = [1.0]
            self.demand_history[i] = [self.cr(s['reference_sales_cr'])]
            self.previous_demand_factor[i] = 1.0
        self.external_raw = float(self.scenario['city']['external_raw_units'])
        self.external_goods = {i: 0.0 for i in self.keys}
        self.consumed = {i: 0.0 for i in self.keys}
        self.raw_used = 0.0
        self.carries, self.scores, self.last_report = {}, {}, None
        self.initial_total_cash = sum(self.cash.values())
        self.initial_goods = {i: self.stock[i] for i in self.keys}
        self.initial_raw = self.external_raw + sum(self.raw.values())
        self.total_produced = {i: 0.0 for i in self.keys}
        self.scenario['model']['status'] = 'shared_runtime'

    @staticmethod
    def cr(value):
        return round(value * 100)

    def sector(self, industry):
        return self.scenario['sectors'][industry]

    def jobs(self, industry):
        return self.definitions[industry]['job_capacity_max']

    def pit(self, industry):
        return 0 if industry == 'AGR' else self.scenario['city']['pit_rate']

    def employment(self):
        return {i: sum(row[i] for row in self.workers.values()) for i in self.keys}

    def pay(self, source, target, amount, kind):
        if type(amount) is not int or amount < 0:
            raise ValueError('付款必须为非负整数分')
        if source == target:
            if amount:
                raise ValueError('不能通过自付款伪造收入')
            return 0
        paid = min(amount, self.cash[source])
        if paid < amount:
            self.shortfalls.append({'payer': source, 'kind': kind, 'unpaid_cents': amount-paid})
        if paid:
            self.cash[source] -= paid
            self.cash[target] += paid
            self.transactions.append({'from': source, 'to': target, 'amount_cents': paid, 'kind': kind})
            if target in self.income:
                self.income[target] += paid
            if source in self.income and kind in ('income_tax', 'property_tax'):
                self.income[source] -= paid
            if target in self.sales and kind not in ('offering', 'church_grant', 'welfare', 'dividend', 'property_income', 'central_transfer'):
                if kind.endswith('purchase'):
                    self.sales[target] += paid
        return paid

    def buy(self, buyer, seller, budget, kind, external=False):
        if buyer == seller or seller == 'GOV':
            return 0
        budget = min(max(0, int(budget)), self.cash[buyer])
        self.requested[seller] += budget / self.prices[seller]
        amount = min(budget, math.floor(self.stock[seller] * self.prices[seller] + 1e-7))
        paid = self.pay(buyer, seller, amount, kind)
        qty = paid / self.prices[seller]
        self.stock[seller] = max(0.0, self.stock[seller] - qty)
        target = self.external_goods if external else self.consumed
        target[seller] += qty
        return paid

    def distribute(self, source, total, weights, kind, taxable=False):
        actual = min(total, self.cash[source])
        for h, amount in split_integer(actual, weights).items():
            paid = self.pay(source, h, amount, kind)
            if taxable:
                self.pay(h, 'GOV', round(paid*self.scenario['city']['pit_rate']), 'income_tax')

    def move_workers(self, result):
        for i, count in result['layoffs'].items():
            allocation = split_integer(count, {h: r[i] for h, r in self.workers.items()})
            for h, number in allocation.items():
                self.workers[h][i] -= number
                self.unemployed_by_group[h] += number
        for (source, target), count in result['switches'].items():
            allocation = split_integer(count, {h: r[source] for h, r in self.workers.items()})
            for h, number in allocation.items():
                self.workers[h][source] -= number
                self.workers[h][target] += number
        for target, count in result['hires_from_unemployment'].items():
            allocation = split_integer(count, self.unemployed_by_group)
            for h, number in allocation.items():
                self.unemployed_by_group[h] -= number
                self.workers[h][target] += number
        assert self.employment() == result['employed']
        self.carries = result['carries']

    def score(self, i, previous=None):
        weights = self.scenario['model']['attractiveness']['history_weights_newest_first']
        def weighted(history):
            values = list(reversed(history[-len(weights):]))
            return sum(v*w for v, w in zip(values, weights))/sum(weights[:len(values)])
        s, e = self.sector(i), self.employment()[i]
        wage = self.current_wages[i]
        necessary = max(1, self.cr(s['operating_cr']+s['rent_cr']) + e*wage)
        return attractiveness(net_paid_wage_cents=round(weighted(self.wage_history[i])),
                              delivered_benefit_cents=0, living_cost_cents=self.living_cost_cents,
                              timely_pay_ratio=min(self.pay_history[i][-1], weighted(self.pay_history[i])),
                              cash_runway_months=self.cash[i]/necessary,
                              workplace_safety=s['safety_quality'], working_time_quality=s['working_time_quality'],
                              previous_score=previous, params=self.scenario['model'])

    def evaluate_jobs(self):
        c, model = self.scenario['city'], self.scenario['model']
        capacities, targets, hard, details = {}, {}, {}, {}
        e = self.employment()
        for i in self.keys:
            s, j = self.sector(i), self.jobs(i)
            # 计划跟随有资金的真实需求历史；外部订单变化只加一次增量，
            # 不能把倍率每月重复乘在已经含该变化的历史上。
            factor = s['demand_multiplier']
            planned = self.initial_activity(i)*factor
            if i != 'GOV':
                external_delta = self.cr((s['external_demand_cr']+s['tourism_cr'])*self.cpi
                                         *(factor-self.previous_demand_factor[i]))
                demand_value = max(0, median(self.demand_history[i][-3:])+external_delta)
                planned = demand_value / max(1, self.prices[i]) / max(self.qbar[i], 1e-9)
            planned = min(c['security'], max(0.0, planned))
            activity_scale = planned/max(self.initial_activity(i), 1e-9)
            raw_goal = self.full_raw[i]*planned*(1+c['input_buffer_months'])
            needed = min(max(0.0, raw_goal-self.raw[i]),
                         self.full_raw[i]*self.initial_activity(i)*s['input_supply_ratio'])
            hold_wages = e[i]*self.current_wages[i]
            raw_budget = max(0, self.cash[i]-hold_wages-self.cr(s['rent_cr']))
            raw_price = self.cr(c['raw_price_cr'])*self.cpi
            qty = min(needed, self.external_raw, raw_budget/max(1, raw_price))
            raw_paid = self.pay(i, 'EXT', min(raw_budget, math.floor(qty*raw_price+1e-7)), 'raw_input')
            qty = raw_paid/max(1, raw_price)
            self.raw[i] += qty
            self.external_raw -= qty
            nonpayroll = self.cr((s['operating_cr']*c['domestic_input_share']*activity_scale+s['rent_cr'])*self.cpi)
            if i == 'GOV':
                nonpayroll = self.cr(c['government_purchases_cr']+c['public_investment_cr']+c['welfare_cr']+c['external_interest_cr'])
            payroll_budget = max(0, self.cash[i]-nonpayroll)
            cap = capacity(jobs=j, wage_cents=self.current_wages[i], payroll_budget_cents=payroll_budget,
                           recurring_receipts_cents=round(median(self.receipts_history[i][-3:])),
                           recurring_nonpayroll_cents=self.cr((s['operating_cr']*activity_scale+s['rent_cr'])*self.cpi),
                           reserve_cents=payroll_budget, full_load_inputs={'RAW': self.full_raw[i]},
                           available_inputs={'RAW': self.raw[i]}, nonlabor_factor=c['security'], params=model)
            capacities[i], hard[i] = cap.limit, cap.hard_limit
            targets[i] = employment_target(jobs=j, deliverable_activity=planned,
                                           nonlabor_factor=c['security'], limit=cap.limit, params=model)
            details[i] = asdict(cap)
            if i not in self.scores:
                self.scores[i] = self.score(i)['score']
        return capacities, targets, hard, details

    def initial_activity(self, i):
        e = self.definitions[i]['initial_employees_industrial_city']
        return self.scenario['city']['security']*(e/self.jobs(i))**self.scenario['model']['capacity']['labor_output_exponent']

    def advance(self):
        """副本上结算，所有真实断言通过才提交，出错不留下半个月状态。"""
        candidate = deepcopy(self)
        report = candidate._advance()
        self.__dict__.update(candidate.__dict__)
        return deepcopy(report)

    def _advance(self):
        validate_scenario(self.scenario)
        c = self.scenario['city']
        self.transactions, self.sales, self.shortfalls = [], {i: 0 for i in self.keys}, []
        self.requested, self.income = {i: 0.0 for i in self.keys}, {h: 0 for h in self.population}
        opening = dict(self.cash)
        self.current_wages = {i: max(1, round(self.nominal_wages[i]*self.sector(i)['wage_multiplier'])) for i in self.keys}
        capacities, targets, hard, cap_details = self.evaluate_jobs()
        flow = labor_month(employed=self.employment(), unemployed=sum(self.unemployed_by_group.values()),
                           capacities=capacities, hard_limits=hard, targets=targets, scores=self.scores,
                           params=self.scenario['model'], carries=self.carries)
        self.move_workers(flow)
        e, wages, input_cost, outputs = self.employment(), {}, {}, {}
        self.actual_activity = {}
        for i in self.keys:
            wages[i] = e[i]*self.current_wages[i]
            for h, row in self.workers.items():
                amount = row[i]*self.current_wages[i]
                actual = self.pay(i, h, amount, 'wage')
                if actual != amount:
                    raise AssertionError('已录用岗位工资未足额支付')
                self.pay(h, 'GOV', round(actual*self.pit(i)), 'income_tax')
            activity = min(c['security']*(e[i]/self.jobs(i))**self.scenario['model']['capacity']['labor_output_exponent'],
                           self.raw[i]/self.full_raw[i] if self.full_raw[i] > 0 else 1)
            used = min(self.raw[i], self.full_raw[i]*activity)
            self.actual_activity[i] = activity
            self.raw[i] -= used
            self.raw_used += used
            input_cost[i] = round(used*self.cr(c['raw_price_cr'])*self.cpi)
            outputs[i] = self.qbar[i]*activity
            self.stock[i] += outputs[i]
            self.total_produced[i] += outputs[i]
            if i == 'GOV':
                self.stock[i] -= outputs[i]
                self.consumed[i] += outputs[i]
        self.market_available = dict(self.stock)
        self.settle_markets(opening, wages)
        # 真实利润税和分红；购入未消耗原料属于库存资产，不重复当现金费用。
        profits, industry_taxes = {}, {}
        for i in self.keys:
            s = self.sector(i)
            costs = self.actual_domestic[i]+input_cost[i]+self.actual_rent[i]+wages[i]+self.cr(s['depreciation_cr']*self.cpi)
            profit = self.sales[i]-costs
            rate_key = self.definitions[i]['tax_tier']
            rate = read_config('industries.json')['_meta']['tax_tier_rate'][rate_key] if i != 'GOV' else 0
            tax = self.pay(i, 'GOV', round(max(0, profit)*min(1, rate*c['profit_tax_multiplier'])), 'industry_tax') if i != 'GOV' else 0
            if i not in ('CHU', 'GOV'):
                self.distribute(i, round(max(0, profit-tax)*c['dividend_ratio']), self.population, 'dividend', taxable=True)
            profits[i], industry_taxes[i] = profit, tax
        self.distribute('PROP', self.cash['PROP'], self.population, 'property_income', taxable=True)
        self.distribute('GOV', self.cr(c['welfare_cr']), self.unemployed_by_group, 'welfare')
        for h, value in split_integer(self.cr(c['property_tax_cr']), self.population).items():
            self.pay(h, 'GOV', value, 'property_tax')
        self.pay('EXT', 'GOV', self.cr(c['central_transfer_cr']), 'central_transfer')
        self.pay('CHU', 'GOV', self.cr(c['church_grant_cr']), 'church_grant')
        self.pay('GOV', 'EXT', self.cr(c['external_interest_cr']), 'debt_interest')
        for i in self.keys:
            offerings = sum(t['amount_cents'] for t in self.transactions if t['kind'] == 'offering')
            support = self.sales[i] + (offerings if i == 'CHU' else 0)
            if i == 'GOV':
                support = wages[i]
            self.receipts_history[i].append(support)
            self.receipts_history[i] = self.receipts_history[i][-3:]
            self.demand_history[i].append(round(self.requested[i]*self.prices[i]))
            self.demand_history[i] = self.demand_history[i][-3:]
            self.previous_demand_factor[i] = self.sector(i)['demand_multiplier']
            if e[i]:
                self.wage_history[i].append(round(self.current_wages[i]*(1-self.pit(i))))
                self.pay_history[i].append(1.0)
            else:
                self.wage_history[i].append(0)
                self.pay_history[i].append(self.scenario['model']['attractiveness']['new_employer_prior_pay_ratio'])
            self.wage_history[i], self.pay_history[i] = self.wage_history[i][-3:], self.pay_history[i][-3:]
            self.scores[i] = self.score(i, self.scores[i])['score']
        self.month += 1
        self.assert_invariants()
        report = self.make_report(opening, e, wages, outputs, profits, industry_taxes, capacities, targets, cap_details, flow)
        self.update_prices_and_wages()
        self.last_report = report
        return report

    def settle_markets(self, opening, wages):
        from backend.systems.v21.industry_settle import settle_markets
        settle_markets(self, opening, wages)

    def update_prices_and_wages(self):
        c = self.scenario['city']
        for i in self.keys:
            available = self.market_available[i]
            pressure = c['price_pressure_k']*(self.requested[i]-available)/max(self.requested[i], 1e-9)
            change = max(-c['price_change_cap'], min(c['price_change_cap'], pressure))
            floor = self.cr(self.sector(i)['output_price_cr'])*c['price_floor_ratio']
            ceiling = self.cr(self.sector(i)['output_price_cr'])*c['price_ceiling_ratio']
            self.prices[i] = max(1, round(min(ceiling*self.cpi, max(floor*self.cpi, self.prices[i]*(1+c['inflation'])*(1+change)))))
            backlog = self.base_wages[i]*self.cpi/max(1, self.nominal_wages[i])-1
            growth = max(c['wage_cap_down'], min(c['wage_cap_up'], c['wage_alpha']*c['inflation']+c['wage_gamma']*backlog))
            self.nominal_wages[i] = max(1, round(self.nominal_wages[i]*(1+growth)))
        self.cpi *= 1+c['inflation']
        self.living_cost_cents = max(1, round(self.cr(c['living_cost_cr'])*self.cpi))

    def assert_invariants(self):
        if sum(self.cash.values()) != self.initial_total_cash:
            raise AssertionError('全账户现金不守恒')
        if any(type(x) is not int or x < 0 for x in self.cash.values()):
            raise AssertionError('现金不是非负整数分')
        for h, row in self.workers.items():
            if any(type(n) is not int or n < 0 for n in row.values()):
                raise AssertionError('群体就业人数无效')
            if sum(row.values())+self.unemployed_by_group[h] > self.population[h]:
                raise AssertionError('群体劳动力超过人口')
        if any(type(n) is not int or n < 0 for n in self.unemployed_by_group.values()):
            raise AssertionError('失业人数无效')
        if any(not math.isfinite(v) or not 0 <= v < 1+1e-8 for v in self.carries.values()):
            raise AssertionError('人员流动小数余量无效')
        if sum(self.employment().values())+sum(self.unemployed_by_group.values()) != self.labor_force:
            raise AssertionError('劳动力不守恒')
        raw = self.external_raw+sum(self.raw.values())+self.raw_used
        if abs(raw-self.initial_raw) > max(1e-5, self.initial_raw*1e-10):
            raise AssertionError('原料不守恒')
        for i in self.keys:
            stock = self.stock[i]+self.external_goods[i]+self.consumed[i]
            if abs(stock-self.initial_goods[i]-self.total_produced[i]) > 1e-5:
                raise AssertionError(f'{i}产出不守恒')
        if any(not math.isfinite(v) or v < -1e-7 for v in [self.external_raw, self.raw_used, *self.raw.values(), *self.stock.values(), *self.external_goods.values(), *self.consumed.values()]):
            raise AssertionError('库存出现负数/NaN/Inf')

    def make_report(self, opening, e, wages, outputs, profits, taxes, capacities, targets, details, flow):
        sectors = {i: {'name': self.sector(i)['name'], 'employed': e[i], 'capacity': capacities[i],
                       'target': targets[i], 'attractiveness': self.scores[i], 'wage_cents': self.current_wages[i],
                       'cash_cents': self.cash[i], 'sales_cents': self.sales[i], 'profit_cents': profits[i] if i != 'GOV' else None,
                       'tax_cents': taxes[i], 'output_units': outputs[i], 'stock_units': self.stock[i],
                       'raw_units': self.raw[i], 'price_cents': self.prices[i], 'capacity_details': details[i],
                       'score_details': self.score(i)} for i in self.keys}
        fiscal = self.cash['GOV']-opening['GOV']
        return {'month': self.month, 'employment': e, 'unemployed': sum(self.unemployed_by_group.values()),
                'sectors': sectors, 'wages_cents': sum(wages.values()), 'sales_cents': sum(self.sales.values()),
                'consumer_spending_cents': sum(t['amount_cents'] for t in self.transactions if t['kind'] in ('consumer_purchase', 'residential_rent')),
                'government_cash_cents': self.cash['GOV'], 'household_cash_cents': sum(self.cash[h] for h in self.population),
                'industry_cash_cents': sum(self.cash[i] for i in self.keys if i != 'GOV'),
                'external_cash_cents': self.cash['EXT'], 'fiscal_balance_cents': fiscal,
                'cash_residual_cents': sum(self.cash.values())-self.initial_total_cash,
                'living_cost_cents': self.living_cost_cents, 'transactions': deepcopy(self.transactions),
                'switches': [{'from': a, 'to': b, 'people': n} for (a, b), n in flow['switches'].items()],
                'layoffs': flow['layoffs'], 'hires': flow['hires_from_unemployment'],
                'shortfalls': deepcopy(self.shortfalls),
                'raw_reserve_units': self.external_raw, 'scope': self.scenario['scope']}

    def to_dict(self):
        data = deepcopy(self.__dict__)
        data['carries'] = [[i, kind, v] for (i, kind), v in self.carries.items()]
        data['_state_version'] = self.scenario['version']
        return data

    @classmethod
    def from_dict(cls, data):
        if data.get('_state_version') != default_scenario()['version']:
            raise ValueError('存档版本不兼容')
        data = deepcopy(data)
        data.pop('_state_version')
        validate_scenario(data['scenario'])
        city = cls(data['scenario'])
        data['carries'] = {(i, kind): v for i, kind, v in data['carries']}
        city.__dict__.update(data)
        city.assert_invariants()
        return city
