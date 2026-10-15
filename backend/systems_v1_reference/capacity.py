"""产能引擎：设计稿 §4.1 的 Qcap 公式与 §4.2 的基准产能反推。

为什么需要这一层
----------------
没有这一层，营业额是**配置里的死数**。城市盖多少房、招多少人、建什么设施，
营业额纹丝不动——所以城市永远不会发展，只是把同一本账反复算 60 遍。

有了产能公式，企业能不能开张、要不要扩张、会不会倒闭，全都由
「招了多少人 × 技能够不够 × 原料够不够 × 安不安全 × 信仰占多少工时」决定。
玩家盖学校、建货站、调传教政策，都会从这里开始影响经济结果。

公式（设计稿 §4.1）
--------------------
    Qcap_i = K_i × F_labor × F_skill × F_resource × F_service × F_safety × F_worktime

各因子的定义全部来自设计稿，不自行发明：

============  ==============================================  ==========
因子          含义                                          玩家手段
============  ==============================================  ==========
F_labor       实际用工 / 需求用工，0.80 次幂                建厂招人
F_skill       有效技能 / 需求技能，0.30 次幂，夹在 0.65~1    盖学校
F_resource    战略品可用量 / 计划用量                        建货站、贸易
F_service     供水供电运行系数，MVP 默认为 1                 建基础设施
F_safety      1 − 期望袭击×停工占比，夹在 0.65~1             建魔法师驻地
F_worktime    (1−0.04f)/(1−0.04×0.55)，f 为信仰比例          调传教政策
============  ==============================================  ==========

基准产能反推（§4.2）
--------------------
初始校准取 80% 产能利用率：``K_i = 初始营业额 / 0.80``。
这样反推出的 K 能让「一切因子都为 1」时恰好重现原始营业额 340000，
P02 的 14 项基准不会因为引入产能层而漂移。
"""
from dataclasses import dataclass, replace
from fractions import Fraction

from backend.core.errors import GameError

# 初始校准的产能利用率（设计稿 §4.2 明确选 0.80 作为基准校准）
REFERENCE_UTILISATION = Fraction(80, 100)
# 设计稿 §4.1 的两个幂次，不可随意改动
LABOUR_EXPONENT = Fraction(80, 100)
SKILL_EXPONENT = Fraction(30, 100)
# 参考城的信仰比例基准，用于 F_worktime 归一化
FAITH_REFERENCE = Fraction(55, 100)
# 信仰对工时的最大占用（全信徒时 4%）
FAITH_WORKTIME_LOSS = Fraction(4, 100)
# 各因子的允许区间，来自设计稿原文
SKILL_FLOOR = Fraction(65, 100)
SAFETY_FLOOR = Fraction(65, 100)


def _clamp(value: Fraction, low: Fraction = Fraction(0), high: Fraction = Fraction(1)) -> Fraction:
    """夹到区间内。所有因子都必须落在设计稿给定的范围内。"""
    return max(low, min(high, value))


@dataclass(frozen=True)
class CapacityFactors:
    """一个月度快照里，每个产能因子各自的值。

    这些值都是**可追溯到玩家行为**的：
    - labour 随招聘人数变化
    - skill 随教育产出变化
    - resource 随战略品库存变化
    - service 由基础设施决定
    - safety 由魔法师覆盖决定
    - worktime 由信仰比例决定
    """

    labour: Fraction = Fraction(1)      # F_labor
    skill: Fraction = Fraction(1)       # F_skill
    resource: Fraction = Fraction(1)    # F_resource
    service: Fraction = Fraction(1)     # F_service
    safety: Fraction = Fraction(1)      # F_safety
    worktime: Fraction = Fraction(1)    # F_worktime

    @property
    def product(self) -> Fraction:
        """全部因子相乘。没有这个乘积，Qcap 公式就不成立。"""
        return (self.labour * self.skill * self.resource
                * self.service * self.safety * self.worktime)

    def to_dict(self) -> dict[str, str]:
        return {name: f"{float(getattr(self, name)):.4f}"
                for name in ("labour", "skill", "resource", "service", "safety", "worktime")}


def labour_factor(actual_workers: int, required_workers: int) -> Fraction:
    """F_labor = min(1, (实际用工 / 需求用工)^0.80)。

    招不满人产能就打折，招够了也不会超过 100%。分母下限取 1，
    避免「需求用工为 0」时除零——那种情况按满负荷算。
    """
    if required_workers <= 0:
        return Fraction(1)
    ratio = Fraction(max(0, actual_workers), max(1, required_workers))
    return _clamp(min(Fraction(1), ratio ** LABOUR_EXPONENT))


def skill_factor(effective_skill: int, required_skill: int) -> Fraction:
    """F_skill = clamp((有效技能 / 需求技能)^0.30, 0.65, 1.00)。

    下限 0.65 的含义：技能再差也只打六五折，不会归零——
    建模的是「培训不足拖慢效率」，不是「没有技能就完全不能生产」。
    """
    if required_skill <= 0:
        return Fraction(1)
    ratio = Fraction(max(0, effective_skill), required_skill)
    return _clamp(ratio ** SKILL_EXPONENT, SKILL_FLOOR, Fraction(1))


def resource_factor(available: int, required: int) -> Fraction:
    """F_resource = min(1, 本月可用关键资源 / 本月计划消耗)。

    没有资源约束的行业传 required=0，直接返回 1。

    **负库存一律报错**，不做 max(0,…) 兜底：设计稿 §13 TEST-19 要求
    「城市生产不足时战略物资库存不会出现负吨数/负箱数」。悄悄把负数当 0
    会让库存错误被产能层吞掉，问题永远暴露不出来。
    """
    if available < 0 or required < 0:
        raise GameError(f"战略资源数量不能为负：可用 {available}，需求 {required}")
    if required == 0:
        return Fraction(1)
    return _clamp(Fraction(available, required))


def safety_factor(expected_attacks: Fraction, downtime_share: Fraction) -> Fraction:
    """F_safety = clamp(1 − 期望袭击×停工占比, 0.65, 1.00)。

    安全城市不会因为堆魔法师而收益无穷：袭击趋近 0 时本因子就封顶在 1。
    """
    loss = Fraction(max(0, expected_attacks)) * Fraction(max(0, downtime_share))
    return _clamp(Fraction(1) - loss, SAFETY_FLOOR, Fraction(1))


def worktime_factor(faith_share: Fraction) -> Fraction:
    """F_worktime = (1 − 0.04f) / (1 − 0.04×0.55)。

    相对**初始城市**校准，所以信仰比例等于基准 0.55 时恰好为 1，
    P02 的 14 项基准不会因为引入产能层而整体下移。

    这里刻意**不设上限 1**：信仰比例低于基准时分子变大，工人有更多可支配工时，
    产出应当高于基准而不是被夹回去。夹回 1 会让「降低信仰」这个玩家手段失效。
    """
    f = _clamp(Fraction(max(0, faith_share)))
    numerator = Fraction(1) - FAITH_WORKTIME_LOSS * f
    denominator = Fraction(1) - FAITH_WORKTIME_LOSS * FAITH_REFERENCE
    if denominator <= 0:
        return Fraction(1)
    return numerator / denominator


@dataclass(frozen=True)
class SectorCapacity:
    """单个行业的产能计算结果。金额一律整数分。

    ``baseline_units`` 是满负荷基准 K（设计稿 §4.2 用 K = 营业额/0.80 反推）。
    ``revenue_cents`` 是**按利用率打折后的实际产出**：利用率 100% 时
    恰好等于配置里的原始营业额，利用率 80% 时才是原始营业额 × 0.8。
    """

    sector_id: str
    baseline_units: int      # K：满负荷时的基准产能，单位「分」
    utilisation: Fraction    # 实际利用率 = 各因子乘积
    revenue_cents: int       # 本月实际产出 = K × 利用率 × 参考利用率
    factors: CapacityFactors

    @property
    def utilisation_ratio(self) -> float:
        return float(self.utilisation)


def baseline_capacity(revenue_cents: int) -> int:
    """从初始营业额反推基准产能 K（设计稿 §4.2 的 80% 校准）。

        K = 初始营业额 / 0.80

    这样「各因子全为 1」时 K×0.80 恰好等于原始营业额，
    原始营业额 340000 因而能被精确重现，P02 基准不漂移。
    """
    if revenue_cents <= 0:
        raise GameError("行业营业额必须为正，无法反推基准产能。")
    return int(round(revenue_cents / float(REFERENCE_UTILISATION)))


def sector_capacity(sector_id: str, revenue_cents: int, factors: CapacityFactors) -> SectorCapacity:
    """算一个行业的本月产能与营业额。

    ``revenue_cents`` 是配置里的**参考营业额**，对应设计稿 §4.2 的初始 80% 利用率。
    所以实际产出 = K × 利用率 × 参考利用率，其中 K = 参考营业额 / 参考利用率。
    利用率为 1 时精确回到参考营业额；所有因子都打折时按比例缩水。

    营业额不会低于 0：停产就是 0，不是负数。
    """
    baseline = baseline_capacity(revenue_cents)
    utilisation = factors.product
    actual = int(round(baseline * float(utilisation) * float(REFERENCE_UTILISATION)))
    return SectorCapacity(sector_id=sector_id,
                          baseline_units=baseline,
                          utilisation=utilisation,
                          revenue_cents=max(0, actual),
                          factors=factors)


def uniform_factors(count: int, factors: CapacityFactors) -> list[CapacityFactors]:
    """给每个行业复制同一组因子。

    聚合阶段六行业面对的是同一座城市——同一批劳动力、同一个信仰比例、
    同一套治安水平，所以因子相同；逐店建模（P05）之后各店才会不同。
    """
    if count < 0:
        raise GameError("行业数量不能为负。")
    return [factors] * count


def with_factors(base: CapacityFactors, **changes) -> CapacityFactors:
    """复制一组因子并替换其中几项，避免调用方逐项重写。"""
    unknown = set(changes) - {f for f in CapacityFactors.__dataclass_fields__}
    if unknown:
        raise GameError(f"未知的产能因子：{sorted(unknown)}")
    return replace(base, **changes)


__all__ = [
    "REFERENCE_UTILISATION", "CapacityFactors", "SectorCapacity",
    "baseline_capacity", "labour_factor", "resource_factor", "safety_factor",
    "sector_capacity", "skill_factor", "uniform_factors", "worktime_factor",
    "with_factors",
]