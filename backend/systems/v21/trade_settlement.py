"""V2.1 阶段 5：跨城交易结算与政府贸易模式（§4.5 + §4.7 + 双城镜像）。

【2026-10-15】Bipole 城市聚合经济系统理论说明书 V2.1 §4.5 + §4.7。

【V2.1 §4.5 政府贸易两种模式】
- ORGANIZE：政府制定协议与平台规则，但货款直接在实际行业/产权持有者之间转移。
  （政府不实际收钱/付钱，仅作平台；货款 buyer→seller）
- DIRECT：政府使用市政钱包实际购销，用于储备/公共建设/政府资产经营。
  （政府真实收钱/付钱；buyer=GOV）

【V2.1 §4.7 市场预算来源】
- 居民来自消费预算；
- 行业来自期初流动资金/经营贷款/本期已到账有效资金；
- 政府来自财政现金/授权融资；
- 境外买家来自对应城市真实支付账户。
- 销售收入到账后可以支持下月运营；不能让本月同一笔「预计销售」无限循环资助自身需求。

【V2.1 §4.4 双城镜像】
- 商品和现金跨城移转必须双方城市同时生成镜像记录。

【V2.1 阶段 5 范围】
本模块承载：
- GovernmentTradeMode 枚举（ORGANIZE / DIRECT）
- settle_order()：单笔订单结算（含政府模式判定 + 双城镜像过账）
- TradeSettlement 数据：返回两城账本各自的过账记录
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from backend.core.errors import GameError
from backend.systems.v21.accounts import government_account_id
from backend.systems.v21.goods import get_good
from backend.systems.v21.orders import Order, MatchResult
from backend.systems.v21.transit import TransitStock, TransitShipment, dispatch_cross_city


class GovernmentTradeMode(str, Enum):
    """V2.1 §4.5 政府贸易两种模式。"""
    ORGANIZE = 'organize'  # 平台模式：货款 buyer→seller，政府不介入
    DIRECT = 'direct'      # 钱包模式：政府钱包实际购销


@dataclass
class TradeSettlement:
    """V2.1 §4 单笔跨城交易结算结果。

    字段：
    - order_id：订单 ID
    - q_filled：成交量
    - mode：政府贸易模式（仅 public_or_private=True 才有意义）
    - payer_actual：实际付款账户（ORGANIZE 时 = buyer；DIRECT 时 = 政府）
    - payee_goods：商品收款方（卖方账户）
    - payee_freight：运费收款方（物流账户）
    - payee_tariff：关税收款方（出发城市政府）
    - payee_contract：中介费收款方（中介账户）
    - ledger_origin_transactions：出发城市账本过账记录
    - ledger_destination_transactions：目的城市账本过账记录
    - transit_shipment：在途订单（用于双城镜像）
    """
    order_id: str
    q_filled: float
    mode: Optional[GovernmentTradeMode]
    payer_actual: str
    payee_goods: str
    payee_freight: Optional[str]
    payee_tariff: str
    payee_contract: Optional[str]
    ledger_origin_transactions: list[tuple[str, str, int, str]] = field(default_factory=list)
    ledger_destination_transactions: list[tuple[str, str, int, str]] = field(default_factory=list)
    transit_shipment: Optional[TransitShipment] = None


def settle_cross_city_trade(
    *,
    order: Order,
    match_result: MatchResult,
    transit_origin: TransitStock,
    transit_destination: TransitStock,
    freight_payee: Optional[str] = None,
    contract_payee: Optional[str] = None,
    current_month: int = 0,
) -> TradeSettlement:
    """V2.1 §4.5 + §4.4 跨城交易结算。

    步骤：
    1. 政府模式判定（仅 public_or_private=True 生效）
       - ORGANIZE：payer_actual = buyer（政府不介入付款）
       - DIRECT：payer_actual = government（政府钱包实际购销）
    2. 实际付款账户 → 卖方账户（货款）
    3. 实际付款账户 → 物流账户（运费；如有）
    4. 实际付款账户 → 出发城市政府（关税；如有）
    5. 实际付款账户 → 中介账户（中介费；如有）
    6. 创建 TransitShipment + 双城镜像（origin.dispatch + destination.mirror_for_remote）

    参数：
    - order：原始订单
    - match_result：match_order() 的结果（必须有 q_filled > 0）
    - transit_origin / transit_destination：双方城市在途库存
    - freight_payee：物流服务收款账户（如不传则无运费过账）
    - contract_payee：中介费收款账户（如不传则无中介费过账）
    - current_month：当前月份（用于在途订单 departure_month）

    返回：TradeSettlement（双方过账记录 + 在途订单）

    抛 GameError 当成交量为 0。
    """
    if match_result.q_filled <= 0:
        raise GameError(f"订单 {order.order_id} 成交量为 0，不能结算")

    # 1. 政府模式判定
    if order.public_or_private:
        mode = GovernmentTradeMode.ORGANIZE  # 默认 ORGANIZE（V2.1 §4.5）
        payer_actual = order.buyer_account
        # 注：DIRECT 模式在订单层用 buyer_account = government_account_id() 表示
        if order.buyer_account == government_account_id():
            mode = GovernmentTradeMode.DIRECT
            payer_actual = government_account_id()
    else:
        mode = None
        payer_actual = order.buyer_account

    # 2-5. 计算各附加费
    unit_price = match_result.effective_unit_price_cents
    goods_value = int(match_result.q_filled * unit_price)
    freight_value = order.freight_fee_cents
    tariff_value = order.tariff_cents
    contract_value = order.contract_fee_cents

    # V2.1 §4.4 双城镜像过账（商品现金同时移动）
    origin_transactions = []
    destination_transactions = []

    # 货款：payer → seller
    origin_transactions.append((payer_actual, order.seller, goods_value, 'trade_goods'))
    destination_transactions.append((payer_actual, order.seller, goods_value, 'trade_goods_mirror'))

    # 运费：payer → freight_payee
    if freight_value > 0 and freight_payee:
        origin_transactions.append((payer_actual, freight_payee, freight_value, 'trade_freight'))
        destination_transactions.append((payer_actual, freight_payee, freight_value, 'trade_freight_mirror'))

    # 关税：payer → 出发城市政府
    if tariff_value > 0:
        origin_gov = government_account_id_for_city(order.origin)
        origin_transactions.append((payer_actual, origin_gov, tariff_value, 'trade_tariff'))
        destination_transactions.append((payer_actual, origin_gov, tariff_value, 'trade_tariff_mirror'))

    # 中介费：payer → 中介账户
    if contract_value > 0 and contract_payee:
        origin_transactions.append((payer_actual, contract_payee, contract_value, 'trade_contract'))
        destination_transactions.append((payer_actual, contract_payee, contract_value, 'trade_contract_mirror'))

    # 6. 在途订单 + 双城镜像
    transit = TransitShipment(
        shipment_id=f'transit-{order.order_id}',
        origin=order.origin,
        destination=order.destination,
        good_id=order.good_id,
        quantity=match_result.q_filled,
        departure_month=current_month,
        arrival_month=order.delivery_date,
        seller_account=order.seller,
        buyer_account=order.beneficiary,
        unit_price_cents=unit_price,
        freight_fee_cents=freight_value,
        tariff_cents=tariff_value,
        contract_fee_cents=contract_value,
    )
    dispatch_cross_city(transit_origin, transit_destination, transit)

    return TradeSettlement(
        order_id=order.order_id,
        q_filled=match_result.q_filled,
        mode=mode,
        payer_actual=payer_actual,
        payee_goods=order.seller,
        payee_freight=freight_payee,
        payee_tariff=government_account_id_for_city(order.origin),
        payee_contract=contract_payee,
        ledger_origin_transactions=origin_transactions,
        ledger_destination_transactions=destination_transactions,
        transit_shipment=transit,
    )


def government_account_id_for_city(city_id: str) -> str:
    """V2.1 §4.7 城市政府账户 ID（单城时直接 government_account_id()）。"""
    # V2.1 §4.4 双城镜像需要区分不同城市的政府账户；
    # 单城阶段用统一 government_account_id()；多城阶段由 §11 迁移扩展
    return government_account_id()
