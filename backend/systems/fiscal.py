"""P02 市政财政：税率、税收与教会收付。

税率只作用于正税基，亏损不产生负税（通俗比喻：亏了钱就没有税可交，但不能倒过来退税）。
基准首月一律使用期初信仰，月底慢变只影响下一月。
"""
from fractions import Fraction

from backend.config import REFERENCE_CITY
from backend.model.economy import apply_rate, cr_to_cents, to_rate

CHURCH_DONATION_BASE_CR = 1200


class FiscalPolicy:
    """可调的市政税率集合。调整受季度政策窗口与冷却限制，冷却逻辑在 calendar_v1.json。"""

    def __init__(self, config=None):
        config = config or REFERENCE_CITY
        fiscal, religion = config["fiscal"], config["religion"]
        self.income_tax_rate = to_rate(fiscal["income_tax_rate"])
        self.profit_tax_rate = to_rate(fiscal["profit_tax_rate"])
        self.property_tax_rate = to_rate(fiscal["property_tax_monthly_rate"])
        self.relief_rate = to_rate(fiscal["property_relief_religion_factor"])
        self.central_grant_cents = cr_to_cents(fiscal["central_grant"])
        self.relief_payments_cents = cr_to_cents(fiscal["relief_payments"])
        loan = config["finance"]
        self.loan_principal_cents = cr_to_cents(loan["government_loan_principal"])
        self.loan_interest_rate = to_rate(loan["monthly_interest_rate"])
        # 宽限期内只付息，不偿还本金
        self.interest_cents = apply_rate(self.loan_principal_cents, self.loan_interest_rate)
        self.subsidy_by_tier = {t["tier"]: cr_to_cents(t["subsidy"])
                                for t in religion["policy_tiers"]}
        self.opening_faith = to_rate(religion["opening_faith"])

    # --- 税收 ---------------------------------------------------------

    def income_tax(self, gross_income_cents: int) -> int:
        return apply_rate(gross_income_cents, self.income_tax_rate)

    def profit_tax(self, profit_cents: int) -> int:
        """只对正利润征税，亏损主体的税基为零而不是负数。"""
        return apply_rate(max(0, profit_cents), self.profit_tax_rate)

    def property_tax(self, assessed_cents: int, faith: Fraction) -> int:
        """评估值 × 月率 × (1 − 宗教减免 × 期初信仰)。"""
        base = apply_rate(assessed_cents, self.property_tax_rate)
        return apply_rate(base, 1 - self.relief_rate * faith)

    # --- 教会 ---------------------------------------------------------

    def church_donation(self, church_cash_cents: int, faith: Fraction) -> int:
        """教会捐市政＝min(教会可用现金, 1200 × 信仰)；没钱不捐。"""
        return min(church_cash_cents, apply_rate(cr_to_cents(CHURCH_DONATION_BASE_CR), faith))

    def official_subsidy(self, policy_tier: int) -> int:
        """官方传教扶持由市政实际转给教会；中立档为 0。"""
        return self.subsidy_by_tier.get(policy_tier, 0)

    # --- 等式校验辅助 ---------------------------------------------------

    def municipal_surplus(self, income_cents: int, spending_cents: int) -> int:
        """财政结余＝实际收入−实际支出。贷款本金不记财政收入，偿还本金不入费用。"""
        return income_cents - spending_cents


def resident_gross_income(private_wages: int, public_wages: int, dividends: int,
                          net_property_income: int) -> int:
    """居民税前收入＝工资＋已收股息＋净房产收入。

    净房产收入是租金减维修的统计口径，家庭钱包仍按每笔实际收付记账，不再重复加一次。
    """
    return private_wages + public_wages + dividends + net_property_income


def net_property_income(residential_rent: int, commercial_rent: int, repair_spend: int) -> int:
    return residential_rent + commercial_rent - repair_spend