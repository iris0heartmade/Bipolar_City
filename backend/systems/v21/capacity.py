"""V2.1 阶段 7：城市承载力 K 与宜居度 L（§10 + §11.1）。

【2026-10-15】Bipole 城市聚合经济系统理论说明书 V2.1 §10 城市承载力 K
+ §11.1 宜居度 L。

【V2.1 §10 K 关键公式】
- K = floor(s^0.25 * min(K_H, K_F, K_J))                              §10.6
- K_H = H_total_people = 可入住建筑容纳人数 + 农场附属住房人数          §10.2
- K_F = F_sustainable / food_per_person                                §10.3
  F_sustainable = smooth3(本地正常月粮食产出 + 可履约常规进口
                        - 已承诺出口 - 非居民工业必需消耗 - 常态损耗
                        + 有计划且可持续的正常库存释放)
  smooth3(x) = 最近 3 个月有效供应的平均；首月用 3 月等价合成历史。
  基准每人每月 1 食物单位。
- K_J = J_eff / (lambda * e_target)                                    §10.4
  J_eff = Jop * [min(1, e/e_target)]^0.20    e_target=0.90, lambda=0.55
  e = E / max(F, 1)
- s = clip(s_base + k_s * 已投入安全人数 / max(N,1) - threat, 0, 1)     §10.5

【示例 §10.6】N=3000, H=3600, K_F=3300, Jop=1800, e=1500/1650=0.9091, s=0.95
→ K_J = 1800 / (0.55 * 0.9) = 3636.36
→ K = floor(0.95^0.25 * 3300) = 3257

【V2.1 §11.1 L 关键公式】
- L = 100 * (0.35h + 0.35a + 0.20v + 0.10s)                            §11.1
- h = clip((K / max(N,1) - 0.90) / 0.45, 0, 1)                          [容量余裕]
- a = clip(total_disposable_income / max(N,1) / (1.20 * Cneed_city), 0, 1)
- v = clip(A_service / max(N,1), 0, 1) * clip(funded_public_ops / required_public_ops, 0, 1)
- s = security_coefficient                                             [安全]
- K/N 建议 1.08~1.30；L 目标 65~82

【V2.1 阶段 7 范围】
本模块承载：
- 默认参数（lambda=0.55, e_target=0.90, job_use_exp=0.20, food_per_person=1.0）
- KComponents 数据类（K_H/K_F/K_J/s）
- compute_K_H / compute_K_F (smooth3) / compute_K_J
- compute_K 集成（§10.6）
- LComponents 数据类（h/a/v/s）
- compute_L（§11.1）
- 双重计量预防：s 在 K 与 L 中均出现，但 L 中权重仅 .10
"""
from dataclasses import dataclass, field
from typing import Optional


# ===== V2.1 §10.4 + §11.1 默认参数 =====

# §10.4 有效就业
DEFAULT_LAMBDA = 0.55                # 劳动参与率（劳动力/总人口）
DEFAULT_E_TARGET = 0.90              # 目标就业率
DEFAULT_JOB_USE_EXP = 0.20           # J_eff 折算指数

# §10.3 粮食
DEFAULT_FOOD_PER_PERSON = 1.0        # 基准每人每月 1 食物单位

# §10.5 安全
DEFAULT_S_BASE = 0.50                # 基础安全系数
DEFAULT_K_S = 0.50                   # 安全投入边际系数

# §11.1 L 参数
DEFAULT_H_FLOOR = 0.90               # h 分母起点（容量余裕公式）
DEFAULT_H_DIVISOR = 0.45             # h 归一化分母
DEFAULT_L_CNEED_MULTIPLIER = 1.20    # a 分母 Cneed 乘数
DEFAULT_L_WEIGHTS = {                # L 四维权重
    'h': 0.35, 'a': 0.35, 'v': 0.20, 's': 0.10,
}


# ===== 工具函数 =====

def smooth3(values: list[float]) -> float:
    """V2.1 §10.3 smooth3(x) = 最近 3 个月有效供应的平均。

    首月用 3 月等价合成历史：当 values 长度 < 3 时，结果 = sum(values) / 3
    （把现有值当作整 3 月供应的均匀分布，不以空历史当 0）。
    """
    if not values:
        return 0.0
    total = sum(values)
    # 取最近 3 个；如果不足 3 个，把总数除以 3（按整 3 月合成）
    if len(values) >= 3:
        recent = values[-3:]
        return sum(recent) / 3.0
    return total / 3.0


def clip(x: float, low: float = 0.0, high: float = 1.0) -> float:
    """clip(x, low, high)：把 x 限制到 [low, high]。"""
    return max(low, min(high, x))


# ===== V2.1 §10 K 三分量 =====

@dataclass(frozen=True)
class KComponents:
    """V2.1 §10 K 的三个分量与安全系数。"""
    K_H: int                     # §10.2 住房人口容量
    K_F: int                     # §10.3 粮食人口容量
    K_J: float                   # §10.4 就业人口容量（连续）
    s: float                     # §10.5 安全系数 [0, 1]


def compute_K_H(housing_capacity: int, farm_annex_housing: int = 0) -> int:
    """V2.1 §10.2 K_H = 可入住建筑容纳人数 + 农场附属住房人数。

    农场附属住房仅来自本城农场（V2.1 §10.2），不允许国外房产权增加本城 K_H。
    """
    return housing_capacity + farm_annex_housing


def compute_K_F(F_sustainable: float, food_per_person: float = DEFAULT_FOOD_PER_PERSON) -> int:
    """V2.1 §10.3 K_F = F_sustainable / food_per_person（向下取整）。

    F_sustainable 由 smooth3 算出后传入。
    """
    if food_per_person <= 0:
        raise ValueError(f"food_per_person 必须为正：{food_per_person}")
    return int(F_sustainable / food_per_person)


def compute_K_J(
    Jop: float,
    E: float,
    F: float,
    *,
    lambda_: float = DEFAULT_LAMBDA,
    e_target: float = DEFAULT_E_TARGET,
    job_use_exp: float = DEFAULT_JOB_USE_EXP,
    eps: float = 1e-9,
) -> float:
    """V2.1 §10.4 K_J = J_eff / (lambda * e_target)。

    J_eff = Jop * [min(1, e/e_target)]^job_use_exp
    e = E / max(F, 1)
    """
    if lambda_ <= 0 or e_target <= 0:
        raise ValueError(f"lambda/e_target 必须为正：{lambda_}/{e_target}")
    e = E / max(F, eps)
    e_ratio = min(1.0, e / e_target)
    J_eff = Jop * (e_ratio ** job_use_exp)
    return J_eff / (lambda_ * e_target)


def compute_s(
    *,
    s_base: float = DEFAULT_S_BASE,
    k_s: float = DEFAULT_K_S,
    safety_persons: float = 0.0,
    N: int = 1,
    threat: float = 0.0,
) -> float:
    """V2.1 §10.5 s = clip(s_base + k_s * safety_persons / max(N,1) - threat, 0, 1)。"""
    return clip(s_base + k_s * safety_persons / max(N, 1) - threat, 0.0, 1.0)


def compute_K(K_H: int, K_F: int, K_J: float, s: float) -> int:
    """V2.1 §10.6 K = floor(s^0.25 * min(K_H, K_F, K_J))。

    注意：s 已经在温和指数 0.25 折算，不再在每一项 K 上重复扣安全百分比。
    """
    if s < 0 or s > 1:
        raise ValueError(f"s 越界 [0, 1]：{s}")
    if K_H <= 0 or K_F <= 0 or K_J <= 0:
        return 0  # 任何分量为 0 → K = 0
    s_factor = s ** 0.25
    min_K = min(K_H, K_F, K_J)
    return int(s_factor * min_K)


# ===== V2.1 §11.1 L 四分量 =====

@dataclass(frozen=True)
class LComponents:
    """V2.1 §11.1 L 的四个分量。"""
    h: float         # 容量余裕 [0, 1]
    a: float         # 实际购买力 [0, 1]
    v: float         # 公共服务满足率 [0, 1]
    s: float         # 安全系数 [0, 1]


def compute_h(
    K: int,
    N: int,
    *,
    floor: float = DEFAULT_H_FLOOR,
    divisor: float = DEFAULT_H_DIVISOR,
    eps: float = 1e-9,
) -> float:
    """V2.1 §11.1 h = clip((K/max(N,1) - 0.90) / 0.45, 0, 1)。

    容量余裕分母 0.45 经开局城市校准选定，使 K/N ≈ 1.08~1.30 的城市
    在其他条件正常时具有不同但可比较的 L。
    """
    if divisor <= 0:
        raise ValueError(f"divisor 必须为正：{divisor}")
    ratio = (K / max(N, eps)) - floor
    return clip(ratio / divisor, 0.0, 1.0)


def compute_a(
    total_disposable_income_cents: int,
    N: int,
    Cneed_city_cr_per_person: float,
    *,
    multiplier: float = DEFAULT_L_CNEED_MULTIPLIER,
    eps: float = 1e-9,
) -> float:
    """V2.1 §11.1 a = clip(人均 disposable / (1.20 * Cneed_city), 0, 1)。

    multiplier=1.20 即公式的 1.20*Cneed_city。
    """
    if Cneed_city_cr_per_person <= 0:
        raise ValueError(f"Cneed_city 必须为正：{Cneed_city_cr_per_person}")
    if multiplier <= 0:
        raise ValueError(f"multiplier 必须为正：{multiplier}")
    per_capita_cr = (total_disposable_income_cents / 100.0) / max(N, eps)
    denominator = multiplier * Cneed_city_cr_per_person
    return clip(per_capita_cr / denominator, 0.0, 1.0)


def compute_v(
    A_service_per_person: float,
    funded_public_ops: float,
    required_public_ops: float,
) -> float:
    """V2.1 §11.1 v = clip(A_service/N, 0, 1) * clip(funded/required, 0, 1)。

    参数：
    - A_service_per_person：人均已运营公共服务量（v 第一个 clip）
    - funded_public_ops：已付费公共服务运营数
    - required_public_ops：所需公共服务运营数
    """
    a_part = clip(A_service_per_person, 0.0, 1.0)
    ops_part = clip(funded_public_ops / required_public_ops, 0.0, 1.0) if required_public_ops > 0 else 0.0
    return a_part * ops_part


def compute_L(
    h: float,
    a: float,
    v: float,
    s: float,
    *,
    weights: Optional[dict[str, float]] = None,
) -> float:
    """V2.1 §11.1 L = 100 * (0.35h + 0.35a + 0.20v + 0.10s)。

    安全系数 s 在 K 与 L 中均出现，但 L 中权重仅 0.10（V2.1 §11.1 双重计量预防）。
    """
    w = weights or DEFAULT_L_WEIGHTS
    total = w['h'] * h + w['a'] * a + w['v'] * v + w['s'] * s
    return 100.0 * total


def weights_sum_to_one(weights: dict[str, float] = None) -> bool:
    """校验 L 权重总和 = 1.0。"""
    w = weights or DEFAULT_L_WEIGHTS
    return abs(sum(w.values()) - 1.0) < 1e-6


# 启动校验
assert weights_sum_to_one(), \
    f"V2.1 §11.1 L 权重总和必须为 1.0，实际 {sum(DEFAULT_L_WEIGHTS.values())}"
