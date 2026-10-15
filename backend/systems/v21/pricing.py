"""V2.1 阶段 9 补：商品报价（§4.6）。

【2026-10-15】Bipole 城市聚合经济系统理论说明书 V2.1 §4.6 供需与报价。

【V2.1 §4.6 关键公式】
- price_relative_change_g = clip(kp * (Dpaid - A) / max(Dpaid, eps), -price_cap, +price_cap)
- p_g,t+1 = p_g,t * (1 + pi_t) * (1 + price_relative_change_g)
- 全局 pi 本身不由商品价格再次反推，避免「通胀重复计入」
- 实际购买金额由数量 × 当期价格决定；涨价不代表实物销量增加
- 仅对普通可报价商品采用；债券、地产产权、协议定价可使用专用报价算法

【V2.1 阶段 9 补范围】
本模块承载：
- GoodPrice 数据类（good_id/price_cents/prev_price_cents）
- compute_price_relative_change() 单一商品价格变化
- update_good_price() 单商品月度更新
- update_all_good_prices() 批量更新
"""
from dataclasses import dataclass, field
from typing import Optional

from backend.systems.v21.goods import GoodDefinition, get_good


# ===== V2.1 §4.6 价格数据 =====

@dataclass
class GoodPrice:
    """V2.1 §4.6 商品价格状态（整数分）。

    字段：
    - good_id：商品编号
    - price_cents：当前月度价格（整数分）
    - prev_price_cents：上期价格（用于价格变化诊断）
    - D_paid：本月已支付需求（V2.1 §4.6 Dpaid）
    - A：实际可销售供给（V2.1 §4.6 A）
    """
    good_id: str
    price_cents: int
    prev_price_cents: int = 0
    D_paid: float = 0.0
    A: float = 0.0

    def __post_init__(self):
        if self.prev_price_cents == 0:
            self.prev_price_cents = self.price_cents


# ===== V2.1 §4.6 公式 =====

def compute_price_relative_change(
    *,
    D_paid: float,
    A: float,
    kp: float = 0.08,                 # world.price_pressure_k 默认
    price_cap: float = 0.05,          # world.price_relative_cap 默认
    eps: float = 1e-9,
) -> float:
    """V2.1 §4.6 price_relative_change_g = clip(kp*(Dpaid-A)/max(Dpaid,eps), -cap, +cap)。

    返回小数（如 0.05 表示 +5% 相对变化）。
    """
    if D_paid <= 0:
        return 0.0
    raw = kp * (D_paid - A) / max(D_paid, eps)
    return max(-price_cap, min(price_cap, raw))


def update_good_price(
    price: GoodPrice,
    *,
    inflation_pi: float,
    kp: float = 0.08,
    price_cap: float = 0.05,
    goods_rounding: float = 0.001,    # 内部用更高精度，只对显示生效
    eps: float = 1e-9,
) -> GoodPrice:
    """V2.1 §4.6 月度更新：p_{g,t+1} = p_{g,t} * (1 + pi_t) * (1 + price_relative_change_g)。

    返回新的 GoodPrice（prev_price_cents 更新为旧 price_cents）。
    """
    relative = compute_price_relative_change(
        D_paid=price.D_paid, A=price.A,
        kp=kp, price_cap=price_cap, eps=eps,
    )
    new_price = price.price_cents * (1.0 + inflation_pi) * (1.0 + relative)
    new_price = int(new_price + 0.5)  # 四舍五入到分
    if new_price < 0:
        new_price = 0
    return GoodPrice(
        good_id=price.good_id,
        price_cents=new_price,
        prev_price_cents=price.price_cents,
        D_paid=price.D_paid,
        A=price.A,
    )


def update_all_good_prices(
    prices: dict[str, GoodPrice],
    *,
    inflation_pi: float,
    kp: float = 0.08,
    price_cap: float = 0.05,
) -> dict[str, GoodPrice]:
    """V2.1 §4.6 批量更新所有商品价格。"""
    return {
        gid: update_good_price(p, inflation_pi=inflation_pi, kp=kp, price_cap=price_cap)
        for gid, p in prices.items()
    }
