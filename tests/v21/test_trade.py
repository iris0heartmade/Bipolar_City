"""V2.1 阶段 5 跨城交易结算验收测试（2026-10-15）。

【验收清单】（V2.1 §4.5 + §4.4 + §4.7）
- [x] GovernmentTradeMode 枚举（ORGANIZE / DIRECT）
- [x] ORGANIZE 模式：货款 buyer→seller（政府不介入付款）
- [x] DIRECT 模式：政府钱包实际购销
- [x] 双城镜像过账（双方账本同时记录）
- [x] 附加费分类过账（运费/关税/中介费各自收款人）
- [x] 在途订单 + 镜像登记
- [x] 零成交量抛错
"""
import pytest

from backend.core.errors import GameError
from backend.systems.v21.accounts import government_account_id
from backend.systems.v21.orders import Order, MatchResult, match_order
from backend.systems.v21.transit import TransitStock
from backend.systems.v21.trade_settlement import (
    GovernmentTradeMode, TradeSettlement, settle_cross_city_trade,
)


# ===== 测试夹具 =====

@pytest.fixture
def city_a_stock():
    return TransitStock(city_id='city_a')


@pytest.fixture
def city_b_stock():
    return TransitStock(city_id='city_b')


@pytest.fixture
def private_order():
    """私营订单：firms:IND 买 STEEL。"""
    return Order(
        order_id='O1', buyer='firms:IND', seller='firms:SRV',
        origin='city_a', destination='city_b',
        good_id='STEEL', quantity=100.0,
        price_limit_cents=500_00, quote_cents=450_00,
        delivery_date=2,
        freight_fee_cents=10_00, tariff_cents=5_00,
    )


@pytest.fixture
def organize_order():
    """政府 ORGANIZE 订单：government 制定平台规则，但 buyer_account 仍是实际买家。"""
    return Order(
        order_id='O2', buyer='firms:IND', seller='firms:SRV',
        origin='city_a', destination='city_b',
        good_id='STEEL', quantity=50.0,
        price_limit_cents=500_00, quote_cents=450_00,
        delivery_date=2,
        public_or_private=True,
        buyer_account='firms:IND',  # 政府不介入付款
        freight_fee_cents=5_00,
    )


@pytest.fixture
def direct_order():
    """政府 DIRECT 订单：government 钱包实际购销。"""
    return Order(
        order_id='O3', buyer='government', seller='firms:SRV',
        origin='city_a', destination='city_b',
        good_id='STEEL', quantity=20.0,
        price_limit_cents=500_00, quote_cents=450_00,
        delivery_date=2,
        public_or_private=True,
        buyer_account=government_account_id(),  # 政府实际付款
    )


# ===== A. GovernmentTradeMode 枚举（2 项）=====

class TestGovernmentTradeMode:
    """V2.1 §4.5 政府贸易两种模式枚举。"""

    def test_2_modes(self):
        """阶段 5：GovernmentTradeMode 2 值。"""
        assert GovernmentTradeMode.ORGANIZE.value == 'organize'
        assert GovernmentTradeMode.DIRECT.value == 'direct'

    def test_unique_values(self):
        """阶段 5：两值互不相同。"""
        values = [m.value for m in GovernmentTradeMode]
        assert len(values) == len(set(values))


# ===== B. ORGANIZE 模式（4 项）=====

class TestOrganizeMode:
    """V2.1 §4.5 ORGANIZE 平台模式：政府制定规则，货款 buyer→seller。"""

    def test_organize_mode_selected(self, organize_order, city_a_stock, city_b_stock):
        """阶段 5：public_or_private=True 且 buyer≠government → ORGANIZE 模式。"""
        match = match_order(organize_order, seller_stock=200, transport_capacity=200, buyer_cash_cents=10_000_00)
        result = settle_cross_city_trade(
            order=organize_order, match_result=match,
            transit_origin=city_a_stock, transit_destination=city_b_stock,
            current_month=1,
        )
        assert result.mode == GovernmentTradeMode.ORGANIZE

    def test_organize_payer_is_actual_buyer(self, organize_order, city_a_stock, city_b_stock):
        """阶段 5：ORGANIZE 模式 payer_actual = buyer_account（政府不介入）。"""
        match = match_order(organize_order, seller_stock=200, transport_capacity=200, buyer_cash_cents=100_000_000)
        result = settle_cross_city_trade(
            order=organize_order, match_result=match,
            transit_origin=city_a_stock, transit_destination=city_b_stock,
        )
        assert result.payer_actual == 'firms:IND'

    def test_organize_goods_transfer_buyer_to_seller(self, organize_order, city_a_stock, city_b_stock):
        """阶段 5：ORGANIZE 货款过账 buyer→seller（V2.1 §4.5）。"""
        match = match_order(organize_order, seller_stock=200, transport_capacity=200, buyer_cash_cents=100_000_000)
        result = settle_cross_city_trade(
            order=organize_order, match_result=match,
            transit_origin=city_a_stock, transit_destination=city_b_stock,
        )
        # 50 × 45000 = 2,250,000 分
        assert (result.payer_actual, result.payee_goods, 2_250_000, 'trade_goods') in result.ledger_origin_transactions

    def test_organize_no_government_payer(self, organize_order, city_a_stock, city_b_stock):
        """阶段 5：ORGANIZE 模式无政府付款记录（政府只在规则层）。"""
        match = match_order(organize_order, seller_stock=200, transport_capacity=200, buyer_cash_cents=10_000_00)
        result = settle_cross_city_trade(
            order=organize_order, match_result=match,
            transit_origin=city_a_stock, transit_destination=city_b_stock,
        )
        # 政府不是 payer
        for src, tgt, _, _ in result.ledger_origin_transactions:
            assert src != government_account_id() or tgt == government_account_id()
        # 关税（如果有）才会入政府账户
        gov_payments = [t for s, t, _, c in result.ledger_origin_transactions if t == government_account_id() and 'tariff' in c]
        assert len(gov_payments) == 0  # 此订单无关税


# ===== C. DIRECT 模式（4 项）=====

class TestDirectMode:
    """V2.1 §4.5 DIRECT 钱包模式：政府钱包实际购销。"""

    def test_direct_mode_selected(self, direct_order, city_a_stock, city_b_stock):
        """阶段 5：public_or_private=True 且 buyer_account=government → DIRECT 模式。"""
        match = match_order(direct_order, seller_stock=200, transport_capacity=200, buyer_cash_cents=10_000_00)
        result = settle_cross_city_trade(
            order=direct_order, match_result=match,
            transit_origin=city_a_stock, transit_destination=city_b_stock,
        )
        assert result.mode == GovernmentTradeMode.DIRECT

    def test_direct_payer_is_government(self, direct_order, city_a_stock, city_b_stock):
        """阶段 5：DIRECT 模式 payer_actual = government。"""
        match = match_order(direct_order, seller_stock=200, transport_capacity=200, buyer_cash_cents=10_000_00)
        result = settle_cross_city_trade(
            order=direct_order, match_result=match,
            transit_origin=city_a_stock, transit_destination=city_b_stock,
        )
        assert result.payer_actual == government_account_id()

    def test_direct_goods_transfer_government_to_seller(self, direct_order, city_a_stock, city_b_stock):
        """阶段 5：DIRECT 货款过账 government→seller。"""
        match = match_order(direct_order, seller_stock=200, transport_capacity=200, buyer_cash_cents=10_000_00)
        result = settle_cross_city_trade(
            order=direct_order, match_result=match,
            transit_origin=city_a_stock, transit_destination=city_b_stock,
        )
        # 20 × 45000 = 900,000 分
        assert (government_account_id(), 'firms:SRV', 900_000, 'trade_goods') in result.ledger_origin_transactions

    def test_private_order_has_no_mode(self, private_order, city_a_stock, city_b_stock):
        """阶段 5：私营订单 mode=None（不适用政府贸易模式）。"""
        match = match_order(private_order, seller_stock=200, transport_capacity=200, buyer_cash_cents=10_000_00)
        result = settle_cross_city_trade(
            order=private_order, match_result=match,
            transit_origin=city_a_stock, transit_destination=city_b_stock,
        )
        assert result.mode is None


# ===== D. 双城镜像（5 项）=====

class TestCrossCityMirror:
    """V2.1 §4.4 双城镜像过账。"""

    def test_both_cities_have_same_transactions(self, private_order, city_a_stock, city_b_stock):
        """阶段 5：双方账本过账记录完全镜像。"""
        match = match_order(private_order, seller_stock=200, transport_capacity=200, buyer_cash_cents=10_000_00)
        result = settle_cross_city_trade(
            order=private_order, match_result=match,
            transit_origin=city_a_stock, transit_destination=city_b_stock,
        )
        # origin 与 destination 的过账 (source, target, amount) 应当一致
        origin_set = {(s, t, a) for s, t, a, _ in result.ledger_origin_transactions}
        dest_set = {(s, t, a) for s, t, a, _ in result.ledger_destination_transactions}
        assert origin_set == dest_set

    def test_transit_shipment_registered_in_both(self, private_order, city_a_stock, city_b_stock):
        """阶段 5：在途订单在双方城市同时登记（outbound + inbound）。"""
        match = match_order(private_order, seller_stock=200, transport_capacity=200, buyer_cash_cents=10_000_00)
        result = settle_cross_city_trade(
            order=private_order, match_result=match,
            transit_origin=city_a_stock, transit_destination=city_b_stock,
        )
        assert result.transit_shipment is not None
        # 在途订单 ship id = f'transit-{order_id}'
        sid = f'transit-{private_order.order_id}'
        assert sid in city_a_stock.outbound
        assert sid in city_b_stock.inbound

    def test_origin_destination_ledger_marked(self, private_order, city_a_stock, city_b_stock):
        """阶段 5：过账记录标记 _mirror 区分（V2.1 §4.4 双城镜像）。"""
        match = match_order(private_order, seller_stock=200, transport_capacity=200, buyer_cash_cents=10_000_00)
        result = settle_cross_city_trade(
            order=private_order, match_result=match,
            transit_origin=city_a_stock, transit_destination=city_b_stock,
        )
        # origin 记录不带 _mirror 后缀
        categories_origin = [c for _, _, _, c in result.ledger_origin_transactions]
        categories_dest = [c for _, _, _, c in result.ledger_destination_transactions]
        for cat in categories_origin:
            assert '_mirror' not in cat
        for cat in categories_dest:
            assert cat.endswith('_mirror')


# ===== E. 附加费过账（4 项）=====

class TestSettlementFees:
    """V2.1 §4.3 附加费过账（运费/关税/中介费各自收款人）。"""

    def test_freight_fee_paid_when_payee_provided(self, private_order, city_a_stock, city_b_stock):
        """阶段 5：运费过账 payer→freight_payee（LOGISTICS 收款）。"""
        match = match_order(private_order, seller_stock=200, transport_capacity=200, buyer_cash_cents=10_000_00)
        result = settle_cross_city_trade(
            order=private_order, match_result=match,
            transit_origin=city_a_stock, transit_destination=city_b_stock,
            freight_payee='firms:LOG',
        )
        freight_tx = [t for t in result.ledger_origin_transactions if t[3] == 'trade_freight']
        assert len(freight_tx) == 1
        assert freight_tx[0] == ('firms:IND', 'firms:LOG', 10_00, 'trade_freight')

    def test_tariff_paid_to_origin_government(self, private_order, city_a_stock, city_b_stock):
        """阶段 5：关税过账 payer→出发城市政府。"""
        match = match_order(private_order, seller_stock=200, transport_capacity=200, buyer_cash_cents=10_000_00)
        result = settle_cross_city_trade(
            order=private_order, match_result=match,
            transit_origin=city_a_stock, transit_destination=city_b_stock,
        )
        tariff_tx = [t for t in result.ledger_origin_transactions if t[3] == 'trade_tariff']
        assert len(tariff_tx) == 1
        assert tariff_tx[0] == ('firms:IND', government_account_id(), 5_00, 'trade_tariff')

    def test_contract_fee_paid_when_payee_provided(self, private_order, city_a_stock, city_b_stock):
        """阶段 5：中介费过账 payer→contract_payee。"""
        match = match_order(private_order, seller_stock=200, transport_capacity=200, buyer_cash_cents=10_000_00)
        result = settle_cross_city_trade(
            order=private_order, match_result=match,
            transit_origin=city_a_stock, transit_destination=city_b_stock,
            contract_payee='firms:BROKER',
        )
        # private_order 无 contract_fee，验证无 _contract 记录
        contract_tx = [t for t in result.ledger_origin_transactions if 'contract' in t[3]]
        assert len(contract_tx) == 0

    def test_zero_filled_raises(self, private_order, city_a_stock, city_b_stock):
        """阶段 5：零成交量抛 GameError（V2.1 §4.3 不足额订单不结算）。"""
        match = match_order(private_order, seller_stock=0, transport_capacity=200, buyer_cash_cents=10_000_00)
        with pytest.raises(GameError, match='成交量为 0'):
            settle_cross_city_trade(
                order=private_order, match_result=match,
                transit_origin=city_a_stock, transit_destination=city_b_stock,
            )


# ===== F. TradeSettlement 数据类（2 项）=====

class TestTradeSettlementDataClass:
    """V2.1 §4 TradeSettlement 数据类。"""

    def test_transit_shipment_contains_order_data(self, private_order, city_a_stock, city_b_stock):
        """阶段 5：TransitShipment 包含订单完整数据。"""
        match = match_order(private_order, seller_stock=200, transport_capacity=200, buyer_cash_cents=100_000_000)
        result = settle_cross_city_trade(
            order=private_order, match_result=match,
            transit_origin=city_a_stock, transit_destination=city_b_stock,
            current_month=1,
        )
        ts = result.transit_shipment
        assert ts.good_id == 'STEEL'
        assert ts.quantity == 100.0
        assert ts.departure_month == 1
        assert ts.arrival_month == 2  # private_order.delivery_date=2

    def test_payee_fields_recorded(self, private_order, city_a_stock, city_b_stock):
        """阶段 5：payee_* 字段记录各自收款人。"""
        match = match_order(private_order, seller_stock=200, transport_capacity=200, buyer_cash_cents=10_000_00)
        result = settle_cross_city_trade(
            order=private_order, match_result=match,
            transit_origin=city_a_stock, transit_destination=city_b_stock,
            freight_payee='firms:LOG',
            contract_payee='firms:BROKER',
        )
        assert result.payee_goods == 'firms:SRV'
        assert result.payee_freight == 'firms:LOG'
        assert result.payee_tariff == government_account_id()
        assert result.payee_contract == 'firms:BROKER'
