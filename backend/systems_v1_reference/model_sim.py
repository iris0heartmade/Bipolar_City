"""可审计的实验室月度调度：实际收付、经营反馈、危机与恢复。"""
from copy import deepcopy
from dataclasses import asdict
import math

from backend.config import REFERENCE_CITY, LABOR_CONSUMPTION, INPUT_OUTPUT
from backend.core.errors import GameError
from backend.model.economy import cr_to_cents
from backend.model.parameters import EconomyParameters
from backend.systems.model_engine import SimulationResult, build_sectors, build_cohorts, build_resources
from backend.systems.model_ledger import Ledger, split
from backend.systems import model_market as market, model_dynamics as dynamics


class EconomySimulation:
    def __init__(self, months=120, parameters=None, seed=20261013, shocks=None):
        self.months = max(1, int(months))
        self.params = deepcopy(parameters) if parameters else EconomyParameters()
        self.params.validate_all_deep()
        # 跨项一致性校验：人口不能超过住房承载过多、劳动力不能超过总人口。
        # 单独抽出来是因为它依赖 population 和 housing 多个字段,
        # 放在 validate_identity() 里会让单位测试要同时构造两套数据。
        self.params.validate_identity_cross()
        self.seed = seed  # 模型确定性；保留接口，不假装有随机事件。
        self.shocks = deepcopy(shocks or [])
        self._validate_shocks()
        self._shock_defaults = {k: deepcopy(self.params.get(k)) for x in self.shocks for k in x.get('overrides', {})}
        self.scale = self.params.get('population.employed') / REFERENCE_CITY['demographics']['employed']
        self.sectors = build_sectors(self.params)
        self.cohorts = build_cohorts(self.params)
        self.resources = {r.resource_id: r for r in build_resources(self.params)}
        self.resource_targets = {k: r.stock for k, r in self.resources.items()}
        self.public_jobs = self.params.get('population.employed') - sum(s.employees for s in self.sectors)
        self.public_staff = self.public_jobs
        self.housing_units = self.params.get('housing.units')
        self.housing_queue = []
        self.housing_orders = []
        self.rent_cents = cr_to_cents(self.params.get('housing.rent_per_renter_month'))
        self.debt = cr_to_cents(self.params.get('city.start_debt'))
        self.principal_due = round(self.debt / self.params.get('city.amortization_months'))
        self.interest_arrears = 0
        self.public_arrears = 0
        self.obligations = {}
        self.written_off = 0
        self.faith_share = self.params.get('religion.start_faith_ratio')
        self.cpi = self.skill_factor = self.public_wage_ratio = 1.0
        self.trained = 0
        self.month = 0
        self.last_margin = self.params.get('business.entry_expected_margin_min')
        self.history = []
        self.events = []
        self.bankrupt = self.relocated = self.entered = 0
        self.credit = 0.0
        self.interest_rate = self.params.get('city.loan_interest_rate_monthly')
        income_scale = self.params.get('population.start') / REFERENCE_CITY['demographics']['population']
        households_opening = cr_to_cents(self.params.get('population.opening_disposable_base')) * income_scale * self.params.get('feedback.household_cash_multiplier')
        cashes = split(round(households_opening), [r['wage_income'] + r['asset_income'] for r in LABOR_CONSUMPTION['income_bands']])
        opening = {s.account: s.cash_cents for s in self.sectors}
        opening.update({c.account: v for c, v in zip(self.cohorts, cashes)})
        opening.update(government=cr_to_cents(self.params.get('city.start_cash')),
                       church=cr_to_cents(self.params.get('religion.start_church_cash')), landlords=0, ocean=0)
        # A-01 改造（2026-10-15）：'external' → 'ocean'；语义与 0.7.0 完全一致（海平面近似恒定）
        opening['ocean'] = -sum(opening.values())
        self.ledger = Ledger(opening)
        self._seed_disposable()
        self._baseline_budget()
        self._sync_cash()

    def _validate_shocks(self):
        for shock in self.shocks:
            if not isinstance(shock.get('start'), int) or shock['start'] < 1:
                raise GameError('冲击开始月份必须为正整数')
            if 'end' in shock and (not isinstance(shock['end'], int) or shock['end'] < shock['start']):
                raise GameError('冲击结束月份不得早于开始月份')
            candidate = deepcopy(self.params)
            for key, value in shock.get('overrides', {}).items():
                if candidate.get(key) is None:
                    raise GameError(f'冲击参数不存在：{key}')
                candidate.set(key, value)
            candidate.validate_identity()
            candidate.validate_identity_cross()
            candidate.validate_resource_values()

    def _seed_disposable(self):
        total_wage = sum(s.employees * s.wage_per_employee_cents for s in self.sectors) + self._public_wage_due()
        wages = split(total_wage, [c.wage_weight for c in self.cohorts])
        assets = split(cr_to_cents(self.params.get('housing.asset_income_base')) * self.params.get('population.start') // REFERENCE_CITY['demographics']['population'], [c.asset_weight for c in self.cohorts])
        property_tax = split(self._property_tax_due(), [c.property_tax_share for c in self.cohorts])
        relief = split(cr_to_cents(self.params.get('city.welfare_monthly_initial')), [c.relief_share for c in self.cohorts])
        for c, w, a, tax, help_ in zip(self.cohorts, wages, assets, property_tax, relief):
            c.wage_income_cents, c.asset_income_cents = w, a
            c.last_disposable_cents = max(0, w + a - round((w + a) * self.params.get('tax.income_flat_rate')) - tax + help_)

    def _baseline_budget(self):
        # 默认收入分层的消费习惯归一化到输入表119000，后续只用实际历史收入。
        rows = LABOR_CONSUMPTION['income_bands']
        default_donation = EconomyParameters().get('feedback.donation_income_share')
        baseline = 0
        baseline_budgets = []
        rentals = split(cr_to_cents(REFERENCE_CITY['housing']['residential_rent_derived']), [r['households'] for r in rows])
        property_taxes = split(round(cr_to_cents(REFERENCE_CITY['housing']['assessed_value']) * REFERENCE_CITY['fiscal']['property_tax_monthly_rate'] * (1 - REFERENCE_CITY['fiscal']['property_relief_religion_factor'] * REFERENCE_CITY['religion']['opening_faith'])), [r['property_tax_share'] for r in rows])
        relief = split(cr_to_cents(INPUT_OUTPUT['resident_flows']['inflows']['relief']), [r['relief_share'] for r in rows])
        for row, rent, tax, help_ in zip(rows, rentals, property_taxes, relief):
            gross = cr_to_cents(row['wage_income'] + row['asset_income'])
            disposable = gross - round(gross * REFERENCE_CITY['fiscal']['income_tax_rate']) - tax + help_
            budget = max(0, disposable - rent - round(disposable * default_donation)) * row['spend_ratio']
            baseline += budget
            baseline_budgets.append(budget)
        self.consumption_normalisation = cr_to_cents(INPUT_OUTPUT['firm_revenue_sources']['household_consumption']) / max(1, baseline)
        self._calibrate_baskets(baseline_budgets)
        self.baseline_basket = {k: sum(r['population'] * r['basket'].get(k, 0) for r in rows) / sum(r['population'] for r in rows)
                                for k in rows[0]['basket']}

    def _calibrate_baskets(self, budgets):
        """初始化有限轮矩阵归一化：保留各层口味，同时还原行业基准。"""
        rows = LABOR_CONSUMPTION['income_bands']
        mapping = self.params.get('feedback.basket_sector_map')
        revenues = [s.base['revenue'] for s in self.sectors]
        matrix = [[r['basket'].get(mapping[s.sector_id], 0) * s.base['revenue'] for s in self.sectors] for r in rows]
        original = deepcopy(matrix)
        targets = [sum(budgets) * value / sum(revenues) for value in revenues]
        for _ in range(self.params.get('feedback.calibration.basket_iterations')):
            for j, target in enumerate(targets):
                denominator = sum(row[j] for row in matrix)
                for row in matrix:
                    row[j] *= target / max(1e-12, denominator)
            for i, target in enumerate(budgets):
                denominator = sum(matrix[i])
                matrix[i] = [value * target / max(1e-12, denominator) for value in matrix[i]]
        self.basket_calibration = [[matrix[i][j] / max(1e-12, original[i][j]) for j in range(len(self.sectors))] for i in range(len(rows))]

    def _public_wage_due(self):
        return round(cr_to_cents(REFERENCE_CITY['public_sector']['wage_bill']) * self.public_staff / REFERENCE_CITY['public_sector']['employees'])

    def _property_tax_due(self):
        return round(cr_to_cents(self.params.get('housing.assessed_value')) * self.params.get('tax.property_monthly_rate')
                     * (1 - self.params.get('religion.property_exemption_strength') * self.faith_share)
                     * self.params.get('tax.collection_efficiency'))

    def _sync_cash(self):
        for owner in self.sectors + self.cohorts:
            owner.cash_cents = self.ledger.cash(owner.account)

    def distribute(self, source, amount, category, kind):
        if kind == 'wage':
            rows = LABOR_CONSUMPTION['income_bands']
            weights = [c.wage_weight * c.employed / max(1, row['employed']) for c, row in zip(self.cohorts, rows)]
        elif kind == 'relief':
            weights = [c.relief_share * (1 + c.unemployed) / (1 + row['unemployed']) for c, row in zip(self.cohorts, LABOR_CONSUMPTION['income_bands'])]
        else:
            weights = [c.asset_weight if c.population else 0 for c in self.cohorts]
        # 先整体封顶，避免分配顺序决定谁拿得到钱。
        # A-02 改造（2026-10-15）：免检规则收敛到 self.ledger.available(source, amount)；
        # 仅 ocean 免检，邻城（is_peer）必须受 available_cents 上限。
        available = self.ledger.available(source, amount)
        requested = split(max(0, int(amount)), weights)
        allocations = split(max(0, int(available)), weights)
        total = 0
        for c, value, due in zip(self.cohorts, allocations, requested):
            paid = self.ledger.pay(source, c.account, value, category)
            if due > paid:
                name = f'{source}->{c.account}:{category}'
                self.ledger.shortfall[name] = self.ledger.shortfall.get(name, 0) + due - paid
            if kind == 'wage':
                c.wage_income_cents += paid
            elif category in ('dividend', 'property_income'):
                c.asset_income_cents += paid
            elif category in ('relief', 'church_relief'):
                c.relief_paid += paid
            total += paid
        return total

    def run(self):
        for _ in range(self.months):
            self.step()
        return self.history

    def step(self):
        # 整月失败回滚钱包、资源、冲击、月份；历史仅在成功后追加。
        before = deepcopy({k: v for k, v in self.__dict__.items() if k != 'history'})
        try:
            result = self._step()
            self.history.append(result)
            return result
        except Exception:
            history = self.history
            self.__dict__.clear()
            self.__dict__.update(before)
            self.history = history
            raise

    def _apply_shocks(self):
        for key, value in self._shock_defaults.items():
            self.params.set(key, value, validate=False)
        for shock in self.shocks:
            if shock['start'] <= self.month <= shock.get('end', self.month):
                for key, value in shock.get('overrides', {}).items():
                    self.params.set(key, value, validate=False)
            if self.month == shock['start']:
                self.events.append('条件冲击开始：' + shock.get('name', '自定义'))
            if self.month == shock.get('end', -1) + 1:
                self.events.append('条件冲击结束：' + shock.get('name', '自定义'))
        self.params.validate_all()
        self.params.validate_resource_values()
        for item in self.params.get('resources.items'):
            r = self.resources[item['id']]
            r.base_production, r.base_use = item['produced'], item['used']
            r.base_import, r.base_export = item['imported'], item['exported']
        # 冲击生效后立刻校验跨项一致性，避免迁徙在错的条件下跑 12 月才发现。
        self.params.validate_identity_cross()

    def _step(self):
        self.month += 1
        self.events = []
        self.written_off = 0
        self._apply_shocks()
        self.ledger.begin_month(self.month)
        opening = self.ledger.local_total
        self.opening_cash = self.ledger.balances
        for s in self.sectors:
            s.wage_per_employee_cents = max(s.wage_per_employee_cents, cr_to_cents(self.params.get('labour.min_wage')))
        dynamics.match_labour(self)
        self.period_employed = sum(c.employed for c in self.cohorts)
        self.period_population = sum(c.population for c in self.cohorts)
        for c in self.cohorts:
            c.wage_income_cents = c.asset_income_cents = c.relief_paid = c.taxes_paid = c.donation_paid = 0
        self._production_factors()
        market.household_budgets(self)
        # 预算已冻结；公共工资优先支付，不能让采购侵占工资钱包。
        wage_due = self._public_wage_due()
        self.public_wage_paid = self.distribute('government', wage_due + self.public_arrears, 'public_wage', 'wage')
        self.public_arrears = max(0, wage_due + self.public_arrears - self.public_wage_paid)
        self.public_wage_ratio = min(1, self.public_wage_paid / wage_due) if wage_due else 1
        market.execute_market(self)
        self._firms_settlement()
        self._household_public_settlement()
        self._debt_settlement()
        # 本期统计冻结，所有下一期目标另行更新，现金变化仍统一对账。
        self.period_stats = self._income_stats()
        capacity = max(1, sum(s.capacity_units for s in self.sectors))
        self.period_stats.update(real_output=round(sum(s.revenue_cents / s.price for s in self.sectors)),
                                 utilisation=sum(s.utilisation * s.capacity_units for s in self.sectors) / capacity,
                                 readiness=sum(s.readiness * s.capacity_units for s in self.sectors) / capacity, cpi=self.cpi)
        self.period_prices = {s.sector_id: s.price for s in self.sectors}
        dynamics.firms(self)
        dynamics.wages_prices(self)
        self._religion()
        dynamics.education_housing(self)
        # K 值必须在 dynamics.population() 之前算出：因为迁徙 target 直接读 sim.city_capacity。
        # K 值依赖 resources/stock、planned_jobs、magic_risk、skill_factor、worktime_factor、school_coverage，
        # 这些在 firms()/wages_prices()/_religion() 已就位。
        self.city_capacity = dynamics.compute_capacity(self)
        migration = dynamics.population(self)
        self._sync_cash()
        self._assert_invariants(opening)
        return self._report(opening, migration)

    def _production_factors(self):
        p = self.params
        mages = self.public_staff * next(x['staff'] for x in REFERENCE_CITY['public_sector']['posts'] if x['id'] == 'mages') / REFERENCE_CITY['public_sector']['employees'] * self.public_wage_ratio
        self.magic_risk = p.get('security.base_threat_intensity') / (1 + p.get('security.default_kappa') * p.get('security.mage_coverage_per_mage') * mages)
        self.safety_factor = max(p.get('capacity.safety_factor_floor'), 1 - self.magic_risk * p.get('security.default_downtime_share'))
        # 基准状态归一化，默认安全损失不重复扣基准80%利用率。
        base_mages = next(x['staff'] for x in REFERENCE_CITY['public_sector']['posts'] if x['id'] == 'mages')
        defaults = EconomyParameters()
        reference = 1 - defaults.get('security.base_threat_intensity') / (1 + base_mages * defaults.get('security.default_kappa') * defaults.get('security.mage_coverage_per_mage')) * defaults.get('security.default_downtime_share')
        self.safety_factor = min(1.0, self.safety_factor / reference)
        self.worktime_factor = (1 - p.get('religion.worktime_loss_at_full_faith') * self.faith_share) / max(0.01, 1 - p.get('religion.worktime_loss_at_full_faith') * p.get('capacity.reference_faith_share'))
        teacher = next(x for x in REFERENCE_CITY['public_sector']['posts'] if x['id'] == 'teachers')
        seats = teacher['staff'] * self.public_staff / REFERENCE_CITY['public_sector']['employees'] * p.get('education.seats_per_teacher')
        self.school_coverage = min(1.0, seats / max(1, p.get('education.school_age_population')))

    def _firms_settlement(self):
        p = self.params
        for s in self.sectors:
            s.wage_bill_cents = s.employees * s.wage_per_employee_cents
            s.fees_cents = split(cr_to_cents(p.get('tax.license_fee_total')), [x.base['fees'] * x.firm_count / max(1, x.base['firms']) for x in self.sectors])[self.sectors.index(s)] if any(x.firm_count for x in self.sectors) else 0
            for category, due in [('wage', s.wage_bill_cents), ('commercial_rent', s.commercial_rent_cents if s.firm_count else 0), ('fees', s.fees_cents)]:
                key = (s.sector_id, category)
                owed = due + self.obligations.get(key, 0)
                if category == 'wage':
                    paid = self.distribute(s.account, owed, category, 'wage')
                    s.wage_paid = paid
                else:
                    paid = self.ledger.pay(s.account, 'landlords' if category == 'commercial_rent' else 'government', owed, category)
                    if category == 'commercial_rent':
                        s.rent_paid = paid
                    else:
                        s.fees_paid = paid
                self.obligations[key] = owed - paid
            s.arrears = sum(self.obligations.get((s.sector_id, k), 0) for k in ('wage', 'commercial_rent', 'fees'))
            # 应计工资/租金费用保留，欠薪不会让利润虚增。
            s.profit_before_tax_cents = s.revenue_cents - s.input_cost_cents - s.wage_bill_cents - s.commercial_rent_cents - s.depreciation_cents - s.fees_cents
            tax_due = round(max(0, s.profit_before_tax_cents) * p.get('business.profit_tax_rate') * p.get('tax.collection_efficiency'))
            s.profit_tax_cents = self.ledger.pay(s.account, 'government', tax_due, 'profit_tax')
            s.profit_after_tax_cents = s.profit_before_tax_cents - tax_due
        total_dividend = round(sum(max(0, s.profit_after_tax_cents) for s in self.sectors) * p.get('business.default_payout_ratio'))
        for s, amount in zip(self.sectors, split(total_dividend, [max(0, s.profit_after_tax_cents) for s in self.sectors])):
            # 欠付未清时不得向股东派息。
            s.dividend_cents = self.distribute(s.account, amount if not s.arrears else 0, 'dividend', 'asset')
        self.last_margin = sum(s.profit_before_tax_cents for s in self.sectors) / max(1, sum(s.revenue_cents for s in self.sectors))

    def _household_public_settlement(self):
        p = self.params
        self.distribute('landlords', self.ledger.cash('landlords'), 'property_income', 'asset')
        property_taxes = split(self._property_tax_due(), [c.property_tax_share for c in self.cohorts])
        gross_weights = [c.gross_income_cents for c in self.cohorts]
        income_taxes = split(round(sum(gross_weights) * p.get('tax.income_flat_rate') * p.get('tax.collection_efficiency')), gross_weights)
        donations = split(round(sum(gross_weights) * p.get('feedback.donation_income_share') * self.faith_share / max(0.01, p.get('capacity.reference_faith_share'))), gross_weights)
        for c, tax, income_tax, donation in zip(self.cohorts, property_taxes, income_taxes, donations):
            c.taxes_paid = self.ledger.pay(c.account, 'government', income_tax, 'income_tax')
            c.taxes_paid += self.ledger.pay(c.account, 'government', tax, 'property_tax')
            c.donation_paid = self.ledger.pay(c.account, 'church', donation, 'donation')
        # A-01 改造（2026-10-15）：中央转移付款方 'external' → 'ocean'
        self.ledger.pay('ocean', 'government', cr_to_cents(p.get('city.central_transfer_monthly')), 'grant')
        unemployed = sum(c.unemployed for c in self.cohorts)
        baseline_unemployed = round(REFERENCE_CITY['demographics']['unemployed'] * p.get('population.start') / REFERENCE_CITY['demographics']['population'])
        relief_due = cr_to_cents(p.get('city.welfare_monthly_initial')) * max(1, unemployed / max(1, baseline_unemployed))
        self.government_relief_paid = self.distribute('government', round(relief_due), 'relief', 'relief')
        tier = next(x for x in p.get('religion.policy_tiers') if x['tier'] == p.get('religion.policy'))
        self.ledger.pay('government', 'church', cr_to_cents(tier['subsidy']), 'church_subsidy')
        # 教会救济是用户新增玩法：仅新增失业压力触发，优先于捐市政。
        self.church_relief_due = cr_to_cents(p.get('feedback.church_relief_per_unemployed')) * max(0, unemployed - baseline_unemployed)
        self.church_relief_paid = self.distribute('church', self.church_relief_due, 'church_relief', 'relief')
        self.church_relief_shortfall = self.church_relief_due - self.church_relief_paid
        self.ledger.pay('church', 'government', round(cr_to_cents(p.get('religion.grant_ceiling_at_full_faith')) * self.faith_share), 'church_grant')
        for c in self.cohorts:
            c.disposable_cents = max(0, c.gross_income_cents - c.taxes_paid + c.relief_paid)
            c.last_disposable_cents = c.disposable_cents
        if self.church_relief_shortfall:
            self.events.append(f'教会救济不足，缺口{self.church_relief_shortfall / 100:.2f} CR')

    def _debt_settlement(self):
        p = self.params
        self.interest_rate = max(0, p.get('city.loan_interest_rate_monthly') + p.get('city.credit_interest_spread') * max(0, -self.credit))
        due = round(self.debt * self.interest_rate) + self.interest_arrears
        # A-01 改造（2026-10-15）：利息/本金收款方 'external' → 'ocean'
        self.interest_paid = self.ledger.pay('government', 'ocean', due, 'interest')
        self.interest_arrears = due - self.interest_paid
        principal = min(self.debt, self.principal_due) if self.month > p.get('city.loan_initial_grace_months') else 0
        self.principal_paid = self.ledger.pay('government', 'ocean', principal, 'principal')
        self.debt -= self.principal_paid
        if self.interest_arrears or self.principal_paid < principal:
            self.events.append('市政债务偿付不足：利息欠付或本金逾期')

    def _income_stats(self):
        gross = sum(c.gross_income_cents for c in self.cohorts)
        disposable = sum(c.disposable_cents for c in self.cohorts)
        poverty = sum(c.population for c in self.cohorts if c.disposable_cents / max(1, c.population) < cr_to_cents(self.params.get('feedback.poverty_line_per_person')) * self.cpi)
        return {'gross': gross, 'disposable': disposable, 'poverty': poverty,
                'wage': sum(s.wage_paid for s in self.sectors) + self.public_wage_paid}

    def _religion(self):
        tier = next(x for x in self.params.get('religion.policy_tiers') if x['tier'] == self.params.get('religion.policy'))
        self.faith_share = max(0, min(1, self.faith_share + tier['a'] * (1 - self.faith_share) - tier['b'] * self.faith_share))

    def _assert_invariants(self, opening):
        if self.ledger.local_total - opening + self.ledger.external_net != 0:
            raise GameError('现金守恒断言失败')
        if any(a.cash_cents < 0 for a in self.ledger.book.local_leaves()):
            raise GameError('本地钱包不允许负余额')
        for c in self.cohorts:
            if c.employed + c.unemployed + c.non_labour != c.population or min(c.employed, c.unemployed, c.non_labour) < 0:
                raise GameError('人口身份断言失败')
        if sum(c.employed for c in self.cohorts) != self.public_staff + sum(s.employees for s in self.sectors):
            raise GameError('就业岗位断言失败')
        if any(r.stock < 0 for r in self.resources.values()):
            raise GameError('资源库存断言失败')

    def _report(self, opening, migration):
        totals = self.ledger.by_category()
        incoming = sum(v for a, b, v, _ in self.ledger.transactions if b == 'government')
        outgoing = sum(v for a, b, v, k in self.ledger.transactions if a == 'government' and k != 'principal')
        balance = incoming - outgoing
        self.credit = max(-1, min(1, balance / max(1, outgoing) - self.debt / max(1, self.debt + self.ledger.cash('government')) - min(1, self.interest_arrears / max(1, outgoing))))
        population = sum(c.population for c in self.cohorts)
        employed = sum(c.employed for c in self.cohorts)
        unemployed = sum(c.unemployed for c in self.cohorts)
        sales = sum(s.revenue_cents for s in self.sectors)
        profit = sum(s.profit_before_tax_cents for s in self.sectors)
        imports = totals.get('import', 0) + totals.get('external_purchase', 0)
        exports = totals.get('export', 0)
        household_delta = self.ledger.balances['households'] - self.opening_cash['households']
        gva = sales - sum(s.input_cost_cents for s in self.sectors) - totals.get('commercial_rent', 0)
        gva += totals.get('residential_rent', 0) + totals.get('commercial_rent', 0) - self.owner_repair_paid + self.public_wage_paid
        self.metrics = {
            'population': population, 'employed': employed,
            'employment_rate': employed / max(1, employed + unemployed),
            'unemployment_rate': unemployed / max(1, employed + unemployed), 'migration_net': migration,
            'vacant_homes': max(0, self.housing_units - sum(c.households for c in self.cohorts)),
            'avg_wage_employed': self.period_stats['wage'] / max(1, self.period_employed),
            'avg_income_gross_per_capita': self.period_stats['gross'] / max(1, self.period_population),
            'avg_income_disposable_per_capita': self.period_stats['disposable'] / max(1, self.period_population),
            'household_cash': self.ledger.balances['households'],
            'household_savings_rate': household_delta / max(1, self.period_stats['disposable']),
            'poverty_rate': self.period_stats['poverty'] / max(1, self.period_population),
            'firm_sales': sales, 'firm_pre_tax_profit': profit,
            'firm_after_tax_profit': sum(s.profit_after_tax_cents for s in self.sectors),
            'firm_profit_tax': totals.get('profit_tax', 0), 'license_fee': totals.get('fees', 0),
            'firm_profit_margin': profit / max(1, sales), 'firm_cash': self.ledger.balances['firms'],
            'private_firm_count': sum(s.firm_count for s in self.sectors),
            'capacity_utilisation': self.period_stats['utilisation'],
            'capacity_readiness': self.period_stats['readiness'],
            'firm_cash_runway_months': min((s.cash_runway_months for s in self.sectors if s.firm_count), default=0),
            'municipal_revenue': incoming, 'municipal_expense': outgoing, 'fiscal_balance': balance,
            'municipal_cash': self.ledger.cash('government'), 'municipal_debt': self.debt,
            'credit_score': self.credit, 'loan_interest_rate': self.interest_rate,
            'fiscal_runway_months': min(999, self.ledger.cash('government') / abs(balance)) if balance < 0 else 999,
            'total_exports': exports, 'total_imports': imports, 'export_import_ratio': exports / max(1, imports),
            'net_external_flow': -self.ledger.external_net, 'trade_balance_warning': exports / max(1, imports),
            'strategic_stock_total': sum(round(r.stock * r.unit_price_cents) for r in self.resources.values()),
            'gva': gva, 'faith_share': self.faith_share, 'church_cash': self.ledger.cash('church'),
            'public_staff': self.public_staff, 'school_coverage': self.school_coverage,
            'effective_magic_risk': self.magic_risk, 'accounting_residual_cents': self.ledger.local_total - opening + self.ledger.external_net,
            'total_city_cash': self.ledger.local_total, 'unpaid_cents': sum(self.ledger.shortfall.values()),
            'church_relief_paid': self.church_relief_paid, 'church_relief_shortfall': self.church_relief_shortfall,
            'government_relief_paid': self.government_relief_paid, 'firms_bankrupt': self.bankrupt,
            'firms_relocated': self.relocated, 'firms_entered': self.entered,
            'wage_arrears': sum(self.obligations.values()) + self.public_arrears,
            'debt_principal_paid': self.principal_paid, 'debt_interest_arrears': self.interest_arrears,
            'capital_flight': totals.get('capital_flight', 0), 'trained_workers': self.trained,
            'housing_units': self.housing_units, 'cpi': self.period_stats['cpi'],
            'real_output': self.period_stats['real_output'],
            'written_off_obligations': self.written_off,
            'city_capacity': self.city_capacity['value'],
            'city_capacity_margin': self.city_capacity['value'] - population,
            'city_capacity_multiplier': self.city_capacity['multiplier'],
        }
        # K 值瓶颈名（"住房承载" / "岗位承载" / "资源承载"）通过事件流输出，
        # 不进 metrics dict——避免污染数字指标健康度判定。
        bottleneck_name = next(
            (comp['name'] for comp in self.city_capacity['components'].values()
             if comp['is_bottleneck']), '住房/岗位/资源')
        self.events.append(f'本月K值瓶颈：{bottleneck_name}'
                           f'={self.city_capacity["bottleneck"]:.0f}，'
                           f'乘子={self.city_capacity["multiplier"]:.3f}')
        for key, value in self.metrics.items():
            if not math.isfinite(value):
                raise GameError(f'指标非有限值：{key}')
        self.events += [f'{name}本月支付缺口{gap / 100:.2f} CR' for name, gap in self.ledger.shortfall.items()]
        return SimulationResult(self.month, dict(self.metrics),
            [dict(id=s.sector_id, name=s.name, employees=s.employees, firm_count=s.firm_count,
                  revenue=s.revenue_cents, profit=s.profit_before_tax_cents, cash=s.cash_cents, utilisation=s.utilisation, price=self.period_prices[s.sector_id], next_price=s.price, arrears=s.arrears) for s in self.sectors],
            [dict(id=c.band_id, name=c.name, population=c.population, employed=c.employed, unemployed=c.unemployed,
                  non_labour=c.non_labour, households=c.households, income=c.gross_income_cents, cash=c.cash_cents) for c in self.cohorts],
            list(self.events), [tx.to_dict() for tx in self.ledger.settlement.journal], [asdict(r) for r in self.resources.values()])

    @property
    def latest(self):
        if not self.history:
            raise GameError('还没有跑过月结')
        return self.history[-1]
