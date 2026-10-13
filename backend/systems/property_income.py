"""P02 房产收益：住宅租金、商业租金与业主维修。

房东是家庭的财产身份，不是独立人口（通俗比喻：房东是这套房的主人，
不是多出来的一个住户）。租金进房东账户，维修由同一账户支出；
家庭主钱包不能因为同一笔租金再被镜像入账。
"""
from dataclasses import dataclass

from backend.model.economy import ReferenceCity


@dataclass
class PropertyFlows:
    residential_rent_cents: int     # 居民租户付给业主
    commercial_rent_cents: int     # 企业付给业主
    owner_repair_cents: int        # 业主付给维修企业

    @property
    def gross_rent_cents(self) -> int:
        return self.residential_rent_cents + self.commercial_rent_cents

    @property
    def net_property_income_cents(self) -> int:
        """净房产收入是统计口径，不是一次额外的钱包进账。"""
        return self.gross_rent_cents - self.owner_repair_cents


def property_flows(city: ReferenceCity) -> PropertyFlows:
    return PropertyFlows(
        residential_rent_cents=city.residential_rent_cents(),
        commercial_rent_cents=city.commercial_rent_cents,
        owner_repair_cents=city.owner_repair_cents(),
    )


def housing_capacity_check(city: ReferenceCity) -> dict[str, int]:
    """住房容量核对：出租 + 自住 + 空置必须等于总套数。"""
    housing = city.config["housing"]
    total = housing["total_units"]
    rented = housing["rented_units"]
    owner_occupied = housing["owner_occupied_units"]
    vacant = housing["vacant_units"]
    return {"total": total, "occupied": rented + owner_occupied, "vacant": vacant,
            "balanced": rented + owner_occupied + vacant == total}