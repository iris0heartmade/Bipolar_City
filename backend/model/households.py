"""P03 家庭群体：按收入层聚合，不逐人模拟。

群体人口而非个体模拟（通俗比喻：按「低保户/中产/富裕」三本账统计，
而不是给 3000 个人各开一本）。收入层是经济分组，不是新的身份；
房东等财产身份单独挂在家庭主体上，不额外增加人口。
"""
from dataclasses import dataclass, field
from typing import Optional

from backend.config import LABOR_CONSUMPTION, REFERENCE_CITY
from backend.core.errors import GameError
from backend.model.economy import cr_to_cents


@dataclass
class HouseholdCohort:
    """一个收入层的家庭群体。"""

    band_id: str
    name: str
    households: int
    population: int
    employed: int
    unemployed: int
    not_in_labour_force: int
    wage_income_cents: int
    asset_income_cents: int
    property_tax_share: float
    relief_share: float
    spend_ratio: float
    basket: dict[str, float]
    cash_cents: int = 0
    last_disposable_cents: int = 0

    # --- 人口恒等式 -----------------------------------------------------

    @property
    def labour_force(self) -> int:
        return self.employed + self.unemployed

    @property
    def population_check(self) -> bool:
        return self.employed + self.unemployed + self.not_in_labour_force == self.population

    @property
    def gross_income_cents(self) -> int:
        return self.wage_income_cents + self.asset_income_cents

    @property
    def average_wage_cents(self) -> float:
        """就业者人均工资。收入层是按人均富余程度划的，不是按总额划的。"""
        return self.wage_income_cents / self.employed if self.employed else 0.0

    @property
    def per_capita_income_cents(self) -> float:
        """人均收入，这是「收入层」高低的真正排序依据。

        高收入层人少，总额未必比低收入层多；但人均一定更高。
        用总额排序会把「高收入层」排到最后去，是这类分群最容易犯的错。
        """
        return self.gross_income_cents / self.population if self.population else 0.0

    @property
    def remaining_jobseekers(self) -> int:
        """尚未录用的可用劳动者；录用后必须相应减少，防止同一人被两家企业录用。"""
        return self.unemployed

    def validate(self) -> None:
        if self.labour_force > self.population:
            raise GameError(f"收入层 {self.band_id} 劳动力超过总人口")
        if not self.population_check:
            raise GameError(
                f"收入层 {self.band_id} 人口不闭合："
                f"{self.employed}+{self.unemployed}+{self.not_in_labour_force} != {self.population}")
        if abs(sum(self.basket.values()) - 1.0) > 1e-9:
            raise GameError(f"收入层 {self.band_id} 消费篮子占比合计不等于 1")
        if not 0 < self.spend_ratio <= 1:
            raise GameError(f"收入层 {self.band_id} 消费率必须落在 (0,1]")


class HouseholdRegistry:
    """三个收入层的集合，提供总量汇总与分群查询。"""

    def __init__(self, cohorts=None):
        self.cohorts = list(cohorts or build_cohorts())

    def __iter__(self):
        return iter(self.cohorts)

    def __getitem__(self, band_id: str) -> HouseholdCohort:
        return next(c for c in self.cohorts if c.band_id == band_id)

    @property
    def total_population(self) -> int:
        return sum(c.population for c in self.cohorts)

    @property
    def total_households(self) -> int:
        return sum(c.households for c in self.cohorts)

    @property
    def total_employed(self) -> int:
        return sum(c.employed for c in self.cohorts)

    @property
    def total_unemployed(self) -> int:
        return sum(c.unemployed for c in self.cohorts)

    @property
    def total_not_in_labour_force(self) -> int:
        return sum(c.not_in_labour_force for c in self.cohorts)

    @property
    def total_wage_income_cents(self) -> int:
        return sum(c.wage_income_cents for c in self.cohorts)

    @property
    def total_asset_income_cents(self) -> int:
        return sum(c.asset_income_cents for c in self.cohorts)

    @property
    def total_gross_income_cents(self) -> int:
        return self.total_wage_income_cents + self.total_asset_income_cents

    @property
    def total_cash_cents(self) -> int:
        return sum(c.cash_cents for c in self.cohorts)

    def population_identity_holds(self) -> bool:
        return (self.total_employed + self.total_unemployed + self.total_not_in_labour_force
                == self.total_population)


def build_cohorts(config=None) -> list[HouseholdCohort]:
    """从 P03 配置装配收入层，并逐层校验人口恒等式与篮子占比。"""
    config = config or LABOR_CONSUMPTION
    cohorts = [HouseholdCohort(
        band_id=row["id"],
        name=row["name"],
        households=row["households"],
        population=row["population"],
        employed=row["employed"],
        unemployed=row["unemployed"],
        not_in_labour_force=row["not_in_labour_force"],
        wage_income_cents=cr_to_cents(row["wage_income"]),
        asset_income_cents=cr_to_cents(row["asset_income"]),
        property_tax_share=row["property_tax_share"],
        relief_share=row["relief_share"],
        spend_ratio=row["spend_ratio"],
        basket=dict(row["basket"]),
    ) for row in config["income_bands"]]
    for cohort in cohorts:
        cohort.validate()
    return cohorts


def reconcile_with_reference_city(registry: HouseholdRegistry) -> dict:
    """把分群合计与 P00 参考城对照。

    人口类返回差值（应为 0）；收入类返回绝对额（单位分），因为原始输入没有给出分群后的收入，
    需要人工校验其加总是否等于参考城的全城工资与资产收入。
    """
    demo = REFERENCE_CITY["demographics"]
    return {
        "population_deltas": {
            "population": registry.total_population - demo["population"],
            "households": registry.total_households - demo["households"],
            "employed": registry.total_employed - demo["employed"],
            "unemployed": registry.total_unemployed - demo["unemployed"],
            "not_in_labour_force": (registry.total_not_in_labour_force
                                    - demo["not_in_labour_force"]),
        },
        "income_cents": {
            "wage": registry.total_wage_income_cents,
            "asset": registry.total_asset_income_cents,
            "gross": registry.total_gross_income_cents,
        },
    }