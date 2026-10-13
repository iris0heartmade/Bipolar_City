"""P02 月报与 GVA 计算。

四个口径互不等价，报表必须分开呈现（通俗比喻：卖出 100 万不等于
赚了 100 万，账户多 5 万和这座城创造了 80 万价值更是三回事）：
- 营业额 revenue：本期卖出的总金额，含成本部分
- 利润 profit：扣掉成本、工资、折旧、规费后的账面剩余
- 现金变化 cash_change：账户余额本期实际增减，只认真实收付
- GVA 增加值：这座城新创造的生产价值，扣掉重复的中间购销
"""
from dataclasses import asdict, dataclass

from backend.model.economy import ReferenceCity, cr_to_cents
from backend.systems.property_income import PropertyFlows
from backend.systems.production import CorporateLedger


@dataclass
class GvaBreakdown:
    private_firms_cents: int
    property_and_landlords_cents: int
    government_non_market_cents: int

    @property
    def total_cents(self) -> int:
        return (self.private_firms_cents + self.property_and_landlords_cents
                + self.government_non_market_cents)

    def to_dict(self):
        data = asdict(self)
        data["total_cents"] = self.total_cents
        return data


def gva_production_method(city: ReferenceCity, ledger: CorporateLedger,
                          property_flows: PropertyFlows) -> GvaBreakdown:
    """生产法：增加值＝劳动报酬＋折旧＋生产税＋营业盈余。

    政府非市场产出按公共工资计（GVA 只覆盖市场生产，此处为便于与基准对齐而单列）。
    """
    private = (ledger.wage_cents + ledger.depreciation_cents + ledger.profit_tax_cents
               + ledger.fees_cents + ledger.profit_after_tax_cents)
    return GvaBreakdown(
        private_firms_cents=private,
        property_and_landlords_cents=property_flows.net_property_income_cents,
        government_non_market_cents=city.public_wage_bill_cents,
    )


@dataclass
class MonthReport:
    month: int
    currency: str
    unit: str

    revenue_cents: int
    input_cost_cents: int
    profit_before_tax_cents: int
    profit_tax_cents: int
    profit_after_tax_cents: int
    dividend_cents: int
    retained_profit_cents: int
    depreciation_cents: int

    resident_gross_income_cents: int
    resident_disposable_income_cents: int
    resident_cash_change_cents: int
    income_tax_cents: int
    property_tax_cents: int
    average_wage_cents: int

    municipal_income_cents: int
    municipal_spending_cents: int
    municipal_surplus_cents: int

    church_reserve_change_cents: int

    city_cash_change_cents: int
    external_cash_change_cents: int

    gva_production_cents: int
    gva_production_breakdown: dict

    def to_dict(self):
        return asdict(self)


def average_wage(city: ReferenceCity) -> int:
    """劳动者平均工资＝全城工资／就业人数。与居民人均收入口径不同，不能互相替代。"""
    employed = city.config["demographics"]["employed"]
    return city.total_wage_bill_cents // employed


def disposable_income(gross_income: int, income_tax: int, property_tax: int,
                      relief: int) -> int:
    """居民可支配收入＝税前收入−所得税−房产税＋救济。"""
    return gross_income - income_tax - property_tax + relief


def resident_cash_change(disposable: int, consumption: int, residential_rent: int,
                         donation: int) -> int:
    return disposable - consumption - residential_rent - donation