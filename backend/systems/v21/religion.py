"""V2.1 阶段 11：宗教（§9.5 + §9.6 + §9.7）。

【2026-10-15】Bipole 城市聚合经济系统理论说明书 V2.1 §9.5-9.7 教会系统。

【V2.1 §9.5 教会四效应（只能保留这四种常规效果，不私自无限扩张）】
- 好1：教会向政府真实捐赠 Grant = min(可支配教会储备, 1200 * f)
- 好2：信徒对低工资容忍：工资长期目标的轻微修正（参考强度 0.05），不可重复减薪
- 坏1：宗教义务活动轻微占用劳作时间 worktime = (1 - 0.04 * f) / (1 - 0.04 * 0.55)
- 坏2：免税教会资产（房产税减免，§9.4 用 (1-0.15*f) 折扣近似）

【V2.1 §9.6 传教政策（沿用 V1.0，不直接控制信徒人数）】
政策         a新入信/月   b退出信/月   政府补助/月
严格限制 -2   .0010       .0060           0
有限限制 -1   .0006       .0050           0
中立      0   0          0               0
鼓励     +1   0          0               300
扶持     +2   0          0               600
- 政策至少 6 个月才能再次调整
- 宗教政策变更的稳定度短期冲击仅触发一次

【V2.1 §9.7 教会资金】
ChurchCash_next = ChurchCash + actual_offerings + real_religious_sales
                - church_wages - church_operations - real_grants - other_paid_cost
- 财政贷款本金不算收入；还本不算费用；真正的贸易利润只在政府持有并实际卖货时出现。

【V2.1 阶段 11 范围】
本模块承载：
- FaithfulRatio 数据类
- ReligionPolicy 5 档枚举 + 参数（a 新入信率 / b 退出率 / 补助）
- 四效应计算函数（grant_to_government / wage_tolerance / worktime_loss / property_tax_exemption）
- ChurchCashFlow 月度资金流计算（§9.7）
- 政策冷却 6 个月校验
"""
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Optional


# ===== V2.1 §9.5 + §9.6 默认参数 =====

DEFAULT_F = 0.55                  # §9.5 + §9.4 初始信徒比
DEFAULT_GRANT_PER_FAITHFUL = 1200.0  # §9.5 好1 Grant 上限
DEFAULT_WAGE_TOLERANCE = 0.05     # §9.5 好2 工资目标修正强度
DEFAULT_WORKTIME_F_COEF = 0.04    # §9.5 坏2 工时损失系数
DEFAULT_POLICY_COOLDOWN_MONTHS = 6  # §9.6 政策冷却期


# ===== V2.1 §9.5 信徒比 =====

@dataclass(frozen=True)
class FaithfulRatio:
    """V2.1 §9.5 信徒比 f ∈ [0, 1]。"""
    f: float = DEFAULT_F

    def __post_init__(self):
        if not 0.0 <= self.f <= 1.0:
            raise ValueError(f"信徒比越界 [0, 1]：{self.f}")


# ===== V2.1 §9.6 五档政策 =====

class ReligionPolicy(IntEnum):
    """V2.1 §9.6 五档宗教政策（沿用 V1.0）。"""
    STRICT_LIMIT = -2          # 严格限制
    LIMITED_LIMIT = -1         # 有限限制
    NEUTRAL = 0                # 中立
    ENCOURAGE = 1              # 鼓励
    SUPPORT = 2                # 扶持


@dataclass(frozen=True)
class ReligionPolicyParams:
    """V2.1 §9.6 单档政策参数（a 新入信率 / b 退出率 / 政府补助）。"""
    a_new_faith_per_month: float     # 新入信比例
    b_exit_faith_per_month: float     # 退出信比例
    government_subsidy_cents: int     # 政府补助（整数分）


# §9.6 五档政策参数严格匹配规范
RELIGION_POLICY_PARAMS: dict[ReligionPolicy, ReligionPolicyParams] = {
    ReligionPolicy.STRICT_LIMIT: ReligionPolicyParams(
        a_new_faith_per_month=0.0,
        b_exit_faith_per_month=0.0060,
        government_subsidy_cents=0,
    ),
    ReligionPolicy.LIMITED_LIMIT: ReligionPolicyParams(
        a_new_faith_per_month=0.0,
        b_exit_faith_per_month=0.0050,
        government_subsidy_cents=0,
    ),
    ReligionPolicy.NEUTRAL: ReligionPolicyParams(
        a_new_faith_per_month=0.0,
        b_exit_faith_per_month=0.0,
        government_subsidy_cents=0,
    ),
    ReligionPolicy.ENCOURAGE: ReligionPolicyParams(
        a_new_faith_per_month=0.0,
        b_exit_faith_per_month=0.0,
        government_subsidy_cents=300_00,   # 300 CR
    ),
    ReligionPolicy.SUPPORT: ReligionPolicyParams(
        a_new_faith_per_month=0.0,
        b_exit_faith_per_month=0.0,
        government_subsidy_cents=600_00,   # 600 CR
    ),
}


def get_policy_params(policy: ReligionPolicy) -> ReligionPolicyParams:
    """V2.1 §9.6 查表。"""
    return RELIGION_POLICY_PARAMS[policy]


# ===== V2.1 §9.5 四效应计算 =====

def compute_grant_to_government_cents(
    *,
    church_disposable_cash_cents: int,
    faithful_ratio: float,
    grant_per_faithful: float = DEFAULT_GRANT_PER_FAITHFUL,
) -> int:
    """V2.1 §9.5 好1 Grant = min(可支配教会储备, 1200 * f)。

    返回教会当月向政府的真实捐赠（整数分）。
    """
    ceiling = int(grant_per_faithful * faithful_ratio * 100)  # 1200 * f * 100 cents
    return min(church_disposable_cash_cents, ceiling)


def compute_wage_tolerance_modifier(
    faithful_ratio: float,
    strength: float = DEFAULT_WAGE_TOLERANCE,
) -> float:
    """V2.1 §9.5 好2 信徒对低工资容忍：工资目标的小比例修正。

    返回 policy_sector_wage_modifier 的修正项（用于 IndustryWageState.policy_modifier）：
    modifier = 1.0 - strength * faithful_ratio

    即 f=0.55 → modifier = 1 - 0.05*0.55 = 0.9725（工资目标下调 2.75%）。
    """
    if not 0.0 <= strength <= 0.5:
        raise ValueError(f"strength 越界 [0, 0.5]：{strength}")
    return 1.0 - strength * faithful_ratio


def compute_worktime_ratio(
    faithful_ratio: float,
    coef: float = DEFAULT_WORKTIME_F_COEF,
    ref_f: float = 0.55,
) -> float:
    """V2.1 §9.5 坏2 工时损失 worktime = (1 - 0.04 * f) / (1 - 0.04 * 0.55)。

    用于 §5.4 eta_worktime_i 公式；基准 f=0.55 → worktime=1.0。
    """
    return (1.0 - coef * faithful_ratio) / (1.0 - coef * ref_f)


def compute_property_tax_exemption_factor(faithful_ratio: float) -> float:
    """V2.1 §9.5 坏1 + §9.4 房产税宗教减免 (1 - 0.15 * f)。

    返回折扣因子（用于 PropertyTaxInput.faithful_ratio）。
    """
    return 1.0 - 0.15 * faithful_ratio


# ===== V2.1 §9.6 政策冷却 =====

@dataclass(frozen=True)
class PolicyCooldownState:
    """V2.1 §9.6 政策冷却状态：距上次调整 N 月。"""
    last_change_month: Optional[int] = None    # None = 从未调整过
    current_policy: ReligionPolicy = ReligionPolicy.NEUTRAL

    def months_since_change(self, current_month: int) -> Optional[int]:
        """距上次调整的月数；None 表示从未调整过。"""
        if self.last_change_month is None:
            return None
        return current_month - self.last_change_month

    def can_change(self, current_month: int) -> bool:
        """V2.1 §9.6 政策至少 6 个月才能再次调整（初始/冷却已过 可调整）。"""
        if self.last_change_month is None:
            return True   # 初始状态可立即调整
        return (current_month - self.last_change_month) >= DEFAULT_POLICY_COOLDOWN_MONTHS

    def apply_change(self, new_policy: ReligionPolicy, current_month: int) -> 'PolicyCooldownState':
        """应用新政策；返回新状态。"""
        if not self.can_change(current_month):
            elapsed = self.months_since_change(current_month)
            raise ValueError(
                f"V2.1 §9.6 政策冷却：距上次调整仅 {elapsed} 月，"
                f"需 ≥ {DEFAULT_POLICY_COOLDOWN_MONTHS} 月"
            )
        return PolicyCooldownState(
            last_change_month=current_month,
            current_policy=new_policy,
        )


# ===== V2.1 §9.7 教会月度资金流 =====

@dataclass
class ChurchCashFlow:
    """V2.1 §9.7 教会月度资金流（5 项变化）。"""
    church_cash_start_cents: int       # 期初现金
    actual_offerings_cents: int = 0    # 实际捐助
    real_religious_sales_cents: int = 0  # 实际宗教商品销售
    church_wages_cents: int = 0        # 教会工资支出
    church_operations_cents: int = 0   # 教会运营支出
    real_grants_cents: int = 0         # 实际政府捐赠/资助支出
    other_paid_cost_cents: int = 0     # 其他支出

    def next_cash_cents(self) -> int:
        """V2.1 §9.7 ChurchCash_next = start + offerings + sales - wages - ops - grants - other。"""
        return (
            self.church_cash_start_cents
            + self.actual_offerings_cents
            + self.real_religious_sales_cents
            - self.church_wages_cents
            - self.church_operations_cents
            - self.real_grants_cents
            - self.other_paid_cost_cents
        )


def compute_church_cash_next(flow: ChurchCashFlow) -> int:
    """V2.1 §9.7 短工具：ChurchCash_next 计算。"""
    return flow.next_cash_cents()
