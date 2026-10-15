"""V2.1 阶段 5 订单与买卖匹配验收测试（2026-10-15）。

【验收清单】（V2.1 §4.3）
- [x] OrderStatus 枚举 4 值
- [x] Order 数据类 12 字段
- [x] OrderBook 集合（add/get/iter/by_buyer/by_seller/by_origin/by_destination/cancel）
- [x] match_order 4 约束：q = min(订单量, 卖方库存, 运输容量, 买方实付能力)
- [x] 部分成交/全部成交/零成交状态判定
- [x] 含税到岸单价计算
- [x] 不可储瞬时服务豁免运输约束
- [x] 取消订单（不足额产生未成交记录）
"""
import pytest

from backend.core.errors import GameError
from backend.systems.v21.goods import get_good
from backend.systems.v21.orders import (
    OrderStatus, Order, MatchResult, OrderBook, match_order,
)


# ===== 测试夹具 =====

@pytest.fixture
def basic_order():
    """基础订单：买 IND 100 吨 STEEL @ 500 CR。"""
    return Order(
        order_id='O1', buyer='firms:IND', seller='firms:SRV',
        origin='city_a', destination='city_b',
        good_id='STEEL', quantity=100.0,
        price_limit_cents=500_00, quote_cents=450_00,
        delivery_date=2,
        freight_fee_cents=10_00, tariff_cents=5_00,
    )


@pytest.fixture
def point_order():
    """点数订单：买 50 点 RESEARCH @ 500 CR。"""
    return Order(
        order_id='O2', buyer='firms:IND', seller='firms:MAG',
        origin='city_a', destination='city_b',
        good_id='RESEARCH', quantity=50.0,
        price_limit_cents=500_00, quote_cents=480_00,
        delivery_date=1,
    )


# ===== A. OrderStatus 枚举（2 项）=====

class TestOrderStatusEnum:
    """V2.1 §4.3 OrderStatus 枚举。"""

    def test_4_status_values(self):
        """阶段 5：OrderStatus 4 值。"""
        assert OrderStatus.PENDING.value == 'pending'
        assert OrderStatus.PARTIAL.value == 'partial'
        assert OrderStatus.FILLED.value == 'filled'
        assert OrderStatus.CANCELLED.value == 'cancelled'

    def test_unique_values(self):
        """阶段 5：4 个状态值互不相同。"""
        values = [s.value for s in OrderStatus]
        assert len(values) == len(set(values))


# ===== B. Order 数据类（5 项）=====

class TestOrderDataClass:
    """V2.1 §4.3 Order 数据类 12 字段。"""

    def test_construction_with_defaults(self):
        """阶段 5：默认值收敛（buyer_account = buyer, beneficiary = buyer）。"""
        o = Order(
            order_id='O1', buyer='firms:IND', seller='firms:SRV',
            origin='city_a', destination='city_b',
            good_id='STEEL', quantity=100.0,
            price_limit_cents=500_00, quote_cents=450_00,
            delivery_date=2,
        )
        assert o.buyer_account == 'firms:IND'  # 默认 = buyer
        assert o.beneficiary == 'firms:IND'      # 默认 = buyer
        assert o.public_or_private is False
        assert o.freight_fee_cents == 0

    def test_explicit_buyer_account_and_beneficiary(self):
        """阶段 5：显式指定 buyer_account / beneficiary。"""
        o = Order(
            order_id='O1', buyer='government', seller='firms:SRV',
            origin='city_a', destination='city_b',
            good_id='STEEL', quantity=10.0,
            price_limit_cents=500_00, quote_cents=450_00,
            delivery_date=2, buyer_account='government', beneficiary='cohorts:H1',
        )
        assert o.buyer_account == 'government'
        assert o.beneficiary == 'cohorts:H1'

    def test_order_is_frozen(self):
        """阶段 5：Order 是 frozen dataclass。"""
        o = Order(
            order_id='O1', buyer='firms:IND', seller='firms:SRV',
            origin='a', destination='b',
            good_id='STEEL', quantity=1.0,
            price_limit_cents=100, quote_cents=90, delivery_date=1,
        )
        with pytest.raises((AttributeError, Exception)):
            o.quantity = 999  # frozen 拒改

    def test_effective_unit_price(self):
        """阶段 5：有效成交单价 = min(price_limit, quote)。"""
        o = Order(
            order_id='O1', buyer='a', seller='b', origin='x', destination='y',
            good_id='STEEL', quantity=1.0,
            price_limit_cents=500_00, quote_cents=450_00, delivery_date=1,
        )
        assert o.effective_unit_price_cents() == 450_00

        # 反向：quote > price_limit
        o2 = Order(
            order_id='O2', buyer='a', seller='b', origin='x', destination='y',
            good_id='STEEL', quantity=1.0,
            price_limit_cents=400_00, quote_cents=500_00, delivery_date=1,
        )
        assert o2.effective_unit_price_cents() == 400_00

    def test_good_property_returns_definition(self):
        """阶段 5：order.good 返回对应 GoodDefinition。"""
        o = Order(
            order_id='O1', buyer='a', seller='b', origin='x', destination='y',
            good_id='FOOD', quantity=10.0,
            price_limit_cents=100, quote_cents=90, delivery_date=1,
        )
        assert o.good.good_id == 'FOOD'
        assert o.good.category.value == 'food'


# ===== C. OrderBook 集合（6 项）=====

class TestOrderBook:
    """V2.1 §4 OrderBook 集合操作。"""

    def test_add_normal_order(self, basic_order):
        """阶段 5：正常订单成功添加。"""
        book = OrderBook()
        book.add(basic_order)
        assert 'O1' in book
        assert book.get('O1') is basic_order

    def test_add_duplicate_id_raises(self, basic_order):
        """阶段 5：order_id 重复抛 KeyError。"""
        book = OrderBook()
        book.add(basic_order)
        with pytest.raises(KeyError, match='订单 ID 重复'):
            book.add(basic_order)

    def test_add_zero_quantity_raises(self):
        """阶段 5：数量 ≤ 0 抛 ValueError。"""
        book = OrderBook()
        with pytest.raises(ValueError, match='订单数量必须为正'):
            book.add(Order(
                order_id='O1', buyer='a', seller='b', origin='x', destination='y',
                good_id='STEEL', quantity=0.0,
                price_limit_cents=100, quote_cents=90, delivery_date=1,
            ))

    def test_orders_by_buyer_and_seller(self, basic_order, point_order):
        """阶段 5：按 buyer / seller 聚合。"""
        book = OrderBook()
        book.add(basic_order)
        book.add(point_order)
        # 两个订单都是 firms:IND 买入
        assert len(book.orders_by_buyer('firms:IND')) == 2
        # 不同卖方
        assert len(book.orders_by_seller('firms:SRV')) == 1
        assert len(book.orders_by_seller('firms:MAG')) == 1

    def test_orders_by_origin_destination(self, basic_order, point_order):
        """阶段 5：按 origin / destination 聚合（V2.1 §4.4 跨城）。"""
        book = OrderBook()
        book.add(basic_order)
        book.add(point_order)
        assert len(book.orders_by_origin('city_a')) == 2
        assert len(book.orders_by_destination('city_b')) == 2

    def test_cancel_pending_order(self, basic_order):
        """阶段 5：取消 PENDING 订单变 CANCELLED。"""
        book = OrderBook()
        book.add(basic_order)
        cancelled = book.cancel('O1')
        assert cancelled.status == OrderStatus.CANCELLED
        assert book.get('O1').status == OrderStatus.CANCELLED


# ===== D. match_order 4 约束（10 项）=====

class TestMatchOrderConstraints:
    """V2.1 §4.3 q = min(订单量, 卖方库存, 运输容量, 买方实付能力/含税到岸单价)。"""

    def test_all_constraints_sufficient_fills_order(self, basic_order):
        """阶段 5：所有约束都满足时全部成交。"""
        result = match_order(
            basic_order,
            seller_stock=200.0, transport_capacity=200.0,
            buyer_cash_cents=100_000_000,  # 足够
        )
        assert result.is_fully_filled
        assert result.q_filled == 100.0
        assert result.status == OrderStatus.FILLED
        assert result.constraint_tight == 'order'

    def test_stock_constraint_partial_fill(self, basic_order):
        """阶段 5：库存不足时按库存成交量。"""
        result = match_order(
            basic_order,
            seller_stock=30.0,  # 库存只够 30 吨
            transport_capacity=200.0,
            buyer_cash_cents=100_000_000,
        )
        assert result.is_partial
        assert result.q_filled == 30.0
        assert result.shortfall == 70.0
        assert result.constraint_tight == 'stock'
        assert result.status == OrderStatus.PARTIAL

    def test_transport_constraint_partial_fill(self, basic_order):
        """阶段 5：运输容量不足时按运输成交量。"""
        result = match_order(
            basic_order,
            seller_stock=200.0,
            transport_capacity=20.0,  # 运输只够 20 吨
            buyer_cash_cents=100_000_000,
        )
        assert result.q_filled == 20.0
        assert result.constraint_tight == 'transport'
        assert result.status == OrderStatus.PARTIAL

    def test_cash_constraint_partial_fill(self, basic_order):
        """阶段 5：买方现金不足时按现金成交量。

        100 吨 × 450 CR = 45,000 CR = 4,500,000 分；
        现金 2,000,000 分 → 2,000,000 // 45,000 = 44 吨。
        """
        result = match_order(
            basic_order,
            seller_stock=200.0, transport_capacity=200.0,
            buyer_cash_cents=2_000_000,  # 只够买 44 吨
        )
        assert result.q_filled == 44.0
        assert result.constraint_tight == 'cash'
        assert result.status == OrderStatus.PARTIAL

    def test_zero_cash_returns_zero(self, basic_order):
        """阶段 5：买方零现金时成交量 = 0。"""
        result = match_order(
            basic_order,
            seller_stock=200.0, transport_capacity=200.0,
            buyer_cash_cents=0,
        )
        assert result.q_filled == 0.0
        assert result.constraint_tight == 'cash'
        assert result.status == OrderStatus.PENDING  # 零成交保持 PENDING

    def test_zero_stock_returns_zero(self, basic_order):
        """阶段 5：卖方零库存时成交量 = 0。"""
        result = match_order(
            basic_order,
            seller_stock=0.0, transport_capacity=200.0,
            buyer_cash_cents=100_000_000,
        )
        assert result.q_filled == 0.0
        assert result.constraint_tight == 'stock'
        assert result.status == OrderStatus.PENDING

    def test_instant_service_skips_transport_constraint(self, point_order):
        """阶段 5：瞬时服务（RESEARCH 等）不受运输约束限制（V2.1 §4.2b 点数瞬时交付）。"""
        result = match_order(
            point_order,
            seller_stock=200.0,
            transport_capacity=0.0,  # 运输 = 0
            buyer_cash_cents=100_000_000,
        )
        # 运输约束被豁免（inf），订单量 50 满足
        assert result.q_filled == 50.0
        assert result.status == OrderStatus.FILLED

    def test_landed_unit_price_includes_tariff(self, basic_order):
        """阶段 5：含税到岸单价 = effective_price + 单位附加费（关税/中介费按单位摊）。"""
        # 100 吨订单，关税 500 分 = 5 CR，按单位摊 = 5 分/吨
        # effective_price = 45000 分/吨；landed = 45005 分/吨
        # 现金 100,000,000 分 → 100,000,000 // 45005 = 2222 吨 ≥ 100
        result = match_order(
            basic_order,
            seller_stock=200.0, transport_capacity=200.0,
            buyer_cash_cents=100_000_00,
            landed_unit_price_cents=basic_order.effective_unit_price_cents() + 5,
        )
        assert result.is_fully_filled  # 现金仍够

    def test_landed_unit_price_reduces_cash_quantity(self, basic_order):
        """阶段 5：高含税到岸单价降低现金成交量。"""
        # landed = 45000 + 5000 = 50000 分/吨
        # 现金 1,000,000 分 → 1,000,000 // 50000 = 20 吨
        result = match_order(
            basic_order,
            seller_stock=200.0, transport_capacity=200.0,
            buyer_cash_cents=1_000_000,
            landed_unit_price_cents=50_000,
        )
        assert result.q_filled == 20.0
        assert result.constraint_tight == 'cash'

    def test_tightest_constraint_identification(self, basic_order):
        """阶段 5：最紧约束被正确标识。"""
        # 订单 100；库存 50；运输 80；现金够 → tight = stock
        result = match_order(
            basic_order,
            seller_stock=50.0, transport_capacity=80.0,
            buyer_cash_cents=100_000_000,
        )
        assert result.constraint_tight == 'stock'
        assert result.q_filled == 50.0


# ===== E. match_order 输入校验（3 项）=====

class TestMatchOrderValidation:
    """V2.1 §4.3 match_order 输入校验。"""

    def test_zero_quantity_raises(self):
        """阶段 5：订单量 ≤ 0 抛 GameError。"""
        o = Order(
            order_id='O1', buyer='a', seller='b', origin='x', destination='y',
            good_id='STEEL', quantity=0.0,
            price_limit_cents=100, quote_cents=90, delivery_date=1,
        )
        with pytest.raises(GameError, match='数量必须为正'):
            match_order(o, seller_stock=100, transport_capacity=100, buyer_cash_cents=10_000)

    def test_zero_price_limit_raises(self, basic_order):
        """阶段 5：price_limit ≤ 0 抛 GameError。"""
        bad_order = Order(
            order_id='O2', buyer='a', seller='b', origin='x', destination='y',
            good_id='STEEL', quantity=10.0,
            price_limit_cents=0, quote_cents=100, delivery_date=1,
        )
        with pytest.raises(GameError, match='报价/限价'):
            match_order(bad_order, seller_stock=100, transport_capacity=100, buyer_cash_cents=10_000)

    def test_zero_quote_raises(self):
        """阶段 5：quote ≤ 0 抛 GameError。"""
        bad_order = Order(
            order_id='O3', buyer='a', seller='b', origin='x', destination='y',
            good_id='STEEL', quantity=10.0,
            price_limit_cents=100, quote_cents=0, delivery_date=1,
        )
        with pytest.raises(GameError, match='报价/限价'):
            match_order(bad_order, seller_stock=100, transport_capacity=100, buyer_cash_cents=10_000)


# ===== F. MatchResult 数据类（2 项）=====

class TestMatchResultDataClass:
    """V2.1 §4.3 MatchResult。"""

    def test_total_value_includes_fees(self, basic_order):
        """阶段 5：total_value_cents = q*p + freight + tariff + contract_fee。"""
        result = match_order(
            basic_order,
            seller_stock=100, transport_capacity=100, buyer_cash_cents=100_000_000,
        )
        # 100 × 45000 + 1000 (freight) + 500 (tariff) + 0 = 4,501,500
        assert result.total_value_cents == 4_501_500

    def test_status_field_consistency(self, basic_order):
        """阶段 5：status 与 is_fully_filled / is_partial 一致。"""
        full = match_order(basic_order, seller_stock=200, transport_capacity=200, buyer_cash_cents=100_000_000)
        assert full.status == OrderStatus.FILLED and full.is_fully_filled and not full.is_partial

        # 零成交
        zero = match_order(basic_order, seller_stock=0, transport_capacity=200, buyer_cash_cents=100_000_000)
        assert zero.status == OrderStatus.PENDING and not zero.is_fully_filled and not zero.is_partial
