"""城市K值（Carrying Capacity）测试：
验证 compute_capacity() 在默认参数下行为正确，并确认迁徙上限与设计稿§5.2 一致。
"""
import pytest

from backend.config import REFERENCE_CITY
from backend.core.errors import GameError
from backend.model.parameters import EconomyParameters
from backend.systems import model_dynamics as dynamics
from backend.systems.model_sim import EconomySimulation


def _init_and_step(parameters=None):
    """触发一次 _step 并返回 sim，便于读取 sim.city_capacity。"""
    sim = EconomySimulation(months=1, parameters=parameters)
    sim.month = 1
    sim.step()
    return sim


# --- 1. 默认参数下 K 值就是人口上限 ------------------------------------

def test_capacity_at_default_matches_design_baseline():
    """默认参数下，首月 K 值 ≈ 3000，与基准人口相符。"""
    sim = _init_and_step()
    r = sim.history[-1]
    assert 2990 <= r.metrics['city_capacity'] <= 3030, (
        f"K 值 {r.metrics['city_capacity']} 偏离基准 3000")


def test_capacity_does_not_exceed_physical_housing_cap():
    """人口永远不超住房物理容量（避免「无家可归」）。"""
    sim = EconomySimulation(months=48)
    sim.run()
    p = sim.params
    physical_cap = sim.housing_units * p.get('housing.household_size')
    assert max(r.metrics['population'] for r in sim.history) <= physical_cap


# --- 2. 各类比例因子的方向与区间 ----------------------------------------

def test_housing_factor_scales_with_units_and_household_size():
    """住房承载 K_housing 随住房套数增加而上升。"""
    base = _init_and_step().city_capacity
    big = _init_and_step(parameters=EconomyParameters({
        'housing.units': 1600,    # 翻倍
    }))
    assert big.city_capacity['k_housing'] > base['k_housing']


def test_jobs_factor_scales_with_planned_jobs():
    """K_jobs 随私营岗位增多而上升。"""
    base = _init_and_step().city_capacity
    # 增加就业 500，失业/非劳动力同步调整保持恒等
    more = _init_and_step(parameters=EconomyParameters({
        'population.employed': 2000,
        'population.unemployed': 150,
        'population.out_of_labour_force': 850,   # 总人口仍 3000
        'population.labour_force': 2150,
    }))
    assert more.city_capacity['k_jobs'] >= base['k_jobs']


def test_resource_factor_does_not_go_to_zero_on_shortage():
    """资源项作调节乘子，单资源耗尽时 K_resource 收缩但不归零（保 5% 下限）。"""
    params = EconomyParameters()
    items = [{**item, 'produced': 0, 'imported': 0}
             for item in params.section('resources')['items']]
    sim = EconomySimulation(months=3, parameters=params, shocks=[
        {'start': 1, 'overrides': {'resources.items': items}},
    ])
    sim.run()
    last = sim.city_capacity
    assert last['k_resource'] >= 0.05, f"K_resource {last['k_resource']} 跌破下限"


def test_safety_exp_clamps_factor_below_one():
    """魔物风险提高 → K_safety 降低。"""
    base = _init_and_step().city_capacity
    danger = _init_and_step(parameters=EconomyParameters({
        'security.base_threat_intensity': 1.0,
        'city.k_safety_exp': 1.0,
    }))
    assert danger.city_capacity['k_safety'] < base['k_safety']


# --- 3. 迁徙上限与设计稿 §5.2 一致 -------------------------------------

def test_migration_hard_cap_default_is_zero():
    """migration_max_per_month 默认 0：关闭硬上限，由比率上限主导。"""
    params = EconomyParameters()
    assert params.get('population.migration_max_per_month') == 0


# --- 4. validate_identity_cross 不拦合法场景 ----------------------------

def test_identity_cross_allows_legitimate_tight_housing():
    """住房少人口多（迁移场景）应该允许，留给 K 值处理。"""
    p = EconomyParameters({
        'housing.units': 400,           # 容量 1600
        'population.start': 3000,
    })
    # 不抛错即可
    p.validate_identity_cross()


def test_identity_cross_rejects_extreme_population_explosion():
    """人口 100 倍于住房：应当拦。"""
    p = EconomyParameters()
    p.set('housing.units', 100, validate=False)
    p.set('population.start', 100000, validate=False)  # 远超 5 倍上限
    with pytest.raises(GameError):
        p.validate_identity_cross()


def test_identity_cross_rejects_labour_force_over_population():
    """劳动力超过总人口：硬违反。"""
    p = EconomyParameters()
    p.set('population.start', 1000, validate=False)
    p.set('population.labour_force', 1500, validate=False)
    p.set('population.employed', 1500, validate=False)
    p.set('population.unemployed', 0, validate=False)
    p.set('population.out_of_labour_force', -500, validate=False)
    with pytest.raises(GameError):
        p.validate_identity_cross()
