"""V2.1 阶段 6：通胀与世局指标（§6.1 + §6.2）。

【2026-10-15】Bipole 城市聚合经济系统理论说明书 V2.1 §6 通胀、工资、生活篮子。

【V2.1 §6.1 全球标准价格指数与消费标准】
- I_t+1 = I_t * (1 + pi_t)
- C_t+1 = C_t * (1 + pi_t)
- 初始 I_0 = 1, C_0 = 50 CR/人/月
- C 与 I 不能独立应用两遍通胀。建议存 C 为状态、I 为对照指数，正常 C_t = C_0 * I_t。
- 解释：消费者「维持同一基准实际生活数量」的名义费用上涨，不是购买实物更多。

【V2.1 §6.2 城市本地实际生活成本】
- 全局篮子权重：食品 45% / 工业消费品 20% / 住房 20% / 普通服务 13% / 金融教育等 2%
- sum basket_weight = 1
- 对城市 c，针对本地标准篮子相对世界价格系数 r_c,k,t（基准均为 1）：
    Cneed_c,t = C_t * sum_k basket_weight_k * r_c,k,t
- 食品篮子包含加工、零售、运输增加值，不要求「农业营业额=居民食品总支出」。
- 住房支付使用真实租金和住房服务权；如果本地实际租金与篮子估计不同，按实际支付更新。

【V2.1 阶段 6 范围】
本模块承载：
- BasketWeights 数据类（5 类权重，§6.2）
- CityPriceCoefficients 数据类（r_c,k,t，§6.2）
- WorldState dataclass（月度 I/C/pi 全局量，§6.1）
- update_world_index() 月度更新 I_t/C_t
- compute_Cneed_c_t() 计算城市本地实际生活成本
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


# ===== V2.1 §6.1 初始值 =====

DEFAULT_INFLATION_PI = 0.003            # 默认初始月度通胀率 0.3%/月
DEFAULT_I_0 = 1.0                       # 全球标准价格指数初始
DEFAULT_C_0_CR_PER_PERSON = 50.0       # 消费标准初始 50 CR/人/月

# ===== V2.1 §3 世界全局参数（补 4 项缺失）=====

DEFAULT_MONTH_DAYS = 30                 # sim.month_days
DEFAULT_CURRENCY_SCALE = 100            # sim.currency_scale（整数分）
DEFAULT_PRICE_PRESSURE_K = 0.08         # world.price_pressure_k（§4.6 报价压力）
DEFAULT_PRICE_RELATIVE_CAP = 0.05       # world.price_relative_cap（§4.6 月变限额）
DEFAULT_GOODS_ROUNDING = 0.001          # world.goods_rounding（数量显示精度）


# ===== V2.1 §6.2 篮子权重（5 类）=====

class BasketCategory(str, Enum):
    """V2.1 §6.2 消费篮子分类。"""
    FOOD = 'food'                # 食品 45%
    INDUSTRIAL = 'industrial'    # 工业消费品 20%
    HOUSING = 'housing'          # 住房 20%
    SERVICES = 'services'        # 普通服务 13%
    FINANCIAL_EDUCATION = 'fin_edu'  # 金融教育等 2%


# V2.1 §6.2 标准篮子权重（全球通用，sum = 1.00）
DEFAULT_BASKET_WEIGHTS: dict[str, float] = {
    BasketCategory.FOOD.value: 0.45,
    BasketCategory.INDUSTRIAL.value: 0.20,
    BasketCategory.HOUSING.value: 0.20,
    BasketCategory.SERVICES.value: 0.13,
    BasketCategory.FINANCIAL_EDUCATION.value: 0.02,
}
# 校验：sum = 1.00
assert abs(sum(DEFAULT_BASKET_WEIGHTS.values()) - 1.00) < 1e-9, \
    f"V2.1 §6.2 篮子权重总和必须为 1.00，实际 {sum(DEFAULT_BASKET_WEIGHTS.values())}"


@dataclass(frozen=True)
class BasketWeights:
    """V2.1 §6.2 篮子权重（5 类）。"""
    weights: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_BASKET_WEIGHTS))

    def __post_init__(self):
        # 校验总和 = 1
        total = sum(self.weights.values())
        if abs(total - 1.00) > 1e-6:
            raise ValueError(f"V2.1 §6.2 篮子权重总和必须为 1.00，实际 {total}")

    def weight(self, category: BasketCategory) -> float:
        return self.weights.get(category.value, 0.0)


@dataclass(frozen=True)
class CityPriceCoefficients:
    """V2.1 §6.2 城市本地相对世界价格系数 r_c,k,t。

    每类商品的"本城价格 / 世界价格"；基准均为 1。
    r_c,k,t > 1 表示本城比世界贵；< 1 表示便宜。
    """
    city_id: str
    r_food: float = 1.0
    r_industrial: float = 1.0
    r_housing: float = 1.0
    r_services: float = 1.0
    r_fin_edu: float = 1.0

    def r(self, category: BasketCategory) -> float:
        return {
            BasketCategory.FOOD: self.r_food,
            BasketCategory.INDUSTRIAL: self.r_industrial,
            BasketCategory.HOUSING: self.r_housing,
            BasketCategory.SERVICES: self.r_services,
            BasketCategory.FINANCIAL_EDUCATION: self.r_fin_edu,
        }[category]


# ===== V2.1 §6.1 WorldState =====

@dataclass
class WorldState:
    """V2.1 §6.1 全球世局状态（V2.1 §1.1 WorldState 完整版）。

    字段（V2.1 §1.1 完整 14 字段）：
    - month：当前月份
    - inflation_pi：本月通胀率
    - global_I：全球标准价格指数（I_0 = 1.0）
    - global_C：全球消费标准 CR/人/月（C_0 = 50）
    - month_days：sim.month_days = 30
    - currency_scale：sim.currency_scale = 100
    - price_pressure_k：world.price_pressure_k = 0.08（§4.6 报价）
    - price_relative_cap：world.price_relative_cap = 0.05
    - goods_rounding：world.goods_rounding = 0.001
    - reference_wages：行业基准工资（占位 dict，阶段 6 已用 INDUSTRY_DEFINITIONS）
    - product_catalog：商品目录（占位，阶段 5 GOOD_DEFINITIONS）
    - city_ids / city_distance_matrix：城市列表 + 距离矩阵（阶段 8 DistanceMatrix）
    - trade_contracts / trade_quotes：贸易合同/报价（占位 list）
    - external_market_accounts：外部市场账户（占位 dict）
    - migration_parameters：迁移参数（阶段 8 已用）
    - policy_event_queue：政策事件队列（占位 list）
    - rng_seed：随机种子（占位 int）

    关键约束（§6.1）：
    - I_t+1 = I_t * (1 + pi_t)
    - C_t+1 = C_t * (1 + pi_t)
    """
    month: int = 0
    inflation_pi: float = DEFAULT_INFLATION_PI
    global_I: float = DEFAULT_I_0
    global_C: float = DEFAULT_C_0_CR_PER_PERSON
    # §3 补 4 项
    month_days: int = DEFAULT_MONTH_DAYS
    currency_scale: int = DEFAULT_CURRENCY_SCALE
    price_pressure_k: float = DEFAULT_PRICE_PRESSURE_K
    price_relative_cap: float = DEFAULT_PRICE_RELATIVE_CAP
    goods_rounding: float = DEFAULT_GOODS_ROUNDING
    # §1.1 占位字段（阶段 5/6/8/9 已有对应实现，本类持有引用）
    reference_wages: dict = field(default_factory=dict)
    product_catalog: dict = field(default_factory=dict)
    city_ids: list = field(default_factory=list)
    city_distance_matrix: dict = field(default_factory=dict)
    trade_contracts: list = field(default_factory=list)
    trade_quotes: dict = field(default_factory=dict)
    external_market_accounts: dict = field(default_factory=dict)
    migration_parameters: dict = field(default_factory=dict)
    policy_event_queue: list = field(default_factory=list)
    rng_seed: int = 0

    def update_world_index(self) -> None:
        """V2.1 §6.1 月度更新：I_t+1 = I_t * (1 + pi_t), C_t+1 = C_t * (1 + pi_t)。"""
        if self.inflation_pi < -1.0:
            raise ValueError(f"通胀率越界（< -100%）：{self.inflation_pi}")
        self.global_I = self.global_I * (1.0 + self.inflation_pi)
        self.global_C = self.global_C * (1.0 + self.inflation_pi)
        self.month += 1

    def verify_consistency(self, tol: float = 1e-6) -> bool:
        """V2.1 §6.1 校验：C_t == C_0 * I_t。"""
        expected_C = DEFAULT_C_0_CR_PER_PERSON * self.global_I
        return abs(self.global_C - expected_C) < tol


# ===== V2.1 §6.2 Cneed_c,t 计算 =====

def compute_Cneed_c_t(
    world_C: float,
    weights: BasketWeights,
    r: CityPriceCoefficients,
) -> float:
    """V2.1 §6.2 计算城市本地实际生活成本。

    Cneed_c,t = C_t * sum_k basket_weight_k * r_c,k,t

    参数：
    - world_C：当前全球消费标准 CR/人/月（WorldState.global_C）
    - weights：篮子权重（§6.2）
    - r：城市本地价格系数（CityPriceCoefficients）

    返回：Cneed_c,t（CR/人/月）
    """
    weighted_sum = sum(
        weights.weight(cat) * r.r(cat)
        for cat in BasketCategory
    )
    return world_C * weighted_sum
