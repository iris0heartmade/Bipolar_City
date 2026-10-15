"""V2.1 阶段 6 工资传导验收测试（2026-10-15）。

【验收清单】（V2.1 §6.3）
- [x] 默认参数 alpha=0.40, gamma=0.02, cap_up=+0.03, cap_down=-0.02
- [x] WagePolicyModifier 校验
- [x] IndustryWageState 字段 + w_0 默认 = wage_cents
- [x] compute_wstar_cents = w0 * I * modifier
- [x] compute_gW = clip(alpha*pi + gamma*backlog, cap_down, cap_up)
- [x] update_industry_wage 月度更新（4 步骤）
- [x] w_min 保护（不允许低于最低工资）
- [x] 工资变化上下限（涨不超过 +3%、跌不超过 -2%）
- [x] backlog 长期追赶（防固定通胀自动崩盘）
- [x] 批量更新 update_all_industry_wages
"""
import pytest

from backend.systems.v21.wages import (
    DEFAULT_WAGE_ALPHA, DEFAULT_WAGE_GAMMA,
    DEFAULT_WAGE_CAP_UP, DEFAULT_WAGE_CAP_DOWN,
    WagePolicyModifier, IndustryWageState, WageUpdateResult,
    compute_wstar_cents, compute_gW,
    update_industry_wage, update_all_industry_wages,
)


# ===== 测试夹具 =====

@pytest.fixture
def wage_state():
    """AGR 行业工资状态：100 CR/月 = 10,000 分，w_min = 50 CR = 5,000 分。"""
    return IndustryWageState(
        industry_id='AGR',
        wage_cents=10_000,           # 100 CR
        w_min_cents=5_000,           # 50 CR 最低
    )


# ===== A. 默认参数（2 项）=====

class TestDefaultParams:
    """V2.1 §6.3 默认参数。"""

    def test_alpha_is_0_40(self):
        """阶段 6：alpha = 0.40（短期 40% 跟随通胀）。"""
        assert DEFAULT_WAGE_ALPHA == 0.40

    def test_gamma_is_0_02(self):
        """阶段 6：gamma = 0.02（长期 2%/月 追赶）+ 上下月限 +3% / -2%。"""
        assert DEFAULT_WAGE_GAMMA == 0.02
        assert DEFAULT_WAGE_CAP_UP == 0.03
        assert DEFAULT_WAGE_CAP_DOWN == -0.02


# ===== B. WagePolicyModifier（2 项）=====

class TestWagePolicyModifier:
    """V2.1 §6.3 政策工资修正。"""

    def test_modifier_default_1_0(self):
        """阶段 6：默认 modifier = 1.0（无修正）。"""
        m = WagePolicyModifier(industry_id='AGR')
        assert m.modifier == 1.0

    def test_modifier_out_of_range_raises(self):
        """阶段 6：modifier 越界 [0.5, 2.0] 抛 ValueError。"""
        with pytest.raises(ValueError, match='工资修正 modifier 越界'):
            WagePolicyModifier(industry_id='AGR', modifier=3.0)


# ===== C. IndustryWageState（3 项）=====

class TestIndustryWageState:
    """V2.1 §6.3 行业工资状态。"""

    def test_initial_state(self, wage_state):
        """阶段 6：IndustryWageState 基础字段。"""
        assert wage_state.industry_id == 'AGR'
        assert wage_state.wage_cents == 10_000
        assert wage_state.w_min_cents == 5_000
        assert wage_state.policy_modifier == 1.0

    def test_w_0_defaults_to_wage_cents(self):
        """阶段 6：w_0 默认 = wage_cents（基准即初始）。"""
        s = IndustryWageState(industry_id='IND', wage_cents=9_000)
        assert s.w_0_cents == 9_000

    def test_negative_wage_raises(self):
        """阶段 6：负工资抛 ValueError。"""
        with pytest.raises(ValueError, match='工资不能为负'):
            IndustryWageState(industry_id='X', wage_cents=-1)


# ===== D. compute_wstar_cents（3 项）=====

class TestComputeWstar:
    """V2.1 §6.3 参考工资目标。"""

    def test_wstar_basic(self):
        """阶段 6：wstar = w_0 * I_t * modifier（向下取整到分）。"""
        # 10,000 分 × 1.05 × 1.0 = 10,500 分
        assert compute_wstar_cents(w_0_cents=10_000, world_I=1.05) == 10_500

    def test_wstar_with_policy_modifier(self):
        """阶段 6：政策 modifier 影响 wstar。"""
        # 10,000 × 1.0 × 1.1 = 11,000
        assert compute_wstar_cents(w_0_cents=10_000, world_I=1.0, policy_modifier=1.1) == 11_000

    def test_wstar_negative_w_0_raises(self):
        """阶段 6：负 w_0 抛 ValueError。"""
        with pytest.raises(ValueError, match='w_0 不能为负'):
            compute_wstar_cents(w_0_cents=-100, world_I=1.0)


# ===== E. compute_gW（5 项）=====

class TestComputeGW:
    """V2.1 §6.3 gW = clip(alpha*pi + gamma*backlog, cap_down, cap_up)。"""

    def test_gW_in_normal_range(self):
        """阶段 6：常态 gW 在 [cap_down, cap_up] 区间内。"""
        # pi=0.01, backlog=0 → alpha*pi + gamma*backlog = 0.004
        gW = compute_gW(inflation_pi=0.01, backlog=0.0)
        assert abs(gW - 0.004) < 1e-9

    def test_gW_capped_at_cap_up(self):
        """阶段 6：极大 pi + 正 backlog 被 cap_up=+0.03 截断。"""
        # pi=1.0, backlog=1.0 → alpha*pi + gamma*backlog = 0.4 + 0.02 = 0.42 → clip to 0.03
        gW = compute_gW(inflation_pi=1.0, backlog=1.0)
        assert gW == DEFAULT_WAGE_CAP_UP

    def test_gW_capped_at_cap_down(self):
        """阶段 6：负 backlog 被 cap_down=-0.02 截断（工资不能大跌超过 2%）。"""
        # pi=-0.5, backlog=-1.0 → -0.2 + (-0.02) = -0.22 → clip to -0.02
        gW = compute_gW(inflation_pi=-0.5, backlog=-1.0)
        assert gW == DEFAULT_WAGE_CAP_DOWN

    def test_gW_negative_inflation_decreases_wage(self):
        """阶段 6：通缩时工资缓慢下降。"""
        # pi=-0.01, backlog=0 → -0.004（小幅下降）
        gW = compute_gW(inflation_pi=-0.01, backlog=0.0)
        assert abs(gW - (-0.004)) < 1e-9

    def test_gW_positive_backlog_accelerates(self):
        """阶段 6：正 backlog 加速工资追赶（防长期固定通胀自动崩）。"""
        # pi=0.005, backlog=0.5 → 0.4*0.005 + 0.02*0.5 = 0.002 + 0.01 = 0.012
        gW = compute_gW(inflation_pi=0.005, backlog=0.5)
        assert abs(gW - 0.012) < 1e-9


# ===== F. update_industry_wage 月度更新（7 项）=====

class TestUpdateIndustryWage:
    """V2.1 §6.3 月度更新单行业工资。"""

    def test_zero_inflation_no_backlog_stable(self, wage_state):
        """阶段 6：零通胀 + backlog=0 时工资不变（gW = 0）。"""
        result = update_industry_wage(
            wage_state, world_I=1.0, inflation_pi=0.0,
        )
        assert result.w_old_cents == 10_000
        assert result.w_new_cents == 10_000
        assert result.gW == 0.0

    def test_positive_inflation_increases_wage(self, wage_state):
        """阶段 6：通胀时工资上涨（短期 40%）。"""
        # pi=0.01, backlog=0 → gW = 0.004
        # w_new = 10,000 * 1.004 = 10,040
        result = update_industry_wage(
            wage_state, world_I=1.0, inflation_pi=0.01,
        )
        assert result.w_new_cents == 10_040
        assert abs(result.gW - 0.004) < 1e-9

    def test_wage_respects_cap_up(self, wage_state):
        """阶段 6：极端通胀时工资上涨被 cap_up=+3% 限制。"""
        result = update_industry_wage(
            wage_state, world_I=1.0, inflation_pi=1.0,  # 100% 月通胀
        )
        # w_new = 10,000 * 1.03 = 10,300（cap_up 截断）
        assert result.w_new_cents == 10_300
        assert result.gW == DEFAULT_WAGE_CAP_UP

    def test_wage_respects_cap_down(self, wage_state):
        """阶段 6：极端 backlog 时工资下降被 cap_down=-2% 限制。"""
        result = update_industry_wage(
            wage_state, world_I=1.0, inflation_pi=-1.0,  # 极端通缩
        )
        # w_new = 10,000 * 0.98 = 9,800
        assert result.w_new_cents == 9_800
        assert result.gW == DEFAULT_WAGE_CAP_DOWN

    def test_w_min_protection(self, wage_state):
        """阶段 6：极端下降不会突破 w_min（V2.1 §6.3 w_i,t+1 = max(w_min_i, ...)）。"""
        # 设 w_min=9,900（接近当前工资）；pi=-1.0 → gW=-0.02 → 期望 w_new=9,800
        # 但 w_min=9,900 → w_new 实际 = max(9,900, 9,800) = 9,900
        wage_state.w_min_cents = 9_900
        result = update_industry_wage(
            wage_state, world_I=1.0, inflation_pi=-1.0,
        )
        assert result.w_new_cents == 9_900

    def test_state_is_mutated(self, wage_state):
        """阶段 6：update_industry_wage 原地修改 state.wage_cents。"""
        update_industry_wage(wage_state, world_I=1.0, inflation_pi=0.01)
        assert wage_state.wage_cents == 10_040  # 原地修改

    def test_long_term_catchup(self, wage_state):
        """阶段 6：长期 backlog 追赶（防固定通胀自动崩）。"""
        # w_0=10,000；I=2.0 → wstar=20,000；当前 w=10,000 → backlog=1.0
        # pi=0 → alpha*pi=0；gamma*backlog=0.02*1.0=0.02
        # gW = 0.02（未超 cap）
        result = update_industry_wage(
            wage_state, world_I=2.0, inflation_pi=0.0,
        )
        assert result.backlog == 1.0
        assert abs(result.gW - 0.02) < 1e-9
        # w_new = 10,000 * 1.02 = 10,200
        assert result.w_new_cents == 10_200


# ===== G. update_all_industry_wages 批量（3 项）=====

class TestBatchUpdate:
    """V2.1 §6.3 批量更新。"""

    def test_batch_updates_all_industries(self):
        """阶段 6：批量更新所有行业工资。"""
        states = {
            'AGR': IndustryWageState(industry_id='AGR', wage_cents=10_000),
            'IND': IndustryWageState(industry_id='IND', wage_cents=9_000),
        }
        results = update_all_industry_wages(states, world_I=1.0, inflation_pi=0.01)
        assert 'AGR' in results
        assert 'IND' in results
        assert isinstance(results['AGR'], WageUpdateResult)

    def test_batch_preserves_state(self):
        """阶段 6：批量更新后 state.wage_cents 同步更新。"""
        states = {
            'AGR': IndustryWageState(industry_id='AGR', wage_cents=10_000),
        }
        update_all_industry_wages(states, world_I=1.0, inflation_pi=0.01)
        assert states['AGR'].wage_cents == 10_040

    def test_batch_with_different_states(self):
        """阶段 6：批量更新时不同行业独立计算。"""
        states = {
            'AGR': IndustryWageState(industry_id='AGR', wage_cents=10_000, policy_modifier=1.0),
            'MAG': IndustryWageState(industry_id='MAG', wage_cents=9_000, policy_modifier=1.2),
        }
        results = update_all_industry_wages(states, world_I=1.0, inflation_pi=0.0)
        # MAG 的 wstar = 9000 * 1.0 * 1.2 = 10800；backlog = 10800/9000 - 1 = 0.2
        assert abs(results['MAG'].backlog - 0.2) < 1e-6
        # gW_MAG = 0.02 * 0.2 = 0.004
        assert abs(results['MAG'].gW - 0.004) < 1e-9
