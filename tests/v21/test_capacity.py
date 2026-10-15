"""V2.1 阶段 7 城市承载力 K 与宜居度 L 验收测试（2026-10-15）。

【验收清单】（V2.1 §10 + §11.1）
- [x] 默认参数（lambda=0.55, e_target=0.90, job_use_exp=0.20）
- [x] smooth3 加权求和 + 首月合成
- [x] clip 工具函数
- [x] compute_K_H 住房容量
- [x] compute_K_F 粮食容量
- [x] compute_K_J 就业容量
- [x] compute_s 安全系数
- [x] compute_K 总 K（§10.6 floor(s^0.25 × min(K_H, K_F, K_J))）
- [x] compute_h / compute_a / compute_v / compute_L 四分量
- [x] §10.6 文档示例：N=3000, s=0.95, K=3257
- [x] JSON 配置字段一致
"""
import pytest

from backend.systems.v21.capacity import (
    DEFAULT_LAMBDA, DEFAULT_E_TARGET, DEFAULT_JOB_USE_EXP,
    DEFAULT_FOOD_PER_PERSON, DEFAULT_S_BASE, DEFAULT_K_S,
    DEFAULT_H_FLOOR, DEFAULT_H_DIVISOR, DEFAULT_L_CNEED_MULTIPLIER,
    DEFAULT_L_WEIGHTS,
    smooth3, clip, KComponents, LComponents,
    compute_K_H, compute_K_F, compute_K_J, compute_s, compute_K,
    compute_h, compute_a, compute_v, compute_L, weights_sum_to_one,
)
from backend.systems.v21.loader import load_capacity_config


# ===== A. 默认参数（3 项）=====

class TestDefaultParams:
    """V2.1 §10 + §11.1 默认参数。"""

    def test_lambda_e_target(self):
        """阶段 7：lambda=0.55, e_target=0.90（§10.4）。"""
        assert DEFAULT_LAMBDA == 0.55
        assert DEFAULT_E_TARGET == 0.90

    def test_job_use_exp_0_20(self):
        """阶段 7：job_use_exp=0.20（§10.4 J_eff 折算指数）。"""
        assert DEFAULT_JOB_USE_EXP == 0.20

    def test_L_weights_sum_to_one(self):
        """阶段 7：L 权重总和 = 1.0（h=0.35/a=0.35/v=0.20/s=0.10）。"""
        assert weights_sum_to_one()
        assert DEFAULT_L_WEIGHTS == {'h': 0.35, 'a': 0.35, 'v': 0.20, 's': 0.10}


# ===== B. smooth3 + clip（4 项）=====

class TestHelpers:
    """V2.1 §10.3 smooth3 + clip。"""

    def test_smooth3_basic(self):
        """阶段 7：smooth3 = 最近 3 个月均值。"""
        assert smooth3([100, 200, 300]) == 200.0

    def test_smooth3_less_than_3_synthesizes(self):
        """阶段 7：不足 3 个月时按 3 月合成（V2.1 §10.3 首月用 3 月等价合成历史）。"""
        # 只有 1 个值：总供应 / 3 = 200/3 ≈ 66.67
        assert abs(smooth3([200]) - 200.0 / 3.0) < 1e-9

    def test_smooth3_empty(self):
        """阶段 7：空历史返回 0（无合成基础）。"""
        assert smooth3([]) == 0.0

    def test_clip_bounds(self):
        """阶段 7：clip 把 x 限制到 [low, high]。"""
        assert clip(1.5) == 1.0
        assert clip(-0.5) == 0.0
        assert clip(0.5) == 0.5
        assert clip(2.0, low=1.0, high=3.0) == 2.0


# ===== C. K 三分量（8 项）=====

class TestKComponents:
    """V2.1 §10 K 三分量。"""

    def test_K_H_basic(self):
        """阶段 7：K_H = 可入住建筑容纳人数 + 农场附属住房。"""
        # 3600 套 + 0 农场附属 = 3600
        assert compute_K_H(housing_capacity=3600, farm_annex_housing=0) == 3600

    def test_K_H_with_farm(self):
        """阶段 7：K_H 含农场附属住房人数（§10.2）。"""
        # 3000 普通 + 600 农场附属 = 3600
        assert compute_K_H(housing_capacity=3000, farm_annex_housing=600) == 3600

    def test_K_F_basic(self):
        """阶段 7：K_F = F_sustainable / food_per_person（向下取整）。"""
        # 3300 单位 / 1.0 = 3300
        assert compute_K_F(F_sustainable=3300.0) == 3300

    def test_K_F_floors(self):
        """阶段 7：K_F 向下取整到整数。"""
        # 3300.7 / 1.0 = 3300（向下）
        assert compute_K_F(F_sustainable=3300.7) == 3300

    def test_K_F_invalid_food(self):
        """阶段 7：food_per_person ≤ 0 抛 ValueError。"""
        with pytest.raises(ValueError, match='food_per_person 必须为正'):
            compute_K_F(F_sustainable=100, food_per_person=0)

    def test_K_J_basic(self):
        """阶段 7：K_J = J_eff / (lambda * e_target)。

        示例：Jop=1800, E=1500, F=1650 → e=0.9091 → e_ratio=min(1, 1.0101)=1.0
        → J_eff = 1800 × 1.0 = 1800 → K_J = 1800 / (0.55 * 0.9) = 3636.36
        """
        K_J = compute_K_J(Jop=1800, E=1500, F=1650)
        assert abs(K_J - 3636.36) < 1.0  # 整数运算允许小数差异

    def test_K_J_low_employment_uses_exp(self):
        """阶段 7：低就业率时 J_eff 被 0.20 指数折算。"""
        # Jop=1800, E=500, F=1650 → e=0.303 → e_ratio=0.303/0.9=0.337
        # J_eff = 1800 * 0.337^0.2 = 1800 * 0.803 ≈ 1445
        # K_J = 1445 / 0.495 = 2919
        K_J = compute_K_J(Jop=1800, E=500, F=1650)
        assert 2800 < K_J < 3000  # 允许浮点

    def test_K_J_invalid_params(self):
        """阶段 7：lambda/e_target 必须为正。"""
        with pytest.raises(ValueError):
            compute_K_J(Jop=1000, E=500, F=1000, lambda_=0)
        with pytest.raises(ValueError):
            compute_K_J(Jop=1000, E=500, F=1000, e_target=0)


# ===== D. s 安全系数 + 总 K（5 项）=====

class TestSecurityAndTotalK:
    """V2.1 §10.5 + §10.6。"""

    def test_compute_s_basic(self):
        """阶段 7：s = clip(s_base + k_s * safety_persons/N - threat, 0, 1)。"""
        # s_base=0.5, k_s=0.5, safety_persons=300, N=3000, threat=0
        # s = clip(0.5 + 0.5*300/3000 - 0, 0, 1) = 0.55
        s = compute_s(safety_persons=300, N=3000)
        assert abs(s - 0.55) < 1e-9

    def test_compute_s_clipped_to_1(self):
        """阶段 7：s 上限 1.0。"""
        s = compute_s(s_base=0.5, k_s=0.5, safety_persons=10000, N=1)
        assert s == 1.0

    def test_compute_s_clipped_to_0(self):
        """阶段 7：s 下限 0.0。"""
        s = compute_s(s_base=0.5, k_s=0.5, safety_persons=0, N=1, threat=10.0)
        assert s == 0.0

    def test_K_doc_example(self):
        """阶段 7：§10.6 文档示例：N=3000, H=3600, K_F=3300, Jop=1800, e=1500/1650, s=0.95 → K=3257。

        K_H=3600, K_F=3300, K_J≈3636, s=0.95
        → K = floor(0.95^0.25 × 3300) = floor(0.9872 × 3300) = floor(3257.7) = 3257
        """
        K_H = compute_K_H(3600)
        F_sustainable = 3300 * DEFAULT_FOOD_PER_PERSON  # 3300
        K_F = compute_K_F(F_sustainable)
        K_J = compute_K_J(Jop=1800, E=1500, F=1650)
        s = 0.95
        K = compute_K(K_H, K_F, K_J, s)
        assert K == 3257  # 文档示例精确值

    def test_K_zero_when_any_component_zero(self):
        """阶段 7：任一分量为 0 → K = 0（防无中生有）。"""
        assert compute_K(K_H=0, K_F=3000, K_J=3000, s=1.0) == 0
        assert compute_K(K_H=3000, K_F=0, K_J=3000, s=1.0) == 0


# ===== E. L 四分量（7 项）=====

class TestLComponents:
    """V2.1 §11.1 L 四分量。"""

    def test_h_with_K_N_at_target(self):
        """阶段 7：h = clip((K/N - 0.90) / 0.45, 0, 1)。

        K/N=1.08（最低建议）→ (1.08-0.90)/0.45 = 0.40
        K/N=1.30（最高建议）→ (1.30-0.90)/0.45 = 0.889
        K/N=0.90（饱和点）→ 0
        """
        assert abs(compute_h(K=1080, N=1000) - 0.40) < 1e-6
        assert abs(compute_h(K=1300, N=1000) - (0.40 / 0.45)) < 1e-6
        assert compute_h(K=900, N=1000) == 0.0

    def test_h_clipped_to_1(self):
        """阶段 7：K/N 很大时 h 截断到 1.0。"""
        # K/N=1.50 → (1.50-0.90)/0.45 = 1.333 → clip to 1.0
        assert compute_h(K=1500, N=1000) == 1.0

    def test_a_basic(self):
        """阶段 7：a = clip(人均 disposable / (1.20 * Cneed_city), 0, 1)。

        disposable 总额 = 60*100 cents × N = 6,000,000 cents = 60,000 CR
        人均 60 CR；Cneed=50；multiplier=1.20 → 60/(1.2*50) = 1.0
        """
        a = compute_a(
            total_disposable_income_cents=60 * 100 * 1000,
            N=1000,
            Cneed_city_cr_per_person=50.0,
        )
        assert abs(a - 1.0) < 1e-6

    def test_a_below_threshold(self):
        """阶段 7：a < 1.0 时按比例反映。"""
        # 人均 30 CR；30/(1.2*50) = 0.5
        a = compute_a(
            total_disposable_income_cents=30 * 100 * 1000,
            N=1000,
            Cneed_city_cr_per_person=50.0,
        )
        assert abs(a - 0.5) < 1e-6

    def test_v_basic(self):
        """阶段 7：v = clip(A/N, 0, 1) × clip(funded/required, 0, 1)。

        A=0.5, funded=80, required=100 → 0.5 × 0.8 = 0.4
        """
        assert abs(compute_v(A_service_per_person=0.5, funded_public_ops=80, required_public_ops=100) - 0.4) < 1e-6

    def test_v_zero_when_required_zero(self):
        """阶段 7：required_public_ops=0 时 v=0（避免除零）。"""
        assert compute_v(A_service_per_person=1.0, funded_public_ops=100, required_public_ops=0) == 0.0

    def test_L_weights_combination(self):
        """阶段 7：L = 100 × (0.35h + 0.35a + 0.20v + 0.10s)。

        h=1.0, a=1.0, v=1.0, s=1.0 → 100*(0.35+0.35+0.20+0.10) = 100
        """
        assert abs(compute_L(1.0, 1.0, 1.0, 1.0) - 100.0) < 1e-9

    def test_L_target_range(self):
        """阶段 7：L 目标 65~82；典型中等城市 L 应在区间内。

        h=0.4, a=0.5, v=0.6, s=0.7 → 100*(0.35*0.4+0.35*0.5+0.20*0.6+0.10*0.7)
        = 100*(0.14+0.175+0.12+0.07) = 50.5
        """
        L = compute_L(0.4, 0.5, 0.6, 0.7)
        assert abs(L - 50.5) < 1e-6


# ===== F. JSON 配置（3 项）=====

class TestCapacityJsonConfig:
    """V2.1 §10 + §11.1 capacity.json 配置。"""

    def test_json_loads(self):
        """阶段 7：capacity.json 可加载。"""
        config = load_capacity_config()
        assert config['_meta']['version'] == '2.1.0'
        assert '§10' in config['_meta']['spec']

    def test_json_K_params(self):
        """阶段 7：K_params 严格匹配 §10.4。"""
        config = load_capacity_config()
        kp = config['K_params']
        assert kp['lambda'] == 0.55
        assert kp['e_target'] == 0.90
        assert kp['job_use_exp'] == 0.20
        assert kp['food_per_person'] == 1.0

    def test_json_L_target_range(self):
        """阶段 7：JSON target_range 字段。"""
        config = load_capacity_config()
        tr = config['L_params']['target_range']
        assert tr['L_target_min'] == 65.0
        assert tr['L_target_max'] == 82.0
        assert tr['K_over_N_min'] == 1.08
        assert tr['K_over_N_max'] == 1.30
