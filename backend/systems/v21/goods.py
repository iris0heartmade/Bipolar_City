"""V2.1 阶段 5：商品目录与跨城可交易资产（§4.1 + §4.2）。

【2026-10-15】Bipole 城市聚合经济系统理论说明书 V2.1 §4 商品、服务、知识、产权。

【V2.1 §4.1 商品目录 GoodDefinition】
字段：good_id, category, unit, base_price, stockable, shipment_mode,
     deliverable_location, recipe_inputs, perish_rate, contract_transfer_rule,
     settlement_trigger, technological_constraints。

【V2.1 §4.2「万物可交易」技术含义】
a) 实物：付款后按运输交付，不得未交付先加库存。
b) 可储点数：卖方点数扣除、买方点数增加，付款与收款入账；无法凭空复制。
c) 服务：出售的是按地点和期间兑现的服务能力，不能无地点瞬时改善宜居度。
d) 产权：跨城转让所有权，但房地产所在城市不变。
e) 债权：贷款债权的持有人可变化；本金义务不会由于买卖债权消失。
f) 本地建筑：国外提供材料/施工服务，完成建设后才更新本城岗位、住房、产能。
g) 货币为统一 CR；不设置外汇兑换，除非作者将来明确加入。
h) 人口、就业者、信徒不能直接作为可交易库存，严禁变相劳动力买卖。

【V2.1 阶段 5 范围】
本模块承载 17 类商品的目录定义与商品编号工厂；具体订单生成由 V2.1 阶段 5
orders.py 完成；订单匹配与双城镜像由 trade_settlement.py 完成。
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


# ===== V2.1 §4.1 商品分类枚举（17 类）=====

class GoodCategory(str, Enum):
    """V2.1 §4.1 商品分类。"""
    FOOD = 'food'                          # 粮食（基础生存）
    FUEL = 'fuel'                          # 燃料（生产基础）
    ORE = 'ore'                            # 矿物（工业原料）
    STEEL = 'steel'                        # 钢（工业制成品）
    GOODS = 'goods'                        # 一般工业制成品
    LOGISTICS = 'logistics'                # 物流（运输服务）
    SERVICES = 'services'                  # 生活服务
    FINANCIAL_SERVICE = 'financial_service'  # 金融服务
    EDUCATION = 'education'                # 教育服务
    RESEARCH = 'research'                  # 科技点数/研究成果
    MAGIC = 'magic'                        # 魔法点数
    FAITH = 'faith'                        # 信仰点数
    CIVIC = 'civic'                        # 市政点数
    MAGIC_MATERIAL = 'magic_material'      # 魔法材料
    PROPERTY_TITLE = 'property_title'      # 产权
    CONSTRUCTION = 'construction'          # 建筑材料与本地履约施工
    SECURITY_SERVICE = 'security_service'  # 安全服务


class ShipmentMode(str, Enum):
    """V2.1 §4.2 运输模式。"""
    INSTANT = 'instant'        # 瞬时交付（不可储的服务/点数）
    DEFERRABLE = 'deferrable'  # 可递延交付（实物按运输容量与距离）
    TRANSIT = 'transit'        # 跨城运输中（在途库存独立追踪）


class SettlementTrigger(str, Enum):
    """V2.1 §4.1 结算触发。"""
    ON_PAYMENT = 'on_payment'    # 付款时立即结算（点数/产权）
    ON_DELIVERY = 'on_delivery'  # 交付时结算（实物/在途）
    ON_USE = 'on_use'            # 使用时结算（生活服务/安全服务）


# ===== GoodDefinition 数据类（V2.1 §4.1）=====

@dataclass(frozen=True)
class GoodDefinition:
    """V2.1 §4.1 商品定义。

    字段对应规范：
    - good_id：商品编号（主键）
    - category：分类（GoodCategory 枚举）
    - unit：计量单位（如"吨"/"点"/"次"/"套"/"月"）
    - base_price_cents：基础价格（整数分；CR/单位）
    - stockable：是否可库存（实物 True；点数/服务/产权 False）
    - shipment_mode：运输模式
    - deliverable_location：交付地点（'local'/'remote'/'either'）
    - recipe_inputs：投入配方 dict[good_id, qty_per_unit_output]
    - perish_rate：腐坏率（0~1；FOOD 等才有意义）
    - contract_transfer_rule：合同转让规则（V2.1 §4.2 d/e/f）
    - settlement_trigger：结算触发
    - technological_constraints：技术约束（list[str]）

    价格调整策略：base_price_cents 是初始基准，由 §4.6 价格公式动态调整。
    """
    good_id: str
    category: GoodCategory
    unit: str
    base_price_cents: int
    stockable: bool
    shipment_mode: ShipmentMode
    deliverable_location: str                       # 'local' | 'remote' | 'either'
    recipe_inputs: dict[str, float] = field(default_factory=dict)
    perish_rate: float = 0.0                        # 0 = 不腐坏
    contract_transfer_rule: str = 'standard'        # 默认标准规则
    settlement_trigger: SettlementTrigger = SettlementTrigger.ON_DELIVERY
    technological_constraints: tuple[str, ...] = field(default_factory=tuple)
    notes: str = ''

    @property
    def is_point_like(self) -> bool:
        """§4.2(b) 可储点数类（不能凭空复制，需卖方扣 + 买方加）。"""
        return self.shipment_mode == ShipmentMode.INSTANT and not self.stockable

    @property
    def is_real_good(self) -> bool:
        """§4.2(a) 实物类（必须按运输交付）。"""
        return self.stockable

    @property
    def is_service(self) -> bool:
        """§4.2(c) 服务类（按地点和期间兑现）。"""
        return self.category in {
            GoodCategory.SERVICES, GoodCategory.FINANCIAL_SERVICE,
            GoodCategory.EDUCATION, GoodCategory.SECURITY_SERVICE,
            GoodCategory.CONSTRUCTION,
        }

    @property
    def is_property(self) -> bool:
        """§4.2(d) 产权类（跨城转让所有权，房地产所在城市不变）。"""
        return self.category == GoodCategory.PROPERTY_TITLE


# ===== V2.1 §4.1 17 类商品的最小完整定义 =====

# 数值单位：CR/单位 → 整数分（×100）
# 注意：base_price 仅作占位基准，由 §4.6 报价公式动态调整（阶段 5 不实现）

GOOD_DEFINITIONS: dict[str, GoodDefinition] = {
    # §4.1 实物/资源类
    'FOOD': GoodDefinition(
        good_id='FOOD', category=GoodCategory.FOOD, unit='吨',
        base_price_cents=10_000,  # 100 CR/吨
        stockable=True, shipment_mode=ShipmentMode.TRANSIT,
        deliverable_location='either',
        perish_rate=0.05,  # 5%/月 自然损耗
        notes='基础生存物资；农业产出',
    ),
    'FUEL': GoodDefinition(
        good_id='FUEL', category=GoodCategory.FUEL, unit='吨',
        base_price_cents=15_000,  # 150 CR/吨
        stockable=True, shipment_mode=ShipmentMode.TRANSIT,
        deliverable_location='either',
        perish_rate=0.0,
        notes='燃料；工业生产基础',
    ),
    'ORE': GoodDefinition(
        good_id='ORE', category=GoodCategory.ORE, unit='吨',
        base_price_cents=20_000,  # 200 CR/吨
        stockable=True, shipment_mode=ShipmentMode.TRANSIT,
        deliverable_location='either',
        perish_rate=0.0,
        notes='矿物原料；钢的投入品',
    ),
    'STEEL': GoodDefinition(
        good_id='STEEL', category=GoodCategory.STEEL, unit='吨',
        base_price_cents=50_000,  # 500 CR/吨
        stockable=True, shipment_mode=ShipmentMode.TRANSIT,
        deliverable_location='either',
        recipe_inputs={'ORE': 1.5},
        perish_rate=0.0,
        notes='钢；重工业制成品，ORE 投入',
    ),
    'GOODS': GoodDefinition(
        good_id='GOODS', category=GoodCategory.GOODS, unit='吨',
        base_price_cents=80_000,  # 800 CR/吨
        stockable=True, shipment_mode=ShipmentMode.TRANSIT,
        deliverable_location='either',
        recipe_inputs={'STEEL': 0.4, 'FUEL': 0.2},
        perish_rate=0.0,
        notes='一般工业制成品；STEEL+FUEL 投入',
    ),

    # §4.1 服务类（不可储，按地点期间兑现）
    'LOGISTICS': GoodDefinition(
        good_id='LOGISTICS', category=GoodCategory.LOGISTICS, unit='趟',
        base_price_cents=40_000,  # 400 CR/趟
        stockable=False, shipment_mode=ShipmentMode.DEFERRABLE,
        deliverable_location='either',
        settlement_trigger=SettlementTrigger.ON_DELIVERY,
        notes='运输服务；§4.3 运输容量',
    ),
    'SERVICES': GoodDefinition(
        good_id='SERVICES', category=GoodCategory.SERVICES, unit='次',
        base_price_cents=30_000,  # 300 CR/次
        stockable=False, shipment_mode=ShipmentMode.INSTANT,
        deliverable_location='either',
        settlement_trigger=SettlementTrigger.ON_USE,
        notes='生活服务；不可储，按地点使用',
    ),
    'FINANCIAL_SERVICE': GoodDefinition(
        good_id='FINANCIAL_SERVICE', category=GoodCategory.FINANCIAL_SERVICE, unit='笔',
        base_price_cents=500_000,  # 5000 CR/笔
        stockable=False, shipment_mode=ShipmentMode.INSTANT,
        deliverable_location='either',
        settlement_trigger=SettlementTrigger.ON_PAYMENT,
        notes='金融服务；BNK 行业产出',
    ),
    'EDUCATION': GoodDefinition(
        good_id='EDUCATION', category=GoodCategory.EDUCATION, unit='人·月',
        base_price_cents=100_000,  # 1000 CR/人·月
        stockable=False, shipment_mode=ShipmentMode.INSTANT,
        deliverable_location='either',
        settlement_trigger=SettlementTrigger.ON_USE,
        notes='教育服务；MAG 行业产出',
    ),
    'SECURITY_SERVICE': GoodDefinition(
        good_id='SECURITY_SERVICE', category=GoodCategory.SECURITY_SERVICE, unit='月',
        base_price_cents=50_000,  # 500 CR/月
        stockable=False, shipment_mode=ShipmentMode.INSTANT,
        deliverable_location='local',  # 安全服务必须本地提供
        settlement_trigger=SettlementTrigger.ON_USE,
        notes='安全服务；必须本地提供，不可跨城购买',
    ),

    # §4.1 可储点数类（卖方扣 + 买方加；不能凭空复制）
    'RESEARCH': GoodDefinition(
        good_id='RESEARCH', category=GoodCategory.RESEARCH, unit='点',
        base_price_cents=50_000,  # 500 CR/点
        stockable=False, shipment_mode=ShipmentMode.INSTANT,
        deliverable_location='either',
        settlement_trigger=SettlementTrigger.ON_PAYMENT,
        notes='科技点数；MAG/IND 行业产出',
    ),
    'MAGIC': GoodDefinition(
        good_id='MAGIC', category=GoodCategory.MAGIC, unit='点',
        base_price_cents=200_000,  # 2000 CR/点
        stockable=False, shipment_mode=ShipmentMode.INSTANT,
        deliverable_location='either',
        settlement_trigger=SettlementTrigger.ON_PAYMENT,
        notes='魔法点数；MAG 行业产出',
    ),
    'FAITH': GoodDefinition(
        good_id='FAITH', category=GoodCategory.FAITH, unit='点',
        base_price_cents=10_000,  # 100 CR/点
        stockable=False, shipment_mode=ShipmentMode.INSTANT,
        deliverable_location='either',
        settlement_trigger=SettlementTrigger.ON_PAYMENT,
        notes='信仰点数；教会产出',
    ),
    'CIVIC': GoodDefinition(
        good_id='CIVIC', category=GoodCategory.CIVIC, unit='点',
        base_price_cents=80_000,  # 800 CR/点
        stockable=False, shipment_mode=ShipmentMode.INSTANT,
        deliverable_location='either',
        settlement_trigger=SettlementTrigger.ON_PAYMENT,
        notes='市政点数；GOV 产出',
    ),
    'MAGIC_MATERIAL': GoodDefinition(
        good_id='MAGIC_MATERIAL', category=GoodCategory.MAGIC_MATERIAL, unit='单位',
        base_price_cents=100_000,  # 1000 CR/单位
        stockable=True, shipment_mode=ShipmentMode.TRANSIT,
        deliverable_location='either',
        perish_rate=0.0,
        notes='魔法材料；MAG 行业投入',
    ),

    # §4.1 产权类
    'PROPERTY_TITLE': GoodDefinition(
        good_id='PROPERTY_TITLE', category=GoodCategory.PROPERTY_TITLE, unit='套',
        base_price_cents=10_000_000,  # 100,000 CR/套
        stockable=False, shipment_mode=ShipmentMode.DEFERRABLE,
        deliverable_location='either',  # 产权可跨城转让，地理位置不变
        settlement_trigger=SettlementTrigger.ON_PAYMENT,
        contract_transfer_rule='property_cross_city',
        notes='产权；§4.2(d) 跨城转让所有权，房地产所在城市不变',
    ),

    # §4.1 建筑类（V2.1 §4.2(f) 本地建筑）
    'CONSTRUCTION': GoodDefinition(
        good_id='CONSTRUCTION', category=GoodCategory.CONSTRUCTION, unit='单位',
        base_price_cents=20_000,  # 200 CR/单位
        stockable=False, shipment_mode=ShipmentMode.DEFERRABLE,
        deliverable_location='local',  # 建筑施工必须本地履约
        settlement_trigger=SettlementTrigger.ON_DELIVERY,
        contract_transfer_rule='local_only',
        notes='建筑材料与本地履约施工；§4.2(f) 完成建设后才更新本城岗位/住房/产能',
    ),
}

# ===== V2.1 §4.1 必含的 17 类商品清单（用于校验完整性）=====

REQUIRED_17_CATEGORIES: tuple[GoodCategory, ...] = (
    GoodCategory.FOOD, GoodCategory.FUEL, GoodCategory.ORE, GoodCategory.STEEL,
    GoodCategory.GOODS, GoodCategory.LOGISTICS, GoodCategory.SERVICES,
    GoodCategory.FINANCIAL_SERVICE, GoodCategory.EDUCATION, GoodCategory.RESEARCH,
    GoodCategory.MAGIC, GoodCategory.FAITH, GoodCategory.CIVIC,
    GoodCategory.MAGIC_MATERIAL, GoodCategory.PROPERTY_TITLE,
    GoodCategory.CONSTRUCTION, GoodCategory.SECURITY_SERVICE,
)


def assert_catalog_complete() -> None:
    """V2.1 §4.1 校验：17 类商品必须全部定义。"""
    defined_categories = {g.category for g in GOOD_DEFINITIONS.values()}
    missing = set(REQUIRED_17_CATEGORIES) - defined_categories
    if missing:
        raise ValueError(
            f"V2.1 §4.1 商品目录不完整：缺 {sorted(c.value for c in missing)}"
        )


# ===== 商品工厂 =====

def good_ids() -> list[str]:
    """返回所有已定义商品的 good_id 列表。"""
    return list(GOOD_DEFINITIONS.keys())


def get_good(good_id: str) -> GoodDefinition:
    """按 good_id 查商品定义；不存在抛 KeyError。"""
    if good_id not in GOOD_DEFINITIONS:
        raise KeyError(f"V2.1 §4.1 商品不存在：{good_id}")
    return GOOD_DEFINITIONS[good_id]
