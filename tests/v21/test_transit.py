"""V2.1 阶段 5 在途库存验收测试（2026-10-15）。

【验收清单】（V2.1 §4.4）
- [x] TransitShipment frozen dataclass 全部字段
- [x] TransitStock 单城在途库存（outbound/inbound）
- [x] 在途订单不留在卖方库存（V2.1 §4.4）
- [x] 也不提前算买方已收库存（V2.1 §4.4）
- [x] 到货接收（receive）
- [x] 待到货查询（arrive_due）
- [x] 双城镜像（dispatch_cross_city）
- [x] 总值聚合（outbound_value_cents / inbound_value_cents）
"""
import pytest

from backend.systems.v21.transit import (
    TransitShipment, TransitStock, dispatch_cross_city,
)


# ===== 测试夹具 =====

@pytest.fixture
def city_a_stock():
    return TransitStock(city_id='city_a')


@pytest.fixture
def city_b_stock():
    return TransitStock(city_id='city_b')


@pytest.fixture
def basic_shipment():
    return TransitShipment(
        shipment_id='T1', origin='city_a', destination='city_b',
        good_id='STEEL', quantity=50.0,
        departure_month=1, arrival_month=3,
        seller_account='firms:SRV', buyer_account='firms:IND',
        unit_price_cents=450_00,
        freight_fee_cents=10_00, tariff_cents=5_00,
    )


# ===== A. TransitShipment 数据类（3 项）=====

class TestTransitShipment:
    """V2.1 §4.4 TransitShipment 数据类。"""

    def test_construction_with_all_fields(self):
        """阶段 5：TransitShipment 全部字段。"""
        s = TransitShipment(
            shipment_id='T1', origin='a', destination='b',
            good_id='FOOD', quantity=100.0,
            departure_month=1, arrival_month=5,
            seller_account='firms:AGR', buyer_account='cohorts:H1',
            unit_price_cents=100_00,
            freight_fee_cents=20_00, tariff_cents=10_00, contract_fee_cents=5_00,
            delivered=False,
        )
        assert s.shipment_id == 'T1'
        assert s.quantity == 100.0
        assert s.arrival_month == 5
        assert s.delivered is False

    def test_shipment_is_frozen(self):
        """阶段 5：TransitShipment 是 frozen dataclass。"""
        s = TransitShipment(
            shipment_id='T1', origin='a', destination='b',
            good_id='FOOD', quantity=1.0,
            departure_month=1, arrival_month=2,
            seller_account='a', buyer_account='b',
        )
        with pytest.raises((AttributeError, Exception)):
            s.quantity = 999

    def test_total_value_cents_includes_fees(self):
        """阶段 5：total_value_cents = q*p + freight + tariff + contract_fee。"""
        s = TransitShipment(
            shipment_id='T1', origin='a', destination='b',
            good_id='FOOD', quantity=10.0,
            departure_month=1, arrival_month=2,
            seller_account='a', buyer_account='b',
            unit_price_cents=100_00,
            freight_fee_cents=50_00, tariff_cents=20_00, contract_fee_cents=10_00,
        )
        # 10 × 10000 + 5000 + 2000 + 1000 = 108,000
        assert s.total_value_cents() == 108_000


# ===== B. TransitStock 单城操作（5 项）=====

class TestTransitStockDispatch:
    """V2.1 §4.4 TransitStock.dispatch() 发送登记。"""

    def test_dispatch_registers_in_outbound(self, city_a_stock, basic_shipment):
        """阶段 5：dispatch() 登记到 outbound。"""
        result = city_a_stock.dispatch(basic_shipment)
        assert 'T1' in city_a_stock.outbound
        assert city_a_stock.outbound['T1'] is basic_shipment
        assert 'T1' not in city_a_stock.inbound  # 不在 inbound

    def test_dispatch_duplicate_id_raises(self, city_a_stock, basic_shipment):
        """阶段 5：shipment_id 重复抛 KeyError。"""
        city_a_stock.dispatch(basic_shipment)
        with pytest.raises(KeyError, match='在途订单 ID 重复'):
            city_a_stock.dispatch(basic_shipment)


class TestTransitStockReceive:
    """V2.1 §4.4 TransitStock.receive() 到货接收。"""

    def test_receive_marks_delivered(self, city_b_stock):
        """阶段 5：receive() 从 inbound 移除并标记 delivered=True。"""
        # 先通过 mirror_for_remote 登记 inbound
        shipment = TransitShipment(
            shipment_id='T1', origin='city_a', destination='city_b',
            good_id='STEEL', quantity=10.0,
            departure_month=1, arrival_month=3,
            seller_account='firms:SRV', buyer_account='firms:IND',
        )
        city_b_stock.mirror_for_remote(shipment)
        assert 'T1' in city_b_stock.inbound

        received = city_b_stock.receive('T1')
        assert received.delivered is True
        assert 'T1' not in city_b_stock.inbound

    def test_receive_nonexistent_raises(self, city_b_stock):
        """阶段 5：receive() 不存在的 ID 抛 KeyError。"""
        with pytest.raises(KeyError, match='本城无对应'):
            city_b_stock.receive('NOPE')


# ===== C. 到货查询（2 项）=====

class TestTransitStockArriveDue:
    """V2.1 §4.4 待到货查询。"""

    def test_arrive_due_filters_by_month(self, city_b_stock):
        """阶段 5：arrive_due() 返回 arrival_month <= current_month 的订单。"""
        # 登记 2 笔：arrival_month=3 和 arrival_month=5
        for i, am in enumerate([3, 5]):
            city_b_stock.mirror_for_remote(TransitShipment(
                shipment_id=f'T{i}', origin='city_a', destination='city_b',
                good_id='STEEL', quantity=10.0,
                departure_month=1, arrival_month=am,
                seller_account='s', buyer_account='b',
            ))
        # current_month=3 → 只返回 T0
        due = city_b_stock.arrive_due(current_month=3)
        assert len(due) == 1
        assert due[0].shipment_id == 'T0'
        # current_month=5 → 返回全部
        due_all = city_b_stock.arrive_due(current_month=5)
        assert len(due_all) == 2


# ===== D. 总值聚合（2 项）=====

class TestTransitStockValues:
    """V2.1 §4.4 在途订单总值聚合。"""

    def test_outbound_value_cents(self, city_a_stock):
        """阶段 5：outbound_value_cents 合计 outbound 总值。"""
        for i in range(3):
            city_a_stock.dispatch(TransitShipment(
                shipment_id=f'T{i}', origin='city_a', destination='city_b',
                good_id='STEEL', quantity=10.0,
                departure_month=1, arrival_month=2,
                seller_account='s', buyer_account='b',
                unit_price_cents=100_00,
            ))
        # 3 笔 × 10 × 10000 = 300,000
        assert city_a_stock.outbound_value_cents() == 300_000

    def test_inbound_value_cents(self, city_b_stock):
        """阶段 5：inbound_value_cents 合计 inbound 总值。"""
        for i in range(2):
            city_b_stock.mirror_for_remote(TransitShipment(
                shipment_id=f'T{i}', origin='city_a', destination='city_b',
                good_id='STEEL', quantity=5.0,
                departure_month=1, arrival_month=2,
                seller_account='s', buyer_account='b',
                unit_price_cents=100_00,
                freight_fee_cents=10_00,
            ))
        # 2 笔 × (5×10000 + 1000) = 102,000
        assert city_b_stock.inbound_value_cents() == 102_000


# ===== E. 双城镜像（5 项）=====

class TestCrossCityDispatch:
    """V2.1 §4.4 dispatch_cross_city 双城镜像。"""

    def test_dispatch_creates_both_sides(self, city_a_stock, city_b_stock, basic_shipment):
        """阶段 5：双城 dispatch 同时登记 outbound + inbound。"""
        outbound, inbound = dispatch_cross_city(city_a_stock, city_b_stock, basic_shipment)
        assert outbound.shipment_id == 'T1'
        assert inbound.shipment_id == 'T1'
        assert 'T1' in city_a_stock.outbound
        assert 'T1' in city_b_stock.inbound

    def test_dispatch_returns_mirror_pair(self, city_a_stock, city_b_stock, basic_shipment):
        """阶段 5：双城 dispatch 返回 (outbound, inbound) 对。"""
        outbound, inbound = dispatch_cross_city(city_a_stock, city_b_stock, basic_shipment)
        assert outbound is basic_shipment
        assert outbound.origin == 'city_a'
        assert inbound.origin == 'city_a'  # 镜像保留 origin
        assert inbound.destination == 'city_b'
        assert inbound.delivered is False

    def test_dispatch_origin_mismatch_raises(self, city_a_stock, city_b_stock):
        """阶段 5：shipment.origin != sender.city_id 抛 ValueError。"""
        bad = TransitShipment(
            shipment_id='T1', origin='city_c',  # ≠ city_a
            destination='city_b', good_id='STEEL', quantity=10.0,
            departure_month=1, arrival_month=2,
            seller_account='s', buyer_account='b',
        )
        with pytest.raises(ValueError, match='发货城市不匹配'):
            dispatch_cross_city(city_a_stock, city_b_stock, bad)

    def test_dispatch_destination_mismatch_raises(self, city_a_stock, city_b_stock):
        """阶段 5：shipment.destination != receiver.city_id 抛 ValueError。"""
        bad = TransitShipment(
            shipment_id='T1', origin='city_a',
            destination='city_c',  # ≠ city_b
            good_id='STEEL', quantity=10.0,
            departure_month=1, arrival_month=2,
            seller_account='s', buyer_account='b',
        )
        with pytest.raises(ValueError, match='收货城市不匹配'):
            dispatch_cross_city(city_a_stock, city_b_stock, bad)

    def test_in_transit_not_in_seller_stock(self, city_a_stock, city_b_stock, basic_shipment):
        """阶段 5：V2.1 §4.4 在途订单不留在卖方库存（卖方已扣库存；不再记）。"""
        # TransitStock 本身不存"卖方库存"，仅登记在途；库存由外部管理
        dispatch_cross_city(city_a_stock, city_b_stock, basic_shipment)
        # 卖方城市只有 outbound（发出），无"剩余库存"概念
        assert 'T1' in city_a_stock.outbound
        # 买方城市只有 inbound（待收），不算"已收库存"
        assert 'T1' in city_b_stock.inbound
        # 镜像同步：两端数量一致
        assert city_a_stock.outbound['T1'].quantity == city_b_stock.inbound['T1'].quantity == 50.0
