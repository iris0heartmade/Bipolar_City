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
    landlord_repair_cents: int     # 其中由出租房业主承担的维修
    household_repair_cents: int    # 其中由自住房主（居民）自行承担的维修

    @property
    def gross_rent_cents(self) -> int:
        return self.residential_rent_cents + self.commercial_rent_cents

    @property
    def net_property_income_cents(self) -> int:
        """净房产收入是统计口径，不是一次额外的钱包进账。

        只扣出租房业主实际承担的维修：自住房的维修是居民自己的居住成本，
        不是房东的经营收入，混进来会同时高估 GVA 的房产分项和低估居民支出。
        """
        return self.gross_rent_cents - self.landlord_repair_cents


def property_flows(city: ReferenceCity) -> PropertyFlows:
    """装配房产收付。

    参考城只有出租房与商业用房，维修全部记在房东账户上。
    接入可玩世界时会出现大量自住房——这些房子没有租户、维修费却真实存在，
    此时 live_economy 会写入 owner_repair_rented 把维修拆成两笔：
    出租房部分走房东账户，自住房部分由房主本人（家庭钱包）直接付给维修企业。
    没写这个字段时行为与参考城完全一致，基准不受影响。
    """
    housing = city.config["housing"]
    total = city.owner_repair_cents()
    landlord_share = housing.get("owner_repair_rented")
    landlord_repair = total if landlord_share is None else min(total, landlord_share)
    return PropertyFlows(
        residential_rent_cents=city.residential_rent_cents(),
        commercial_rent_cents=city.commercial_rent_cents,
        owner_repair_cents=total,
        landlord_repair_cents=landlord_repair,
        household_repair_cents=total - landlord_repair,
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