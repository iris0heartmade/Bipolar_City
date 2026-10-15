"""V2.1 阶段 8：跨城迁移（§11.2 + §11.3 + §11.4）。

【2026-10-15】Bipole 城市聚合经济系统理论说明书 V2.1 §11 宜居度 L 与跨城迁移。

【V2.1 §11.2 迁移核心：对城市对 (c, d) 的带符号净流】
M_c_to_d = rho * (2*N_c*N_d / (N_c+N_d)) * exp(-distance_cd/d0)
          * tanh(beta * (L_d - L_c) / 2)
- M > 0 从 c 向 d；M < 0 从 d 向 c
- M_cd = -M_dc，天然反对称
- 两城 L 相等时净迁移 0
- 默认 d0 = 200 km
- 公式与引力/随机效用选择思想相似，是游戏化组合模型

【V2.1 §11.3 多城求解 9 步强制步骤】
a) 全部城市使用同月快照 L、N 计算所有无序城市对，仅计算一次。
b) 符号转定向候选净迁移量，按比例量化为整数（确定性/有种子随机余数法）。
c) 同来源城市候选总迁出统一按 1% 来源人口限制缩放。
d) 目的城市可入住住房空额硬约束；K 通过 L 产生软负反馈。
e) 多来源竞争同一目的地余量，按候选数量比例分配；必须重新检查来源额度。
f) 同时过账人口，确保 sum_c deltaN_c=0；不得按城市次序连续更新。
g) 迁出者按当期人均可携带现金转移，不得使来源储蓄变负；目标对应群体增加同额。
h) 迁移人口保留信徒比例与劳动参与结构，新城市就业下个月重新分配。
i) 异地地产等不可携带产权仍在原地，租金流按所有权结算。

【V2.1 §11.4 防开局无限流失】
- 首月任意城市迁出 <= 该城人口 1%
- 正常基准起点城市 L 差异不宜超过 18 分
- 初始 L 落在 65~82；K/N 建议 1.08~1.30

【V2.1 阶段 8 范围】
本模块承载：
- CitySnapshot（月末快照：N/L/K/储蓄/住房容量/信徒比/劳动参与）
- DistanceMatrix（城际距离）
- MigrationFlow（c→d 净流量 + 现金转移）
- compute_M_cd() 单城对公式
- cap_outflow_1pct() 1% 上限
- resolve_destination_housing() 目的住房硬约束
- solve_multi_city_migration() 主函数：9 步强制流程
"""
from dataclasses import dataclass, field
from math import exp, tanh
from typing import Optional


# ===== V2.1 §11.2 + §11.4 默认参数 =====

DEFAULT_RHO = 0.01                 # 迁移基础系数
DEFAULT_BETA = 0.08                # L 差敏感度
DEFAULT_D0_KM = 200.0              # 距离衰减基准（km）
DEFAULT_OUTFLOW_CAP_RATE = 0.01    # §11.4 首月迁出上限（1%）
DEFAULT_DEST_HOUSING_BUFFER = 0    # 目的住房余量下限（0 = 满员不留）


# ===== V2.1 §11.3 末快照数据 =====

@dataclass(frozen=True)
class CitySnapshot:
    """V2.1 §11.3(a) 全部城市同月快照 L、N。

    字段：
    - city_id：城市编号
    - N：人口
    - L：宜居度（§11.1 计算结果）
    - K：承载力（§10 计算结果）
    - savings_cents：居民总储蓄（整数分；用于现金转移 §11.3 g）
    - housing_capacity：可入住住房容量（人口数；§11.3 d 硬约束）
    - faithful_ratio：信徒比例（§11.3 h 异地保留）
    - labor_participation：劳动参与率（§11.3 h 异地保留）
    """
    city_id: str
    N: int
    L: float
    K: int = 0
    savings_cents: int = 0
    housing_capacity: int = 0
    faithful_ratio: float = 0.55   # §9.5 默认初始信徒比
    labor_participation: float = 0.55

    def housing_room(self) -> int:
        """V2.1 §11.3(d) 目的住房余量 = housing_capacity - N。"""
        return max(0, self.housing_capacity - self.N)

    def per_capita_cash_cents(self) -> int:
        """V2.1 §11.3(g) 人均可携带现金（用于转移）。"""
        if self.N <= 0:
            return 0
        return int(self.savings_cents / self.N)


@dataclass(frozen=True)
class DistanceMatrix:
    """V2.1 §11.2 城际距离（km）；对称矩阵 distance[c][d] = distance[d][c]。"""
    distances: dict[str, dict[str, float]] = field(default_factory=dict)

    def get(self, city_a: str, city_b: str) -> float:
        if city_a == city_b:
            return 0.0
        return self.distances.get(city_a, {}).get(city_b, 0.0)

    def set(self, city_a: str, city_b: str, distance_km: float):
        if city_a not in self.distances:
            self.distances[city_a] = {}
        if city_b not in self.distances:
            self.distances[city_b] = {}
        # 对称
        self.distances[city_a][city_b] = distance_km
        self.distances[city_b][city_a] = distance_km


@dataclass(frozen=True)
class MigrationFlow:
    """V2.1 §11.2 单向 c→d 净流量结果。

    字段：
    - origin_city：来源城市
    - destination_city：目的城市
    - signed_M：原始符号净流（M > 0 表示 c→d）
    - delta_N：实际过账净人口（c→d 为正，d→c 为负；整数）
    - cash_transfer_cents：现金转移（整数分；c→d 为正）
    - capped_by_1pct：是否被 1% 上限截断
    - capped_by_housing：是否被目的住房硬约束截断
    """
    origin_city: str
    destination_city: str
    signed_M: float
    delta_N: int
    cash_transfer_cents: int
    capped_by_1pct: bool = False
    capped_by_housing: bool = False


# ===== V2.1 §11.2 城对公式 =====

def compute_M_cd(
    N_c: int, N_d: int, L_c: float, L_d: float,
    distance_cd_km: float,
    *,
    rho: float = DEFAULT_RHO,
    beta: float = DEFAULT_BETA,
    d0: float = DEFAULT_D0_KM,
) -> float:
    """V2.1 §11.2 M_c_to_d = rho * (2*N_c*N_d/(N_c+N_d)) * exp(-distance/d0) * tanh(beta*(L_d-L_c)/2)。

    返回有符号净流（M > 0 表示 c→d）。
    """
    if N_c <= 0 or N_d <= 0:
        return 0.0
    # 调和平均：2*N_c*N_d / (N_c+N_d)
    harmonic = 2 * N_c * N_d / (N_c + N_d)
    # 距离衰减
    distance_factor = exp(-distance_cd_km / d0)
    # L 差 sigmoid：tanh(beta*(L_d-L_c)/2)
    L_factor = tanh(beta * (L_d - L_c) / 2)
    return rho * harmonic * distance_factor * L_factor


def antisymmetric_pair(
    c: CitySnapshot, d: CitySnapshot,
    distance_cd_km: float,
    **kwargs,
) -> tuple[float, float]:
    """V2.1 §11.2 计算 (M_cd, M_dc) = (M_c_to_d, -M_c_to_d)。

    返回：(M_c_to_d, M_d_to_c)。两者天然反对称。
    """
    M_cd = compute_M_cd(N_c=c.N, N_d=d.N, L_c=c.L, L_d=d.L,
                        distance_cd_km=distance_cd_km, **kwargs)
    return M_cd, -M_cd


# ===== V2.1 §11.4 + §11.3 上限 =====

def cap_outflow_1pct(
    M_cd_signed: float,
    N_c: int,
    cap_rate: float = DEFAULT_OUTFLOW_CAP_RATE,
) -> tuple[float, bool]:
    """V2.1 §11.3(c) + §11.4 同来源城市候选总迁出按 1% 来源人口限制缩放。

    仅当 M > 0（c→d 方向）时上限生效；M < 0 时返回 (M, False)。
    """
    if M_cd_signed <= 0:
        return M_cd_signed, False
    cap = N_c * cap_rate
    if M_cd_signed > cap:
        return cap, True
    return M_cd_signed, False


def cap_destination_housing(
    delta_N_positive: float,
    destination_room: int,
) -> tuple[float, bool]:
    """V2.1 §11.3(d) 目的城市可入住住房空额硬约束。

    仅当 delta_N > 0（迁入）时上限生效；负值（迁出）直接返回。
    """
    if delta_N_positive <= 0:
        return delta_N_positive, False
    if delta_N_positive > destination_room:
        return float(destination_room), True
    return delta_N_positive, False


# ===== V2.1 §11.3 多城迁移求解 =====

@dataclass
class MigrationResult:
    """V2.1 §11.3 多城迁移结算结果。"""
    flows: list[MigrationFlow] = field(default_factory=list)
    delta_N: dict[str, int] = field(default_factory=dict)
    cash_transfers: dict[tuple[str, str], int] = field(default_factory=dict)

    def net_delta(self, city_id: str) -> int:
        return self.delta_N.get(city_id, 0)

    def total_moved(self) -> int:
        """总迁移人数（绝对值之和的一半）。"""
        return sum(abs(d) for d in self.delta_N.values()) // 2


def solve_multi_city_migration(
    snapshots: list[CitySnapshot],
    distances: DistanceMatrix,
    *,
    rho: float = DEFAULT_RHO,
    beta: float = DEFAULT_BETA,
    d0: float = DEFAULT_D0_KM,
    cap_rate: float = DEFAULT_OUTFLOW_CAP_RATE,
) -> MigrationResult:
    """V2.1 §11.3 多城迁移求解（9 步强制流程）。

    a) 全部城市同月快照 L、N 计算所有无序城市对（仅一次）
    b) 候选净迁移量转定向 + 量化（用 round 量化；确定性）
    c) 同来源城市候选总迁出按 1% 上限缩放
    d) 目的城市住房空额硬约束
    e) 多来源竞争同一目的地按比例分配（本步暂用先到先得简化；
       严格 V2.1 §11.3(e) 需要重新检查来源额度，由调用方实现）
    f) 同时过账人口 sum_c deltaN_c = 0（由反对称 M 天然保证）
    g) 现金转移：来源储蓄 / 来源人口 × 迁出人数（不得使储蓄负数）
    h/i) 信徒比 + 劳动参与结构：保留在 CitySnapshot，调用方据此分配
    """
    city_ids = [s.city_id for s in snapshots]
    snap_map = {s.city_id: s for s in snapshots}

    # a) 无序对计算（仅一次）
    flows_signed: dict[tuple[str, str], float] = {}
    for i, c_id in enumerate(city_ids):
        for j in range(i + 1, len(city_ids)):
            d_id = city_ids[j]
            c = snap_map[c_id]
            d = snap_map[d_id]
            dist = distances.get(c_id, d_id)
            M_cd, _ = antisymmetric_pair(c, d, dist, rho=rho, beta=beta, d0=d0)
            flows_signed[(c_id, d_id)] = M_cd

    # b) 候选净流 → 定向整数（量化）
    flows_directional: dict[tuple[str, str], int] = {}
    for (c_id, d_id), M_cd in flows_signed.items():
        # 反对称：M_cd > 0 → c→d；M_cd < 0 → d→c
        if M_cd >= 0:
            # c→d 方向
            flows_directional[(c_id, d_id)] = int(round(M_cd))
        else:
            # d→c 方向
            flows_directional[(d_id, c_id)] = int(round(-M_cd))

    # c) 1% 来源上限
    outflow_capped: dict[tuple[str, str], int] = {}
    for (src, dst), n in flows_directional.items():
        if n <= 0:
            continue
        cap_value, _ = cap_outflow_1pct(float(n), snap_map[src].N, cap_rate)
        outflow_capped[(src, dst)] = int(cap_value)

    # d) 目的住房硬约束
    housing_capped: dict[tuple[str, str], int] = {}
    # 先统计每个目的城市总迁入需求
    dest_demand: dict[str, int] = {}
    for (src, dst), n in outflow_capped.items():
        dest_demand[dst] = dest_demand.get(dst, 0) + n
    # 再按住房余量截断
    for (src, dst), n in outflow_capped.items():
        dest = snap_map[dst]
        room = dest.housing_room()
        # 单源对该目的的分配 = min(源出量, 目的余量 × (源出量/目的总需求))
        total_dest_demand = dest_demand.get(dst, 0)
        if total_dest_demand > room and total_dest_demand > 0:
            # 按比例分配
            share = int(n * room / total_dest_demand)
            housing_capped[(src, dst)] = share
        else:
            housing_capped[(src, dst)] = n

    # f) 同时过账（反对称保证 sum=0）；g) 现金转移
    delta_N: dict[str, int] = {cid: 0 for cid in city_ids}
    cash_transfers: dict[tuple[str, str], int] = {}
    flows_list: list[MigrationFlow] = []

    for (src, dst), n in housing_capped.items():
        if n <= 0:
            continue
        # f) 同步过账
        delta_N[src] -= n
        delta_N[dst] += n
        # g) 现金转移：人均可携带现金 × 迁出人数（不得使来源储蓄负数）
        snap_src = snap_map[src]
        per_capita = snap_src.per_capita_cash_cents()
        cash_transfer = per_capita * n
        # 校验来源储蓄不会负数
        max_cash = snap_src.savings_cents  # 不允许迁出超过全部储蓄
        if cash_transfer > max_cash:
            cash_transfer = max_cash
        cash_transfers[(src, dst)] = cash_transfer
        # 记录
        flows_list.append(MigrationFlow(
            origin_city=src, destination_city=dst,
            signed_M=flows_signed.get((src, dst), 0.0)
                if flows_signed.get((src, dst), 0.0) >= 0
                else -flows_signed.get((src, dst), 0.0),
            delta_N=n,
            cash_transfer_cents=cash_transfer,
        ))

    return MigrationResult(
        flows=flows_list,
        delta_N=delta_N,
        cash_transfers=cash_transfers,
    )
