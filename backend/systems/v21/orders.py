"""V2.1 阶段 5：订单与买卖匹配（§4.3）。

【2026-10-15】Bipole 城市聚合经济系统理论说明书 V2.1 §4.3 买卖匹配与库存约束。

【V2.1 §4.3 关键约束】
- 每条订单 Order={buyer, seller, origin, destination, good_id, quantity,
  price_limit, quote, delivery_date, buyer_account, beneficiary, public_or_private}。
- 成交量：q = min(订单量, 卖方可销售存量/当期可交付量, 运输容量, 买方实付能力/含税到岸单价)。
- 货物成交价由有效报价与允许价格条件确定。
- 对跨城有期限的交付使用在途库存。
- 金额：V = q*p + freight_fee + tariffs + contract_fees（每项对应实际收款人）。
- 不足额订单：按资金不足/库存不足/运输不足产生未成交记录，不是负库存或透支。

【V2.1 阶段 5 范围】
本模块承载：OrderStatus 枚举、Order 数据类（12 个字段全部实现）、OrderBook
集合（按 buyer/seller/origin 聚合）、match_order() 成交量计算函数。
实际双城镜像过账由 trade_settlement.py 落地。
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from backend.core.errors import GameError
from backend.systems.v21.goods import GoodDefinition, get_good


class OrderStatus(str, Enum):
    """V2.1 §4.3 订单状态。"""
    PENDING = 'pending'      # 待成交（订单刚下）
    PARTIAL = 'partial'      # 部分成交（q < 订单量）
    FILLED = 'filled'        # 全部成交（q == 订单量）
    CANCELLED = 'cancelled'  # 取消


@dataclass(frozen=True)
class Order:
    """V2.1 §4.3 订单数据。

    字段对照 V2.1 §4.3 规范：
    - buyer / seller：账户 ID（可能是本城行业账户或邻城账户）
    - origin / destination：城市 ID
    - good_id：商品编号（与 GoodDefinition.good_id 对齐）
    - quantity：订购量（浮点；实物按单位/服务按次/产权按套）
    - price_limit_cents：买方最高可接受单价（整数分）
    - quote_cents：卖方报价（整数分）
    - delivery_date：交付月份（V2.1 §4.4 在途库存）
    - buyer_account / beneficiary：付款账户与受益账户
    - public_or_private：政府采购标志（True 时 §4.5 ORGANIZE/DIRECT 生效）
    - freight_fee_cents / tariff_cents / contract_fee_cents：附加费（每项收款人不同）
    """
    order_id: str
    buyer: str                  # 买方账户 ID
    seller: str                 # 卖方账户 ID
    origin: str                 # 出发城市 ID
    destination: str            # 目的城市 ID
    good_id: str                # 商品编号
    quantity: float             # 订购量
    price_limit_cents: int      # 买方最高单价（整数分）
    quote_cents: int            # 卖方报价（整数分）
    delivery_date: int          # 交付月份（V2.1 §4.4 在途库存）
    buyer_account: str = ''     # 付款账户（默认等于 buyer）
    beneficiary: str = ''       # 受益账户（实物交付对象；产权类 = 产权受让人）
    public_or_private: bool = False  # True = 政府采购（§4.5 ORGANIZE/DIRECT）
    freight_fee_cents: int = 0  # 运费（收款人 = 物流账户）
    tariff_cents: int = 0       # 关税（收款人 = 出发城市政府）
    contract_fee_cents: int = 0 # 中介费（收款人 = 中介账户）
    status: OrderStatus = OrderStatus.PENDING
    notes: str = ''

    def __post_init__(self):
        # 默认值收敛（frozen 不能直接赋值，用 object.__setattr__）
        if not self.buyer_account:
            object.__setattr__(self, 'buyer_account', self.buyer)
        if not self.beneficiary:
            object.__setattr__(self, 'beneficiary', self.buyer)

    @property
    def good(self) -> GoodDefinition:
        """订单对应的商品定义。"""
        return get_good(self.good_id)

    def total_value_cents(self) -> int:
        """V2.1 §4.3 金额：V = q*p + freight_fee + tariffs + contract_fees。

        注意：实际成交价由 match_order 决定的 effective_price_cents，不一定等于 quote。
        本方法仅在已成交后使用 effective_price_cents（外部传入）。
        """
        # 仅为占位；实际成交金额由 match_order 返回
        return int(self.quantity * self.quote_cents) + self.freight_fee_cents + self.tariff_cents + self.contract_fee_cents

    def effective_unit_price_cents(self) -> int:
        """实际成交单价（min(price_limit, quote)）；用于结算。"""
        return min(self.price_limit_cents, self.quote_cents)


@dataclass
class MatchResult:
    """V2.1 §4.3 订单匹配结果。

    q_filled：成交量（受 4 约束中的最小值）
    constraint_tight：最紧约束（'order'/'stock'/'transport'/'cash'）
    shortfall：未成交数量 = quantity - q_filled
    effective_unit_price_cents：实际成交单价
    """
    order: Order
    q_filled: float
    constraint_tight: str          # 'order' | 'stock' | 'transport' | 'cash'
    shortfall: float               # quantity - q_filled
    effective_unit_price_cents: int
    total_value_cents: int         # V = q*p + freight + tariff + contract_fee
    status: OrderStatus

    @property
    def is_fully_filled(self) -> bool:
        return self.q_filled >= self.order.quantity

    @property
    def is_partial(self) -> bool:
        return 0 < self.q_filled < self.order.quantity


def match_order(
    order: Order,
    *,
    seller_stock: float,            # 卖方库存/当期可交付量
    transport_capacity: float,      # 运输容量（LOGISTICS 类按此约束；其他默认无限）
    buyer_cash_cents: int,           # 买方实付能力（整数分）
    landed_unit_price_cents: Optional[int] = None,  # 含税到岸单价；默认 = effective_unit_price + 单位附加费
) -> MatchResult:
    """V2.1 §4.3 成交量计算：q = min(订单量, 卖方库存, 运输容量, 买方实付能力/含税到岸单价)。

    参数：
    - seller_stock：卖方可销售存量或当期可交付量
    - transport_capacity：当月运输容量（实物运输才有约束；服务/产权默认无限）
    - buyer_cash_cents：买方实付能力（整数分）
    - landed_unit_price_cents：含税到岸单价（= effective_price + 单位附加费）；默认用 effective_price

    返回：MatchResult，含成交量与最紧约束。

    抛 GameError 当订单基本字段错误（quantity<=0 / 价格越界）。
    """
    if order.quantity <= 0:
        raise GameError(f"订单 {order.order_id} 数量必须为正：{order.quantity}")
    if order.price_limit_cents <= 0 or order.quote_cents <= 0:
        raise GameError(f"订单 {order.order_id} 报价/限价必须为正")

    effective_price = order.effective_unit_price_cents()
    landed = landed_unit_price_cents if landed_unit_price_cents is not None else effective_price

    # 四约束（V2.1 §4.3 q = min）
    q_by_order = order.quantity
    q_by_stock = max(0.0, float(seller_stock))
    q_by_transport = max(0.0, float(transport_capacity))

    # 运输约束：非实物/非可储的服务可豁免（瞬时交付）；实物/可储必走运输
    if order.good.shipment_mode.value == 'instant' and not order.good.stockable:
        # 不可储的瞬时服务（如 FINANCIAL_SERVICE/RESEARCH/MAGIC/FAITH/CIVIC）
        q_by_transport = float('inf')

    if landed <= 0:
        q_by_cash = 0.0
    else:
        # 买方实付能力 = cash_cents（整数分）// landed_unit_price_cents（整数分）
        q_by_cash = max(0.0, float(buyer_cash_cents) // landed)

    # 取最小
    candidates = [
        ('order', q_by_order),
        ('stock', q_by_stock),
        ('transport', q_by_transport),
        ('cash', q_by_cash),
    ]
    constraint_tight, q_filled = min(candidates, key=lambda x: x[1])
    q_filled = max(0.0, float(q_filled))
    # 处理 inf：若所有约束都不限，q_filled = 订单量
    if q_filled == float('inf'):
        q_filled = q_by_order

    shortfall = max(0.0, order.quantity - q_filled)

    # 成交金额（V2.1 §4.3 V = q*p + freight + tariff + contract_fee）
    unit_price = effective_price
    total_value = int(q_filled * unit_price) + order.freight_fee_cents + order.tariff_cents + order.contract_fee_cents

    # 状态判定
    if q_filled >= order.quantity:
        status = OrderStatus.FILLED
    elif q_filled > 0:
        status = OrderStatus.PARTIAL
    else:
        status = OrderStatus.PENDING  # 完全没成交保持 PENDING

    return MatchResult(
        order=order,
        q_filled=q_filled,
        constraint_tight=constraint_tight,
        shortfall=shortfall,
        effective_unit_price_cents=unit_price,
        total_value_cents=total_value,
        status=status,
    )


class OrderBook:
    """V2.1 §4 订单簿：所有订单的集合。

    order_id 唯一；按 buyer/seller/origin 聚合查询。
    """
    def __init__(self):
        self._orders: dict[str, Order] = {}

    def add(self, order: Order) -> Order:
        """添加订单。order_id 必须唯一。"""
        if order.order_id in self._orders:
            raise KeyError(f"订单 ID 重复：{order.order_id}")
        if order.quantity <= 0:
            raise ValueError(f"订单数量必须为正：{order.order_id}")
        self._orders[order.order_id] = order
        return order

    def get(self, order_id: str) -> Optional[Order]:
        return self._orders.get(order_id)

    def __contains__(self, order_id: str) -> bool:
        return order_id in self._orders

    def __iter__(self):
        return iter(self._orders.values())

    def orders_by_buyer(self, buyer_account: str) -> list[Order]:
        return [o for o in self._orders.values() if o.buyer == buyer_account]

    def orders_by_seller(self, seller_account: str) -> list[Order]:
        return [o for o in self._orders.values() if o.seller == seller_account]

    def orders_by_origin(self, origin_city_id: str) -> list[Order]:
        """V2.1 §4.4 跨城订单按出发城市聚合。"""
        return [o for o in self._orders.values() if o.origin == origin_city_id]

    def orders_by_destination(self, destination_city_id: str) -> list[Order]:
        return [o for o in self._orders.values() if o.destination == destination_city_id]

    def cancel(self, order_id: str) -> Order:
        """取消订单（V2.1 §4.3 不足额订单产生未成交记录，不透支）。"""
        if order_id not in self._orders:
            raise KeyError(f"订单不存在：{order_id}")
        order = self._orders[order_id]
        if order.status == OrderStatus.FILLED:
            raise GameError(f"订单 {order_id} 已全部成交，不能取消")
        # frozen dataclass：用 replace
        cancelled = Order(
            order_id=order.order_id, buyer=order.buyer, seller=order.seller,
            origin=order.origin, destination=order.destination,
            good_id=order.good_id, quantity=order.quantity,
            price_limit_cents=order.price_limit_cents, quote_cents=order.quote_cents,
            delivery_date=order.delivery_date, buyer_account=order.buyer_account,
            beneficiary=order.beneficiary, public_or_private=order.public_or_private,
            freight_fee_cents=order.freight_fee_cents, tariff_cents=order.tariff_cents,
            contract_fee_cents=order.contract_fee_cents, status=OrderStatus.CANCELLED,
            notes=order.notes,
        )
        self._orders[order_id] = cancelled
        return cancelled
