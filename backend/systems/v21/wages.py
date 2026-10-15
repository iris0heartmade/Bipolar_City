"""V2.1 阶段 6：行业工资传导（§6.3）。

【2026-10-15】Bipole 城市聚合经济系统理论说明书 V2.1 §6.3 工资传导。

【V2.1 §6.3 关键约束】
- 常态：alpha = 0.40，gamma = 0.02。
- 参考工资目标：wstar_i,t = w0_i * I_t * policy_sector_wage_modifier_i
- backlog_i,t = wstar_i,t / max(w_i,t, eps) - 1
- gW_i,t = clip(alpha * pi_t + gamma * backlog_i,t, -0.02, +0.03)
- w_i,t+1 = max(w_min_i, w_i,t * (1 + gW_i,t))
- 解释：通胀冲击当期工资仅传导约 40%；长期累积的真实工资缺口缓慢回补，
        避免数十个月后所有城市在不施政时必然因固定常态通胀进入不可逆贫困。
- 如作者未来坚持「工资永远只上涨通胀的 40%」，可把 gamma = 0，但必须重新做 120 月稳定性校准。
- 原手稿教会低工资容忍可作为 wstar 的小比例修正，禁止与另一工资惩罚重复计算。

【V2.1 阶段 6 范围】
本模块承载：
- 工资参数默认值（alpha/gamma/上下月限/w_min）
- IndustryWageState 数据类（每个行业的当前工资 + w_min + policy_modifier）
- update_industry_wage() 单行业月度更新
- update_all_industry_wages() 批量更新（返回新旧工资对照）
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from backend.systems.v21.industries import IndustryId


# ===== V2.1 §6.3 默认参数 =====

DEFAULT_WAGE_ALPHA = 0.40            # 短期跟随通胀比例（40%）
DEFAULT_WAGE_GAMMA = 0.02            # 长期追赶系数（2%/月）
DEFAULT_WAGE_CAP_UP = 0.03           # 上涨月限（+3%）
DEFAULT_WAGE_CAP_DOWN = -0.02        # 下降月限（-2%）


# ===== V2.1 §6.3 工资状态 =====

@dataclass(frozen=True)
class WagePolicyModifier:
    """V2.1 §6.3 行业政策工资修正。

    policy_sector_wage_modifier_i ∈ [0.8, 1.2]；
    政府可调高（鼓励）或调低（压制）特定行业工资增长。
    """
    industry_id: str
    modifier: float = 1.0

    def __post_init__(self):
        if self.modifier < 0.5 or self.modifier > 2.0:
            raise ValueError(f"工资修正 modifier 越界 [0.5, 2.0]：{self.modifier}")


@dataclass
class IndustryWageState:
    """V2.1 §6.3 单行业工资状态。

    字段：
    - industry_id：行业编号
    - wage_cents：当前月工资（整数分；1 CR = 100 分）
    - w_min_cents：最低工资（整数分）
    - policy_modifier：政策修正（默认 1.0）
    - w_0_cents：基准工资（用于 wstar 计算；初始 = wage_cents）
    """
    industry_id: str
    wage_cents: int
    w_min_cents: int = 0
    policy_modifier: float = 1.0
    w_0_cents: Optional[int] = None

    def __post_init__(self):
        if self.w_0_cents is None:
            self.w_0_cents = self.wage_cents
        if self.wage_cents < 0:
            raise ValueError(f"工资不能为负：{self.industry_id}")
        if self.w_min_cents < 0:
            raise ValueError(f"最低工资不能为负：{self.industry_id}")


@dataclass
class WageUpdateResult:
    """V2.1 §6.3 单行业工资更新结果。

    字段：
    - industry_id：行业编号
    - w_old_cents：更新前工资（整数分）
    - w_new_cents：更新后工资（整数分）
    - wstar_cents：本月参考工资目标（整数分；向下取整）
    - backlog：本月 backlog（实际/目标 - 1）
    - gW：本月工资增长率（已被 clip 限制）
    """
    industry_id: str
    w_old_cents: int
    w_new_cents: int
    wstar_cents: int
    backlog: float
    gW: float


# ===== V2.1 §6.3 月度更新函数 =====

def compute_wstar_cents(
    w_0_cents: int,
    world_I: float,
    policy_modifier: float = 1.0,
) -> int:
    """V2.1 §6.3 wstar_i,t = w0_i * I_t * policy_modifier_i（向下取整到分）。

    返回整数分（cents）。
    """
    if w_0_cents < 0:
        raise ValueError(f"w_0 不能为负：{w_0_cents}")
    return int(w_0_cents * world_I * policy_modifier)


def compute_gW(
    inflation_pi: float,
    backlog: float,
    alpha: float = DEFAULT_WAGE_ALPHA,
    gamma: float = DEFAULT_WAGE_GAMMA,
    cap_up: float = DEFAULT_WAGE_CAP_UP,
    cap_down: float = DEFAULT_WAGE_CAP_DOWN,
) -> float:
    """V2.1 §6.3 gW = clip(alpha*pi + gamma*backlog, cap_down, cap_up)。"""
    raw = alpha * inflation_pi + gamma * backlog
    return max(cap_down, min(cap_up, raw))


def update_industry_wage(
    state: IndustryWageState,
    *,
    world_I: float,
    inflation_pi: float,
    alpha: float = DEFAULT_WAGE_ALPHA,
    gamma: float = DEFAULT_WAGE_GAMMA,
    cap_up: float = DEFAULT_WAGE_CAP_UP,
    cap_down: float = DEFAULT_WAGE_CAP_DOWN,
    eps: float = 1e-9,
) -> WageUpdateResult:
    """V2.1 §6.3 月度更新单行业工资。

    步骤：
    1. wstar = w0 * I * modifier
    2. backlog = wstar / max(w, eps) - 1
    3. gW = clip(alpha*pi + gamma*backlog, cap_down, cap_up)
    4. w_new = max(w_min, w * (1 + gW))（向下取整到分）

    返回 WageUpdateResult。
    """
    w_old = state.wage_cents

    # 1. 参考工资目标
    wstar = compute_wstar_cents(
        w_0_cents=state.w_0_cents,
        world_I=world_I,
        policy_modifier=state.policy_modifier,
    )

    # 2. backlog
    backlog = wstar / max(w_old, eps) - 1.0

    # 3. gW
    gW = compute_gW(
        inflation_pi=inflation_pi,
        backlog=backlog,
        alpha=alpha, gamma=gamma,
        cap_up=cap_up, cap_down=cap_down,
    )

    # 4. 新工资（受 w_min 保护 + 向下取整到分）
    w_new = max(state.w_min_cents, int(w_old * (1.0 + gW)))
    if w_new < 0:
        w_new = 0  # 防止极端 backlog 负值导致负工资

    state.wage_cents = w_new  # mutate state

    return WageUpdateResult(
        industry_id=state.industry_id,
        w_old_cents=w_old,
        w_new_cents=w_new,
        wstar_cents=wstar,
        backlog=backlog,
        gW=gW,
    )


def update_all_industry_wages(
    states: dict[str, IndustryWageState],
    world_I: float,
    inflation_pi: float,
    **kwargs,
) -> dict[str, WageUpdateResult]:
    """批量更新所有行业工资。

    返回 {industry_id: WageUpdateResult}。
    """
    return {
        iid: update_industry_wage(
            state,
            world_I=world_I,
            inflation_pi=inflation_pi,
            **kwargs,
        )
        for iid, state in states.items()
    }
