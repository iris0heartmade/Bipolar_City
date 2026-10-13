"""P02 企业侧：工资、采购、商业租金、规费、利润税与分红。

每笔支出都有付款方与收款方，不得直接给账户加钱。折旧只进利润表，不进现金账。
分红取税后利润与基准派息比例的较小值；亏损主体不分红、也不产生负税。
"""
from dataclasses import dataclass
from fractions import Fraction

from backend.config import CALIBRATION, INPUT_OUTPUT
from backend.model.economy import ReferenceCity, apply_rate, cr_to_cents, rate_from_text


@dataclass
class CorporateLedger:
    """企业本月应付的全部款项，单位为整数分。"""

    wage_cents: int
    input_cents: int
    commercial_rent_cents: int
    fees_cents: int
    depreciation_cents: int
    profit_before_tax_cents: int
    profit_tax_cents: int
    profit_after_tax_cents: int
    dividend_cents: int
    retained_profit_cents: int
    capital_purchase_cents: int

    @property
    def cash_outflow_cents(self) -> int:
        """企业真实付现合计，不含折旧：折旧是账面成本，不会再扣一次钱包。"""
        return (self.wage_cents + self.input_cents + self.commercial_rent_cents + self.fees_cents
                + self.profit_tax_cents + self.dividend_cents + self.capital_purchase_cents)


def dividend_payout_rate(input_output=None) -> Fraction:
    """参考月用有理数表达派息比例，保证首月恰好 16000 分毫不差。

    比例来自 P00 校准配置而非硬编码；普通演化配置可换成 0.4288，但须相应重算预期。
    """
    calibration = (input_output or {}).get("dividend_payout_value")
    if calibration:
        return Fraction(str(calibration))
    return rate_from_text(CALIBRATION["algorithm_conventions"]["dividend_payout"]["value"])


def corporate_ledger(city: ReferenceCity, policy, faith: Fraction) -> CorporateLedger:
    """按配置与税率重算企业本月的应付账款。"""
    profit_before_tax = city.profit_before_tax_cents
    profit_tax = policy.profit_tax(profit_before_tax)
    profit_after_tax = profit_before_tax - profit_tax

    # 分红从正税后利润中按基准比例取；亏损或零利润时为 0
    dividend = apply_rate(max(0, profit_after_tax), dividend_payout_rate(city.input_output))
    dividend = min(dividend, max(0, profit_after_tax))

    firm_out = city.input_output["firm_flows"]["outflows"]
    return CorporateLedger(
        wage_cents=city.private_wage_bill_cents,
        input_cents=city.input_cost_cents,
        commercial_rent_cents=city.commercial_rent_cents,
        fees_cents=city.fees_cents,
        depreciation_cents=city.depreciation_cents,
        profit_before_tax_cents=profit_before_tax,
        profit_tax_cents=profit_tax,
        profit_after_tax_cents=profit_after_tax,
        dividend_cents=dividend,
        retained_profit_cents=profit_after_tax - dividend,
        capital_purchase_cents=cr_to_cents(firm_out["capital_purchase"]),
    )


def trade_split(input_output=None) -> tuple[int, int]:
    """采购分解为本地互购与进口。本地互购在聚合层是企业付给企业自己，现金净影响为零。"""
    split = (input_output or INPUT_OUTPUT)["firm_input_split"]
    return cr_to_cents(split["local_intermediate"]), cr_to_cents(split["imports"])


def capital_inflow_cents(input_output=None) -> int:
    """私人资本投入：由外部投资者付给企业，与企业资本采购等额进出，故不影响对外净流量。"""
    return cr_to_cents((input_output or INPUT_OUTPUT)["firm_revenue_sources"]["private_capital"])