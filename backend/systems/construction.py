"""蓝图、开工与竣工。

施工款不在这里扣钱：月结会用账户簿覆盖 world.cash，直接扣的金额会被抹掉。
commit_construction 记录待付金额，月结时由市政账户真实付给施工方。
"""
from backend.config import CATALOG
from backend.core.errors import GameError
from backend.model.world import Building
from backend.systems.placement import validate_placement


def place_blueprint(world, definition_id, x, y, rotation, housing_mode="public"):
    if definition_id not in CATALOG or not CATALOG[definition_id]["buildable"]:
        raise GameError("这类建筑不能由玩家新建。")
    validate_placement(world, definition_id, x, y, rotation)
    if housing_mode not in ("public", "for_sale") or (housing_mode == "for_sale" and not CATALOG[definition_id]["capacity"]):
        raise GameError("开发方案无效；只有住宅可设为待售。")
    world.buildings.append(Building(world.next_id, definition_id, x, y, rotation, housing_mode=housing_mode))
    world.next_id += 1


def commit_construction(world):
    """批准开工。钱先记成待付，实际付款在月底随账户结算一起完成。"""
    blueprints = [b for b in world.buildings if b.status == "blueprint"]
    if not blueprints:
        raise GameError("请先在地图上放置蓝图。")
    cost = sum(CATALOG[b.definition_id]["cost"] for b in blueprints)
    if world.cash < cost:
        raise GameError("市政资金不足；取消部分蓝图或等待税收。")
    world.pending_construction_cents = world.pending_construction_cents + cost
    for building in blueprints:
        building.status = "queued"


def cancel_blueprints(world):
    world.buildings = [b for b in world.buildings if b.status != "blueprint"]


def advance_construction(world):
    completed = []
    for building in world.buildings:
        if building.status in ("queued", "constructing"):
            building.status = "constructing"
            building.progress += 1
            if building.progress >= CATALOG[building.definition_id]["duration"]:
                building.status = "completed"
                completed.append(CATALOG[building.definition_id]["name"])
    return completed
