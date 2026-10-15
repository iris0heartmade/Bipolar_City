"""V2.1 阶段 9 补 §5.4-5.5 生产与投入验收测试（2026-10-15）。

【验收清单】（V2.1 §5.4-5.5）
- [x] compute_eta_labor 指数 0.8（注意不是 0.2）
- [x] compute_eta_input 最小瓶颈
- [x] compute_eta_worktime 宗教
- [x] compute_eta 4 分量相乘 clip
- [x] compute_actual_output = Qbar * eta
- [x] compute_required_input 配方
"""
import pytest

from backend.systems.v21.production import (
    DEFAULT_ETA_LABOR_EXP,
    compute_eta_labor, compute_eta_input, compute_eta_safety,
    compute_eta_worktime, compute_eta,
    compute_actual_output, compute_required_input,
)


# ===== A. compute_eta_labor（5 项）=====

class TestEtaLabor:
    """V2.1 §5.4 eta_labor = (E/max(J,1))^0.8。"""

    def test_full_employment(self):
        """阶段 9 补：E=J → eta=1.0。"""
        assert compute_eta_labor(E_i=100, J_i=100) == 1.0

    def test_zero_employment(self):
        """阶段 9 补：E=0 → eta=0。"""
        assert compute_eta_labor(E_i=0, J_i=100) == 0.0

    def test_half_employment(self):
        """阶段 9 补：E=J/2 → (0.5)^0.8 ≈ 0.574。"""
        eta = compute_eta_labor(E_i=50, J_i=100)
        assert abs(eta - 0.5 ** 0.8) < 1e-9

    def test_over_employment_clipped(self):
        """阶段 9 补：E>J（不应发生但容错） → clip to 1。"""
        assert compute_eta_labor(E_i=200, J_i=100) == 1.0

    def test_zero_J_uses_eps(self):
        """阶段 9 补：J=0 用 eps 防除零（行业初创期）。"""
        eta = compute_eta_labor(E_i=0, J_i=0)
        assert eta == 0.0


# ===== B. compute_eta_input（3 项）=====

class TestEtaInput:
    """V2.1 §5.4 eta_input = min(1, min_g available/planned)。"""

    def test_no_required_inputs(self):
        """阶段 9 补：无数投入需求 → eta=1。"""
        assert compute_eta_input(available_inputs={}, planned_required_inputs={}) == 1.0

    def test_all_inputs_sufficient(self):
        """阶段 9 补：所有投入满足需求 → eta=1。"""
        eta = compute_eta_input(
            available_inputs={'ORE': 100, 'FUEL': 50},
            planned_required_inputs={'ORE': 80, 'FUEL': 40},
        )
        assert eta == 1.0

    def test_min_bottleneck(self):
        """阶段 9 补：取最紧瓶颈。"""
        # ORE: 100/100=1.0; FUEL: 30/100=0.3 → min=0.3
        eta = compute_eta_input(
            available_inputs={'ORE': 100, 'FUEL': 30},
            planned_required_inputs={'ORE': 100, 'FUEL': 100},
        )
        assert abs(eta - 0.3) < 1e-9


# ===== C. compute_eta_worktime（2 项）=====

class TestEtaWorktime:
    """V2.1 §5.4/§9.5 坏2 worktime = (1-0.04*f)/(1-0.04*0.55)。"""

    def test_at_reference_faithful(self):
        """阶段 9 补：f=0.55 → worktime=1.0。"""
        assert abs(compute_eta_worktime(0.55) - 1.0) < 1e-9

    def test_higher_faithful_reduces_worktime(self):
        """阶段 9 补：f=1.0 → worktime=0.96/0.978 < 1。"""
        eta = compute_eta_worktime(1.0)
        assert eta < 1.0
        assert abs(eta - 0.96 / 0.978) < 1e-6


# ===== D. compute_eta 整合（3 项）=====

class TestComputeEta:
    """V2.1 §5.4 eta = clip(eta_labor * eta_input * eta_safety * eta_worktime, 0, 1)。"""

    def test_default_all_one(self):
        """阶段 9 补：全满时 eta=1.0。"""
        result = compute_eta(E_i=100, J_i=100, available_inputs={}, planned_required_inputs={})
        assert result['eta'] == 1.0

    def test_zero_eta_labor(self):
        """阶段 9 补：eta_labor=0 → eta=0。"""
        result = compute_eta(E_i=0, J_i=100, available_inputs={}, planned_required_inputs={})
        assert result['eta'] == 0.0

    def test_components_returned(self):
        """阶段 9 补：返回 4 分量 dict。"""
        result = compute_eta(E_i=50, J_i=100, available_inputs={}, planned_required_inputs={})
        assert 'eta_labor' in result
        assert 'eta_input' in result
        assert 'eta_safety' in result
        assert 'eta_worktime' in result
        assert 'eta' in result


# ===== E. compute_actual_output（2 项）=====

class TestActualOutput:
    """V2.1 §5.4 Q_i,g = Qbar * eta。"""

    def test_full_eta(self):
        """阶段 9 补：eta=1 → Q=Qbar。"""
        assert compute_actual_output(Qbar_i_g=100, eta_i=1.0) == 100.0

    def test_half_eta(self):
        """阶段 9 补：eta=0.5 → Q=Qbar/2。"""
        assert compute_actual_output(Qbar_i_g=100, eta_i=0.5) == 50.0


# ===== F. compute_required_input（2 项）=====

class TestRequiredInput:
    """V2.1 §5.5 required = a * planned。"""

    def test_steel_recipe(self):
        """阶段 9 补：STEEL 配方 {ORE: 1.5} × planned 10 吨 → 15 吨 ORE。"""
        result = compute_required_input(recipe={'ORE': 1.5}, planned_output_i=10.0)
        assert result == {'ORE': 15.0}

    def test_multi_input_recipe(self):
        """阶段 9 补：GOODS 配方 {STEEL: 0.4, FUEL: 0.2} × 100 吨。"""
        result = compute_required_input(
            recipe={'STEEL': 0.4, 'FUEL': 0.2}, planned_output_i=100.0,
        )
        assert result == {'STEEL': 40.0, 'FUEL': 20.0}
