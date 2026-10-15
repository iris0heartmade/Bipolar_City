"""V2.1 阶段 5：在途库存（§4.4）。

【2026-10-15】Bipole 城市聚合经济系统理论说明书 V2.1 §4.4 商品库存。

【V2.1 §4.4 关键约束】
- 在途订单计入独立 transit_stock，不在发出后仍留卖方库存，也不提前算买方已收库存。
- Z_g,t+1 = Z_g,t + Q_g,t + imports_delivered_g,t - exports_shipped_g,t
         - domestic_final_use_g,t - intermediate_use_g,t - spoilage_g,t。
- 商品和现金跨城移转必须双方城市同时生成镜像记录。

【V2.1 阶段 5 范围】
本模块承载：
- TransitShipment frozen dataclass（单笔在途订单）
- TransitStock：每城独立的在途库存集合（in_transit 字典）
- 投放在途 / 到货接收 / 双城镜像支持
"""
from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class TransitShipment:
    """V2.1 §4.4 单笔在途订单。

    字段：
    - shipment_id：唯一编号
    - origin / destination：出发城市 / 目的城市
    - good_id：商品编号
    - quantity：在途数量
    - departure_month：发货月份
    - arrival_month：到货月份（V2.1 §4.3 delivery_date）
    - seller_account / buyer_account：卖方/买方账户
    - unit_price_cents：成交单价（整数分）
    - freight_fee_cents / tariff_cents / contract_fee_cents：附加费
    """
    shipment_id: str
    origin: str
    destination: str
    good_id: str
    quantity: float
    departure_month: int
    arrival_month: int
    seller_account: str
    buyer_account: str
    unit_price_cents: int = 0
    freight_fee_cents: int = 0
    tariff_cents: int = 0
    contract_fee_cents: int = 0
    delivered: bool = False
    notes: str = ''

    def total_value_cents(self) -> int:
        """在途订单总值（V2.1 §4.3 V = q*p + 附加费）。"""
        return int(self.quantity * self.unit_price_cents) + self.freight_fee_cents + self.tariff_cents + self.contract_fee_cents


@dataclass
class TransitStock:
    """V2.1 §4.4 单城在途库存。

    每城一个 TransitStock 实例：
    - outbound：从本城发出到邻城的在途订单（待到货）
    - inbound：从邻城发到本城的在途订单（待到货）
    - city_id：本城 ID（用于双城镜像）

    V2.1 §4.4 关键约束：
    - 在途订单不留在卖方库存
    - 也不提前算买方已收库存
    - 商品和现金跨城移转必须双方城市同时生成镜像
    """
    city_id: str
    outbound: dict[str, TransitShipment] = field(default_factory=dict)
    inbound: dict[str, TransitShipment] = field(default_factory=dict)

    def dispatch(self, shipment: TransitShipment) -> TransitShipment:
        """发货：在 outbound 中登记。

        卖方应在调用 dispatch 之前已扣减库存（V2.1 §4.4：发出后不留在卖方库存）。
        """
        if shipment.shipment_id in self.outbound or shipment.shipment_id in self.inbound:
            raise KeyError(f"在途订单 ID 重复：{shipment.shipment_id}")
        self.outbound[shipment.shipment_id] = shipment
        return shipment

    def receive(self, shipment_id: str) -> TransitShipment:
        """到货接收：从 inbound 移除并标记 delivered=True。

        返回接收的 shipment（含原信息）。
        """
        if shipment_id in self.inbound:
            shipment = self.inbound[shipment_id]
            del self.inbound[shipment_id]
            # frozen：用 object.__setattr__ 标记 delivered
            object.__setattr__(shipment, 'delivered', True)
            return shipment
        raise KeyError(f"本城无对应在途订单：{shipment_id}")

    def arrive_due(self, current_month: int) -> list[TransitShipment]:
        """查询本城待到货订单（arrival_month <= current_month）。"""
        return [s for s in self.inbound.values() if s.arrival_month <= current_month]

    def outbound_value_cents(self) -> int:
        """本城对外在途订单总值（含附加费）。"""
        return sum(s.total_value_cents() for s in self.outbound.values())

    def inbound_value_cents(self) -> int:
        """本城对内在途订单总值。"""
        return sum(s.total_value_cents() for s in self.inbound.values())

    def mirror_for_remote(self, shipment: TransitShipment) -> TransitShipment:
        """V2.1 §4.4 双城镜像：在另一城登记对应 inbound。

        调用方负责：发起城市 dispatch + 目的城市 mirror_for_remote 同时进行。
        """
        mirror = TransitShipment(
            shipment_id=shipment.shipment_id,
            origin=shipment.origin,
            destination=shipment.destination,
            good_id=shipment.good_id,
            quantity=shipment.quantity,
            departure_month=shipment.departure_month,
            arrival_month=shipment.arrival_month,
            seller_account=shipment.seller_account,
            buyer_account=shipment.buyer_account,
            unit_price_cents=shipment.unit_price_cents,
            freight_fee_cents=shipment.freight_fee_cents,
            tariff_cents=shipment.tariff_cents,
            contract_fee_cents=shipment.contract_fee_cents,
            delivered=False,
            notes=f'mirror from {self.city_id}',
        )
        self.inbound[shipment.shipment_id] = mirror
        return mirror


# ===== 双城在途镜像（V2.1 §4.4 双方同时生成镜像）=====

def dispatch_cross_city(
    sender: TransitStock,
    receiver: TransitStock,
    shipment: TransitShipment,
) -> tuple[TransitShipment, TransitShipment]:
    """V2.1 §4.4 跨城在途订单：双方城市同时登记镜像。

    参数：
    - sender：出发城市的 TransitStock（outbound 登记）
    - receiver：目的城市的 TransitStock（inbound 登记）
    - shipment：TransitShipment 实例

    返回：(sender 镜像, receiver 镜像)
    """
    if shipment.origin != sender.city_id:
        raise ValueError(f"发货城市不匹配：shipment.origin={shipment.origin} 但 sender.city_id={sender.city_id}")
    if shipment.destination != receiver.city_id:
        raise ValueError(f"收货城市不匹配：shipment.destination={shipment.destination} 但 receiver.city_id={receiver.city_id}")

    outbound = sender.dispatch(shipment)
    inbound = receiver.mirror_for_remote(shipment)
    return outbound, inbound
