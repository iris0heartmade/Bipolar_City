"""V2.1 阶段 9 补：生产效率 eta + 实际产量 Q（§5.4 + §5.5）。

【2026-10-15】Bipole 城市聚合经济系统理论说明书 V2.1 §5.4-5.5 生产与投入。

【V2.1 §5.4 生产效率 4 分量】
- eta_labor_i = (E_i / max(J_i, 1))^0.8  ∈ [0, 1]              [注意指数 0.8，非 0.2]
- eta_input_i = min(1, min_g {available_input_g / planned_required_input_g})
  无投入需求时 = 1
- eta_safety_i ∈ [0, 1] 由城市已付费安全覆盖和风险得到
- eta_worktime_i = (1 - 0.04*f) / (1 - 0.04*0.55)  [§9.5 宗教坏2]
- eta_i = clip(eta_labor * eta_input * eta_safety * eta_worktime, 0, 1)

【V2.1 §5.4 实际产量】
- Q_i,g = Qbar_i,g * eta_i
- 所有 Q 按商品单位计量，不直接赋予"免费营业额"
- 产业技术进步可以升级 Qbar，不必额外叠加十几层任意 BUFF

【V2.1 §5.5 行业投入】
- required_i,g = a_i,g * planned_output_i（投入配方）
- 实际生产由可用原料限制
- 消耗应落在所属行业的原材料账户，售价付款从该行业经营资金扣除

【V2.1 阶段 9 补范围】
本模块承载：
- compute_eta_labor() §5.4（注意指数 0.8）
- compute_eta_input() §5.4
- compute_eta_safety() §5.4 占位
- compute_eta() §5.4 整合（4 分量相乘 clip）
- compute_actual_output() §5.4 Q_i,g = Qbar * eta
- compute_required_input() §5.5 required = a * planned
"""
from dataclasses import dataclass, field
from typing import Optional


# ===== V2.1 §5.4 默认参数 =====

# eta_labor 指数 = 0.8（§5.4）；注意与 §10.4 job_use_exp=0.2 不同
DEFAULT_ETA_LABOR_EXP = 0.8

# 宗教信徒比默认（与 §9.4/§9.5 同步）
DEFAULT_F = 0.55
DEFAULT_WORKTIME_F_COEF = 0.04


# ===== V2.1 §5.4 eta 4 分量 =====

def compute_eta_labor(
    E_i: int,
    J_i: int,
    exp: float = DEFAULT_ETA_LABOR_EXP,
    eps: float = 1e-9,
) -> float:
    """V2.1 §5.4 eta_labor_i = (E_i / max(J_i, 1))^0.8 ∈ [0, 1]。

    注意：指数是 0.8（V2.1 §5.4），不要与 §10.4 job_use_exp=0.2 混淆。
    """
    if E_i < 0:
        raise ValueError(f"E_i 不能为负：{E_i}")
    if J_i < 0:
        raise ValueError(f"J_i 不能为负：{J_i}")
    ratio = E_i / max(J_i, eps)
    return min(1.0, max(0.0, ratio ** exp))


def compute_eta_input(
    available_inputs: dict[str, float],
    planned_required_inputs: dict[str, float],
    eps: float = 1e-9,
) -> float:
    """V2.1 §5.4 eta_input_i = min(1, min_g {available_input_g / planned_required_input_g})。

    无投入需求（planned_required_inputs 为空）→ 返回 1.0。
    """
    if not planned_required_inputs:
        return 1.0
    ratios = []
    for good_id, required in planned_required_inputs.items():
        if required <= 0:
            continue
        available = available_inputs.get(good_id, 0.0)
        ratios.append(available / required)
    if not ratios:
        return 1.0
    return min(1.0, min(ratios))


def compute_eta_safety(
    s: float = 1.0,
) -> float:
    """V2.1 §5.4 eta_safety_i ∈ [0, 1] 由城市已付费安全覆盖和风险得到。

    占位：直接返回 s（§10.5 安全系数）。
    未来可扩展为"已投入安全服务人数 / 风险等级"的细化函数。
    """
    return min(1.0, max(0.0, s))


def compute_eta_worktime(
    faithful_ratio: float = DEFAULT_F,
    coef: float = DEFAULT_WORKTIME_F_COEF,
    ref_f: float = 0.55,
) -> float:
    """V2.1 §5.4/§9.5 坏2 worktime = (1 - 0.04*f) / (1 - 0.04*0.55)。"""
    return (1.0 - coef * faithful_ratio) / (1.0 - coef * ref_f)


def compute_eta(
    E_i: int,
    J_i: int,
    available_inputs: dict[str, float],
    planned_required_inputs: dict[str, float],
    *,
    s: float = 1.0,
    faithful_ratio: float = DEFAULT_F,
    eps: float = 1e-9,
) -> dict[str, float]:
    """V2.1 §5.4 eta_i = clip(eta_labor * eta_input * eta_safety * eta_worktime, 0, 1)。

    返回 4 分量 dict + 整合值（key='eta'）。
    """
    eta_l = compute_eta_labor(E_i, J_i, eps=eps)
    eta_i = compute_eta_input(available_inputs, planned_required_inputs, eps=eps)
    eta_s = compute_eta_safety(s)
    eta_w = compute_eta_worktime(faithful_ratio)
    eta_total = min(1.0, max(0.0, eta_l * eta_i * eta_s * eta_w))
    return {
        'eta_labor': eta_l,
        'eta_input': eta_i,
        'eta_safety': eta_s,
        'eta_worktime': eta_w,
        'eta': eta_total,
    }


# ===== V2.1 §5.4 实际产量 =====

def compute_actual_output(
    Qbar_i_g: float,
    eta_i: float,
    eps: float = 1e-9,
) -> float:
    """V2.1 §5.4 Q_i,g = Qbar_i,g * eta_i。

    Qbar 不可为负；eta 已 clip 到 [0, 1]。
    """
    if Qbar_i_g < 0:
        raise ValueError(f"Qbar 不能为负：{Qbar_i_g}")
    return max(0.0, Qbar_i_g * eta_i)


# ===== V2.1 §5.5 行业投入配方 =====

def compute_required_input(
    recipe: dict[str, float],       # a_i,g 单位产出需要的投入
    planned_output_i: float,
    eps: float = 1e-9,
) -> dict[str, float]:
    """V2.1 §5.5 required_i,g = a_i,g * planned_output_i。

    实际生产由可用原料限制（§5.4 eta_input）。
    """
    if planned_output_i < 0:
        raise ValueError(f"planned_output 不能为负：{planned_output_i}")
    return {good_id: a * planned_output_i for good_id, a in recipe.items()}
