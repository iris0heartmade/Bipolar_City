"""V2.1 阶段 13 测试：scenario_tests.py（§17.2 13 情景 A-M）。

【2026-10-15】V2.1 §17.2 强制情景测试。
"""
import pytest

from backend.systems.v21.scenario_tests import (
    ScenarioResult,
    scenario_A_single_city_baseline,
    scenario_B_profit_tax_hike,
    scenario_C_food_import_reduction,
    scenario_D_industrial_city_food_imported,
    scenario_E_industry_shutdown,
    scenario_F_trade_blocked,
    scenario_G_academy_buys_foreign_magic,
    scenario_H_gov_increases_debt,
    scenario_I_security_drops_to_085,
    scenario_J_equal_livability,
    scenario_K_price_up_quantity_down,
    scenario_L_continuous_inflation,
    scenario_M_close_one_city_trade,
    SCENARIO_REGISTRY,
    run_all_scenarios,
    all_scenarios_passed,
)
from backend.systems.v21.industries import tax_rate, IndustryId
from backend.systems.v21.inflation import WorldState
from backend.systems.v21.capacity import compute_s
from backend.systems.v21.migration import (
    CitySnapshot, DistanceMatrix, solve_multi_city_migration,
)


# ===== §17.2 A 单城常态 =====
class TestScenarioA:
    def test_baseline_passes(self):
        r = scenario_A_single_city_baseline()
        assert r.scenario == 'A'
        assert r.passed is True

    def test_invariants_checked(self):
        r = scenario_A_single_city_baseline()
        assert len(r.invariants_checked) >= 2


# ===== §17.2 B 提高利润税 =====
class TestScenarioB:
    def test_passes(self):
        r = scenario_B_profit_tax_hike()
        assert r.passed is True

    def test_tax_rates_correct(self):
        """V2.1 §2.1：A=15%, B=12%, C=8%, 无=0%, GOV/CHU=0%。"""
        assert tax_rate(IndustryId.IND.value) == 0.12     # IND B
        assert tax_rate(IndustryId.GOV.value) == 0.0      # GOV 0
        assert tax_rate(IndustryId.CHU.value) == 0.0      # CHU 0


# ===== §17.2 C 减少粮食进口 =====
class TestScenarioC:
    def test_passes(self):
        r = scenario_C_food_import_reduction()
        assert r.passed is True


# ===== §17.2 D 工业城市全靠外贸粮 =====
class TestScenarioD:
    def test_passes(self):
        r = scenario_D_industrial_city_food_imported()
        assert r.passed is True


# ===== §17.2 E 行业停工 =====
class TestScenarioE:
    def test_passes(self):
        r = scenario_E_industry_shutdown()
        assert r.passed is True


# ===== §17.2 F 货运受阻 =====
class TestScenarioF:
    def test_passes(self):
        r = scenario_F_trade_blocked()
        assert r.passed is True


# ===== §17.2 G 学院购买外国魔法 =====
class TestScenarioG:
    def test_passes(self):
        r = scenario_G_academy_buys_foreign_magic()
        assert r.passed is True


# ===== §17.2 H 政府举债 =====
class TestScenarioH:
    def test_passes(self):
        r = scenario_H_gov_increases_debt()
        assert r.passed is True


# ===== §17.2 I 安全降至 .85 =====
class TestScenarioI:
    def test_passes(self):
        r = scenario_I_security_drops_to_085()
        assert r.passed is True

    def test_threat_lowers_s(self):
        """§10.5 threat 应该降低 s。"""
        s_normal = compute_s(s_base=0.95, k_s=0.50, safety_persons=0, N=3000, threat=0.0)
        s_threat = compute_s(s_base=0.95, k_s=0.50, safety_persons=0, N=3000, threat=0.10)
        assert s_threat < s_normal
        assert abs(s_threat - 0.85) < 1e-9


# ===== §17.2 J 相同宜居度净迁 0 =====
class TestScenarioJ:
    def test_passes(self):
        r = scenario_J_equal_livability()
        assert r.passed is True

    def test_l_equal_two_cities_no_migration(self):
        """§11.2 严格验证：L 相同 → 净迁移 0。"""
        snapshots = [
            CitySnapshot(city_id='a', N=2000, L=70.0, K=2400, savings_cents=0, housing_capacity=3000),
            CitySnapshot(city_id='b', N=2000, L=70.0, K=2400, savings_cents=0, housing_capacity=3000),
        ]
        dm = DistanceMatrix()
        dm.set('a', 'b', 100.0)
        result = solve_multi_city_migration(snapshots, dm)
        assert result.delta_N.get('a', 0) == 0
        assert result.delta_N.get('b', 0) == 0

    def test_l_unequal_produces_migration(self):
        snapshots = [
            CitySnapshot(city_id='a', N=2000, L=80.0, K=2400, savings_cents=0, housing_capacity=3000),
            CitySnapshot(city_id='b', N=2000, L=60.0, K=2400, savings_cents=0, housing_capacity=3000),
        ]
        dm = DistanceMatrix()
        dm.set('a', 'b', 100.0)
        result = solve_multi_city_migration(snapshots, dm)
        # a L 更高 → 应有迁入
        assert result.delta_N.get('a', 0) > 0
        assert result.delta_N.get('b', 0) < 0


# ===== §17.2 K 涨价成交量降 =====
class TestScenarioK:
    def test_passes(self):
        r = scenario_K_price_up_quantity_down()
        assert r.passed is True


# ===== §17.2 L 连续通胀 =====
class TestScenarioL:
    def test_passes(self):
        r = scenario_L_continuous_inflation()
        assert r.passed is True

    def test_C_monotonic_increase(self):
        """§6.1 C_t+1 = C_t * (1 + pi)，严格单调上升。"""
        world = WorldState(inflation_pi=0.005)
        prev = world.global_C
        for _ in range(24):
            world.update_world_index()
            assert world.global_C > prev
            prev = world.global_C

    def test_C_not_collapse(self):
        world = WorldState(inflation_pi=0.01)
        for _ in range(120):
            world.update_world_index()
        assert world.global_C > 100.0       # §17.2 L 不能自动归零


# ===== §17.2 M 关闭一城贸易 =====
class TestScenarioM:
    def test_passes(self):
        r = scenario_M_close_one_city_trade()
        assert r.passed is True


# ===== §17.2 全 13 情景调度 =====
class TestAllScenarios:
    def test_registry_size_13(self):
        """§17.2 必须正好 13 个情景 A-M。"""
        assert len(SCENARIO_REGISTRY) == 13

    def test_registry_keys_cover_A_to_M(self):
        keys = set(SCENARIO_REGISTRY.keys())
        expected = set('ABCDEFGHIJKLM')
        assert keys == expected

    def test_all_scenarios_pass(self):
        all_pass, results = all_scenarios_passed()
        assert all_pass, f"失败情景: {[r.scenario for r in results if not r.passed]}"
        assert len(results) == 13

    def test_run_all_returns_results(self):
        results = run_all_scenarios()
        assert len(results) == 13
        for r in results:
            assert isinstance(r, ScenarioResult)
            assert r.scenario in 'ABCDEFGHIJKLM'


# ===== §17.2 结果数据类 =====
class TestScenarioResult:
    def test_frozen(self):
        r = scenario_A_single_city_baseline()
        with pytest.raises(Exception):
            r.passed = False        # frozen 应该抛错
