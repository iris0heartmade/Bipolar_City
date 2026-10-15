"""V2.1 阶段 11 宗教验收测试（2026-10-15）。

【验收清单】（V2.1 §9.5 + §9.6 + §9.7）
- [x] FaithfulRatio 校验
- [x] ReligionPolicy 5 档枚举
- [x] 五档政策参数严格匹配 §9.6 表
- [x] §9.5 好1 Grant = min(储备, 1200*f)
- [x] §9.5 好2 工资目标修正
- [x] §9.5 坏2 工时损失
- [x] §9.5 坏1 + §9.4 房产税折扣
- [x] §9.6 政策冷却 6 个月
- [x] §9.7 教会月度资金流（6 项）
"""
import pytest

from backend.systems.v21.religion import (
    DEFAULT_F, DEFAULT_GRANT_PER_FAITHFUL, DEFAULT_WAGE_TOLERANCE,
    DEFAULT_WORKTIME_F_COEF, DEFAULT_POLICY_COOLDOWN_MONTHS,
    FaithfulRatio, ReligionPolicy, ReligionPolicyParams,
    RELIGION_POLICY_PARAMS, PolicyCooldownState,
    ChurchCashFlow,
    get_policy_params,
    compute_grant_to_government_cents, compute_wage_tolerance_modifier,
    compute_worktime_ratio, compute_property_tax_exemption_factor,
    compute_church_cash_next,
)


# ===== A. 默认参数 + FaithfulRatio（3 项）=====

class TestDefaultParams:
    """V2.1 §9.5 + §9.6 默认参数。"""

    def test_default_faithful_ratio(self):
        """阶段 11：f = 0.55（§9.5 + §9.4 同步）。"""
        assert DEFAULT_F == 0.55

    def test_default_grant_per_faithful(self):
        """阶段 11：Grant_per_faithful = 1200（§9.5 好1）。"""
        assert DEFAULT_GRANT_PER_FAITHFUL == 1200.0

    def test_default_wage_tolerance(self):
        """阶段 11：wage_tolerance = 0.05（§9.5 好2 强度）。"""
        assert DEFAULT_WAGE_TOLERANCE == 0.05
        assert DEFAULT_WORKTIME_F_COEF == 0.04
        assert DEFAULT_POLICY_COOLDOWN_MONTHS == 6


class TestFaithfulRatio:
    """V2.1 §9.5 信徒比。"""

    def test_valid_ratio(self):
        """阶段 11：合法信徒比构造。"""
        fr = FaithfulRatio(f=0.55)
        assert fr.f == 0.55

    def test_default(self):
        """阶段 11：默认 0.55。"""
        fr = FaithfulRatio()
        assert fr.f == 0.55

    def test_invalid_out_of_range(self):
        """阶段 11：越界 [0, 1] 抛 ValueError。"""
        with pytest.raises(ValueError, match='信徒比越界'):
            FaithfulRatio(f=1.5)
        with pytest.raises(ValueError, match='信徒比越界'):
            FaithfulRatio(f=-0.1)


# ===== B. ReligionPolicy 五档 + 参数（5 项）=====

class TestReligionPolicy:
    """V2.1 §9.6 五档政策。"""

    def test_has_5_values(self):
        """阶段 11：ReligionPolicy 5 值。"""
        assert len(ReligionPolicy) == 5
        assert ReligionPolicy.STRICT_LIMIT.value == -2
        assert ReligionPolicy.LIMITED_LIMIT.value == -1
        assert ReligionPolicy.NEUTRAL.value == 0
        assert ReligionPolicy.ENCOURAGE.value == 1
        assert ReligionPolicy.SUPPORT.value == 2

    def test_strict_limit_params(self):
        """阶段 11：§9.6 严格限制：b=.0060, 补助=0。"""
        p = get_policy_params(ReligionPolicy.STRICT_LIMIT)
        assert p.b_exit_faith_per_month == 0.0060
        assert p.government_subsidy_cents == 0

    def test_limited_limit_params(self):
        """阶段 11：§9.6 有限限制：b=.0050, 补助=0。"""
        p = get_policy_params(ReligionPolicy.LIMITED_LIMIT)
        assert p.b_exit_faith_per_month == 0.0050
        assert p.government_subsidy_cents == 0

    def test_neutral_params(self):
        """阶段 11：§9.6 中立：全 0。"""
        p = get_policy_params(ReligionPolicy.NEUTRAL)
        assert p.b_exit_faith_per_month == 0.0
        assert p.government_subsidy_cents == 0

    def test_encourage_and_support_subsidies(self):
        """阶段 11：§9.6 鼓励/扶持 补助 = 300/600 CR。"""
        e = get_policy_params(ReligionPolicy.ENCOURAGE)
        s = get_policy_params(ReligionPolicy.SUPPORT)
        assert e.government_subsidy_cents == 300_00   # 300 CR
        assert s.government_subsidy_cents == 600_00   # 600 CR


# ===== C. §9.5 四效应（7 项）=====

class TestFourEffects:
    """V2.1 §9.5 教会四效应。"""

    def test_grant_basic(self):
        """阶段 11：好1 Grant = min(教会储备, 1200*f)。"""
        # f=0.55, ceiling = 1200*0.55 = 660 CR = 66,000 分
        grant = compute_grant_to_government_cents(
            church_disposable_cash_cents=100_000,
            faithful_ratio=0.55,
        )
        # min(100000, 66000) = 66000
        assert grant == 66_000

    def test_grant_capped_by_church_cash(self):
        """阶段 11：好1 Grant 不会超过教会实际可支配现金。"""
        # ceiling=66,000；教会只有 30,000 → min(30,000, 66,000) = 30,000
        grant = compute_grant_to_government_cents(
            church_disposable_cash_cents=30_000,
            faithful_ratio=0.55,
        )
        assert grant == 30_000

    def test_grant_zero_when_no_faithful(self):
        """阶段 11：f=0 → ceiling=0 → Grant=0。"""
        grant = compute_grant_to_government_cents(
            church_disposable_cash_cents=100_000,
            faithful_ratio=0.0,
        )
        assert grant == 0

    def test_wage_tolerance_modifier(self):
        """阶段 11：好2 modifier = 1 - 0.05*f。f=0.55 → 0.9725。"""
        mod = compute_wage_tolerance_modifier(faithful_ratio=0.55)
        assert abs(mod - 0.9725) < 1e-9

    def test_wage_tolerance_invalid_strength(self):
        """阶段 11：strength 越界 [0, 0.5] 抛 ValueError。"""
        with pytest.raises(ValueError, match='strength 越界'):
            compute_wage_tolerance_modifier(faithful_ratio=0.5, strength=0.6)

    def test_worktime_ratio(self):
        """阶段 11：坏2 worktime = (1 - 0.04*f) / (1 - 0.04*0.55)。
        f=0.55 → worktime = 1.0（基准）。
        f=1.0 → worktime = (0.96) / 0.978 = 0.9816...
        """
        assert abs(compute_worktime_ratio(0.55) - 1.0) < 1e-9
        assert abs(compute_worktime_ratio(1.0) - 0.96 / 0.978) < 1e-6

    def test_property_tax_exemption(self):
        """阶段 11：§9.5 坏1 + §9.4 房产税折扣 (1 - 0.15*f)。"""
        # f=0.55 → 1 - 0.15*0.55 = 0.9175
        assert abs(compute_property_tax_exemption_factor(0.55) - 0.9175) < 1e-9
        # f=1.0 → 0.85
        assert abs(compute_property_tax_exemption_factor(1.0) - 0.85) < 1e-9


# ===== D. §9.6 政策冷却（4 项）=====

class TestPolicyCooldown:
    """V2.1 §9.6 政策至少 6 个月才能再次调整。"""

    def test_initial_can_change(self):
        """阶段 11：初始状态可立即调整。"""
        st = PolicyCooldownState()
        assert st.can_change(current_month=0) is True

    def test_cooldown_blocks_immediate_change(self):
        """阶段 11：调整后未满 6 月不能再次调整。"""
        st = PolicyCooldownState(last_change_month=1, current_policy=ReligionPolicy.NEUTRAL)
        # current=3（仅 2 月后）
        assert st.can_change(3) is False
        # current=6（5 月后）
        assert st.can_change(6) is False
        # current=7（6 月后，可以）
        assert st.can_change(7) is True

    def test_apply_change_creates_new_state(self):
        """阶段 11：apply_change 返回新状态。"""
        st = PolicyCooldownState()
        new_st = st.apply_change(ReligionPolicy.ENCOURAGE, current_month=10)
        assert new_st.current_policy == ReligionPolicy.ENCOURAGE
        assert new_st.last_change_month == 10

    def test_apply_change_within_cooldown_raises(self):
        """阶段 11：冷却期内 apply_change 抛 ValueError。"""
        st = PolicyCooldownState(last_change_month=10, current_policy=ReligionPolicy.NEUTRAL)
        with pytest.raises(ValueError, match='政策冷却'):
            st.apply_change(ReligionPolicy.ENCOURAGE, current_month=12)


# ===== E. §9.7 教会月度资金流（5 项）=====

class TestChurchCashFlow:
    """V2.1 §9.7 ChurchCash_next 计算。"""

    def test_cash_next_basic(self):
        """阶段 11：§9.7 期初 + 收入 - 支出 = 下期。"""
        flow = ChurchCashFlow(
            church_cash_start_cents=20_000_00,        # 20,000 CR
            actual_offerings_cents=1_500_00,          # §14 宗教居民捐助 1,500
            real_religious_sales_cents=2_000_00,       # §14 CHU 营业额 2,000
            church_wages_cents=1_500_00,              # §14 CHU 工资 1,500
            church_operations_cents=300_00,            # 300
            real_grants_cents=660_00,                  # §14 教会捐赠 660
            other_paid_cost_cents=200_00,
        )
        # 20,000 + 1,500 + 2,000 - 1,500 - 300 - 660 - 200 = 20,840
        assert flow.next_cash_cents() == 20_840_00
        assert compute_church_cash_next(flow) == 20_840_00

    def test_cash_next_zero_when_all_zero(self):
        """阶段 11：全 0 → 下期 = 期初（默认 0）。"""
        flow = ChurchCashFlow(church_cash_start_cents=0)
        assert flow.next_cash_cents() == 0

    def test_cash_next_no_revenue(self):
        """阶段 11：仅支出（无捐助/销售）→ 下期 = 期初 - 支出。"""
        flow = ChurchCashFlow(
            church_cash_start_cents=1_000_00,
            church_wages_cents=500_00,
        )
        # 1,000 - 500 = 500
        assert flow.next_cash_cents() == 500_00

    def test_cash_next_no_expense(self):
        """阶段 11：仅收入（无支出）→ 下期 = 期初 + 收入。"""
        flow = ChurchCashFlow(
            church_cash_start_cents=1_000_00,
            actual_offerings_cents=500_00,
        )
        # 1,000 + 500 = 1,500
        assert flow.next_cash_cents() == 1_500_00

    def test_cash_next_can_be_negative(self):
        """阶段 11：支出 > 期初时下期 = 负（教会破产信号；§9.7 不阻断）。"""
        flow = ChurchCashFlow(
            church_cash_start_cents=100_00,
            church_wages_cents=500_00,
        )
        # 100 - 500 = -400
        assert flow.next_cash_cents() == -400_00
