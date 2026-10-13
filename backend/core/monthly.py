"""P02 月度结算编排。

严格按 data/economy/calendar_v1.json 的 monthly_cycle 顺序执行，步骤编号与配置一一对应。
核心纪律：任何现金变动都必须是一笔有付款方与收款方的交易，
禁止出现"按人口自动加钱"或直接修改账户余额的写法（见 _enqueue_cash_flows）。
"""
from fractions import Fraction

from backend.model.economy import ReferenceCity, cr_to_cents
from backend.model.transactions import (CAPITAL, COMMERCIAL_RENT, DIVIDEND, DONATION,
                                       EXPORT, FEES, GRANT, IMPORT, INCOME_TAX, INTEREST,
                                       MAINTENANCE, PROCUREMENT, PROFIT_TAX, PROPERTY_TAX,
                                       RELIEF, RESIDENTIAL_RENT, SALE, TRANSFER, WAGE,
                                       Transaction)
from backend.core.settlement import Settlement
from backend.systems.accounting import check_conservation
from backend.systems.fiscal import (FiscalPolicy, net_property_income,
                                   resident_gross_income)
from backend.systems.markets import market_flows, trade_flows
from backend.systems.production import capital_inflow_cents, corporate_ledger, trade_split
from backend.systems.property_income import property_flows
from backend.systems.reports import (MonthReport, average_wage, disposable_income,
                                     gva_production_method, resident_cash_change)


def _enqueue_cash_flows(settlement: Settlement, city: ReferenceCity, ledger, flows,
                        trade: dict, prop, policy: FiscalPolicy, faith: Fraction,
                        taxes: dict, month: int) -> None:
    """步骤 08—12：把本月的每一笔收付变成交易入队。

    每笔都有明确付款方与收款方；source_ref 标明业务来源，便于开发诊断页展开追溯。
    """
    gov, church, households, landlords = (ReferenceCity.GOVERNMENT, ReferenceCity.CHURCH,
                                          ReferenceCity.HOUSEHOLDS, ReferenceCity.LANDLORDS)
    firms, outside = ReferenceCity.FIRMS, ReferenceCity.OUTSIDE
    enqueue = settlement.enqueue

    # 08 企业收到货款：八来源逐笔对应付款账户
    enqueue(Transaction(households, firms, flows.household_consumption_cents, SALE,
                        "household-consumption", month))
    enqueue(Transaction(landlords, firms, flows.owner_repair_cents, MAINTENANCE,
                        "owner-repair", month))
    enqueue(Transaction(gov, firms, flows.municipal_procurement_cents, PROCUREMENT,
                        "municipal-procurement", month))
    enqueue(Transaction(gov, firms, flows.municipal_capital_cents, CAPITAL,
                        "municipal-capital", month))
    enqueue(Transaction(outside, firms, flows.exports_cents, EXPORT, "exports", month))
    enqueue(Transaction(outside, firms, flows.tourist_spend_cents, SALE, "tourism", month))
    # 企业互购是企业付给企业自己，现金净影响为零，但仍要留痕以免营业额被重复计入
    enqueue(Transaction(firms, firms, flows.firm_intermediate_cents, TRANSFER,
                        "firm-intermediate", month))
    enqueue(Transaction(outside, firms, capital_inflow_cents(), CAPITAL,
                        "private-capital-inflow", month))

    # 09 企业支出：采购（含进口）、工资、商业租金、规费
    _local_intermediate, import_cents = trade_split()
    enqueue(Transaction(firms, outside, import_cents, IMPORT, "imports", month))
    enqueue(Transaction(firms, households, ledger.wage_cents, WAGE, "private-wages", month))
    enqueue(Transaction(firms, landlords, ledger.commercial_rent_cents, COMMERCIAL_RENT,
                        "commercial-rent", month))
    enqueue(Transaction(firms, gov, ledger.fees_cents, FEES, "business-fees", month))

    # 10 企业税与分红：分红从税后利润中取，且不超过可用现金
    enqueue(Transaction(firms, gov, ledger.profit_tax_cents, PROFIT_TAX,
                        "profit-tax", month))
    enqueue(Transaction(firms, households, ledger.dividend_cents, DIVIDEND,
                        "dividend", month))
    enqueue(Transaction(firms, outside, ledger.capital_purchase_cents, CAPITAL,
                        "capital-purchase", month))

    # 11 市政支出与税收
    enqueue(Transaction(gov, households, city.public_wage_bill_cents, WAGE,
                        "public-wages", month))
    enqueue(Transaction(gov, households, policy.relief_payments_cents, RELIEF,
                        "relief", month))
    enqueue(Transaction(households, gov, taxes["income_tax"], INCOME_TAX,
                        "income-tax", month))
    enqueue(Transaction(households, gov, taxes["property_tax"], PROPERTY_TAX,
                        "property-tax", month))

    # 12 教会、中央拨款与贷款利息
    enqueue(Transaction(church, gov, taxes["church_donation"], DONATION,
                        "church-donation", month))
    enqueue(Transaction(outside, gov, policy.central_grant_cents, GRANT,
                        "central-grant", month))
    enqueue(Transaction(gov, outside, policy.interest_cents, INTEREST,
                        "loan-interest", month))

    # 居民侧：住宅租金、奉献
    enqueue(Transaction(households, landlords, prop.residential_rent_cents,
                        RESIDENTIAL_RENT, "residential-rent", month))
    enqueue(Transaction(households, church, taxes["donation"], DONATION,
                        "church-donation-residents", month))


def simulate_month(city: ReferenceCity, month: int = 1, policy: FiscalPolicy = None,
                   faith: Fraction = None, settled=()) -> MonthReport:
    """跑完一个月的经济结算，返回月报。

    步骤编号对应 calendar_v1.json 的 monthly_cycle.events 序列。
    传入 settled（通常是存档里的已清流水）后，同一月份重复调用不会二次付款。
    """
    book = city.book
    opening = book.snapshot()                       # 步骤 01 前保存开账余额
    policy = policy or FiscalPolicy(city.config)
    faith = faith if faith is not None else policy.opening_faith

    ledger = corporate_ledger(city, policy, faith)        # 步骤 03—07 的输入
    flows = market_flows()
    trade = trade_flows()
    prop = property_flows(city)

    assessed = cr_to_cents(city.config["housing"]["assessed_value"])
    gross_income = resident_gross_income(
        city.private_wage_bill_cents, city.public_wage_bill_cents,
        ledger.dividend_cents, net_property_income(
            prop.residential_rent_cents, prop.commercial_rent_cents, prop.owner_repair_cents))
    taxes = {
        "income_tax": policy.income_tax(gross_income),
        "property_tax": policy.property_tax(assessed, faith),
        "church_donation": policy.church_donation(book[ReferenceCity.CHURCH].cash_cents, faith),
        "donation": cr_to_cents(city.config["religion"]["resident_donation"]),
    }

    settlement = Settlement(book, month=month, opening=opening, settled=settled)
    _enqueue_cash_flows(settlement, city, ledger, flows, trade, prop, policy, faith,
                        taxes, month)
    settlement.settle()                            # 步骤 08—12：一次清算
    city.last_journal = settlement.journal         # 供读档后恢复幂等

    # 步骤 15：会计断言、生成月报、提交
    report = check_conservation(book, opening, settlement.journal).raise_if_unbalanced()

    consumption = flows.household_consumption_cents
    disposable = disposable_income(gross_income, taxes["income_tax"],
                                  taxes["property_tax"], policy.relief_payments_cents)
    municipal_income = (taxes["income_tax"] + taxes["property_tax"] + ledger.profit_tax_cents
                        + ledger.fees_cents + taxes["church_donation"] + policy.central_grant_cents)
    municipal_spending = (city.public_wage_bill_cents + city.public_procurement_cents()
                          + policy.relief_payments_cents + policy.interest_cents
                          + flows.municipal_capital_cents)
    gva = gva_production_method(city, ledger, prop)

    return MonthReport(
        month=month,
        currency="CR",
        unit="分",
        revenue_cents=flows.total_revenue_cents,
        input_cost_cents=ledger.input_cents,
        profit_before_tax_cents=ledger.profit_before_tax_cents,
        profit_tax_cents=ledger.profit_tax_cents,
        profit_after_tax_cents=ledger.profit_after_tax_cents,
        dividend_cents=ledger.dividend_cents,
        retained_profit_cents=ledger.retained_profit_cents,
        depreciation_cents=ledger.depreciation_cents,
        resident_gross_income_cents=gross_income,
        resident_disposable_income_cents=disposable,
        resident_cash_change_cents=resident_cash_change(disposable, consumption,
                                                        prop.residential_rent_cents,
                                                        taxes["donation"]),
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