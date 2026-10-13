"""P02 参考城的运行时经济实体。

金额一律整数分；比率以分数表达（通俗比喻：用「几分之几」记账，不写 0.08 这种易漂移的小数）。
六行业聚合：一个行业代表一组相似企业，初期共用一个企业账户；
逐店拆分在 P05 进行，届时每个 SectorAggregate 才持有独立账户。
"""
from dataclasses import dataclass
from fractions import Fraction

from backend.config import CALIBRATION, REFERENCE_CITY
from backend.model.accounts import (CHURCH, EXTERNAL, FIRM, HOUSEHOLD, LANDLORD,
                                   MUNICIPAL, AccountBook)

CENTS_PER_CR = 100


def cr_to_cents(amount_cr) -> int:
    """配置以 CR 计价，引擎内部统一为整数分。"""
    return int(round(amount_cr * CENTS_PER_CR))


def to_rate(value) -> Fraction:
    """把配置里的比率转成精确分数。Fraction(str(x)) 保证 0.08 读成 2/25 而非二进制近似。"""
    return Fraction(str(value))


def rate_from_text(text: str) -> Fraction:
    """解析 "16000/37312" 这类有理数配置，用于首月精确派息基准。"""
    numerator, denominator = text.split("/")
    return Fraction(int(numerator), int(denominator))


def apply_rate(cents: int, rate: Fraction) -> int:
    """按比率计算金额，四舍五入到分（0.5 向上，负数同理）。

    聚合舍入残差最多 1 分，必须归属调用方登记的舍入项，不得在月末凭空补一笔调整收入。
    """
    shifted = Fraction(cents) * rate + Fraction(1, 2)
    return shifted // 1


@dataclass
class SectorAggregate:
    """一个行业的聚合企业：多家相似企业共用一套账，便于先验证行业总量。"""

    sector_id: str
    name: str
    firm_count: int
    employees: int
    wage_per_employee: int
    revenue_cents: int
    input_cost_cents: int
    commercial_rent_cents: int
    depreciation_cents: int
    fees_cents: int

    @property
    def wage_bill_cents(self) -> int:
        return self.employees * self.wage_per_employee

    @property
    def profit_before_tax_cents(self) -> int:
        """税前利润＝收入−采购−工资−商租−折旧−规费。折旧是账面成本，不是又付一次现金。"""
        return (self.revenue_cents - self.input_cost_cents - self.wage_bill_cents
                - self.commercial_rent_cents - self.depreciation_cents - self.fees_cents)


class ReferenceCity:
    """由 P00 配置构造的 3000 人参考城，含企业、家庭、市政、教会与外部边界账户。"""

    GOVERNMENT = "gov"
    CHURCH = "church"
    HOUSEHOLDS = "households"
    LANDLORDS = "landlord"
    FIRMS = "firms"
    OUTSIDE = "world"

    def __init__(self, city_id: str = "reference_city", config=None, book=None):
        self.city_id = city_id
        self.config = config or REFERENCE_CITY
        self.calibration = CALIBRATION
        self.last_journal = []
        if book is None:
            self.book = AccountBook()
            self._open_accounts()
        else:
            # 读档路径：沿用存档中的账户簿，不重新开账户，避免覆盖已有余额
            self.book = book
        self.sectors = self._build_sectors()

    # --- 场景装配 -----------------------------------------------------

    def _open_accounts(self) -> None:
        opening = self.calibration["opening_balances"]
        firm_opening = sum(row["opening_cash"] for row in opening["firms"])
        self.book.open(self.GOVERNMENT, MUNICIPAL, "city_government", self.city_id,
                       cr_to_cents(opening["city_government"]))
        self.book.open(self.CHURCH, CHURCH, "city_church", self.city_id,
                       cr_to_cents(opening["church"]))
        self.book.open(self.HOUSEHOLDS, HOUSEHOLD, "all_households", self.city_id,
                       cr_to_cents(opening["households_total"]))
        # 房东是家庭的财产身份，不是独立人口；期初归零，现金全部来自本月实际租金与维修收付
        self.book.open(self.LANDLORDS, LANDLORD, "household_landlords", self.city_id, 0,
                       parent_account_id=self.HOUSEHOLDS)
        self.book.open(self.FIRMS, FIRM, "all_firms", self.city_id, cr_to_cents(firm_opening))
        self.book.open(self.OUTSIDE, EXTERNAL, "rest_of_world", None, 0)

    def _build_sectors(self) -> list[SectorAggregate]:
        """装配六个行业聚合实体。

        进口额只给总量、未给行业分配（校准项 A-06），因此逐行业不保存 import_cents，
        进口在聚合层作为一笔总额处理，不在这里猜测分配。
        """
        return [SectorAggregate(
            sector_id=row["id"],
            name=row["name"],
            firm_count=row["firms"],
            employees=row["employees"],
            wage_per_employee=cr_to_cents(row["wage_per_employee"]),
            revenue_cents=cr_to_cents(row["revenue"]),
            input_cost_cents=cr_to_cents(row["input_cost"]),
            commercial_rent_cents=cr_to_cents(row["commercial_rent"]),
            depreciation_cents=cr_to_cents(row["depreciation"]),
            fees_cents=cr_to_cents(row["fees"]),
        ) for row in self.config["firms"]["sectors"]]

    # --- 汇总口径 -----------------------------------------------------

    @property
    def private_wage_bill_cents(self) -> int:
        return sum(s.wage_bill_cents for s in self.sectors)

    @property
    def public_wage_bill_cents(self) -> int:
        return cr_to_cents(self.config["public_sector"]["wage_bill"])

    @property
    def total_wage_bill_cents(self) -> int:
        return self.private_wage_bill_cents + self.public_wage_bill_cents

    @property
    def revenue_cents(self) -> int:
        return sum(s.revenue_cents for s in self.sectors)

    @property
    def input_cost_cents(self) -> int:
        return sum(s.input_cost_cents for s in self.sectors)

    @property
    def commercial_rent_cents(self) -> int:
        return sum(s.commercial_rent_cents for s in self.sectors)

    @property
    def depreciation_cents(self) -> int:
        return sum(s.depreciation_cents for s in self.sectors)

    @property
    def fees_cents(self) -> int:
        return sum(s.fees_cents for s in self.sectors)

    @property
    def profit_before_tax_cents(self) -> int:
        return sum(s.profit_before_tax_cents for s in self.sectors)

    def account(self, name: str):
        return self.book[name]

    def residential_rent_cents(self) -> int:
        housing = self.config["housing"]
        return cr_to_cents(housing["rented_units"] * housing["rent_per_rented_unit"])

    def owner_repair_cents(self) -> int:
        return cr_to_cents(self.config["housing"]["owner_repair_spend"])

    def public_procurement_cents(self) -> int:
        return cr_to_cents(sum(row["spend"] for row in self.config["public_sector"]["procurement"]))

    def public_relief_cents(self) -> int:
        return cr_to_cents(self.config["fiscal"]["relief_payments"])