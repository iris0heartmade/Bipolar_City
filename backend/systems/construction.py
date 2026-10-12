from backend.config import CATALOG
from backend.core.errors import GameError
from backend.model.world import Building
from backend.systems.finance import record
from backend.systems.placement import validate_placement


def place_blueprint(world, definition_id, x, y, rotation):
    if definition_id not in CATALOG or not CATALOG[definition_id]["buildable"]:
        raise GameError("这类建筑不能由玩家新建。")
    validate_placement(world, definition_id, x, y, rotation)
    world.buildings.append(Building(world.next_id, definition_id, x, y, rotation))
    world.next_id += 1


def commit_construction(world):
    blueprints = [b for b in world.buildings if b.status == "blueprint"]
    if not blueprints:
        raise GameError("请先在地图上放置蓝图。")
    cost = sum(CATALOG[b.definition_id]["cost"] for b in blueprints)
    if world.cash < cost:
        raise GameError("市政资金不足；取消部分蓝图或等待税收。")
    record(world, "construction", -cost, "确认 %d 座建筑施工" % len(blueprints))
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
