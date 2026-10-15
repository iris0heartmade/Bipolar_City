"""V2.1 阶段 14：可劳动人口自然演化。

可劳动人口 = 显示人口。消费按家庭口径（含非劳动人口）放大。
出生率与死亡率是二级参数：基础值用世界银行 2022 年截面回归得到，
再按本期人均工资、相对生活成本、人均教育/服务消费水平做调整。

参考资料（World Bank, WDI 2022-2024）：
- ln(CBR/1000) = a + eta_cbr * ln(GNI)        eta_cbr ≈ -0.41, R² ≈ 0.73
- ln(CDR/1000) = a + d1 * ln(GNI) + d2 * old%  d1 ≈ -0.31, d2 ≈ 0.074, R² ≈ 0.58
- Preston：e0 ≈ a + 5.7 * ln(GNI)              β ≈ 5.7 年 / 单位 ln(收入)
"""
from dataclasses import dataclass
import math


@dataclass(frozen=True)
class DemographyParams:
    reference_gni_ppp_per_capita: float
    gni_cbr_elasticity: float
    gni_cdr_elasticity: float
    old_share_cdr_coef: float
    reference_cbr_per_thousand: float
    reference_cdr_per_thousand: float
    share_birth_consumption_response: float
    share_death_wage_response: float
    fertility_lag_months: int
    mortality_lag_months: int
    min_cbr_per_thousand: float
    max_cbr_per_thousand: float
    min_cdr_per_thousand: float
    max_cdr_per_thousand: float


def load_params(scenario):
    p = scenario['demographics']
    return DemographyParams(
        reference_gni_ppp_per_capita=p['reference_gni_ppp_per_capita'],
        gni_cbr_elasticity=p['gni_cbr_elasticity'],
        gni_cdr_elasticity=p['gni_cdr_elasticity'],
        old_share_cdr_coef=p['old_share_cdr_coef'],
        reference_cbr_per_thousand=p['reference_cbr_per_thousand'],
        reference_cdr_per_thousand=p['reference_cdr_per_thousand'],
        share_birth_consumption_response=p['share_birth_consumption_response'],
        share_death_wage_response=p['share_death_wage_response'],
        fertility_lag_months=p['fertility_lag_months'],
        mortality_lag_months=p['mortality_lag_months'],
        min_cbr_per_thousand=p['min_cbr_per_thousand'],
        max_cbr_per_thousand=p['max_cbr_per_thousand'],
        min_cdr_per_thousand=p['min_cdr_per_thousand'],
        max_cdr_per_thousand=p['max_cdr_per_thousand'],
    )


def reference_income(city):
    """人均月实发工资（按所有就业者分摊到全部显示人口）。"""
    workforce = max(1, sum(city.employment().values()))
    total_paid = sum(t['amount_cents'] for t in city.transactions if t['kind'] == 'wage')
    if total_paid == 0:
        return 0
    return (total_paid / workforce) / 100


def reference_consumption_per_person(city, total_household_consumption_cents):
    population = max(1, sum(city.population.values()))
    return (total_household_consumption_cents / max(1e-9, population)) / 100


def clamp(value, low, high):
    return min(high, max(low, value))


def compute_rates(city, params, total_household_consumption_cents):
    """根据本期人均收入、相对生活成本、人均消费水平推导 cbr/cdr。

    cbr, cdr 的单位是「每千人每月」。
    """
    income_cr = reference_income(city)
    cpi = max(1e-9, city.cpi)
    cost_cr = city.living_cost_cents / 100 / cpi
    # 阶段 14：gni_ratio 直接用人均月收入 / 参考月生活成本
    if income_cr <= 0 or cost_cr <= 0:
        gni_ratio = 1.0
    else:
        gni_ratio = income_cr / cost_cr
    gni_ratio = max(0.05, min(40.0, gni_ratio))
    per_capita_consumption = reference_consumption_per_person(city, total_household_consumption_cents)
    if per_capita_consumption <= 0 or cost_cr <= 0:
        consumption_ratio = 1.0
    else:
        consumption_ratio = per_capita_consumption / max(1e-9, cost_cr)

    base_cbr = params.reference_cbr_per_thousand * (gni_ratio ** params.gni_cbr_elasticity)
    base_cdr = params.reference_cdr_per_thousand * (gni_ratio ** params.gni_cdr_elasticity)

    birth_response = 1 + params.share_birth_consumption_response * (consumption_ratio - 1)
    wage_response = 1 + params.share_death_wage_response * (1 - min(1.5, max(0.4, gni_ratio)))
    cbr = clamp(base_cbr * birth_response, params.min_cbr_per_thousand, params.max_cbr_per_thousand)
    cdr = clamp(base_cdr * wage_response, params.min_cdr_per_thousand, params.max_cdr_per_thousand)
    return cbr / 1000, cdr / 1000, income_cr, consumption_ratio


def natural_change(city, params, total_household_consumption_cents):
    """按可劳动人口和当月自然增长率推算本月净增减（人，整数）。

    返回 (delta_people, cbr_monthly, cdr_monthly, income_cr, consumption_ratio)
    """
    cbr_m, cdr_m, income_cr, cons_ratio = compute_rates(city, params, total_household_consumption_cents)
    net = cbr_m - cdr_m
    population = sum(city.population.values())
    delta = int(round(population * net))
    return delta, cbr_m, cdr_m, income_cr, cons_ratio
