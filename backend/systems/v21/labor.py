"""V2.1 阶段 9 补：就业/招工/可持续岗位（§5.1 + §5.2 + §5.3）。

【2026-10-15】Bipole 城市聚合经济系统理论说明书 V2.1 §5.1-5.3 就业与生产。

【V2.1 §5.1 人口】
- F_t = floor(lambda * N_t)
- E_t = sum_i E_i,t
- U_t = F_t - E_t
- 0 <= E <= F <= N
- 月初就业岗位上限 J_i 来自建筑，实际可支付岗位 Jop_i <= J_i

【V2.1 §5.2 可持续岗位 Jop_i（最简可执行判定）】
- PayrollCapacity_i = max(0, M_operating_budget_i + guaranteed_receipts_i
                                - unavoidable_nonpayroll_cash_i) / max(w_i, eps)
- Jop_i = min(J_i, floor(PayrollCapacity_i))
- M_operating_budget 当月可动用经营现金上限（不是市府全城总现金）
- guaranteed_receipts 只能是已履约/可兑付合同
- 长期可持续性另据 3 月滚动收入估计；开局种子直接给出已核验 Jop_i

【V2.1 §5.3 招工】
- E_i <= Jop_i；sum E_i <= F
- 为避免行业排列顺序影响就业，先为每行业生成申请量 Demand_i，
  按工资级/岗位比例与最小保障比例同时分配，使用整数最大余数法收尾
- 公务员由政府计划先锁定岗位
- 在超额空缺的行业之间可按工资权重分配

【V2.1 阶段 9 补范围】
本模块承载：
- compute_F() §5.1 劳动力 = floor(lambda*N)
- compute_Jop() §5.2 可持续岗位
- integer_allocation_with_largest_remainder() §5.3 整数最大余数法
- allocate_employment() §5.3 多行业招工（先 GOV 锁定，再按权重分配）
- HireDecision 数据类
"""
from dataclasses import dataclass, field
from typing import Optional

from backend.systems.v21.capacity import DEFAULT_LAMBDA, DEFAULT_E_TARGET


# ===== V2.1 §5.1 人口派生 =====

def compute_F_t(N: int, lambda_: float = DEFAULT_LAMBDA) -> int:
    """V2.1 §5.1 F_t = floor(lambda * N_t)。"""
    if N < 0:
        raise ValueError(f"N 不能为负：{N}")
    if lambda_ < 0 or lambda_ > 1.0:
        raise ValueError(f"lambda 越界 [0, 1]：{lambda_}")
    return int(N * lambda_)


# ===== V2.1 §5.2 可持续岗位 =====

def compute_Jop(
    J_i: int,
    M_operating_budget_cents: int,
    guaranteed_receipts_cents: int,
    unavoidable_nonpayroll_cents: int,
    wage_per_worker_cents: int,
    eps: float = 1e-9,
) -> int:
    """V2.1 §5.2 Jop_i = min(J_i, floor(PayrollCapacity_i))。

    PayrollCapacity_i = max(0, M + guaranteed - unavoidable) / max(w, eps)
    """
    if J_i < 0:
        raise ValueError(f"J_i 不能为负：{J_i}")
    if wage_per_worker_cents < 0:
        raise ValueError(f"w_i 不能为负：{wage_per_worker_cents}")
    payroll_capacity = max(
        0.0,
        (M_operating_budget_cents + guaranteed_receipts_cents - unavoidable_nonpayroll_cents)
        / max(wage_per_worker_cents, eps),
    )
    return min(J_i, int(payroll_capacity))


# ===== V2.1 §5.3 整数最大余数法 =====

def integer_allocation_with_largest_remainder(
    total: int,
    weights: list[float],
) -> list[int]:
    """V2.1 §5.3 整数最大余数法分配 total 个名额到 n 个槽位。

    算法：
    1. 规范化权重（保证 sum = 1）
    2. 基础配额 q_i = total * weight_i（向下取整）
    3. 余数 r_i = total * weight_i - q_i
    4. 给余数最大的 (total - sum(q_i)) 个槽位 +1
    """
    if total < 0:
        raise ValueError(f"total 不能为负：{total}")
    if not weights:
        return []
    w_sum = sum(weights)
    if w_sum <= 0:
        # 全部权重为 0：均匀分配
        if total == 0:
            return [0] * len(weights)
        base, remainder = divmod(total, len(weights))
        return [base + (1 if i < remainder else 0) for i in range(len(weights))]
    # 归一化
    norm_w = [w / w_sum for w in weights]
    # 基础配额（向下）
    base = [int(total * w) for w in norm_w]
    remainder = total - sum(base)
    if remainder > 0:
        # 按余数从大到小排序（前 remainder 个加 1）
        fractions = [(total * norm_w[i] - base[i], i) for i in range(len(weights))]
        fractions.sort(key=lambda x: -x[0])
        for _, i in fractions[:remainder]:
            base[i] += 1
    return base


# ===== V2.1 §5.3 多行业招工 =====

@dataclass(frozen=True)
class IndustryJobCapacity:
    """V2.1 §5.3 单行业岗位申请输入。"""
    industry_id: str
    J_op: int                       # 实际可支付岗位
    application_demand: int         # 申请量 Demand_i
    weight: float = 1.0             # 工资权重（超额空缺时按权重分配）


@dataclass(frozen=True)
class HireDecision:
    """V2.1 §5.3 单行业招工结果。"""
    industry_id: str
    E_i: int                       # 实际雇佣人数
    shortage: int                  # 申请量 - 实际雇佣（未满足）


def allocate_employment(
    industries: list[IndustryJobCapacity],
    F: int,                         # 总劳动力
    gov_reserved_E: int = 0,        # 公务员锁定岗位（§5.3 GOV 优先）
    E_GOV: Optional[int] = None,    # 公务员实际雇佣（= min(GOV_Jop, gov_reserved)）
    eps: float = 1e-9,
) -> list[HireDecision]:
    """V2.1 §5.3 多行业招工（先 GOV 锁定，再按权重最大余数法分配）。

    步骤：
    1. 公务员按 GOV Jop 与政府计划先锁定（不超 gov_reserved_E）
    2. 剩余劳动力 = F - E_GOV
    3. 按 Jop 与申请量取最小值得到名义需求
    4. 整数最大余数法按权重分配剩余劳动力
    """
    if F < 0:
        raise ValueError(f"F 不能为负：{F}")
    if gov_reserved_E < 0:
        raise ValueError(f"gov_reserved_E 不能为负：{gov_reserved_E}")

    # 1. GOV 锁定
    if E_GOV is None:
        E_GOV = min(gov_reserved_E, F)

    remaining_F = max(0, F - E_GOV)
    decisions: list[HireDecision] = []

    # 2. 名义需求 = min(Jop, application_demand)
    demands = [min(ind.J_op, ind.application_demand) for ind in industries]
    total_demand = sum(demands)

    if total_demand <= remaining_F:
        # 全部满足，按实际申请量分配
        for ind, d in zip(industries, demands):
            decisions.append(HireDecision(industry_id=ind.industry_id, E_i=d, shortage=max(0, ind.application_demand - d)))
        return decisions

    # 3. 按权重最大余数法分配 remaining_F
    weights = [ind.weight for ind in industries]
    allocations = integer_allocation_with_largest_remainder(remaining_F, weights)
    for ind, alloc, d in zip(industries, allocations, demands):
        actual = min(alloc, d)         # 不超名义需求
        shortage = max(0, ind.application_demand - actual)
        decisions.append(HireDecision(industry_id=ind.industry_id, E_i=actual, shortage=shortage))

    return decisions
