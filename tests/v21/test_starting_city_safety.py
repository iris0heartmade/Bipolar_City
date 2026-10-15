"""V2.1 阶段 13 测试：city_presets.py 5 城预设 + 16 项硬安全边界（§15.1）。

【2026-10-15】V2.1 §15 + §15.1 5 城预设 + 16 项硬边界。
"""
import pytest

from backend.systems.v21.city_presets import (
    CityPreset, CITY_PRESETS,
    SafetyBoundError, validate_preset, assert_preset_safe,
    assert_all_presets_safe, initial_employment_dict,
)


# ===== §15.2 5 城存在性 =====
class TestFivePresetsExist:
    def test_agricultural_town(self):
        assert 'agricultural_town' in CITY_PRESETS

    def test_industrial_city(self):
        assert 'industrial_city' in CITY_PRESETS

    def test_commercial_port(self):
        assert 'commercial_port' in CITY_PRESETS

    def test_magic_academy(self):
        assert 'magic_academy' in CITY_PRESETS

    def test_mining_border(self):
        assert 'mining_border' in CITY_PRESETS

    def test_exactly_5(self):
        assert len(CITY_PRESETS) == 5


# ===== §15.2 N/F/E 数据 =====
class TestPresetNF:
    def test_agricultural_town_2400_1320_1210(self):
        p = CITY_PRESETS['agricultural_town']
        assert p.N == 2400
        assert p.F == 1320
        assert p.E == 1210

    def test_industrial_city_3000_1650_1500(self):
        p = CITY_PRESETS['industrial_city']
        assert p.N == 3000
        assert p.F == 1650
        assert p.E == 1500

    def test_commercial_port_3500_1925_1760(self):
        p = CITY_PRESETS['commercial_port']
        assert p.N == 3500
        assert p.F == 1925
        assert p.E == 1760

    def test_magic_academy_2200_1210_1100(self):
        p = CITY_PRESETS['magic_academy']
        assert p.N == 2200
        assert p.F == 1210
        assert p.E == 1100

    def test_mining_border_2800_1540_1400(self):
        p = CITY_PRESETS['mining_border']
        assert p.N == 2800
        assert p.F == 1540
        assert p.E == 1400


# ===== §15.1 失业率 =====
class TestUnemploymentRate:
    def test_all_in_range(self):
        for p in CITY_PRESETS.values():
            ur = p.unemployment_rate
            assert 0.05 <= ur <= 0.12, f"{p.archetype} 失业率 {ur:.2%} 越界"


# ===== §15.1 H/N 住房比 =====
class TestHousingRatio:
    def test_all_in_range(self):
        for p in CITY_PRESETS.values():
            assert 1.15 <= p.H_over_N <= 1.40, f"{p.archetype} H/N {p.H_over_N:.3f} 越界"


# ===== §15.1 K_F/N 粮食承载 =====
class TestFoodCapacityRatio:
    def test_all_in_range(self):
        for p in CITY_PRESETS.values():
            assert 1.10 <= p.K_F_over_N <= 1.50, f"{p.archetype} K_F/N {p.K_F_over_N:.3f} 越界"


# ===== §15.1 安全 s =====
class TestSecurityS:
    def test_all_in_range(self):
        for p in CITY_PRESETS.values():
            assert 0.90 <= p.s <= 0.99, f"{p.archetype} s {p.s} 越界"


# ===== §15.1 Jop >= E =====
class TestJopGeqE:
    def test_all_pass(self):
        for p in CITY_PRESETS.values():
            assert p.Jop >= p.E, f"{p.archetype} Jop {p.Jop} < E {p.E}"


# ===== §15.4 7 行业就业合计 =====
class TestEmploymentByIndustry:
    def test_industrial_city_7_industries_sum_1500(self):
        emp = initial_employment_dict(CITY_PRESETS['industrial_city'])
        assert sum(emp.values()) == 1500

    def test_all_archetypes_sum_eq_E(self):
        for p in CITY_PRESETS.values():
            emp = initial_employment_dict(p)
            assert sum(emp.values()) == p.E, f"{p.archetype} 7 行业就业合计 {sum(emp.values())} ≠ E {p.E}"

    def test_emp_keys_7(self):
        for p in CITY_PRESETS.values():
            emp = initial_employment_dict(p)
            assert len(emp) == 7


# ===== §15.1 全部 5 城启动校验 =====
class TestAssertAllPresetsSafe:
    def test_no_error(self):
        """模块导入时已 assert，这里再调用一次确认。"""
        assert_all_presets_safe()      # 不抛错

    def test_all_pass(self):
        for p in CITY_PRESETS.values():
            errors = validate_preset(p)
            assert errors == [], f"{p.archetype} 校验失败: {errors}"


# ===== 边界越界拒绝 =====
class TestSafetyBoundRejection:
    def test_population_too_low(self):
        bad = CityPreset(
            name_cn='小', archetype='bad',
            N=100, F=50, E=45,     # 越界 N<1800
            H=200, K_F=200, Jop=50, s=0.95,
            K_over_N=2.0, v=0.5,
            employment_by_industry=(10, 10, 10, 5, 5, 0, 5),
        )
        errors = validate_preset(bad)
        assert any('人口' in e for e in errors)

    def test_K_over_N_too_low(self):
        bad = CityPreset(
            name_cn='小', archetype='bad',
            N=2400, F=1320, E=1210,
            H=3000, K_F=3300, Jop=1320, s=0.97,
            K_over_N=0.5,     # 越界 < 1.08
            v=0.62,
            employment_by_industry=CITY_PRESETS['agricultural_town'].employment_by_industry,
        )
        errors = validate_preset(bad)
        assert any('K/N' in e for e in errors)

    def test_s_too_high(self):
        bad = CityPreset(
            name_cn='小', archetype='bad',
            N=2400, F=1320, E=1210,
            H=3000, K_F=3300, Jop=1320, s=1.5,    # 越界 > 0.99
            K_over_N=1.10, v=0.62,
            employment_by_industry=CITY_PRESETS['agricultural_town'].employment_by_industry,
        )
        errors = validate_preset(bad)
        assert any('安全' in e for e in errors)


# ===== §15.1 assert_preset_safe 异常路径 =====
class TestAssertPresetSafeRaises:
    def test_raises_on_bad(self):
        bad = CityPreset(
            name_cn='X', archetype='bad',
            N=100, F=50, E=45,
            H=200, K_F=200, Jop=50, s=0.95,
            K_over_N=2.0, v=0.5,
            employment_by_industry=(10, 10, 10, 5, 5, 0, 5),
        )
        with pytest.raises(SafetyBoundError):
            assert_preset_safe(bad)
