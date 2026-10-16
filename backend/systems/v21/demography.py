"""居民自然变化。年出生/死亡率按十二个月折算；弹性为游戏参数，非实证估计。"""
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

    配置为每千人每年，返回每人每月概率。
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
    return cbr / 12000, cdr / 12000, income_cr, consumption_ratio


def natural_change(city, params, total_household_consumption_cents):
    """按总居民数和当月自然增长率推算本月净增减（人，整数）。

    返回 (delta_people, cbr_monthly, cdr_monthly, income_cr, consumption_ratio)
    """
    cbr_m, cdr_m, income_cr, cons_ratio = compute_rates(city, params, total_household_consumption_cents)
    net = cbr_m - cdr_m
    population = sum(city.population.values())
    raw_delta = population * net + city.population_carry
    delta = math.trunc(raw_delta)
    city.population_carry = raw_delta - delta
    return delta, cbr_m, cdr_m, income_cr, cons_ratio
