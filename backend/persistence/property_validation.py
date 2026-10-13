from backend.config import CATALOG
from backend.core.errors import GameError
from backend.systems.housing import available_capacity


def validate_property(building):
    definition = CATALOG[building.definition_id]
    for value in (building.sold_units,building.occupancy,building.merchants):
        if type(value) is not int or value < 0:
            raise GameError("存档产权、入住或商户数无效。")
    if building.merchants > definition.get("merchant_slots",0):
        raise GameError("存档商户超出铺位容量。")
    if building.status != "completed" and (building.sold_units or building.occupancy or building.merchants):
        raise GameError("未竣工建筑不能成交、入住或营业。")
    if not definition["capacity"]:
        if building.sold_units or building.occupancy or building.owner_mode != "municipal" or building.housing_mode != "public":
            raise GameError("非住宅包含非法产权或人口。")
        return
    units = definition["units"]
    if building.sold_units > units or building.occupancy > available_capacity(building):
        raise GameError("存档住房户数或入住超出容量。")
    expected_owner = "municipal" if not building.sold_units else "private" if building.sold_units == units else "mixed"
    if building.owner_mode != expected_owner or building.housing_mode not in ("public","for_sale","private"):
        raise GameError("存档产权与已售户数不一致。")
    if (building.housing_mode == "public" and building.sold_units
            or building.housing_mode == "private" and building.sold_units != units):
        raise GameError("私人住房与公共住房记录矛盾。")
