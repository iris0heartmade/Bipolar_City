"""P02 市场侧：营业额八来源与进出口流。

营业额必须有付款方。八来源逐笔可追溯，不允许出现"自动加钱"的收入项。
企业间互购在本城内部闭合，是企业付给企业自己，不产生全城新增现金。
"""
from dataclasses import dataclass

from backend.config import INPUT_OUTPUT
from backend.model.economy import cr_to_cents


@dataclass
class MarketFlows:
    """企业本月收到的全部款项，每一项都标明付款方。"""

    household_consumption_cents: int      # 家庭购买商品与服务
    owner_repair_cents: int               # 业主支付房屋维修
    municipal_procurement_cents: int      # 市政采购与维护
    municipal_capital_cents: int          # 市政资本性支付
    private_capital_cents: int            # 私人资本投入
    exports_cents: int                    # 对外出口
    tourist_spend_cents: int              # 游客消费
    firm_intermediate_cents: int          # 企业间互购（本城内部转移）

    @property
    def total_revenue_cents(self) -> int:
        return (self.household_consumption_cents + self.owner_repair_cents
                + self.municipal_procurement_cents + self.municipal_capital_cents
                + self.private_capital_cents + self.exports_cents
                + self.tourist_spend_cents + self.firm_intermediate_cents)

    @property
    def external_inflow_cents(self) -> int:
        """真正来自本地账户体系之外的收入，不含市政与企业自身的内部往来。"""
        return self.private_capital_cents + self.exports_cents + self.tourist_spend_cents


def market_flows() -> MarketFlows:
    sources = INPUT_OUTPUT["firm_revenue_sources"]
    return MarketFlows(
        household_consumption_cents=cr_to_cents(sources["household_consumption"]),
        owner_repair_cents=cr_to_cents(sources["owner_repair_spend"]),
        municipal_procurement_cents=cr_to_cents(sources["municipal_procurement"]),
        municipal_capital_cents=cr_to_cents(sources["municipal_capital"]),
        private_capital_cents=cr_to_cents(sources["private_capital"]),
        exports_cents=cr_to_cents(sources["exports"]),
        tourist_spend_cents=cr_to_cents(sources["tourist_spend"]),
        firm_intermediate_cents=cr_to_cents(sources["firm_intermediate"]),
    )


def trade_flows() -> dict[str, int]:
    """进出口与外部往来。战略品金额已含在明细内，不得再加一笔付款。"""
    trade = INPUT_OUTPUT["trade"]
    return {
        "imports": cr_to_cents(INPUT_OUTPUT["firm_input_split"]["imports"]),
        "exports": cr_to_cents(sum(line["value"] for line in trade["exports"])),
        "imports_detail": cr_to_cents(sum(line["value"] for line in trade["imports"])),
        "tourist_spend": cr_to_cents(trade["tourist_spend"]),
        "central_grant": cr_to_cents(trade["central_grant"]),
        "external_interest": cr_to_cents(trade["external_interest_paid"]),
    }


def strategic_trade_lines() -> dict[str, list[dict]]:
    """战略品进出口明细，供库存系统（P06）绑定实际库存与路线容量。"""
    trade = INPUT_OUTPUT["trade"]
    return {"imports": trade["imports"], "exports": trade["exports"]}