"""月度演化引擎：设计稿 §10 的十五步结算顺序。

每个函数对应设计稿的一个或多个步骤，并在 docstring 里标出编号。
金额一律整数分（1 CR = 100 分），比率一律 ``Fraction``，禁止二进制浮点累计现金。

守恒纪律
--------
每一笔现金变动都写明付款方与收款方。本模块不直接改余额，
而是维护五本账（企业、家庭、市政、教会、外部）的余额，
每一步都用「谁付给谁」的方式记账，月末断言残差为 0。
"""
from copy import deepcopy
from fractions import Fraction

from backend.core.errors import GameError
from backend.model.economy import cr_to_cents
from backend.model.parameters import EconomyParameters
from backend.systems.model_engine import (CohortState, ResourceState, SectorState,
                                          SimulationResult, build_cohorts,
                                          build_resources, build_sectors)


class Ledger:
    """五本账。任何钱都必须从一本挪到另一本，不允许凭空出现或消失。"""

    ACCOUNTS = ("firms", "households", "government", "church", "external")
    #: 镜像账户：恒为负，代表本地现金的对外镜像，不受余额限制
    EXTERNAL = "external"

    def __init__(self, opening: dict[str, int] = None):
        self.balances = dict.fromkeys(self.ACCOUNTS, 0)
        if opening:
            self.balances.update(opening)
        self.opening_external = self.balances["external"]
        self.transactions: list[tuple[str, str, int, str]] = []
        #: 本月被截断的付款：账目上必须有名字，不能悄悄消失
        self.shortfall: dict[str, int] = {}

    def pay(self, from_: str, to: str, amount: int, category: str) -> int:
        """记一笔真实收付。

        付款方付不起时**按实际能力截断**，绝不记成负数、也绝不凭空垫付：
        记一笔收不回的账，等于假装钱到过手，下个月就会重复计算这笔收入，
        城市会越跑越富。截断的金额记进 ``shortfall``，由调用方作为事件暴露出来。

        外部账户是**镜像**而不是钱包：它恒为负（等于全城现金），
        所以它付款时不受余额限制，否则第一笔对外付款就会误判成资金缺口。

        返回实际支付金额，与请求金额不同即说明发生了资金缺口。
        """
        if from_ not in self.balances or to not in self.balances:
            raise GameError(f"账户不存在：{from_} 或 {to}")
        if amount <= 0:
            return 0
        available = amount if from_ == self.EXTERNAL else max(0, self.balances[from_])
        paid = min(amount, available)
        self.balances[from_] -= paid
        self.balances[to] += paid
        self.transactions.append((from_, to, paid, category))
        if paid < amount:
            key = f"{from_}->{to}:{category}"
            self.shortfall[key] = self.shortfall.get(key, 0) + (amount - paid)
        return paid

    def by_category(self) -> dict[str, int]:
        totals: dict[str, int] = {}
        for _f, _t, amount, category in self.transactions:
            totals[category] = totals.get(category, 0) + amount
        return totals

    @property
    def external_net(self) -> int:
        """外部账户本月的净变化：本地现金的增减完全由它解释。

        注意是**变化量**而不是余额——外部账户期初就是负的全城现金，
        拿余额去对账会凭空差出一个等于期初总额的数。
        """
        return self.balances["external"] - self.opening_external

    def begin_month(self) -> None:
        """把期初基准推到本月初。

        上月收付已经落在余额里，本月的变化量必须以本月初余额为起点，
        否则每月的「净流量」都会把累计余额算进去，残差逐月放大。
        """
        self.opening_external = self.balances["external"]
        self.transactions.clear()
        self.shortfall.clear()

    @property
    def local_total(self) -> int:
        return sum(v for k, v in self.balances.items() if k != "external")


class EconomySimulation:
    """一座城市的经济演化器。

    用法::

        sim = EconomySimulation(months=120)
        history = sim.run()
        print(sim.latest.get("fiscal_balance"))
    """

    def __init__(self, months: int = 120, parameters: EconomyParameters = None,
                 seed: int = 20261013):
        self.months = max(1, int(months))
        self.params = parameters or EconomyParameters()
        self.params.validate_all_deep()
        self.seed = seed

        pop = self.params.section("population")
        city = self.params.section("city")

        # 期初账本：外部账户是镜像，本地四本账从各自期初资金开始
        self.sectors = build_sectors(self.params)
        self.cohorts = build_cohorts(self.params,
                                      public_wage_cents=self._public_wage_cents())
        self.resources = {r.resource_id: r for r in build_resources(self.params)}

        firm_cash = sum(s.cash_cents for s in self.sectors)
        household_cash = cr_to_cents(pop["households"]) * 1200
        church_cash = cr_to_cents(self.params.get("religion.start_church_cash", 20000))
        government_cash = cr_to_cents(city["start_cash"])
        local_opening = firm_cash + household_cash + government_cash + church_cash
        # 外部账户是镜像：全城的钱最初都来自外界，所以期初它是负的。
        # 这样「本地现金变化 == 外部账户变化」在第一月就成立，残差恒为 0。
        # 把它设成 0 会让本地凭空多出 103200000 分，残差永远等于期初总额。
        self.ledger = Ledger({
            "firms": firm_cash,
            "households": household_cash,
            "government": government_cash,
            "church": church_cash,
            "external": -local_opening,
        })
        self.opening_total = local_opening

        # 期初家庭存款要同步到各收入层。账本里有一整本家庭账，
        # 但如果只把钱记在账本上、不分摊到各层，首月算消费预算时
        # 每个群体的 cash 都是 0，「花得起多少」被算成 0，
        # 于是城市第一个月的居民消费预算直接归零。
        opening_pop = sum(c.population for c in self.cohorts) or 1
        for cohort in self.cohorts:
            cohort.cash_cents = int(Fraction(household_cash, opening_pop)
                                    * cohort.population)

        # 动态状态
        self.public_staff = max(1, round(pop["employed"] * 0.08))
        self.faith_share = Fraction(str(self.params.get("religion.start_faith_ratio", 0.55)))
        self.cpi = 1.0
        self.month = 0
        self.history: list[SimulationResult] = []
        self.events: list[str] = []
        self.policy_streak = 0
        # 产能「可以做到多少」与「真的卖出去多少」是两回事：
        # 前者是六个因子的乘积，后者才是玩家关心的产能利用率（§4.2 的 80%）。
        self._capacity_utilisation = Fraction(1)
        self._sell_through = Fraction(1)

    # --- 期初辅助 ------------------------------------------------------

    def _public_wage_cents(self) -> int:
        from backend.config import REFERENCE_CITY
        return cr_to_cents(REFERENCE_CITY["public_sector"]["wage_bill"])

    # --- 主循环 --------------------------------------------------------

    def run(self) -> list[SimulationResult]:
        """跑满指定月数，返回逐月结果。"""
        for _ in range(self.months):
            self.step()
        return self.history

    def step(self) -> SimulationResult:
        """推进一个月，严格按设计稿 §10 的十五步。"""
        self.month += 1
        month = self.month
        events: list[str] = []
        ledger = self.ledger
        params = self.params

        # 步骤 01：把账本期初基准推到本月初。
        # 上月的收付已经落在余额里，外部账户的「变化量」必须以本月初为起点重算；
        # 否则 external_net 会把期初余额一并算进来，让残差从第二个月起逐月累积。
        ledger.begin_month()
        opening_local = ledger.local_total
        ledger.transactions.clear()

        # 步骤 02：道路可达与供水供电（MVP 简化为 1，接口留在这里）
        service_factor = Fraction(1)

        # 步骤 03：企业产能与岗位需求
        self._compute_capacity(service_factor)

        # 步骤 04—07：预算、订单分配
        orders = self._allocate_orders()

        # 步骤 08—12：真实收付
        self._execute_payments(orders, ledger, events)

        # 步骤 13—14：价格、工资、信仰、教育、人口
        self._update_prices_and_wages()
        self._update_religion()
        self._update_labour_market()
        migration = self._update_population()

        # 步骤 13b：把本月实际到手的钱结转为下月消费预算的依据。
        # 这一步不做，下个月就会一直拿第一月的旧预算来花，
        # 于是消费与真实收入越走越远，家庭逐月失血。
        self._carry_over_disposable()

        # 步骤 15：月报与会计断言
        result = self._compile_report(ledger, opening_local, orders, migration, events)
        self.history.append(result)
        return result

    # --- 03 产能 -------------------------------------------------------

    def _compute_capacity(self, service_factor: Fraction) -> None:
        """Qcap = K × F_labor × F_skill × F_resource × F_service × F_safety × F_worktime。"""
        capacity_cfg = self.params.section("capacity")
        edu = self.params.section("education")
        security = self.params.section("security")

        labour_force = sum(c.employed + c.unemployed for c in self.cohorts)
        available = max(1, int(labour_force * 0.92))
        required = sum(s.employees for s in self.sectors)

        f_labour = _pow_ratio(available, required, capacity_cfg["labour_exponent"] * 100)

        # F_skill：技能由教育产出，短期内变化缓慢
        skilled = sum(c.population for c in self.cohorts if c.skill_level != "basic")
        total_pop = max(1, sum(c.population for c in self.cohorts))
        f_skill = _clamp(_pow_ratio(skilled, total_pop // 2, capacity_cfg["skill_exponent"] * 100),
                         Fraction(str(capacity_cfg["skill_factor_floor"])), Fraction(1))

        f_resource = self._plan_resource_demand()

        # F_safety：魔法师驻地压制魔物袭击
        threat = Fraction(str(security["base_threat_intensity"]))
        mages = max(1, self.public_staff // 10)
        coverage = Fraction(str(security["mage_coverage_per_mage"])) * mages
        kappa = Fraction(str(security["default_kappa"]))
        expected_attacks = threat / (1 + kappa * coverage)
        f_safety = _clamp(1 - expected_attacks * Fraction(str(security["default_downtime_share"])),
                          capacity_cfg["safety_factor_floor"], 1.0)

        f_worktime = self._worktime_factor()
        self._factors = {
            "labour": f_labour, "skill": f_skill, "resource": f_resource,
            "service": Fraction(service_factor), "safety": f_safety, "worktime": f_worktime,
        }
        self._capacity_utilisation = (f_labour * f_skill * f_resource
                                      * Fraction(service_factor) * f_safety * f_worktime)
        _ = edu  # 教育产出在 _update_labour_market 里推进
        _ = capacity_cfg

    def _plan_resource_demand(self) -> Fraction:
        """按当月产能规划战略品用量，返回 F_resource。

        两件事在这里一起做：算出各行业要用多少，并写回 ``resource.used``，
        供月底结算库存。产量与进口则按参数维持，进口随缺口自动补足，
        这样煤断供时企业停产但库存不会变成负数（设计稿 TEST-19）。
        """
        deps = self.params.section("resources").get("sector_dependency", {})
        total_capacity = max(1, sum(s.capacity_units for s in self.sectors))
        utilisation = self._capacity_utilisation if hasattr(self, "_capacity_utilisation") else Fraction(1)

        worst = Fraction(1)
        for sector in self.sectors:
            share = Fraction(sector.capacity_units, total_capacity) * utilisation
            for resource_id, weight in deps.get(sector.sector_id, {}).items():
                resource = self.resources.get(resource_id)
                if resource is None:
                    continue
                planned = resource.base_use * float(share) * float(weight)
                resource.used += planned
                # 产量按参数的月度产出维持；缺口靠进口补
                resource.produced = resource.base_production

        # 库存不足的部分由 _trade_resources 用进口补足。
        # 这里只回答「现有库存够不够开工」，所以先扣掉本期产量再看。
        for resource in self.resources.values():
            if resource.used > 0:
                supply = resource.stock + resource.produced
                worst = min(worst, Fraction(str(min(1.0, supply / resource.used))))
        return worst

    def _worktime_factor(self) -> Fraction:
        """F_worktime = (1 − 0.04f) / (1 − 0.04×0.55)。"""
        religion = self.params.section("religion")
        loss = Fraction(str(religion["worktime_loss_at_full_faith"]))
        ref = Fraction(str(religion["start_faith_ratio"]))
        denominator = 1 - loss * ref
        return (1 - loss * self.faith_share) / denominator if denominator else Fraction(1)

    # --- 04—07 订单 ----------------------------------------------------

    def _allocate_orders(self) -> dict:
        """分配本月全部订单。

        核心原则：**企业收入必须等于它的产出**。
        企业要发工资、付进口、付租金、缴税、给股东分红，这些支出由营业收入覆盖。
        如果只把「居民消费预算」当收入，企业必然入不敷出，
        账户会被扣成负数——那不是经济，是记账漏了收入。

        收入来源共五项（设计稿 §3.1 的八来源在本模型中合并到这五本账）：
        居民消费、市政采购与资本支出、业主维修、出口、游客消费。
        企业产出超出本地需求的部分，缺额计入外部采购（§4.4 weight_outside）。
        """
        capacity_cents = int(sum(s.capacity_units for s in self.sectors)
                             * self._capacity_utilisation)
        total_capacity = max(1, sum(s.capacity_units for s in self.sectors))

        household_budget = self._household_budget()
        public_demand = self._public_demand()
        owner_repair = self._owner_repair()

        # §3.1 把 340000 CR 营业额拆成**八个付款方**，这里逐一对应：
        #   居民消费 / 业主维修 / 市政采购 / 市政资本建设 → 最终需求
        #   企业间中间品采购 / 私人资本设备            → 生产环节内部需求
        #   出口 / 旅游                               → 城外需求
        # 漏掉「中间品」与「私人资本设备」这两笔内部需求时，
        # 企业凭空少收 101000 CR，却照样要付全额采购投入，
        # 于是利润永远为负——那不是经济衰退，是账漏了两条收入线。
        final_demand = household_budget + public_demand + owner_repair

        business = self.params.section("business")
        mid_share = Fraction(str(business["intermediate_sales_share"]))
        trade = self.params.section("trade")
        exports_cap = cr_to_cents(trade["exports_base"])
        tourism_cap = cr_to_cents(trade["tourist_spend_base"])
        capex = cr_to_cents(business["firm_capex_monthly"])

        # 中间品占营业额的 86000/340000，但 340000 本身就含这 86000，
        # 直接乘会自我循环。反推成「其余收入的倍数」：
        # 其余 = 340000 − 86000 = 254000，基准 254000 × 86/254 = 86000，
        # 还原设计稿 §3.1 的 340000 总营业额。
        # 基数必须包含出口与旅游，它们同样是「不产生中间品」的外来付款方；
        # 只拿本地最终需求当基数会把中间品少算三成多。
        base_demand = final_demand + capex + exports_cap + tourism_cap
        intermediate = (int(Fraction(base_demand) * mid_share / (1 - mid_share))
                        if mid_share < 1 else 0)
        local_demand = final_demand + intermediate + capex

        # 开工量由**需求**决定，产能只是上限：没订单就不开机。
        # 以前无条件按满产能开工，账面上凭空多出几千万「卖不掉的产出」，
        # 产能利用率因此永远显示接近满载，玩家完全看不出工厂其实在空转。
        production = min(capacity_cents, local_demand + exports_cap + tourism_cap)

        sold_locally = min(production, local_demand)
        unmet = max(0, local_demand - production)

        # §4.4 weight_outside：本地供给不足时，居民的钱流向外部市场，
        # 不是凭空消失；这笔钱必须作为一笔对外付款记进账本。
        market_cfg = self.params.section("market")
        external_spend = unmet if market_cfg["external_substitution"] else 0

        # 余下的产出去向出口与游客：先满足外需，卖不掉的才算存货。
        # 顺序反了会把本可出口的产能错记成存货，企业收入凭空少一大截。
        spare = max(0, production - sold_locally)
        exports = min(exports_cap, spare)
        tourism = min(tourism_cap, max(0, spare - exports))
        inventory = max(0, spare - exports - tourism)

        # 真正被卖掉的产能占比——这才是设计稿 §4.2 里那个 80%。
        self._sell_through = Fraction(min(production, total_capacity), total_capacity)

        return {
            "household_budget": household_budget,
            "public_demand": public_demand,
            "owner_repair": owner_repair,
            "intermediate_sales": intermediate,
            "firm_capex": capex,
            "local_served": sold_locally,
            "production": production,
            "inventory": inventory,
            "unmet_demand": unmet,
            "external_spend": external_spend,
            "exports": exports,
            "tourism": tourism,
            "imports": cr_to_cents(trade["imports_base"]),
            "central_grant": cr_to_cents(self.params.section("city")["central_transfer_monthly"]),
        }

    def _mandatory_ratio_of(self) -> Fraction:
        """本月必付项占毛收入的比例：所得税 + 房产税 + 宗教奉献。

        房产税按房产份额分摊，与收入无直接关系，这里按全城口径折算成比例，
        只用作消费预算的保守封顶，不参与实际计税。
        """
        tax = self.params.section("tax")
        income_tax = Fraction(str(tax["income_flat_rate"])) \
            * Fraction(str(tax["collection_efficiency"]))
        donation = Fraction(1500, 173300)
        housing = self.params.section("housing")
        property = (Fraction(str(housing["assessed_value"]))
                    * Fraction(str(tax["property_monthly_rate"]))
                    * (1 - Fraction(str(self.params.section("religion")
                                        ["property_exemption_strength"])) * self.faith_share))
        gross = max(1, sum(c.gross_income_cents for c in self.cohorts))
        return income_tax + donation + property / gross

    def _household_budget(self) -> int:
        """居民消费预算（设计稿 §4.3）。

        公式：``B_h = min(上期可支配收入 × 消费率, 钱包余额 − 本月必付项)``

        两道约束各管一头：
        - 收入决定「愿意花多少」，用上期可支配收入乘消费率；
        - 现金决定「花得起多少」，用钱包里真实存在的钱扣掉必付项。

        必付项包括所得税、房产税、奉献，以及**业主维修**——
        住宅租金是家庭付给同属家庭的房东，属内部划转，不减少全城现金，
        所以在家庭这一层不重复扣；但维修是付给本地企业的真金白银，必须扣。
        """
        total = 0
        mandatory_ratio = self._mandatory_ratio_of()
        repair = self._owner_repair()
        gross = max(1, sum(c.gross_income_cents for c in self.cohorts))
        repair_ratio = Fraction(repair, gross)

        for cohort in self.cohorts:
            disposable = (cohort.last_disposable_cents
                          or int(cohort.gross_income_cents * 0.9))
            # 本月必付：两税、奉献与业主维修，都从钱包里扣
            mandatory = int(cohort.gross_income_cents
                            * (mandatory_ratio + repair_ratio))
            affordable = max(0, cohort.cash_cents - mandatory)
            spend = int(min(Fraction(disposable) * Fraction(str(cohort.consumption_ratio)),
                            Fraction(affordable)))
            total += max(0, spend)
        return total

    def _public_demand(self) -> int:
        """市政采购与资本支出，按参数给定。"""
        from backend.config import REFERENCE_CITY
        city = self.params.section("city")
        procurement = cr_to_cents(sum(row["spend"]
                                      for row in REFERENCE_CITY["public_sector"]["procurement"]))
        return procurement + cr_to_cents(city["public_capex_monthly_initial"])

    # --- 08—12 收付 ----------------------------------------------------

    def _execute_payments(self, orders: dict, ledger: Ledger, events: list[str]) -> None:
        """执行本月全部真实收付。

        这是本月**唯一**改余额的地方：每一笔都必须写成 ``谁付给谁``。
        企业损益由 ``_settle_firms`` 算出数字，但钱一律从��里过，
        避免「同一个金额在两处各扣一次」这种最常见的记账错误。
        """
        tax = self.params.section("tax")
        city = self.params.section("city")
        religion = self.params.section("religion")
        housing = self.params.section("housing")

        # --- 企业收付：先把损益算出来，才知道该收付多少 ---
        # 企业实际售出 = 本地卖出 + 出口 + 游客；存货不产生现金收入。
        self._realised_revenue = (orders["local_served"] + orders["exports"]
                                  + orders["tourism"])
        self._total_capacity = sum(s.capacity_units for s in self.sectors)
        self._settle_firms(ledger, self.params.section("business"), tax)
        summary = self._firm_summary

        # 企业收入按**实际卖出的口径**分三笔付款。
        # 每笔的上限都来自付款方的真实支付能力：
        # 居民看钱包余额，市政看采购预算，业主维修按维修费定。
        # 这样任何一方都不会为付不起的钱背债。
        household_spend = min(orders["household_budget"], orders["local_served"])
        public_spend = min(orders["public_demand"],
                           max(0, orders["local_served"] - household_spend))
        repair_spend = min(orders["owner_repair"],
                           max(0, orders["local_served"] - household_spend - public_spend))

        ledger.pay("households", "firms", household_spend, "sale")
        ledger.pay("government", "firms", public_spend, "procurement")
        ledger.pay("households", "firms", repair_spend, "owner_repair")
        ledger.pay("external", "firms", orders["exports"] + orders["tourism"], "export")
        ledger.pay("firms", "households", summary["wage_total"], "wage")
        ledger.pay("firms", "households", summary["dividend_total"], "dividend")

        # 企业支出：进口、商业租金（房东即家庭，同账内部划转）、许可费、利润税
        # 商业租金 9500 CR 与许可规费 1200 CR 以前只进了损益表、没过账本，
        # 相当于这两笔钱凭空蒸发：居民少收一笔租金、市政少收一笔规费，
        # 而企业成本照扣。两个账户都跟着设计稿 §2.1 / §2.4 的数字走。
        fee_total = sum(s.fees_cents for s in self.sectors)
        ledger.pay("firms", "households", summary["commercial_rent"], "commercial_rent")
        ledger.pay("firms", "government", fee_total, "license_fee")
        ledger.pay("firms", "external", orders["imports"], "import")
        ledger.pay("firms", "government", summary["profit_tax"], "profit_tax")

        # --- 居民收付 ---
        income_tax = self._income_tax_total()
        property_tax = self._property_tax()
        donation = self._resident_donation()

        # 业主维修与商业租金都是家庭付给本地企业，已在上面记过，
        # 收入侧由企业收入侧对应，不再重复计入毛收入。
        # 住宅租金是家庭内部划转（房东与住户同账），不产生新的对外流量，
        # 但要计入收入层的资产收入，否则可支配收入会低于设计稿基准。
        residential_rent = (cr_to_cents(housing["rent_per_renter_month"])
                            * housing["renter_households"])
        # 设计稿 §2.1：净房产收益 =（住宅 28200 + 商业 9500）− 维修 16700 = 21000
        self._net_property_income = (residential_rent
                                     + summary["commercial_rent"] - repair_spend)

        # 工资与资产收入必须**按当月实际到账重算**，不能沿用开局种子值再叠加。
        # 以前 asset_income_cents 用 `+=` 每月加一份净房产收入，
        # 于是「资产收入」像利息一样逐月复利，越滚越大，
        # 消费预算跟着虚高，最后整座城都在花不存在的钱。
        public_wage = self._public_wage_cents()
        total_wage_paid = summary["wage_total"] + public_wage
        total_asset_paid = summary["dividend_total"] + self._net_property_income
        total_population = sum(c.population for c in self.cohorts) or 1
        for cohort in self.cohorts:
            share = Fraction(cohort.population, total_population)
            cohort.wage_income_cents = int(total_wage_paid * share)
            cohort.asset_income_cents = int(total_asset_paid * share)

        relief = cr_to_cents(city["welfare_monthly_initial"])
        ledger.pay("government", "households", public_wage, "public_wage")
        ledger.pay("government", "households", relief, "relief")
        ledger.pay("households", "external", orders["external_spend"], "external_purchase")
        ledger.pay("households", "government", income_tax, "income_tax")
        ledger.pay("households", "government", property_tax, "property_tax")
        ledger.pay("households", "church", donation, "donation")

        # --- 政府收付 ---
        ledger.pay("external", "government", orders["central_grant"], "grant")
        interest = int(cr_to_cents(city["start_debt"])
                       * Fraction(str(city["loan_interest_rate_monthly"])))
        ledger.pay("government", "external", interest, "interest")

        # --- 教会 ---
        grant = min(ledger.balances["church"],
                    int(cr_to_cents(religion["grant_ceiling_at_full_faith"]) * self.faith_share))
        ledger.pay("church", "government", grant, "church_grant")
        if grant <= 0 and ledger.balances["church"] > 0:
            events.append("教会储备不足，向市政捐赠被截断")

        # --- 战略品 ---
        self._trade_resources(ledger)

        # 资金缺口：付款被截断时显式记为事件与指标。
        # 设计稿 §5.5 说经济恶化要「有明确原因提示和改善路径」，
        # 所以缺口必须能看见，不能靠余额变成负数来暗示。
        for name, gap in sorted(ledger.shortfall.items()):
            events.append(f"{name} 本月少付 {gap} 分，钱不够")

        # 各收入层现金同步到账本：家庭是一本账，内部按比例分摊只作展示
        household_total = ledger.balances["households"]
        for cohort in self.cohorts:
            cohort.cash_cents = int(Fraction(household_total, total_population)
                                    * cohort.population)

    def _resident_donation(self) -> int:
        """居民宗教奉献（设计稿 §2.5）。

        毛收入 173,300 CR 时奉献 1,500 CR，占比 1500/173300。
        本方法用**参数给定的毛收入基准**按比例折算，而不是拿当月实际收入去除，
        否则人口或工资一变，奉献就会跟着漂移。
        """
        params = self.params
        wage_total = sum(s.wage_per_employee_cents * s.employees for s in self.sectors)
        base = cr_to_cents(params.get("housing.asset_income_base", 37000)) + wage_total
        if base <= 0:
            return 0
        return int(base * Fraction(1500, 173300))

    def _settle_firms(self, ledger: Ledger, business: dict, tax: dict) -> None:
        """企业当月损益、税与分红。

        这里**只算损益与税，不动账本余额**。企业真实的收付由
        ``_execute_payments`` 通过 ledger.pay 记过一遍；在这里再扣一次就是
        把同一笔钱记两次，账目必然对不上。

        收入口径用**实际售出**（本地＋出口＋游客），而不是满负荷产能：
        卖不掉的那些是存货，存货不产生现金收入，但生产的成本照样发生，
        所以利润自然会被压低——这正是「盲目扩产会亏钱」的机制来源。

        注意分组：利润税率与派息比例在 ``business`` 组，
        个税与征管效率在 ``tax`` 组，拿错分组会直接读不到键。
        """
        input_ratio = Fraction(154000, 340000)      # 设计稿 §2.2：采购占营业额比例
        rent_ratio = Fraction(9500, 340000)
        profit_tax_rate = Fraction(str(business["profit_tax_rate"]))
        efficiency = Fraction(str(tax["collection_efficiency"]))
        payout = Fraction(str(business["default_payout_ratio"]))
        realised = self._realised_revenue

        for sector in self.sectors:
            share = Fraction(sector.capacity_units, max(1, self._total_capacity))
            revenue = max(0, int(realised * share))
            sector.revenue_cents = revenue
            sector.wage_bill_cents = sector.wage_per_employee_cents * sector.employees
            sector.input_cost_cents = int(revenue * input_ratio)
            rent_cents = int(revenue * rent_ratio)

            # 亏损不征负所得税（设计稿 TEST-15）
            sector.profit_before_tax_cents = max(
                0, revenue - sector.input_cost_cents - sector.wage_bill_cents
                - rent_cents - sector.depreciation_cents - sector.fees_cents)
            sector.profit_tax_cents = int(
                sector.profit_before_tax_cents * profit_tax_rate * efficiency)
            sector.profit_after_tax_cents = (sector.profit_before_tax_cents
                                             - sector.profit_tax_cents)
            # 现金不足时分红必须截断
            available = max(0, revenue - sector.input_cost_cents
                            - sector.wage_bill_cents - rent_cents - sector.fees_cents)
            sector.dividend_cents = min(max(0, int(sector.profit_after_tax_cents * payout)),
                                        available)
            sector.utilisation = self._sell_through

        self._firm_summary = {
            "wage_total": sum(s.wage_bill_cents for s in self.sectors),
            "dividend_total": sum(s.dividend_cents for s in self.sectors),
            "profit_tax": sum(s.profit_tax_cents for s in self.sectors),
            "input_cost": sum(s.input_cost_cents for s in self.sectors),
            "commercial_rent": sum(int(s.revenue_cents * Fraction(9500, 340000))
                                   for s in self.sectors),
            "realised_revenue": realised,
        }

    def _trade_resources(self, ledger: Ledger) -> None:
        """战略品买卖：只有实际付款才改现金账（设计稿 §8.3）。

        库存平衡式 ``Stock_next = Stock + 产 + 进口 − 本地用 − 出口`` 每月结算一次。
        用量已由 ``_plan_resource_demand`` 写回；出口按参数基准值，
        进口按上一步算出的补缺口量，两者都要有真实的收付对应。
        """
        for resource in self.resources.values():
            resource.exported = resource.base_export
            # 本期库存＋产量不够用时，缺口靠进口补足（设计稿 §8.2）
            shortfall = resource.used - resource.stock - resource.produced
            resource.imported = resource.base_import + max(0.0, shortfall)
            if resource.imported > 0:
                cost = int(resource.imported * resource.unit_price_cents)
                ledger.pay("firms", "external", cost, "resource_import")
            if resource.exported > 0:
                revenue = int(resource.exported * resource.unit_price_cents)
                ledger.pay("external", "firms", revenue, "resource_export")
            resource.stock = (resource.stock + resource.produced + resource.imported
                              - resource.used - resource.exported)
            if resource.stock < -1e-6:
                raise GameError(
                    f"战略品 {resource.name} 期末库存为 {resource.stock:.1f}{resource.unit}，"
                    f"不得为负。请调整产量、进口或用量。")
            resource.stock = max(0.0, resource.stock)
            resource.produced = 0.0
            resource.used = 0.0
            resource.imported = 0.0
            resource.exported = 0.0

    def _owner_repair(self) -> int:
        """房产维修支出（设计稿 §2.1：16700 CR/月付给本地企业）。"""
        from backend.config import REFERENCE_CITY
        return cr_to_cents(REFERENCE_CITY["housing"]["owner_repair_spend"])

    def _property_tax(self) -> int:
        """房产税 = 评估值 × 0.12% × (1 − 0.15×信仰)。

        税率在 ``tax`` 组（税基在 ``housing`` 组），别拿错分组。
        """
        housing = self.params.section("housing")
        religion = self.params.section("religion")
        tax = self.params.section("tax")
        base = cr_to_cents(housing["assessed_value"]) * Fraction(str(tax["property_monthly_rate"]))
        relief = 1 - Fraction(str(religion["property_exemption_strength"])) * self.faith_share
        return int(base * relief)

    # --- 13—14 更新 ----------------------------------------------------

    def _update_prices_and_wages(self) -> None:
        """工资向目标缓慢收敛，单月涨跌不超过上限（设计稿 §4.6）。

        岗位紧张度 θ_s = 岗位需求 ÷ 劳动力供给：
        - 人手紧（岗位多、人少）→ θ > 1 → 工资往上走
        - 人手过剩（人多、岗位少）→ θ < 1 → 工资往下走

        之前把「就业率」当成了紧张度，于是就业率越高工资越低，
        方向正好拧了：调整幅度调大反而让工资掉得更快。
        """
        business = self.params.section("business")
        labour_cfg = self.params.section("labour")
        wage_cap = Fraction(str(labour_cfg["wage_change_monthly_cap"]))
        adjust = Fraction(str(labour_cfg["wage_target_adjustment"]))

        supply = sum(c.employed + c.unemployed for c in self.cohorts) or 1
        demand = sum(s.employees for s in self.sectors) + self.public_staff
        tightness = Fraction(demand, supply)

        for sector in self.sectors:
            target = int(sector.wage_per_employee_cents * tightness)
            # 每月只走完差距的 adjust 比例（设计稿默认 25%），
            # 再用 wage_cap 兜住单月上限，两者取更保守的那个。
            wanted = int((target - sector.wage_per_employee_cents) * adjust)
            cap = int(sector.wage_per_employee_cents * wage_cap)
            step = max(-cap, min(cap, wanted))
            sector.wage_per_employee_cents = max(1, sector.wage_per_employee_cents + step)
        _ = business  # 价格由产能利用率承担，此处保留接口位置

    def _carry_over_disposable(self) -> None:
        """步骤 13b：把本月实际到手的钱结转为下月的消费预算依据。

        设计稿 §4.3：「历史收入只决定预算，不保证真正成交」。
        本期新到手的工资进入下一期预算，绝不在本月预支——
        这正是「本期新工资进入下一期预期」的分界。

        可支配 = 毛收入 − 所得税 − 房产税 − 奉献 + 救济。
        维修与消费是本月已经付掉的支出，不从可支配里再扣一次。
        """
        income_tax = self._income_tax_total()
        property_tax = self._property_tax()
        donation = self._resident_donation()
        relief = cr_to_cents(self.params.section("city")["welfare_monthly_initial"])
        gross = max(1, sum(c.gross_income_cents for c in self.cohorts))

        for cohort in self.cohorts:
            share = Fraction(cohort.gross_income_cents, gross)
            realised = (cohort.gross_income_cents
                        - int((income_tax + property_tax + donation) * share)
                        + int(relief * share))
            # 不得用还没到账的钱做预算：以本月实际到手为上限
            cohort.last_disposable_cents = max(0, min(realised, cohort.gross_income_cents))

    def _update_religion(self) -> None:
        """信仰比例按传教政策缓慢变化，不瞬间跳变（§6.2）。"""
        religion = self.params.section("religion")
        tier = next(t for t in religion["policy_tiers"]
                    if t["tier"] == religion["policy"])
        self.faith_share = _clamp(
            self.faith_share + Fraction(str(tier["a"])) * (1 - self.faith_share)
            - Fraction(str(tier["b"])) * self.faith_share, 0, 1)

    def _update_labour_market(self) -> None:
        """劳动力市场：失业者只在**岗位有空缺时**才被录用（§4.6）。

        之前无条件消化四分之一待业人口，结果就业人数一路涨过岗位总数，
        人口上限被顶到岗位数，城市就开始往外吐人。
        真实劳动力市场不会凭空造岗位：没坑位就继续待业。
        """
        business = self.params.section("business")
        post_total = sum(s.employees for s in self.sectors) + self.public_staff
        vacancies = max(0, post_total - sum(c.employed for c in self.cohorts))

        remaining = vacancies
        for cohort in self.cohorts:
            if cohort.unemployed <= 0 or remaining <= 0:
                continue
            hired = min(cohort.unemployed, int(cohort.unemployed * 0.25), remaining)
            cohort.employed += hired
            cohort.unemployed -= hired
            remaining -= hired

        # 岗位扩张或收缩跟着**真实开工率**走，而不是跟着理论产能上限走。
        # 六因子乘积常年接近 1，拿它判断扩产，等于永远在扩产。
        threshold = Fraction(str(business["expansion_utilisation_min"]))
        utilisation = self._sell_through
        if utilisation > threshold and self._factors["labour"] < 1:
            for sector in self.sectors:
                sector.employees += 1
        elif utilisation < Fraction(str(business["contraction_utilisation_max"])):
            for sector in self.sectors:
                sector.employees = max(1, sector.employees - 1)

    def _update_population(self) -> int:
        """人口迁徙：受住房容量与劳动力需求约束（设计稿 §5.2）。

        人口上限不是「岗位数」本身，而是**岗位能养活多少人**：
        3000 人里有 1650 是劳动力，1500 个岗位加 150 个待业刚好匹配。
        拿岗位数当人口上限，会把 3000 人的城压到 1500 人，等于把
        五分之二的居民凭空赶走。

        上限 = 岗位数 ÷ 劳动力参与率，再与住房容量取小。
        """
        pop = self.params.section("population")
        housing = self.params.section("housing")
        housing_capacity = housing["units"] * max(1, housing.get("household_size", 4))

        total = sum(c.population for c in self.cohorts)
        jobs = sum(s.employees for s in self.sectors) + self.public_staff
        participation = max(0.1, pop["labour_force"] / max(1, pop["start"]))
        employed_total = max(1, pop["employed"] + pop["unemployed"])
        employment_rate = max(0.1, pop["employed"] / employed_total)
        # 分母还要带上就业率：1500 个岗位里只有 1500/1650 = 90.9% 的劳动力能上岗，
        # 剩下 9.1% 在待业。只除以劳动参与率会把 3000 人的城压到 2727 人，
        # 等于凭空赶走九分之一的居民。
        target = min(housing_capacity, int(jobs / (participation * employment_rate)))

        cap = max(1, int(total * Fraction(str(pop["migration_monthly_cap_rate"]))))
        delta = max(-cap, min(cap, target - total))
        if delta == 0:
            return 0

        # 按人口比例分摊到各收入层。
        # 用 floor 而不是 int()：int() 对负数是向零取整，-28×0.5 会变成 0，
        # 于是人口只涨不跌——这正是「人口持续下滑/卡死」的真实原因。
        import math
        allocated = 0
        for index, cohort in enumerate(self.cohorts):
            share = cohort.population / max(1, total)
            if delta > 0:
                move = max(1, math.floor(delta * share))
                cohort.population += move
            else:
                move = min(cohort.population - 1, math.floor(delta * share))
                cohort.population += move          # move 已是负数
            allocated += move
        # 残差补到最大的一层，保证分摊合计精确等于 delta
        residual = delta - allocated
        if residual:
            biggest = max(self.cohorts, key=lambda c: c.population)
            biggest.population = max(1, biggest.population + residual)

        # 迁入者自带积蓄，迁出者带走积蓄（每人金额取参数，不写死）
        amount = abs(delta) * cr_to_cents(pop["migration_savings_per_person"])
        if delta > 0:
            self.ledger.pay("external", "households", amount, "migration_in")
        else:
            self.ledger.pay("households", "external", amount, "migration_out")
        return delta

    # --- 15 月报 -------------------------------------------------------

    def _compile_report(self, ledger: Ledger, opening_local: int,
                        orders: dict, migration: int, events: list[str]) -> SimulationResult:
        """生成月报。会计残差必须为 0——钱既没创造也没销毁。"""
        tax = self.params.section("tax")
        city = self.params.section("city")

        population = sum(c.population for c in self.cohorts)
        employed = sum(c.employed for c in self.cohorts)
        labour_force = employed + sum(c.unemployed for c in self.cohorts)
        gross_income = sum(c.gross_income_cents for c in self.cohorts)
        disposable = gross_income - self._income_tax_total() - self._property_tax()

        firm_revenue = sum(s.revenue_cents for s in self.sectors)
        firm_profit = sum(s.profit_before_tax_cents for s in self.sectors)
        firm_after = sum(s.profit_after_tax_cents for s in self.sectors)
        firm_profit_tax = sum(s.profit_tax_cents for s in self.sectors)
        license_fee = sum(s.fees_cents for s in self.sectors)

        # 设计稿 §2.4 的政府收入五项：个税、利润税、房产税、规费、转移支付与教会捐赠。
        # 营业许可与行政规费以前没进这张表，而钱其实是从企业付过来了，
        # 账本记了、报表没记，收入侧和支出侧会对不上。
        municipal_revenue = (self._income_tax_total() + self._property_tax()
                             + firm_profit_tax + license_fee
                             + orders["central_grant"] + self._church_grant())
        municipal_expense = (self._public_wage_cents()
                             + orders["public_demand"]
                             + cr_to_cents(city["welfare_monthly_initial"])
                             + int(cr_to_cents(city["start_debt"])
                                   * Fraction(str(city["loan_interest_rate_monthly"]))))
        fiscal_balance = municipal_revenue - municipal_expense

        # 会计残差：本地现金变化必须与外部账户变化**互为相反数**。
        # 外部账户收 100 分，本地就少 100 分；外部付 100 分，本地就多 100 分。
        # 所以正确的判定式是 local_delta + external_net == 0，
        # 写成 local_delta - external_net 会把守恒的两个数当成同号去减，
        # 于是永远差出两倍，把完全配平的账误判成失衡。
        local_delta = ledger.local_total - opening_local
        residual = local_delta + ledger.external_net

        credit = self._credit_score(fiscal_balance)
        metrics = {
            "population": population,
            "employed": employed,
            "employment_rate": employed / labour_force if labour_force else 0,
            "unemployment_rate": (labour_force - employed) / labour_force if labour_force else 0,
            "migration_net": migration,
            "vacant_homes": max(0, self.params.section("housing")["units"]
                               - sum(c.households for c in self.cohorts)),
            "avg_wage_employed": (sum(s.wage_bill_cents for s in self.sectors)
                                  / employed if employed else 0),
            "avg_income_gross_per_capita": gross_income / population if population else 0,
            "avg_income_disposable_per_capita": (disposable / population if population else 0),
            "household_cash": ledger.balances["households"],
            "household_savings_rate": 0,
            "poverty_rate": 0,
            "firm_sales": firm_revenue,
            "firm_pre_tax_profit": firm_profit,
            "firm_after_tax_profit": firm_after,
            "firm_profit_tax": firm_profit_tax,
            "license_fee": license_fee,
            "firm_profit_margin": firm_profit / firm_revenue if firm_revenue else 0,
            "firm_cash": ledger.balances["firms"],
            "private_firm_count": sum(s.firm_count for s in self.sectors),
            "capacity_utilisation": float(self._sell_through),
            "capacity_readiness": float(self._capacity_utilisation),
            "firm_cash_runway_months": min(
                (s.cash_runway_months for s in self.sectors), default=0),
            "municipal_revenue": municipal_revenue,
            "municipal_expense": municipal_expense,
            "fiscal_balance": fiscal_balance,
            "municipal_cash": ledger.balances["government"],
            "municipal_debt": cr_to_cents(city["start_debt"]),
            "credit_score": credit,
            "loan_interest_rate": float(city["loan_interest_rate_monthly"]) + self._credit_spread(credit),
            "fiscal_runway_months": (ledger.balances["government"] / abs(fiscal_balance)
                                     if fiscal_balance < 0 else 999.0),
            "total_exports": orders["exports"],
            "total_imports": orders["imports"],
            "export_import_ratio": orders["exports"] / max(1, orders["imports"]),
            "net_external_flow": ledger.external_net,
            "trade_balance_warning": orders["exports"] / max(1, orders["imports"]),
            "strategic_stock_total": sum(r.closing for r in self.resources.values()),
            "gva": self._gva(firm_revenue),
            "faith_share": float(self.faith_share),
            "church_cash": ledger.balances["church"],
            "public_staff": self.public_staff,
            "school_coverage": self._school_coverage(),
            "effective_magic_risk": float(self._factors["safety"] * 0 + (1 - self._factors["safety"])),
            "accounting_residual_cents": residual,
            "total_city_cash": ledger.local_total,
            "unpaid_cents": sum(ledger.shortfall.values()),
        }

        return SimulationResult(
            month=self.month, metrics=metrics,
            sectors=[{"id": s.sector_id, "name": s.name, "employees": s.employees,
                      "firm_count": s.firm_count, "revenue": s.revenue_cents,
                      "profit": s.profit_before_tax_cents, "cash": s.cash_cents,
                      "utilisation": float(s.utilisation)} for s in self.sectors],
            cohorts=[{"id": c.band_id, "name": c.name, "population": c.population,
                      "employed": c.employed, "unemployed": c.unemployed,
                      "income": c.gross_income_cents, "cash": c.cash_cents}
                     for c in self.cohorts],
            events=list(events))

    def _income_tax_total(self) -> int:
        tax = self.params.section("tax")
        gross = sum(c.gross_income_cents for c in self.cohorts)
        return int(gross * Fraction(str(tax["income_flat_rate"]))
                   * Fraction(str(tax["collection_efficiency"])))

    def _church_grant(self) -> int:
        religion = self.params.section("religion")
        return min(self.ledger.balances["church"],
                   int(cr_to_cents(religion["grant_ceiling_at_full_faith"]) * self.faith_share))

    def _gva(self, firm_revenue: int) -> int:
        """GVA = 企业增加值 + 房地产增加值 + 政府非市场产出（§3.4）。"""
        input_cost = sum(s.input_cost_cents for s in self.sectors)
        commercial_rent = sum(int(s.revenue_cents * Fraction(9500, 340000)) for s in self.sectors)
        firm_gva = firm_revenue - input_cost - commercial_rent
        property_gva = cr_to_cents(self.params.section("housing")["rent_per_renter_month"]) \
            * self.params.section("housing")["renter_households"] - self._owner_repair()
        gov_gva = self._public_wage_cents()
        return firm_gva + property_gva + gov_gva

    def _credit_score(self, fiscal_balance: int) -> float:
        """城市信用：税收稳定性、现金流、负债率、还款记录与发展（§5.4）。"""
        weights = self.params.section("city")["credit_score_weights"]
        cash_flow = max(-1.0, min(1.0, fiscal_balance / max(1, cr_to_cents(10000))))
        debt_ratio = cr_to_cents(self.params.section("city")["start_debt"]) / \
            max(1, self.ledger.balances["government"]
                + cr_to_cents(self.params.section("city")["start_debt"]))
        return (weights["net_cash_flow"] * cash_flow
                + weights["debt_ratio"] * (1 - debt_ratio))

    def _credit_spread(self, credit: float) -> float:
        """信用分映射到利差加成：信用越好利率越低。"""
        city = self.params.section("city")
        curve = city["credit_spread_curve"]
        for (x0, y0), (x1, y1) in zip(curve, curve[1:]):
            if x0 <= credit <= x1:
                ratio = (credit - x0) / (x1 - x0) if x1 != x0 else 0
                return y0 + (y1 - y0) * ratio
        return curve[-1][1]

    def _school_coverage(self) -> float:
        from backend.config import REFERENCE_CITY
        edu = self.params.section("education")
        teaching = REFERENCE_CITY["public_sector"]["posts"][0]
        seats = teaching["staff"] * edu["seats_per_teacher"]
        school_age = self.params.section("population")["start"] * 0.30
        return min(1.0, seats / max(1, school_age))

    @property
    def latest(self) -> SimulationResult:
        if not self.history:
            raise GameError("还没有跑过月结，请先调用 run() 或 step()。")
        return self.history[-1]


# --- 工具函数 -----------------------------------------------------------

def _clamp(value: Fraction, low: Fraction, high: Fraction) -> Fraction:
    return max(low, min(high, value))


def _pow_ratio(numerator: float, denominator: float, exponent_percent: int) -> Fraction:
    """按 §4.1 的幂次计算因子：ratio^exponent，exponent 以百分数传入。"""
    if denominator <= 0:
        return Fraction(1)
    ratio = Fraction(str(max(0.0, numerator / denominator)))
    return _clamp(min(Fraction(1), _fraction_power(ratio, exponent_percent / 100)), 0, 1)


def _fraction_power(base: Fraction, exponent: float) -> Fraction:
    """分数的实数次幂，结果仍规约为分数。

    ``Fraction ** float`` 会退化成 float，那会让整条现金链出现浮点误差，
    违反「金额一律整数分」的红线。这里对分子分母分别求幂，
    再用 ``limit_denominator`` 把结果压回可精确表示的分数。
    """
    if base <= 0:
        return Fraction(0)
    value = Fraction(base.numerator ** exponent) / Fraction(base.denominator ** exponent)
    return Fraction(value).limit_denominator(10**12)


__all__ = ["EconomySimulation", "Ledger"]