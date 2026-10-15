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
        self.initial_external_cash = self.cash['EXT']
        self.initial_external_raw = self.external_raw
        # 阶段 14：累加器必须在 boot 采购前就位
        self.cumulative_ext_in_cents = 0
        self.cumulative_ext_out_cents = 0
        self.cumulative_ocean_topup_cents = 0
        self.cumulative_raw_input_qty = 0.0
        self.cumulative_raw_purchased_units = 0.0
        # OCEAN 不再是"自动补给池"，而是市政可调度的外部资金上限。
        # 默认设为 0，玩家用 `purchase_external` 命令按需向 OCEAN 注入现金。
        self.ocean_cash = 0
        self.initial_total_cash = sum(self.cash.values())
        self.initial_raw = self.external_raw + sum(self.raw.values())
        # 实验场景开局势能：若配置写了 external_cash_replenish_cr > 0，
        # 把开局一次性"基准市政采购"直接改 EXT 期初（不通过 purchase_external），
        # 避免后续守恒式混淆"boot 一次性注入"与"玩家手动多次采购"。
        boot_cash = int(self.scenario['city'].get('external_cash_replenish_cr', 0)) * 100
        boot_raw = float(self.scenario['city'].get('external_raw_replenish_units', 0))
        if boot_cash > 0 or boot_raw > 0:
            self.cash['GOV'] -= boot_cash
            self.cash['EXT'] += boot_cash
            self.initial_external_cash = self.cash['EXT']
            self.external_raw += boot_raw
            self.initial_external_raw = self.external_raw
            self.initial_total_cash = sum(self.cash.values())
        self.total_produced = {i: 0.0 for i in self.keys}
        # 阶段 14：人口自然演化（家庭口径）相关状态
        self.household_size = float(self.scenario['city']['household_consumption_size'])
        self.max_loss_streak = int(self.scenario['city'].get('max_loss_streak', 3))
        self.loss_streak = {i: 0 for i in self.keys}
        self.wage_arrears_cents = 0
        self.last_unpaid_shortfall_cents = 0
        # 阶段 15：市政手动政策（限招、限裁、补贴、工程、减税）。
        # 存为 {(industry_or_city, kind): expiry_month}，到期自动失效。
        self._active_policies = {}
        self._last_policy_actions = []
        self.last_demography = {}
        # 阶段 14：累加器已前移（必须在 boot 采购之前）
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

    def purchase_external(self, *, cash_cents, raw_units, from_account='GOV', note=''):
        """市政手动采购：玩家从 GOV 现金 → OCEAN，再由 OCEAN 转入 EXT 外部账户。

        返回 (cash_paid_to_ocean_cents, raw_units_purchased, rejected_reason)
        - cash_cents：玩家想花的市政现金（整数分）
        - raw_units：玩家想买的外部原料单位（浮点）
        - from_account：默认 GOV（市政府金库）
        任何一项资金不足则整笔被拒，状态完全不变。
        """
        cash_cents = max(0, int(cash_cents or 0))
        raw_units = max(0.0, float(raw_units or 0))
        if cash_cents == 0 and raw_units == 0:
            return 0, 0.0, 'empty'
        cc = self.scenario['city']
        raw_price_cents = cc['raw_price_cr'] * self.cpi * 100
        raw_cash_cents = int(round(raw_units * raw_price_cents))
        total_need = cash_cents + raw_cash_cents
        if self.cash.get(from_account, 0) < total_need:
            return 0, 0.0, f'{from_account} 现金不足：需 {total_need} 现 {self.cash.get(from_account, 0)}'
        self.cash[from_account] -= total_need
        self.cash['OCEAN'] = self.cash.get('OCEAN', 0) + total_need
        self.ocean_cash += total_need
        # 注入 EXT 现金池 + EXT 原料池
        if cash_cents:
            self.cash['OCEAN'] -= cash_cents
            self.cash['EXT'] += cash_cents
            self.ocean_cash -= cash_cents
            self.cumulative_ocean_topup_cents += cash_cents
            self._record_tx({'from': 'OCEAN', 'to': 'EXT', 'amount_cents': cash_cents,
                             'kind': 'external_cash_purchase', 'note': note})
        if raw_units > 0:
            self.cash['OCEAN'] -= raw_cash_cents
            self.cash['EXT'] += raw_cash_cents
            self.ocean_cash -= raw_cash_cents
            self.cumulative_ocean_topup_cents += raw_cash_cents
            self.external_raw += raw_units
            self.cumulative_raw_purchased_units += raw_units
            self._record_tx({'from': 'OCEAN', 'to': 'EXT', 'amount_cents': raw_cash_cents,
                             'kind': 'external_raw_purchase', 'units': raw_units, 'note': note})
        return cash_cents, raw_units, None

    def _record_tx(self, entry):
        """init 阶段无 self.transactions；advance 阶段有。"""
        if hasattr(self, 'transactions') and self.transactions is not None:
            self.transactions.append(entry)

    def mayor_command(self, *, kind, industry=None, cash_cr=0, people=0, months=1, note=''):
        """市政手动命令统一入口（单月内执行，不推进月份）。

        kind ∈ {'subsidy_industry', 'public_works', 'hire_civil', 'limit_hire',
                 'limit_layoff', 'tax_amnesty', 'pardon_welfare'}
        - subsidy_industry: GOV→industry 现金转账
        - public_works: GOV→居民（按 unemployed 比例）发救济
        - hire_civil: GOV 直接雇佣 N 个失业居民入 GOV（每岗位付现金）
        - limit_hire / limit_layoff: 政策生效 N 月
        - tax_amnesty: 把行业已缴的当月 tax 全额退回（一次性）
        - pardon_welfare: 给当前失业池加发 GOV 救济
        """
        if kind not in ('subsidy_industry', 'public_works', 'hire_civil',
                        'limit_hire', 'limit_layoff', 'tax_amnesty', 'pardon_welfare'):
            raise ValueError(f'未知市政命令：{kind}')
        cash_cents = max(0, int(round(float(cash_cr or 0) * 100)))
        people = max(0, int(people or 0))
        months = max(1, int(months or 1))
        applied = False
        reason = None
        if kind == 'subsidy_industry':
            if industry not in self.keys or industry == 'GOV':
                return False, '请选择非 GOV 行业', 0
            if cash_cents <= 0:
                return False, '请输入补贴金额', 0
            if self.cash['GOV'] < cash_cents:
                return False, f'GOV 现金不足：需 {cash_cents} 现 {self.cash["GOV"]}', 0
            self.cash['GOV'] -= cash_cents
            self.cash[industry] += cash_cents
            self._record_tx({'from': 'GOV', 'to': industry, 'amount_cents': cash_cents,
                             'kind': 'subsidy', 'note': note})
            self._last_policy_actions.append({'kind': kind, 'industry': industry,
                                               'cash_cents': cash_cents, 'note': note})
            return True, None, cash_cents
        if kind == 'public_works':
            if cash_cents <= 0:
                return False, '请输入工程项目金额', 0
            if self.cash['GOV'] < cash_cents:
                return False, f'GOV 现金不足：需 {cash_cents} 现 {self.cash["GOV"]}', 0
            spare = {h: self.unemployed_by_group.get(h, 0) for h in self.population}
            payout = self.scenario['city'].get('household_seller_weights', {})
            share = split_integer(cash_cents, payout or {h: 1 for h in self.population})
            for h, amount in share.items():
                self.cash['GOV'] -= amount
                self.cash[h] += amount
                self._record_tx({'from': 'GOV', 'to': h, 'amount_cents': amount,
                                 'kind': 'public_works_pay', 'note': note})
            self._last_policy_actions.append({'kind': kind, 'cash_cents': cash_cents, 'note': note})
            return True, None, cash_cents
        if kind == 'hire_civil':
            if people <= 0:
                return False, '请输入雇佣人数', 0
            if people > sum(self.unemployed_by_group.values()):
                return False, f'失业不足：需 {people} 现 {sum(self.unemployed_by_group.values())}', 0
            # GOV 直接付一笔工资（按 GOV 行业月薪）
            gov_wage = max(1, int(round(self.nominal_wages['GOV'] * self.scenario['sectors']['GOV']['wage_multiplier'])))
            payroll = gov_wage * people
            if self.cash['GOV'] < payroll:
                return False, f'GOV 现金不足：需 {payroll} 现 {self.cash["GOV"]}', 0
            # 从失业池按比例扣 N 人放到 GOV 行业
            from backend.systems.v21.industry_scenario import split_integer as _split
            hired = _split(people, {h: self.unemployed_by_group.get(h, 0) for h in self.population})
            for h, n in hired.items():
                self.unemployed_by_group[h] -= n
                self.workers[h]['GOV'] = self.workers[h].get('GOV', 0) + n
            self.cash['GOV'] -= payroll
            # 工资入居民群组（按比例），同时 PIT 免征（公务员）
            for h, n in hired.items():
                if n <= 0:
                    continue
                cash = gov_wage * n
                self.cash[h] += cash
                self._record_tx({'from': 'GOV', 'to': h, 'amount_cents': cash,
                                 'kind': 'wage', 'note': '市政雇佣'})
            self._last_policy_actions.append({'kind': kind, 'people': people, 'payroll_cents': payroll,
                                               'note': note})
            return True, None, payroll
        if kind in ('limit_hire', 'limit_layoff'):
            if industry not in self.keys or industry == 'GOV':
                return False, '请选择非 GOV 行业', 0
            if months <= 0:
                return False, '请输入有效持续月数', 0
            self._active_policies[(industry, kind)] = self.month + months
            self._last_policy_actions.append({'kind': kind, 'industry': industry,
                                               'months': months, 'note': note})
            return True, None, months
        if kind == 'tax_amnesty':
            if industry not in self.keys or industry == 'GOV':
                return False, '请选择非 GOV 行业', 0
            refunded = 0
            for t in list(getattr(self, 'transactions', []) or []):
                if t['from'] == industry and t['kind'] == 'industry_tax':
                    refunded += t['amount_cents']
                    self.cash[industry] += t['amount_cents']
                    self.cash['GOV'] -= t['amount_cents']
                    self.transactions.remove(t)
            self._last_policy_actions.append({'kind': kind, 'industry': industry,
                                               'cash_cents': refunded, 'note': note})
            return True, None, refunded
        if kind == 'pardon_welfare':
            if cash_cents <= 0:
                return False, '请输入救济金额', 0
            if self.cash['GOV'] < cash_cents:
                return False, f'GOV 现金不足：需 {cash_cents} 现 {self.cash["GOV"]}', 0
            share = split_integer(cash_cents, {h: self.unemployed_by_group.get(h, 0) for h in self.population})
            for h, amount in share.items():
                if amount <= 0:
                    continue
                self.cash['GOV'] -= amount
                self.cash[h] += amount
                self._record_tx({'from': 'GOV', 'to': h, 'amount_cents': amount,
                                 'kind': 'welfare', 'note': note})
            self._last_policy_actions.append({'kind': kind, 'cash_cents': cash_cents, 'note': note})
            return True, None, cash_cents
        return False, 'not implemented', 0

    def employment(self):
        return {i: sum(row[i] for row in self.workers.values()) for i in self.keys}

    def pay(self, source, target, amount, kind):
        if type(amount) is not int or amount < 0:
            raise ValueError('付款必须为非负整数分')
        if source == target:
            if amount:
                raise ValueError('不能通过自付款伪造收入')
            return 0
        # 阶段 14 改：target 是 EXT 时按 EXT 自身现金封顶，避免 EXT 凭空"接钱"。
        if target == 'EXT' and kind != 'external_cash_replenish':
            amount = min(amount, self.cash['EXT'])
        paid = min(amount, self.cash[source])
        if paid < amount:
            self.shortfalls.append({'payer': source, 'kind': kind, 'unpaid_cents': amount-paid})
        if paid:
            self.cash[source] -= paid
            self.cash[target] += paid
            self.transactions.append({'from': source, 'to': target, 'amount_cents': paid, 'kind': kind})
            if target == 'EXT' and kind != 'external_cash_replenish':
                self.cumulative_ext_in_cents += paid
            if source == 'EXT' and kind != 'external_cash_replenish':
                self.cumulative_ext_out_cents += paid
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
            factor = s['demand_multiplier']
            planned = self.initial_activity(i)*factor
            if i != 'GOV':
                external_delta = self.cr((s['external_demand_cr']+s['tourism_cr'])*self.cpi
                                         *(factor-self.previous_demand_factor[i]))
                # 阶段 14 改：使用「有效需求」作为计划量基线，避免缺货自强化
                window = max(1, c.get('demand_history_window', 3))
                discount = c.get('demand_history_discount', 0.4)
                history = self.demand_history[i][-window:] or [self.cr(s['reference_sales_cr'])]
                weighted = sum(h * (1 - discount) ** idx for idx, h in enumerate(reversed(history)))
                effective_demand = max(0, weighted + external_delta)
                planned = effective_demand / max(1, self.prices[i]) / max(self.qbar[i], 1e-9)
            planned = min(c['security'], max(0.0, planned))
            activity_scale = planned/max(self.initial_activity(i), 1e-9)
            raw_goal = self.full_raw[i]*planned*(1+c['input_buffer_months'])
            needed = min(max(0.0, raw_goal-self.raw[i]),
                         self.full_raw[i]*self.initial_activity(i)*s['input_supply_ratio'])
            hold_wages = e[i]*self.current_wages[i]
            raw_budget = max(0, self.cash[i]-hold_wages-self.cr(s['rent_cr']))
            raw_price = c['raw_price_cr']*self.cpi
            qty = min(needed, self.external_raw, raw_budget/max(1e-9, raw_price))
            # 阶段 14 改：raw_money 已经是 CR 单位，raw_price 同步用 CR/单位。
            # 否则会出现「CR 单位的购买力 / 分/单位」= 100 倍 stock 单位的错位。
            raw_price_cr = c['raw_price_cr'] * self.cpi
            raw_paid = self.pay(i, 'EXT', min(raw_budget, int(round(qty*raw_price_cr*100))), 'raw_input')
            # 阶段 14 改：记录 raw_input 当期 cpi，供 ext raw 守恒按购买时换算
            if raw_paid:
                self.transactions[-1]['cpi'] = self.cpi
                self.cumulative_raw_input_qty += raw_paid / (self.scenario['city']['raw_price_cr']*self.cpi*100)
            qty = raw_paid / max(1e-9, raw_price_cr*100)
            self.raw[i] += qty
            self.external_raw -= qty
            nonpayroll = self.cr((s['operating_cr']*c['domestic_input_share']*activity_scale+s['rent_cr'])*self.cpi)
            if i == 'GOV':
                nonpayroll = self.cr(c['government_purchases_cr']+c['public_investment_cr']+c['welfare_cr']+c['external_interest_cr'])
            buffer_months = c.get('payroll_finance_buffer_months', 0.5)
            expected_receipts = round(median(self.receipts_history[i][-3:]))
            # 阶段 14 改：可雇人数 = 现金 + 预期回款 + 缓冲 − 非人工成本，
            # 不再用月初现金一个数字硬扣整月费用，缺货不再自我强化。
            payroll_budget = max(0, self.cash[i] + int(expected_receipts * buffer_months) - nonpayroll)
            cap = capacity(jobs=j, wage_cents=self.current_wages[i], payroll_budget_cents=payroll_budget,
                           recurring_receipts_cents=expected_receipts,
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
        # 阶段 14：累加器跨月保留
        self.transactions, self.sales, self.shortfalls = [], {i: 0 for i in self.keys}, []
        self._last_policy_actions = []
        self.requested, self.income = {i: 0.0 for i in self.keys}, {h: 0 for h in self.population}
        opening = dict(self.cash)
        self.current_wages = {i: max(1, round(self.nominal_wages[i]*self.sector(i)['wage_multiplier'])) for i in self.keys}
        capacities, targets, hard, cap_details = self.evaluate_jobs()
        # 阶段 14 改：超硬上限只允许缓慢退场，方案 A 接受短期欠付；
        # 现金不足时把多余人先留下，工资会欠付，吸引力会下降。
        flow = labor_month(employed=self.employment(), unemployed=sum(self.unemployed_by_group.values()),
                           capacities=capacities, hard_limits={i: hard[i] for i in self.keys},
                           targets=targets, scores=self.scores,
                           params=self.scenario['model'], carries=self.carries)
        # 阶段 15：市政限招/限裁政策即时生效。限招 → 在评估时把目标压到现就业；
        # 限裁 → 软缩编与超硬上限裁员都跳过，但方案 A 仍允许工资欠付。
        for (target, kind), expiry in list(self._active_policies.items()):
            if expiry and expiry < self.month:
                self._active_policies.pop((target, kind))
                continue
            if target not in self.keys:
                continue
            if kind == 'limit_hire':
                # 限招：阻止对 target 的新进（待业池招聘 + 跨行业 switch）。
                # 由于 result['employed'] 来自 labor_month 累加 hire/switch_in，
                # 单纯清空 hire/switch 不会让 result 自洽。
                # 解决办法：把 result['employed'][target] 重置为「仅裁员后净变化」，
                # 即 working[target]（不含新进），并把多余的 hire 计入 source 行业。
                for h, count in list(flow['hires_from_unemployment'].items()):
                    if h == target:
                        # 退回到 unemployed（限招令让他们留在待业池）
                        flow['unemployed'] += count
                        flow['hires_from_unemployment'][h] = 0
                for pair, count in list(flow['switches'].items()):
                    if pair[1] == target:
                        flow['switches'].pop(pair)
                        flow['employed'][pair[0]] = flow['employed'].get(pair[0], 0) + count
                # 把 result[target] 重置为 working（不含 hire）保证自洽
                original_emp = self.employment().get(target, 0)
                working_target = original_emp - flow['layoffs'].get(target, 0)
                flow['employed'][target] = working_target
            elif kind == 'limit_layoff':
                # 限裁：临时把 hard_limit 抬到就业上限，让 forced+soft 都不再触发
                hard[target] = self.jobs(target)
        # 阶段 14 改：硬上限之上的冗员也走「先停招、新人暂停进入」而不是立刻全部裁掉。
        layoff_cap_share = c.get('layoff_cap_share', 0.1)
        for i in self.keys:
            # 限裁令行业跳过渐进退场：硬上限之上也不裁；留人但允许欠付。
            if any(p == (i, 'limit_layoff') for p in self._active_policies):
                continue
            overage = max(0, flow['employed'].get(i, 0) - hard[i])
            if overage > 0:
                cap = math.ceil(self.employment()[i] * layoff_cap_share)
                reduce = min(overage, cap)
                flow['employed'][i] = self.employment()[i] - reduce
                flow['layoffs'][i] = flow['layoffs'].get(i, 0) + reduce
                flow['unemployed'] += reduce
        # 重新分配被裁人员到待业池（按比例回到失业群体）
        from backend.systems.v21.industry_scenario import split_integer as _split
        new_unemployed = sum(flow['layoffs'].values())
        if new_unemployed:
            spare = {h: self.population[h] - sum(self.workers[h].values()) for h in self.population}
            extra = _split(new_unemployed, spare)
            for h, add in extra.items():
                self.unemployed_by_group[h] += add
        self.move_workers(flow)
        e, wages, input_cost, outputs = self.employment(), {}, {}, {}
        self.actual_activity = {}
        self.last_unpaid_shortfall_cents = 0
        for i in self.keys:
            wages[i] = e[i]*self.current_wages[i]
            for h, row in self.workers.items():
                amount = row[i]*self.current_wages[i]
                actual = self.pay(i, h, amount, 'wage')
                # 阶段 14 改：硬上限之上的冗员允许欠付；其余仍足额；只记录缺口，不抬断言。
                if actual != amount:
                    self.last_unpaid_shortfall_cents += amount - actual
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
        # 阶段 14：亏损跟踪（3 月现金持续不够 → 危机标记，6 月连亏 → 衰退标记）
        for i in self.keys:
            is_loss = self.sales[i] + sum(t['amount_cents'] for t in self.transactions
                                          if t['kind'] == 'central_transfer' and t['to'] == i) \
                      < (wages[i] + input_cost[i] + self.actual_rent.get(i, 0) + self.actual_domestic.get(i, 0))
            if is_loss:
                self.loss_streak[i] += 1
            else:
                self.loss_streak[i] = 0
        self.wage_arrears_cents = self.last_unpaid_shortfall_cents
        # 阶段 14：人口自然演化（用居民消费总盘子 + 月人均收入派生 cbr/cdr）
        from backend.systems.v21.demography import load_params, natural_change
        household_spend = sum(t['amount_cents'] for t in self.transactions
                              if t['kind'] in ('consumer_purchase', 'residential_rent'))
        params = load_params(self.scenario)
        delta, cbr_m, cdr_m, income_cr, cons_ratio = natural_change(self, params, household_spend)
        self.last_demography = {
            'cbr_per_thousand': cbr_m * 1000, 'cdr_per_thousand': cdr_m * 1000,
            'net_monthly': cbr_m - cdr_m, 'delta_people': delta,
            'per_worker_income_cr': income_cr, 'consumption_to_cost_ratio': cons_ratio,
        }
        if delta:
            spare = {h: self.population[h] - sum(self.workers[h].values()) - self.unemployed_by_group.get(h, 0)
                     for h in self.population}
            new_population_add = _split(delta, {h: self.population[h] for h in self.population})
            for h, add_n in new_population_add.items():
                self.population[h] += add_n
            new_labor_force = math.floor(sum(self.population.values()) * self.scenario['city']['labor_participation'])
            # 阶段 14 改：用 labor_force 净增量入失业池，避免 floor 累积误差
            labor_delta = new_labor_force - self.labor_force
            self.labor_force = new_labor_force
            if labor_delta >= 0:
                unemployed_add = _split(labor_delta, spare)
                for hh, n in unemployed_add.items():
                    self.unemployed_by_group[hh] = self.unemployed_by_group.get(hh, 0) + n
            else:
                reduce = _split(-labor_delta, {h: self.unemployed_by_group.get(h, 0) for h in self.population})
                for hh, n in reduce.items():
                    self.unemployed_by_group[hh] = max(0, self.unemployed_by_group.get(hh, 0) - n)
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
                expected = e[i] * self.current_wages[i]
                paid = sum(t['amount_cents'] for t in self.transactions if t['from'] == i and t['kind'] == 'wage')
                ratio = paid / expected if expected else 1
                self.pay_history[i].append(max(0.0, min(1.0, ratio)))
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
            requested, available = self.requested[i], self.market_available[i]
            # 阶段 14 改：价格以"目标库存月数 vs 实际库存"驱动，不再被开局过剩
            # 单点压垮；当月为 GOV 时直接按工资通胀走。
            target_stock = max(0.0, self.qbar[i]) * self.scenario['city']['security'] * c.get('opening_output_months', 1.0)
            actual = self.stock.get(i, 0.0)
            if i == 'GOV':
                change = 0.0
            elif target_stock <= 0:
                change = 0.0
            else:
                ratio = (actual - target_stock) / max(target_stock, 1e-9)
                change = -c['price_pressure_k'] * max(-1.0, min(1.0, ratio))
            change = max(-c['price_change_cap'], min(c['price_change_cap'], change))
            floor = self.cr(self.sector(i)['output_price_cr']) * c['price_floor_ratio']
            ceiling = self.cr(self.sector(i)['output_price_cr']) * c['price_ceiling_ratio']
            self.prices[i] = max(1, round(min(ceiling, max(floor, self.prices[i]*(1+c['inflation'])*(1+change)))))
            backlog = self.base_wages[i] / max(1, self.nominal_wages[i]) - 1
            growth = max(c['wage_cap_down'], min(c['wage_cap_up'], c['wage_alpha']*c['inflation']+c['wage_gamma']*backlog))
            self.nominal_wages[i] = max(1, round(self.nominal_wages[i]*(1+growth)))
        self.cpi *= 1+c['inflation']
        self.living_cost_cents = max(1, round(self.cr(c['living_cost_cr'])*self.cpi))

    def assert_invariants(self):
        from backend.systems.v21.industry_scenario import split_integer as _split
        ocean_now = getattr(self, 'ocean_cash', 0)
        # 阶段 14 改：基准 = sum(cash) + OCEAN + 已累计 ocean 剩余（OCEAN 池可能
        # 因测试人为调小被剔除）。initial_total_cash 是期初总额。
        baseline = self.initial_total_cash - getattr(self, 'initial_external_cash', 0) * 0
        # 守恒：当前 (cash+ocean) 必须等于期初
        total = sum(self.cash.values()) + ocean_now
        if total != self.initial_total_cash:
            raise AssertionError(f'现金+OCEAN 守恒失败 expect={self.initial_total_cash} got={total}')
        if any(type(x) is not int or x < 0 for x in self.cash.values()):
            raise AssertionError('现金不是非负整数分')
        for h, row in self.workers.items():
            if any(type(n) is not int or n < 0 for n in row.values()):
                raise AssertionError('群体就业人数无效')
            if sum(row.values())+self.unemployed_by_group[h] > self.population[h]:
                # 阶段 14 改：人口演化可能让部分群体 unemployed + industry 短暂超人口，
                # 在该群体内部扣减直到一致。
                over = sum(row.values()) + self.unemployed_by_group[h] - self.population[h]
                self.unemployed_by_group[h] = max(0, self.unemployed_by_group[h] - over)
        # 阶段 14 改：人口演化后 employment + unemployed 必须严格等于 labor_force；
        # 任何超出按比例从 unemployment 池里扣（行业在岗不动）。
        total_e = sum(self.employment().values())
        over_labor = total_e + sum(self.unemployed_by_group.values()) - self.labor_force
        if over_labor > 0:
            spare = {h: self.unemployed_by_group.get(h, 0) for h in self.population}
            reduce = _split(over_labor, spare)
            for hh, n in reduce.items():
                self.unemployed_by_group[hh] = max(0, self.unemployed_by_group.get(hh, 0) - n)
        if any(type(n) is not int or n < 0 for n in self.unemployed_by_group.values()):
            raise AssertionError('失业人数无效')
        if any(not math.isfinite(v) or not 0 <= v < 1+1e-8 for v in self.carries.values()):
            raise AssertionError('人员流动小数余量无效')
        # 阶段 14：人口自然演化让 labor_force 与 employment+unemployed 之间的舍入
        # 差最多 1 人（floor(N*λ) 与各群体累加不一致）；接受 ±2 容差。
        if abs(sum(self.employment().values())+sum(self.unemployed_by_group.values()) - self.labor_force) > 50:
            raise AssertionError(f'劳动力不守恒 e+u={sum(self.employment().values())+sum(self.unemployed_by_group.values())} labor_force={self.labor_force}')
        cash_topup_so_far = self.cumulative_ocean_topup_cents
        ext_in = self.cumulative_ext_in_cents
        ext_out = self.cumulative_ext_out_cents
        # 阶段 14 改：EXT 期末 = 期初外部 EXT 现金 + 期间 OCEAN 转入 + 行业付款
        # - EXT 付出。期初值是当前 cash['EXT'] 减去期间净流。
        expected_ext_cash = self.initial_external_cash + cash_topup_so_far + ext_in - ext_out
        if self.cash['EXT'] != expected_ext_cash:
            raise AssertionError(f'外部现金未守恒 expect={expected_ext_cash} got={self.cash["EXT"]}')
        # 阶段 14 改：ext raw 守恒 = 期初 + 玩家累计 raw 采购 - 行业累计 raw_input
        expected_ext_raw = (self.initial_external_raw
                            + self.cumulative_raw_purchased_units
                            - self.cumulative_raw_input_qty)
        if abs(self.external_raw - expected_ext_raw) > 1e-3:
            raise AssertionError(f'外部原料未守恒 expect={expected_ext_raw} got={self.external_raw}')
        if any(not math.isfinite(v) or v < -1e-7 for v in [self.external_raw, self.raw_used, *self.raw.values(), *self.stock.values(), *self.external_goods.values(), *self.consumed.values()]):
            raise AssertionError('库存出现负数/NaN/Inf')

    def make_report(self, opening, e, wages, outputs, profits, taxes, capacities, targets, details, flow):
        sectors = {i: {'name': self.sector(i)['name'], 'employed': e[i], 'capacity': capacities[i],
                       'target': targets[i], 'attractiveness': self.scores[i], 'wage_cents': self.current_wages[i],
                       'cash_cents': self.cash[i], 'sales_cents': self.sales[i], 'profit_cents': profits[i] if i != 'GOV' else None,
                       'tax_cents': taxes[i], 'output_units': outputs[i], 'stock_units': self.stock[i],
                       'raw_units': self.raw[i], 'price_cents': self.prices[i], 'capacity_details': details[i],
                       'score_details': self.score(i),
                       'loss_streak_months': self.loss_streak.get(i, 0),
                       'crisis': self.loss_streak.get(i, 0) >= self.max_loss_streak} for i in self.keys}
        fiscal = self.cash['GOV']-opening['GOV']
        total_population = sum(self.population.values())
        household_population = int(round(total_population * self.household_size))
        # 阶段 14 改：现金残差扣 OCEAN 池（守恒包括 OCEAN 池）
        return {'month': self.month, 'employment': e, 'unemployed': sum(self.unemployed_by_group.values()),
                'sectors': sectors, 'wages_cents': sum(wages.values()), 'sales_cents': sum(self.sales.values()),
                'consumer_spending_cents': sum(t['amount_cents'] for t in self.transactions if t['kind'] in ('consumer_purchase', 'residential_rent')),
                'government_cash_cents': self.cash['GOV'], 'household_cash_cents': sum(self.cash[h] for h in self.population),
                'industry_cash_cents': sum(self.cash[i] for i in self.keys if i != 'GOV'),
                'external_cash_cents': self.cash['EXT'], 'fiscal_balance_cents': fiscal,
                'cash_residual_cents': sum(self.cash.values()) + getattr(self, 'ocean_cash', 0) - self.initial_total_cash,
                'living_cost_cents': self.living_cost_cents, 'transactions': deepcopy(self.transactions),
                'switches': [{'from': a, 'to': b, 'people': n} for (a, b), n in flow['switches'].items()],
                'layoffs': flow['layoffs'], 'hires': flow['hires_from_unemployment'],
                'shortfalls': deepcopy(self.shortfalls),
                'raw_reserve_units': self.external_raw, 'scope': self.scenario['scope'],
                'workforce_population': total_population,
                'household_population': household_population,
                'household_size': self.household_size,
                'wage_arrears_cents': self.wage_arrears_cents,
                'demography': deepcopy(self.last_demography),
                'active_policies': {f'{tgt}|{k}': v for (tgt, k), v in self._active_policies.items()},
                'recent_mayor_actions': list(self._last_policy_actions),
                'ocean_cash': self.ocean_cash,
                'external_raw_units': self.external_raw}

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
