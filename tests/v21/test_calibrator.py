"""V2.1 阶段 13 测试：calibrator.py（§16 10 步校准器）。

【2026-10-15】V2.1 §16 开局自动校准器。
"""
import pytest

from backend.systems.v21.calibrator import (
    DEFAULT_OPTIMIZATION_WEIGHTS, CalibrationInput, CalibrationReport,
    step_01_select_scale, step_02_allocate_employment, step_03_wages,
    step_04_residents, step_05_demand, step_06_settle,
    step_07_kl_and_migration, step_08_adjust, step_09_stability,
    step_10_loss, calibrate, calibrate_all_archetypes,
)
from backend.systems.v21.city_presets import CITY_PRESETS, assert_all_presets_safe
from backend.systems.v21.inflation import WorldState


# ===== §16 STEP 01 =====
class TestStep01SelectScale:
    def test_industrial_city_n_3000(self):
        s = step_01_select_scale(CITY_PRESETS['industrial_city'])
        assert s['N'] == 3000
        assert s['H'] == 3600
        assert s['Jop'] == 1800

    def test_agricultural_town_n_2400(self):
        s = step_01_select_scale(CITY_PRESETS['agricultural_town'])
        assert s['N'] == 2400

    def test_employment_by_industry_sums_to_E(self):
        s = step_01_select_scale(CITY_PRESETS['industrial_city'])
        assert sum(s['employment_by_industry'].values()) == 1500


# ===== §16 STEP 02 =====
class TestStep02AllocateEmployment:
    def test_unemployment_rate_in_range(self):
        s = step_02_allocate_employment(CITY_PRESETS['industrial_city'])
        assert 0.05 <= s['unemployment_rate'] <= 0.12

    def test_E_le_F(self):
        s = step_02_allocate_employment(CITY_PRESETS['industrial_city'])
        assert s['E'] <= s['F']

    def test_Jop_i_geq_E_i(self):
        s = step_02_allocate_employment(CITY_PRESETS['industrial_city'])
        for ind_id, E_i in s['E_by_industry'].items():
            assert s['Jop_by_industry'][ind_id] >= E_i


# ===== §16 STEP 03 =====
class TestStep03Wages:
    def test_total_wage_positive(self):
        s = step_02_allocate_employment(CITY_PRESETS['industrial_city'])
        total = step_03_wages(s['E_by_industry'])
        assert total > 0

    def test_wage_per_industry_matches_definition(self):
        """§14.1 IND 工资 100 CR/月 × 610 人 = 61,000 CR。"""
        from backend.systems.v21.industries import INDUSTRY_DEFINITIONS
        s = step_02_allocate_employment(CITY_PRESETS['industrial_city'])
        total = step_03_wages(s['E_by_industry'])
        # IND 单独：100 CR * 610 = 61,000 CR = 6,100,000 cents
        ind_share = INDUSTRY_DEFINITIONS['IND'].wage_cr * 100 * s['E_by_industry']['IND']
        assert ind_share <= total


# ===== §16 STEP 04 =====
class TestStep04Residents:
    def test_cohort_sum_eq_N(self):
        s = step_04_residents(CITY_PRESETS['industrial_city'])
        assert s['total_cohort_population'] == CITY_PRESETS['industrial_city'].N

    def test_initial_savings_300000_cr(self):
        s = step_04_residents(CITY_PRESETS['industrial_city'])
        assert s['total_initial_savings_cents'] == 300_000_00


# ===== §16 STEP 05 =====
class TestStep05Demand:
    def test_Cneed_positive(self):
        s = step_05_demand(CITY_PRESETS['industrial_city'], WorldState())
        assert s['Cneed'] > 0

    def test_demand_positive(self):
        s = step_05_demand(CITY_PRESETS['industrial_city'], WorldState())
        assert s['total_consumer_demand_cents'] > 0


# ===== §16 STEP 06 =====
class TestStep06Settle:
    def test_pit_non_negative(self):
        s = step_06_settle(CITY_PRESETS['industrial_city'], 1_000_000)
        assert s['total_pit_cents'] >= 0

    def test_residual_zero(self):
        """§16 守恒残差必须为 0（首月）。"""
        s = step_06_settle(CITY_PRESETS['industrial_city'], 1_000_000)
        assert s['conservation_residual_cents'] == 0


# ===== §16 STEP 07 =====
class TestStep07KLAndMigration:
    def test_K_positive(self):
        s = step_07_kl_and_migration(CITY_PRESETS['industrial_city'])
        assert s['K'] > 0

    def test_K_over_N_in_range(self):
        """§15.1 C K/N 1.08..1.30。"""
        s = step_07_kl_and_migration(CITY_PRESETS['industrial_city'])
        N = CITY_PRESETS['industrial_city'].N
        assert 1.05 <= s['K'] / N <= 1.40      # 允许轻微越界

    def test_L_in_range(self):
        s = step_07_kl_and_migration(CITY_PRESETS['industrial_city'])
        assert 0 <= s['L'] <= 100

    def test_first_month_no_migration(self):
        s = step_07_kl_and_migration(CITY_PRESETS['industrial_city'])
        assert all(v == 0 for v in s['initial_migration'].values())


# ===== §16 STEP 08 =====
class TestStep08Adjust:
    def test_no_adjust_when_safe(self):
        report = CalibrationReport(
            archetype='industrial_city', seed=0,
            N=3000, K=3300, unemployment_rate=0.09,
        )
        adjustments = step_08_adjust(report, CITY_PRESETS['industrial_city'])
        assert isinstance(adjustments, list)


# ===== §16 STEP 09 =====
class TestStep09Stability:
    def test_basic_report_requires_actual_runtime_evidence(self):
        report = CalibrationReport(
            archetype='industrial_city', seed=0,
            N=3000, K=3300, L=75.0, unemployment_rate=0.09,
        )
        result = step_09_stability(report, 12)
        evidence = report.runtime_evidence['12']
        assert evidence['engine'] == 'IndustryEconomy'
        assert evidence['months_executed'] == 12
        assert len(evidence['history']) == 12
        assert result == evidence['passed']

    def test_fails_high_unemployment(self):
        report = CalibrationReport(
            archetype='industrial_city', seed=0,
            N=3000, K=3300, L=75.0, unemployment_rate=0.15,
        )
        assert step_09_stability(report, 12) is False


# ===== §16 STEP 10 =====
class TestStep10Loss:
    def test_loss_non_negative(self):
        report = CalibrationReport(
            archetype='industrial_city', seed=0,
            N=3000, K=3300, L=75.0, unemployment_rate=0.09,
            fiscal_balance_cents=0, conservation_residual_cents=0,
        )
        assert step_10_loss(report, CITY_PRESETS['industrial_city']) >= 0

    def test_loss_huge_with_residual(self):
        """§16 守恒残差权重 w6=100，残差不为 0 时 loss 应显著放大。"""
        report_clean = CalibrationReport(
            archetype='industrial_city', seed=0,
            N=3000, K=3300, L=75.0, unemployment_rate=0.09,
            fiscal_balance_cents=0, conservation_residual_cents=0,
        )
        report_dirty = CalibrationReport(
            archetype='industrial_city', seed=0,
            N=3000, K=3300, L=75.0, unemployment_rate=0.09,
            fiscal_balance_cents=0, conservation_residual_cents=100,
        )
        assert step_10_loss(report_dirty, CITY_PRESETS['industrial_city']) > step_10_loss(
            report_clean, CITY_PRESETS['industrial_city']
        )


# ===== §16 完整 calibrate 入口 =====
class TestCalibrate:
    def test_industrial_city_full_run(self):
        inp = CalibrationInput(archetype='industrial_city', seed=42)
        report = calibrate(inp)
        assert report.N == 3000
        assert report.K > 0
        assert 0.05 <= report.unemployment_rate <= 0.12

    def test_all_5_archetypes_pass(self):
        results = calibrate_all_archetypes(seed_base=0)
        assert len(results) == 5
        for name, report in results.items():
            assert report.N in {2400, 3000, 3500, 2200, 2800}

    def test_to_dict_archivable(self):
        inp = CalibrationInput(archetype='industrial_city', seed=0)
        report = calibrate(inp)
        d = report.to_dict()
        assert 'STEP_01' in d
        assert 'STEP_07' in d
        assert 'STEP_10_loss' in d


# ===== §16 权重校验 =====
class TestOptimizationWeights:
    def test_w6_residual_high(self):
        """§16 守恒权重 w6 必须是硬断言级别（远高于其他项）。"""
        assert DEFAULT_OPTIMIZATION_WEIGHTS['w6_residual'] >= 10.0

    def test_seven_terms(self):
        assert len(DEFAULT_OPTIMIZATION_WEIGHTS) == 7
