"""P02 月度结算编排。

严格按 data/economy/calendar_v1.json 的 monthly_cycle 顺序执行，步骤编号与配置一一对应。
核心纪律：任何现金变动都必须是一笔有付款方与收款方的交易，
禁止出现"按人口自动加钱"或直接修改账户余额的写法（见 _enqueue_cash_flows）。
"""
from fractions import Fraction

from backend.config import LABOR_CONSUMPTION
from backend.core.errors import GameError
from backend.model.economy import ReferenceCity, apply_rate, cr_to_cents, to_rate
from backend.model.households import HouseholdRegistry
from backend.model.transactions import (CAPITAL, COMMERCIAL_RENT, DIVIDEND, DONATION,
                                       EXPORT, FEES, GRANT, IMPORT, INCOME_TAX, INTEREST,
                                       MAINTENANCE, PROCUREMENT, PROFIT_TAX, PROPERTY_TAX,
                                       RELIEF, RESIDENTIAL_RENT, SALE, TRANSFER, WAGE,
                                       Transaction)
from backend.core.settlement import Settlement
from backend.systems.accounting import check_conservation
from backend.systems.consumption import allocate_by_share, build_plan
from backend.systems.fiscal import FiscalPolicy
from backend.systems.markets import market_flows, trade_flows
from backend.systems.production import capital_inflow_cents, corporate_ledger, trade_split
from backend.systems.property_income import property_flows
from backend.systems.reports import (MonthReport, average_wage, disposable_income,
                                     gva_production_method, resident_cash_change)


def _landlord_distribution_cents(book, prop, ratio: Fraction) -> int:
    """房东本月末可分配回主钱包的净房产收入。

    房东是家庭的财产身份（通俗比喻：这套房的主人就住在这座城里），
    若租金只进不出，房东的钱会永远堆在子账户里，而住户的钱永远不够花——
    P02 的「第 13 个月破产」就是这么来的。分配比例可配，1.0 表示全额回主钱包。
    """
    balance = book[ReferenceCity.LANDLORDS].cash_cents + prop.gross_rent_cents - prop.owner_repair_cents
    return max(0, apply_rate(balance, ratio))


def _band_taxes(registry, policy: FiscalPolicy, assessed_cents: int,
                faith: Fraction) -> tuple[dict[str, int], int, int]:
    """按收入层计算所得税与房产税。

    分层合计必须精确等于全城税额：所得税以全城毛收入为税基分别落到各层，
    房产税以评估值为税基按房产份额拆分。各层单独四舍五入产生的残差
    由最后一层用「全城总额−其余各层」吸收，保证不会出现负税额这种荒谬结果。
    """
    cohorts = list(registry)
    city_income_tax = policy.income_tax(registry.total_gross_income_cents)
    income_tax_by_band, total_income_tax = {}, 0
    for cohort in cohorts[:-1]:
        tax = policy.income_tax(cohort.gross_income_cents)
        income_tax_by_band[cohort.band_id] = tax
        total_income_tax += tax

    property_tax_by_band, total_property_tax = {}, 0
    base_tax_total = policy.property_tax(assessed_cents, faith)
    for cohort in cohorts[:-1]:
        share = int(base_tax_total * cohort.property_tax_share)
        property_tax_by_band[cohort.band_id] = share
        total_property_tax += share

    last = cohorts[-1]
    income_tax_by_band[last.band_id] = city_income_tax - total_income_tax
    property_tax_by_band[last.band_id] = base_tax_total - total_property_tax
    if income_tax_by_band[last.band_id] < 0 or property_tax_by_band[last.band_id] < 0:
        raise GameError(f"收入层 {last.band_id} 分摊到负税额，请检查税率与份额配置")
    return ({"income_tax": income_tax_by_band, "property_tax": property_tax_by_band},
            city_income_tax, base_tax_total)


def _carry_over(registry, plan, band_tax: dict, relief: int) -> None:
    """步骤 13：把本月实际实现的可支配收入写回各收入层，作为下一期消费预算的依据。

    本期新到手的工资进入「下一期可支配收入」，绝不在本月预支——
    这正是设计文档要求「本期新工资进入下一期预期」的分界。

    注意这里只更新 last_disposable，不动 cash_cents：钱包余额由账户簿真实记账，
    下个月开头 _sync_cohort_cash 会从账户簿重新同步。在这一步用公式覆盖钱包
    会把往期储蓄抹掉，等于凭空销毁居民的钱。
    """
    relief_by_band = allocate_by_share(relief, [c.relief_share for c in registry])

    for index, cohort in enumerate(registry):
        band = plan.band(cohort.band_id)
        taxes_paid = band_tax["income_tax"][cohort.band_id] + band_tax["property_tax"][cohort.band_id]
        # 本期实现的可支配收入 = 税前收入 − 两项税 + 本期救济
        realized = cohort.gross_income_cents - taxes_paid + relief_by_band[index]
        # 不得超过上期实际到手的钱，避免用没到账的收入做下期预算
        actually_available = band.capacity_cents + band.mandatory_cents
        cohort.last_disposable_cents = min(realized, actually_available)


def _enqueue_cash_flows(settlement: Settlement, city: ReferenceCity, ledger, flows,
                        trade: dict, prop, policy: FiscalPolicy, faith: Fraction,
                        taxes: dict, month: int, consumption_cents: int,
                        landlord_distribution: int, settlement_io: dict,
                        extra_transactions=()) -> None:
    """步骤 08—12：把本月的每一笔收付变成交易入队。

    每笔都有明确付款方与收款方；source_ref 标明业务来源，便于开发诊断页展开追溯。
    居民消费额来自 P03 消费计划，不再是写死的校准常数。
    """
    gov, church, households, landlords = (ReferenceCity.GOVERNMENT, ReferenceCity.CHURCH,
                                          ReferenceCity.HOUSEHOLDS, ReferenceCity.LANDLORDS)
    firms, outside = ReferenceCity.FIRMS, ReferenceCity.OUTSIDE
    enqueue = settlement.enqueue

    # 清算层坚持「金额必须为正」，那是防止空交易混进账本的最后一道闸。
    # 但真实世界里的收付本来就可能是零（全城失业 → 所得税为 0，
    # 企业亏损 → 不分红）。所以「本月有没有这笔义务」由编排层判断，
    # 零额直接不入队，而不是伪造一笔 1 分的交易去骗过清算层。
    def pay(sender, receiver, amount, category, ref):
        if amount > 0:
            enqueue(Transaction(sender, receiver, amount, category, ref, month))

    # 08 企业收到货款：八来源逐笔对应付款账户
    pay(households, firms, consumption_cents, SALE, "household-consumption")
    pay(landlords, firms, prop.landlord_repair_cents, MAINTENANCE, "owner-repair")
    pay(gov, firms, flows.municipal_procurement_cents, PROCUREMENT, "municipal-procurement")
    pay(gov, firms, flows.municipal_capital_cents, CAPITAL, "municipal-capital")
    pay(outside, firms, flows.exports_cents, EXPORT, "exports")
    pay(outside, firms, flows.tourist_spend_cents, SALE, "tourism")
    # 企业互购是企业付给企业自己，现金净影响为零，但仍要留痕以免营业额被重复计入
    pay(firms, firms, flows.firm_intermediate_cents, TRANSFER, "firm-intermediate")
    pay(outside, firms, capital_inflow_cents(), CAPITAL, "private-capital-inflow")

    # 09 企业支出：采购（含进口）、工资、商业租金、规费
    _local_intermediate, import_cents = trade_split(settlement_io)
    pay(firms, outside, import_cents, IMPORT, "imports")
    pay(firms, households, ledger.wage_cents, WAGE, "private-wages")
    pay(firms, landlords, ledger.commercial_rent_cents, COMMERCIAL_RENT, "commercial-rent")
    pay(firms, gov, ledger.fees_cents, FEES, "business-fees")

    # 10 企业税与分红：分红从税后利润中取，且不超过可用现金
    pay(firms, gov, ledger.profit_tax_cents, PROFIT_TAX, "profit-tax")
    pay(firms, households, ledger.dividend_cents, DIVIDEND, "dividend")
    pay(firms, outside, ledger.capital_purchase_cents, CAPITAL, "capital-purchase")

    # 11 市政支出与税收
    pay(gov, households, city.public_wage_bill_cents, WAGE, "public-wages")
    pay(gov, households, policy.relief_payments_cents, RELIEF, "relief")
    pay(households, gov, taxes["income_tax"], INCOME_TAX, "income-tax")
    pay(households, gov, taxes["property_tax"], PROPERTY_TAX, "property-tax")

    # 12 教会、中央拨款与贷款利息
    pay(church, gov, taxes["church_donation"], DONATION, "church-donation")
    pay(outside, gov, policy.central_grant_cents, GRANT, "central-grant")
    pay(gov, outside, policy.interest_cents, INTEREST, "loan-interest")

    # 居民侧：住宅租金、献奉
    pay(households, landlords, prop.residential_rent_cents,
        RESIDENTIAL_RENT, "residential-rent")
    pay(households, church, taxes["donation"], DONATION, "church-donation-residents")
    # 自住房没有租户、维修却真实存在，由房主本人直接付给维修企业。
    # 参考城的住宅全部有租赁市场，这笔为 0；接入可玩世界后才会出现。
    pay(households, firms, prop.household_repair_cents, MAINTENANCE, "owner-self-repair")

    # 12b 房东净房产收入分配回家庭主钱包：房东是家庭财产身份，本阶段不另设消费行为。
    # 这一步只把子账户里的钱挪回同一个家庭的主钱包，不创造也不销毁现金。
    pay(landlords, households, landlord_distribution, TRANSFER, "landlord-property-income")

    # 接入运行时才有的交易（例如居民购屋）。它们同样是账户之间的真实收付，
    # 不是绕过账本直接改余额；参考月没有这些交易，因此基准不受影响。
    for tx in extra_transactions or ():
        pay(tx.from_account, tx.to_account, tx.amount_cents, tx.category,
            f"{tx.source_ref}")


def simulate_month(city: ReferenceCity, month: int = 1, policy: FiscalPolicy = None,
                   faith: Fraction = None, settled=(),
                   registry: HouseholdRegistry = None,
                   extra_transactions=()) -> MonthReport:
    """跑完一个月的经济结算，返回月报。

    步骤编号对应 calendar_v1.json 的 monthly_cycle.events 序列。
    传入 settled（通常是存档里的已清流水）后，同一月份重复调用不会二次付款。
    传入 registry 可延续上月的收入层现金与可支配收入，实现跨月消费反馈。
    传入 extra_transactions 可加入接入运行时特有的账户间交易（如居民购屋）。
    """
    book = city.book
    opening = book.snapshot()                       # 步骤 01 前保存开账余额
    policy = policy or FiscalPolicy(city.config)
    faith = faith if faith is not None else policy.opening_faith
    # 默认沿用挂在城上的登记表，跨月的收入—消费反馈才不会因为漏传参数而断链
    registry = registry if registry is not None else city.household_registry

    ledger = corporate_ledger(city, policy, faith)        # 步骤 03—07 的输入
    io = city.input_output
    flows = market_flows(io)
    trade = trade_flows(io)
    prop = property_flows(city)

    assessed = cr_to_cents(city.config["housing"]["assessed_value"])
    band_tax, income_tax_total, property_tax_total = _band_taxes(registry, policy, assessed, faith)

    gross_income = registry.total_gross_income_cents
    relief = policy.relief_payments_cents
    donation = cr_to_cents(city.config["religion"]["resident_donation"])

    # 步骤 04：消费预算由上期可支配收入与实际储蓄决定，并受本期真实现金容量封顶
    plan = build_plan(registry, month=month,
                      household_cash_cents=book[ReferenceCity.HOUSEHOLDS].cash_cents,
                      relief_cents=relief,
                      residential_rent_cents=prop.residential_rent_cents,
                      donation_cents=donation, band_tax=band_tax)
    consumption_cents = plan.total_spend_cents
    if consumption_cents <= 0:
        raise GameError(
            f"第 {month} 月消费预算为零：各收入层既没有上期可支配收入，也没有可动用现金。"
            f"请检查收入层登记表是否在跨月时被重新创建。")

    taxes = {
        "income_tax": income_tax_total,
        "property_tax": property_tax_total,
        "church_donation": policy.church_donation(book[ReferenceCity.CHURCH].cash_cents, faith),
        "donation": donation,
    }

    distribution_ratio = to_rate(city.labor_config["cash_rules"]["landlord_distribution_ratio"])
    landlord_distribution = _landlord_distribution_cents(book, prop, distribution_ratio)

    settlement = Settlement(book, month=month, opening=opening, settled=settled)
    _enqueue_cash_flows(settlement, city, ledger, flows, trade, prop, policy, faith,
                        taxes, month, consumption_cents, landlord_distribution, io,
                        extra_transactions)
    settlement.settle()                            # 步骤 08—12：一次清算
    city.last_journal = settlement.journal         # 供读档后恢复幂等

    # 步骤 15：会计断言、生成月报、提交
    report = check_conservation(book, opening, settlement.journal).raise_if_unbalanced()

    disposable = plan.total_disposable_cents
    municipal_income = (taxes["income_tax"] + taxes["property_tax"] + ledger.profit_tax_cents
                        + ledger.fees_cents + taxes["church_donation"] + policy.central_grant_cents)
    municipal_spending = (city.public_wage_bill_cents + city.public_procurement_cents()
                          + policy.relief_payments_cents + policy.interest_cents
                          + flows.municipal_capital_cents)
    gva = gva_production_method(city, ledger, prop)

    # 步骤 13：把本月的实际结果写回各收入层，作为下一期预算的依据
    _carry_over(registry, plan, band_tax, relief)

    return MonthReport(
        month=month,
        currency="CR",
        unit="分",
        revenue_cents=flows.total_revenue_cents - flows.household_consumption_cents + consumption_cents,
        input_cost_cents=ledger.input_cents,
        profit_before_tax_cents=ledger.profit_before_tax_cents,
        profit_tax_cents=ledger.profit_tax_cents,
        profit_after_tax_cents=ledger.profit_after_tax_cents,
        dividend_cents=ledger.dividend_cents,
        retained_profit_cents=ledger.retained_profit_cents,
        depreciation_cents=ledger.depreciation_cents,
        resident_gross_income_cents=gross_income,
        resident_disposable_income_cents=disposable,
        resident_cash_change_cents=resident_cash_change(
            disposable, consumption_cents, prop.residential_rent_cents, taxes["donation"]),
        income_tax_cents=taxes["income_tax"],
        property_tax_cents=taxes["property_tax"],
        average_wage_cents=average_wage(city),
        municipal_income_cents=municipal_income,
        municipal_spending_cents=municipal_spending,
        municipal_surplus_cents=policy.municipal_surplus(municipal_income, municipal_spending),
        church_reserve_change_cents=(taxes["donation"] + policy.official_subsidy(0)
                                     - taxes["church_donation"]),
        city_cash_change_cents=report.city_cash_delta_cents,
        external_cash_change_cents=report.external_cash_delta_cents,
        gva_production_cents=gva.total_cents,
        gva_production_breakdown=gva.to_dict(),
    )