"""V2.1 阶段 9 补 §4.6 报价公式验收测试（2026-10-15）。

【验收清单】（V2.1 §4.6）
- [x] GoodPrice 数据类
- [x] compute_price_relative_change 公式
- [x] update_good_price 月度更新（pi + 相对变化）
- [x] update_all_good_prices 批量
"""
import pytest

from backend.systems.v21.pricing import (
    GoodPrice, compute_price_relative_change,
    update_good_price, update_all_good_prices,
)


# ===== A. compute_price_relative_change（5 项）=====

class TestPriceRelativeChange:
    """V2.1 §4.6 price_relative_change_g = clip(kp*(D-A)/max(D,eps), -cap, +cap)。"""

    def test_zero_demand(self):
        """阶段 9 补：D=0 时返回 0（避免除零）。"""
        assert compute_price_relative_change(D_paid=0, A=10) == 0.0

    def test_demand_equals_supply(self):
        """阶段 9 补：D=A 时相对变化 0。"""
        assert compute_price_relative_change(D_paid=100, A=100) == 0.0

    def test_demand_exceeds_supply_positive(self):
        """阶段 9 补：D>A 时价格上涨。"""
        # kp=0.08, (D-A)/D = (200-100)/200 = 0.5 → 0.04
        r = compute_price_relative_change(D_paid=200, A=100)
        assert abs(r - 0.04) < 1e-9

    def test_demand_below_supply_negative(self):
        """阶段 9 补：D<A 时价格下降。"""
        r = compute_price_relative_change(D_paid=100, A=200)
        assert r < 0

    def test_capped_at_cap(self):
        """阶段 9 补：极端 D/A 被 cap=0.05 截断。"""
        r = compute_price_relative_change(D_paid=10000, A=1, kp=0.08, price_cap=0.05)
        assert r == 0.05

    def test_capped_at_negative_cap(self):
        """阶段 9 补：极端负向被 -cap 截断。"""
        r = compute_price_relative_change(D_paid=1, A=10000, kp=0.08, price_cap=0.05)
        assert r == -0.05


# ===== B. update_good_price（3 项）=====

class TestUpdateGoodPrice:
    """V2.1 §4.6 p_{g,t+1} = p_{g,t} * (1+pi) * (1+relative)。"""

    def test_basic_update(self):
        """阶段 9 补：基础月度更新。"""
        p = GoodPrice(good_id='FOOD', price_cents=10_000, D_paid=200, A=100)
        new_p = update_good_price(p, inflation_pi=0.01, kp=0.08, price_cap=0.05)
        # 10000 * 1.01 * 1.04 = 10504
        assert abs(new_p.price_cents - 10_504) <= 1
        assert new_p.prev_price_cents == 10_000

    def test_zero_inflation(self):
        """阶段 9 补：pi=0 时只受相对变化影响。"""
        p = GoodPrice(good_id='STEEL', price_cents=50_000, D_paid=200, A=100)
        new_p = update_good_price(p, inflation_pi=0.0, kp=0.08, price_cap=0.05)
        # 50000 * 1.04 = 52000
        assert abs(new_p.price_cents - 52_000) <= 1

    def test_demand_equals_supply_no_change(self):
        """阶段 9 补：D=A 时仅受 pi 影响。"""
        p = GoodPrice(good_id='FOOD', price_cents=10_000, D_paid=100, A=100)
        new_p = update_good_price(p, inflation_pi=0.01)
        # 10000 * 1.01 = 10100
        assert abs(new_p.price_cents - 10_100) <= 1


# ===== C. update_all_good_prices（2 项）=====

class TestBatchUpdate:
    """V2.1 §4.6 批量更新。"""

    def test_batch_updates_all(self):
        """阶段 9 补：批量更新所有商品。"""
        prices = {
            'FOOD': GoodPrice(good_id='FOOD', price_cents=10_000, D_paid=200, A=100),
            'STEEL': GoodPrice(good_id='STEEL', price_cents=50_000, D_paid=100, A=100),
        }
        new_prices = update_all_good_prices(prices, inflation_pi=0.01)
        assert 'FOOD' in new_prices
        assert 'STEEL' in new_prices
        assert new_prices['FOOD'].price_cents > 10_000  # 价格涨

    def test_batch_size_preserved(self):
        """阶段 9 补：批量更新不增删商品。"""
        prices = {gid: GoodPrice(good_id=gid, price_cents=1000) for gid in ['A', 'B', 'C']}
        new_prices = update_all_good_prices(prices, inflation_pi=0.01)
        assert set(new_prices.keys()) == {'A', 'B', 'C'}
