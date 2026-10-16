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
    STATE_VERSION = 2
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
        from backend.systems.v21.map_economy import housing_capacity
        self.housing_capacity = housing_capacity(self)
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
        self.government_raw_units = 0.0
        self.cumulative_government_raw_resale_units = 0.0
        self.cumulative_government_raw_resale_cents = 0
        # OCEAN 不再是"自动补给池"，而是市政可调度的外部资金上限。
        # 默认设为 0，玩家用 `purchase_external` 命令按需向 OCEAN 注入现金。
        self.ocean_cash = 0
        self.initial_total_cash = sum(self.cash.values())
        self.initial_raw = self.external_raw + sum(self.raw.values())
        self.initial_stock = dict(self.stock)
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
            self.initial_raw += boot_raw
            self.initial_total_cash = sum(self.cash.values())
        self.total_produced = {i: 0.0 for i in self.keys}
        # 阶段 14：人口自然演化（家庭口径）相关状态
        self.household_size = float(self.scenario['city']['household_consumption_size'])
        self.max_loss_streak = int(self.scenario['city'].get('max_loss_streak', 3))
        self.loss_streak = {i: 0 for i in self.keys}
        self.wage_arrears_cents = 0
        self.arrears = {i: {h: 0 for h in self.population} for i in self.keys}
        self.population_carry = 0.0
        self.pending_transactions = []
        self._settling = False
        self.tax_refunded = {}
        self.funded_civil_staff_plan = 0
        self._fiscal_opening_cash_cents = self.cash['GOV']
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

    def add_industry_capacity(self, industry, jobs_added):
        """在地图上建造 / 拆除一座行业建筑。

        - `jobs_added > 0`：在地图上新增一座建筑，给行业 +N jobs
        - `jobs_added < 0`：拆除建筑，给行业 -N jobs（但不能低于开局基准）
        - 不立即招人或裁员；下一次 advance() 的 evaluate_jobs() 会按新容量计算
        """
        if industry not in self.keys:
            raise ValueError(f'未知行业：{industry}')
        jobs_added = int(jobs_added)
        if jobs_added == 0:
            raise ValueError('jobs_added 不能为 0')
        current = self.definitions[industry]['job_capacity_max']
        baseline = int(self.definitions[industry]['initial_employees_industrial_city'])
        new_capacity = current + jobs_added
        if new_capacity < baseline:
            raise ValueError(
                f'行业 {industry} 的容量不能低于开局基准 {baseline}（欲变为 {new_capacity}）')
        self.definitions[industry]['job_capacity_max'] = new_capacity
        return new_capacity

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
        if from_account != 'GOV':
            return 0, 0.0, '市政采购仅支持GOV账户'
        if type(cash_cents) is not int or cash_cents < 0:
            raise ValueError('采购金额必须为非负整数分')
        if type(raw_units) not in (int, float) or not math.isfinite(raw_units) or raw_units < 0:
            raise ValueError('采购原料须为有限非负数')
        raw_units = float(raw_units)
        if cash_cents == 0 and raw_units == 0:
            return 0, 0.0, 'empty'
        cc = self.scenario['city']
        raw_price_cents = cc['raw_price_cr'] * self.cpi * 100
        raw_cash_cents = int(round(raw_units * raw_price_cents))
        total_need = cash_cents + raw_cash_cents
        if self.cash.get(from_account, 0) < total_need:
            return 0, 0.0, f'{from_account} 现金不足：需 {total_need} 现 {self.cash.get(from_account, 0)}'
        self._record_tx({'from': from_account, 'to': 'OCEAN', 'amount_cents': total_need,
                         'kind': 'external_purchase_funding', 'note': note})
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
            self.government_raw_units += raw_units
            self.cumulative_raw_purchased_units += raw_units
            self._record_tx({'from': 'OCEAN', 'to': 'EXT', 'amount_cents': raw_cash_cents,
                             'kind': 'external_raw_purchase', 'units': raw_units, 'note': note})
        return cash_cents, raw_units, None

    def _record_tx(self, entry):
        """init 阶段无 self.transactions；advance 阶段有。"""
        entry = {**entry, 'action_month': self.month}
        if self._settling:
            self.transactions.append(entry)
        else:
            self.pending_transactions.append(entry)

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
        if type(cash_cr) not in (int, float) or not math.isfinite(cash_cr) or cash_cr < 0:
            raise ValueError('政策金额须为有限非负数')
        if type(people) is not int or people < 0:
            raise ValueError('人数须为非负整数')
        if type(months) is not int or not 1 <= months <= 120:
            raise ValueError('政策持续月数须为1—120整数')
        cash_cents = round(cash_cr * 100)
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
            if not sum(spare.values()):
                return False, '当前没有失业居民', 0
            share = split_integer(cash_cents, spare)
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
            # 持续行政岗位授权与预付工资分开；后续每月仍按实际财政支付工资。
            self.scenario.setdefault('_map_capacity_base', {
                i: {'jobs': self.jobs(i), 'qbar': self.qbar[i], 'full_raw': self.full_raw[i]}
                for i in self.keys})
            old_jobs = self.jobs('GOV')
            new_jobs = self.add_industry_capacity('GOV', people)
            self.qbar['GOV'] *= new_jobs / old_jobs
            self.full_raw['GOV'] *= new_jobs / old_jobs
            self.funded_civil_staff_plan += people
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
            taxes = sum(t['amount_cents'] for t in getattr(self, 'transactions', [])
                        if t['from'] == industry and t['kind'] == 'industry_tax')
            key = f'{self.month}|{industry}'
            refunded = max(0, taxes - self.tax_refunded.get(key, 0))
            if self.cash['GOV'] < refunded:
                return False, 'GOV 现金不足，无法退税', 0
            self.cash['GOV'] -= refunded
            self.cash[industry] += refunded
            self.tax_refunded[key] = self.tax_refunded.get(key, 0) + refunded
            if refunded:
                self._record_tx({'from': 'GOV', 'to': industry, 'amount_cents': refunded,
                                 'kind': 'tax_refund', 'note': note})
            self._last_policy_actions.append({'kind': kind, 'industry': industry,
                                               'cash_cents': refunded, 'note': note})
            return True, None, refunded
        if kind == 'pardon_welfare':
            if cash_cents <= 0:
                return False, '请输入救济金额', 0
            if self.cash['GOV'] < cash_cents:
                return False, f'GOV 现金不足：需 {cash_cents} 现 {self.cash["GOV"]}', 0
            if not sum(self.unemployed_by_group.values()):
                return False, '当前没有失业居民', 0
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
            if i == 'GOV':
                planned_staff = self.definitions[i]['initial_employees_industrial_city'] + self.funded_civil_staff_plan
                planned = c['security'] * (planned_staff / j) ** model['capacity']['labor_output_exponent']
            if i != 'GOV':
                external_delta = self.cr((s['external_demand_cr']+s['tourism_cr'])*self.cpi
                                         *(factor-self.previous_demand_factor[i]))
                # 阶段 14 改：使用「有效需求」作为计划量基线，避免缺货自强化
                window = max(1, c.get('demand_history_window', 3))
                discount = c.get('demand_history_discount', 0.4)
                history = self.demand_history[i][-window:] or [self.cr(s['reference_sales_cr'])]
                history_weights = [(1 - discount) ** idx for idx in range(len(history))]
                weighted = sum(h * w for h, w in zip(reversed(history), history_weights)) / sum(history_weights)
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
            qty = min(needed, self.external_raw, raw_budget/max(1e-9, raw_price*100))
            self.purchase_industry_raw(i, qty, raw_budget)
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

    def purchase_industry_raw(self, industry, wanted_units, budget_cents):
        """市政预付库存优先转售；只按行业实付款交货，同一批不重复付给外部。"""
        price = self.scenario['city']['raw_price_cr'] * self.cpi * 100
        remaining = min(max(0.0, wanted_units), self.external_raw)
        budget = min(max(0, int(budget_cents)), self.cash[industry])
        purchased = 0.0
        for owner in ('GOV', 'EXT'):
            available = self.government_raw_units if owner == 'GOV' else self.external_raw - self.government_raw_units
            qty = min(remaining, max(0.0, available))
            amount = min(budget, math.floor(qty * price + 1e-9))
            if owner == industry or amount <= 0:
                continue
            kind = 'raw_resale_payment' if owner == 'GOV' else 'raw_input'
            paid = self.pay(industry, owner, amount, kind)
            delivered = paid / price
            if paid:
                self.transactions[-1].update(cpi=self.cpi, units=delivered, raw_owner=owner)
            self.external_raw -= delivered
            self.raw[industry] += delivered
            self.cumulative_raw_input_qty += delivered
            if owner == 'GOV':
                self.government_raw_units -= delivered
                self.cumulative_government_raw_resale_units += delivered
                self.cumulative_government_raw_resale_cents += paid
            remaining -= delivered
            budget -= paid
            purchased += delivered
        return purchased

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
        self.transactions, self.sales, self.shortfalls = self.pending_transactions, {i: 0 for i in self.keys}, []
        self.pending_transactions = []
        self._settling = True
        self.requested, self.income = {i: 0.0 for i in self.keys}, {h: 0 for h in self.population}
        opening = dict(self.cash)
        # 月间付款已改变现金，待结算流水仍属于本期，因此逆推本期财政期初。
        pending_government_net = sum(
            (t['amount_cents'] if t['to'] == 'GOV' else 0)
            - (t['amount_cents'] if t['from'] == 'GOV' else 0)
            for t in self.transactions)
        self._fiscal_opening_cash_cents = opening['GOV'] - pending_government_net
        self.current_wages = {i: max(1, round(self.nominal_wages[i]*self.sector(i)['wage_multiplier'])) for i in self.keys}
        from backend.systems.v21.map_economy import advance_buildings
        advance_buildings(self)
        capacities, targets, hard, cap_details = self.evaluate_jobs()
        active = {key for key, expiry in self._active_policies.items() if expiry > self.month}
        flow = labor_month(employed=self.employment(), unemployed=sum(self.unemployed_by_group.values()),
                           capacities=capacities, hard_limits=hard, targets=targets, scores=self.scores,
                           params=self.scenario['model'], carries=self.carries,
                           limit_hire={i for i, k in active if k == 'limit_hire'},
                           limit_layoff={i for i, k in active if k == 'limit_layoff'},
                           layoff_cap_share=c.get('layoff_cap_share', 0.1))
        self.move_workers(flow)
        e, wages, input_cost, outputs = self.employment(), {}, {}, {}
        self.actual_activity = {}
        self.last_unpaid_shortfall_cents = 0
        for i in self.keys:
            self.repay_arrears(i)
            wages[i] = e[i]*self.current_wages[i]
            for h, row in self.workers.items():
                amount = row[i]*self.current_wages[i]
                actual = self.pay(i, h, amount, 'wage')
                # 阶段 14 改：硬上限之上的冗员允许欠付；其余仍足额；只记录缺口，不抬断言。
                if actual != amount:
                    self.last_unpaid_shortfall_cents += amount - actual
                    self.arrears[i][h] += amount - actual
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
        self.wage_arrears_cents = sum(sum(row.values()) for row in self.arrears.values())
        # 阶段 14：人口自然演化（用居民消费总盘子 + 月人均收入派生 cbr/cdr）
        from backend.systems.v21.demography import load_params, natural_change
        household_spend = sum(t['amount_cents'] for t in self.transactions
                              if t['kind'] in ('consumer_purchase', 'residential_rent'))
        params = load_params(self.scenario)
        delta, cbr_m, cdr_m, income_cr, cons_ratio = natural_change(self, params, household_spend)
        self.last_demography = {
            'cbr_per_thousand': cbr_m * 12000, 'cdr_per_thousand': cdr_m * 12000,
            'rate_period': 'annual',
            'cbr_monthly_per_thousand': cbr_m * 1000, 'cdr_monthly_per_thousand': cdr_m * 1000,
            'net_monthly': cbr_m - cdr_m, 'delta_people': delta,
            'per_worker_income_cr': income_cr, 'consumption_to_cost_ratio': cons_ratio,
        }
        before_population = sum(self.population.values())
        self.change_population(delta)
        self.last_demography['delta_people'] = sum(self.population.values()) - before_population
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
                paid_wages = sum(t['amount_cents'] for t in self.transactions if t['from'] == i and t['kind'] == 'wage')
                self.wage_history[i].append(round(paid_wages / e[i] * (1-self.pit(i))))
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
        self._active_policies = {k: v for k, v in self._active_policies.items() if v > self.month}
        self.assert_invariants()
        report = self.make_report(opening, e, wages, outputs, profits, industry_taxes, capacities, targets, cap_details, flow)
        self.update_prices_and_wages()
        self.last_report = report
        self._last_policy_actions = []
        self._settling = False
        return report

    def settle_markets(self, opening, wages):
        from backend.systems.v21.industry_settle import settle_markets
        settle_markets(self, opening, wages)

    def update_prices_and_wages(self):
        c = self.scenario['city']
        for i in self.keys:
            requested, available = self.requested[i], self.market_available[i]
            # 库存月数以真实成交销量衡量；满产能力不是市场每月能消化的量。
            # 无成交时仅参考有真实购买预算的请求量，不生成收入或虚构销量。
            sold_units = self.sales[i] / max(1, self.prices[i])
            monthly_demand = sold_units if sold_units > 0 else max(0.0, self.requested[i])
            target_stock = monthly_demand * c.get('target_inventory_months', 1.0)
            actual = self.stock.get(i, 0.0)
            if i == 'GOV':
                change = 0.0
            elif target_stock <= 0:
                change = -c['price_pressure_k'] if actual > 0 else 0.0
            else:
                ratio = (actual - target_stock) / max(target_stock, 1e-9)
                change = -c['price_pressure_k'] * max(-1.0, min(1.0, ratio))
            change = max(-c['price_change_cap'], min(c['price_change_cap'], change))
            floor = self.cr(self.sector(i)['output_price_cr']) * c['price_floor_ratio']
            ceiling = self.cr(self.sector(i)['output_price_cr']) * c['price_ceiling_ratio']
            self.prices[i] = max(1, round(min(ceiling, max(floor, self.prices[i]*(1+c['inflation'])*(1+change)))))
            backlog = self.base_wages[i] * self.cpi / max(1, self.nominal_wages[i]) - 1
            growth = max(c['wage_cap_down'], min(c['wage_cap_up'], c['wage_alpha']*c['inflation']+c['wage_gamma']*backlog))
            self.nominal_wages[i] = max(1, round(self.nominal_wages[i]*(1+growth)))
        self.cpi *= 1+c['inflation']
        self.living_cost_cents = max(1, round(self.cr(c['living_cost_cr'])*self.cpi))

    def assert_invariants(self):
        if sum(self.cash.values()) != self.initial_total_cash:
            raise AssertionError('现金守恒失败')
        if any(type(v) is not int or v < 0 for v in self.cash.values()):
            raise AssertionError('现金不是非负整数分')
        for h, row in self.workers.items():
            values = [self.population[h], self.unemployed_by_group[h], *row.values()]
            if any(type(v) is not int or v < 0 for v in values):
                raise AssertionError('人口、就业或失业人数无效')
            if sum(row.values()) + self.unemployed_by_group[h] > self.population[h]:
                raise AssertionError('群体劳动力超过居民数')
        if sum(self.employment().values()) + sum(self.unemployed_by_group.values()) != self.labor_force:
            raise AssertionError('劳动力不守恒')
        if self.labor_force != math.floor(sum(self.population.values()) * self.scenario['city']['labor_participation']):
            raise AssertionError('劳动力参与率不符')
        if any(not math.isfinite(v) or not 0 <= v < 1+1e-8 for v in self.carries.values()):
            raise AssertionError('人员流动小数余量无效')
        if any(type(v) is not int or v < 0 for row in self.arrears.values() for v in row.values()):
            raise AssertionError('欠薪债权无效')
        if self.wage_arrears_cents != sum(sum(row.values()) for row in self.arrears.values()):
            raise AssertionError('欠薪总额与债权不符')
        if not math.isclose(self.initial_raw + self.cumulative_raw_purchased_units,
                            self.external_raw + sum(self.raw.values()) + self.raw_used, abs_tol=1e-6):
            raise AssertionError('全局原料不守恒')
        if (not math.isfinite(self.government_raw_units)
                or not -1e-8 <= self.government_raw_units <= self.external_raw + 1e-8):
            raise AssertionError('市政原料所有权库存无效')
        if not math.isclose(self.government_raw_units + self.cumulative_government_raw_resale_units,
                            self.cumulative_raw_purchased_units, abs_tol=1e-6):
            raise AssertionError('市政原料购入、转售和剩余库存不守恒')
        for i in self.keys:
            if not math.isclose(self.initial_stock[i] + self.total_produced[i],
                                self.stock[i] + self.consumed[i] + self.external_goods[i], abs_tol=1e-6):
                raise AssertionError(f'行业 {i} 成品不守恒')
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

        from backend.systems.v21.map_economy import assert_map_invariants
        assert_map_invariants(self)

    def make_report(self, opening, e, wages, outputs, profits, taxes, capacities, targets, details, flow):
        ending_employment = self.employment()
        sectors = {i: {'name': self.sector(i)['name'], 'employed': ending_employment[i], 'capacity': capacities[i],
                       'target': targets[i], 'attractiveness': self.scores[i], 'wage_cents': self.current_wages[i],
                       'cash_cents': self.cash[i], 'sales_cents': self.sales[i], 'profit_cents': profits[i] if i != 'GOV' else None,
                       'tax_cents': taxes[i], 'output_units': outputs[i], 'stock_units': self.stock[i],
                       'raw_units': self.raw[i], 'price_cents': self.prices[i], 'capacity_details': details[i],
                       'score_details': self.score(i),
                       'loss_streak_months': self.loss_streak.get(i, 0),
                       'crisis': self.loss_streak.get(i, 0) >= self.max_loss_streak} for i in self.keys}
        fiscal = self.cash['GOV'] - self._fiscal_opening_cash_cents
        total_population = sum(self.population.values())
        household_population = int(round(total_population * self.household_size))
        # 阶段 14 改：现金残差扣 OCEAN 池（守恒包括 OCEAN 池）
        return {'month': self.month, 'cpi': self.cpi, 'population': total_population,
                'labor_force': self.labor_force, 'employment': ending_employment,
                'payroll_employment': dict(e),
                'funded_civil_staff_plan': self.funded_civil_staff_plan, 'unemployed': sum(self.unemployed_by_group.values()),
                'sectors': sectors, 'wages_cents': sum(wages.values()), 'sales_cents': sum(self.sales.values()),
                'private_capital_spending_cents': sum(t['amount_cents'] for t in self.transactions if t['kind'] == 'capital_purchase' and t['from'] != 'GOV'),
                'capital_spending_scope': '标准包消费；不自动增加地图设施或行业产能',
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
                'current_unpaid_wages_cents': self.last_unpaid_shortfall_cents,
                'paid_wages_cents': sum(t['amount_cents'] for t in self.transactions if t['kind'] == 'wage'),
                'wage_arrears_repaid_cents': sum(t['amount_cents'] for t in self.transactions if t['kind'] == 'wage_arrears_repayment'),
                'arrears_by_employer': deepcopy(self.arrears),
                'consumption_equivalent_population': household_population,
                'total_population': total_population,
                'housing_capacity': self.housing_capacity,
                'map_month_report': deepcopy(getattr(self, 'map_month_report', [])),
                'demography': deepcopy(self.last_demography),
                'active_policies': {f'{tgt}|{k}': v for (tgt, k), v in self._active_policies.items()},
                'recent_mayor_actions': list(self._last_policy_actions),
                'ocean_cash': self.ocean_cash,
                'external_raw_units': self.external_raw,
                'government_raw_units': self.government_raw_units,
                'government_raw_resale_cents': self.cumulative_government_raw_resale_cents,
                'government_raw_resale_units': self.cumulative_government_raw_resale_units}

    def repay_arrears(self, employer):
        """先偿旧债；债权留在原居民群体，转业也不丢失。"""
        debts = self.arrears[employer]
        budget = min(self.cash[employer], sum(debts.values()))
        for h, amount in split_integer(budget, debts).items():
            paid = self.pay(employer, h, amount, 'wage_arrears_repayment')
            debts[h] -= paid
            self.pay(h, 'GOV', round(paid * self.pit(employer)), 'income_tax')
        self.wage_arrears_cents = sum(sum(row.values()) for row in self.arrears.values())

    def change_population(self, delta):
        """居民变化后重建合法劳动力分布；退休/死亡先离开失业池再离岗。"""
        delta = max(-sum(self.population.values()), int(delta))
        if delta > 0:
            delta = min(delta, max(0, getattr(self, 'housing_capacity', sum(self.population.values()) + delta) - sum(self.population.values())))
        changes = split_integer(abs(delta), self.population)
        for h, n in changes.items():
            self.population[h] += n if delta >= 0 else -n
            over = max(0, sum(self.workers[h].values()) + self.unemployed_by_group[h] - self.population[h])
            removed = min(over, self.unemployed_by_group[h])
            self.unemployed_by_group[h] -= removed
            over -= removed
            for i, n in split_integer(over, self.workers[h]).items():
                self.workers[h][i] -= n
        self.labor_force = math.floor(sum(self.population.values()) * self.scenario['city']['labor_participation'])
        excess = sum(self.employment().values()) + sum(self.unemployed_by_group.values()) - self.labor_force
        if excess > 0:
            reduction = split_integer(min(excess, sum(self.unemployed_by_group.values())), self.unemployed_by_group)
            for h, n in reduction.items():
                self.unemployed_by_group[h] -= n
            excess -= sum(reduction.values())
            employment = {h: sum(row.values()) for h, row in self.workers.items()}
            for h, count in split_integer(excess, employment).items():
                for i, n in split_integer(count, self.workers[h]).items():
                    self.workers[h][i] -= n
        elif excess < 0:
            room = {h: self.population[h] - sum(self.workers[h].values()) - self.unemployed_by_group[h] for h in self.population}
            for h, n in split_integer(-excess, room).items():
                self.unemployed_by_group[h] += n

    def to_dict(self):
        data = deepcopy(self.__dict__)
        data['carries'] = [[i, kind, v] for (i, kind), v in self.carries.items()]
        data['_active_policies'] = [[i, kind, expiry] for (i, kind), expiry in self._active_policies.items()]
        data['_state_version'] = self.STATE_VERSION
        return data

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict) or data.get('_state_version') != cls.STATE_VERSION:
            raise ValueError('存档版本不兼容')
        data = deepcopy(data)
        try:
            validate_scenario(data['scenario'])
            city = cls(data['scenario'])
            missing = set(city.__dict__) - set(data)
            if missing:
                raise ValueError('存档缺少必需字段：' + ', '.join(sorted(missing)))
            data.pop('_state_version')
            data['carries'] = {(i, kind): v for i, kind, v in data['carries']}
            data['_active_policies'] = {(i, kind): expiry for i, kind, expiry in data['_active_policies']}
            if type(data['month']) is not int or data['month'] < 0:
                raise ValueError('存档月份无效')
            for (i, kind), expiry in data['_active_policies'].items():
                if i not in city.keys or kind not in ('limit_hire', 'limit_layoff') or type(expiry) is not int or expiry <= data['month']:
                    raise ValueError('政策状态无效')
            from backend.systems.v21.industry_state import validate_state
            validate_state(data, city)
            city.__dict__.update(data)
            city.assert_invariants()
            return city
        except (KeyError, TypeError, AssertionError, OverflowError) as error:
            raise ValueError(f'存档状态无效：{error}') from error
