"""住房产权、实际成交和入住；未售资产不能冒充现金。"""
from math import ceil

from backend.config import BALANCE, CATALOG
from backend.core.errors import GameError


def is_housing(building):
    return bool(CATALOG[building.definition_id]["capacity"])


def available_capacity(building):
    definition = CATALOG[building.definition_id]
    if building.status != "completed":
        return 0
    if building.housing_mode == "for_sale":
        return building.sold_units * definition["unit_capacity"]
    return definition["capacity"]


def maintenance_cost(building):
    definition = CATALOG[building.definition_id]
    if building.status != "completed":
        return 0
    if not is_housing(building):
        return definition["maintenance"]
    return ceil(definition["maintenance"] * (definition["units"]-building.sold_units) / definition["units"])


def assign_occupancy(world):
    residents = world.population
    # Sold homes are occupied first; publicly owned homes absorb remaining demand.
    homes = sorted((b for b in world.buildings if is_housing(b)),
                   key=lambda b:(b.sold_units == 0, -b.id))
    for building in homes:
        building.occupancy = min(residents, available_capacity(building))
        residents -= building.occupancy


def set_housing_mode(world, building_id, mode):
    if mode not in ("public", "for_sale"):
        raise GameError("住宅方案只能选择公共出租或建成待售。")
    building = next((b for b in world.buildings if b.id == building_id), None)
    if not building or not is_housing(building):
        raise GameError("请选择一栋住宅。")
    if building.sold_units:
        raise GameError("已有居民购买，不能收回或改为公共出租。")
    if mode == "for_sale":
        other_capacity = sum(available_capacity(b) for b in world.buildings if b.id != building_id)
        if world.population > other_capacity:
            raise GameError("现有居民缺少替代住房，不能把这栋公共住宅转为待售。")
    building.housing_mode = mode
    assign_occupancy(world)


def sell_homes(world):
    from backend.systems.finance import record

    remaining = min(BALANCE["housing_sales_per_month"], world.buyer_demand,
                    world.population // BALANCE["household_size"])
    sold, revenue = 0, 0
    for building in world.buildings:
        if building.status != "completed" or building.housing_mode != "for_sale" or not is_housing(building):
            continue
        definition = CATALOG[building.definition_id]
        quantity = min(remaining, definition["units"]-building.sold_units,
                       world.resident_funds // definition["sale_price_per_unit"])
        if not quantity:
            continue
        payment = quantity * definition["sale_price_per_unit"]
        world.resident_funds -= payment
        world.buyer_demand -= quantity
        building.sold_units += quantity
        remaining -= quantity
        sold += quantity
        revenue += payment
        building.owner_mode = "private" if building.sold_units == definition["units"] else "mixed"
        record(world, "housing_sale", payment, "%s 成交 %d 户" % (definition["name"], quantity))
    return sold, revenue


def housing_summary(world):
    homes = [b for b in world.buildings if is_housing(b) and b.status == "completed"]
    unsold = [b for b in homes if b.housing_mode == "for_sale"]
    return {
        "physical_capacity":sum(CATALOG[b.definition_id]["capacity"] for b in homes),
        "public_residents":sum(b.occupancy for b in homes if b.housing_mode == "public"),
        "private_residents":sum(b.occupancy for b in homes if b.housing_mode != "public"),
        "unsold_units":sum(CATALOG[b.definition_id]["units"]-b.sold_units for b in unsold),
        "unsold_asset_value":sum(CATALOG[b.definition_id]["cost"]*(CATALOG[b.definition_id]["units"]-b.sold_units)
                                 //CATALOG[b.definition_id]["units"] for b in unsold),
        "sold_units":sum(b.sold_units for b in homes),
        "buyer_demand":world.buyer_demand,
        "resident_funds":world.resident_funds,
    }
