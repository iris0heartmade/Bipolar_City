"""本地单槽存档，原子替换；恢复失败不触碰当前世界。"""
import hashlib
import json
import os
import shutil
from pathlib import Path

from backend.config import BALANCE, CATALOG, TEMPLATE
from backend.core.errors import GameError
from backend.model.world import SCHEMA_VERSION, World
from backend.systems.placement import validate_placement
from backend.systems.population import city_capacity


def config_hash():
    data = json.dumps([BALANCE, CATALOG, TEMPLATE], sort_keys=True, ensure_ascii=False).encode()
    return hashlib.sha256(data).hexdigest()


def save_world(world, directory: Path):
    directory.mkdir(parents=True, exist_ok=True)
    path, temp = directory / "slot1.json", directory / "slot1.tmp"
    payload = {"schema_version":SCHEMA_VERSION,"config_hash":config_hash(),"world":world.to_dict()}
    try:
        with temp.open("w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        if path.exists():
            shutil.copy2(path, directory / "slot1.bak")
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def validate_world(world):
    if world.schema_version != SCHEMA_VERSION or world.template_id != TEMPLATE["id"]:
        raise GameError("存档的世界版本或地图不兼容。")
    if type(world.generator_version) is not int or world.generator_version != 1:
        raise GameError("存档生成版本不兼容。")
    for value in (world.seed, world.day, world.cash, world.population, world.next_id, world.revision):
        if type(value) is not int:
            raise GameError("存档包含非法数值。")
    if world.day < 0 or world.population < 0 or world.revision < 0:
        raise GameError("存档时间或人口无效。")
    if type(world.paused) is not bool or world.speed not in (0.5, 1, 2):
        raise GameError("存档时间控制无效。")
    if len(world.terrain) != TEMPLATE["height"] or any(len(row) != TEMPLATE["width"] for row in world.terrain):
        raise GameError("存档地图尺寸无效。")
    if any(cell not in ("grass", "road", "bridge", "water") for row in world.terrain for cell in row):
        raise GameError("存档地形无效。")
    ids = set()
    empty = World(world.seed, world.terrain, [], world.cash, 0)
    for building in world.buildings:
        if type(building.id) is not int or building.id <= 0 or building.id in ids:
            raise GameError("存档建筑编号重复或无效。")
        if building.status not in ("blueprint", "queued", "constructing", "completed"):
            raise GameError("存档施工状态无效。")
        if type(building.progress) is not int or building.progress < 0 or building.owner_mode != "municipal":
            raise GameError("存档建筑进度或产权无效。")
        validate_placement(empty, building.definition_id, building.x, building.y, building.rotation)
        duration = CATALOG[building.definition_id]["duration"]
        if (building.status in ("blueprint", "queued") and building.progress != 0
                or building.status == "constructing" and not 0 < building.progress < duration
                or building.status == "completed" and building.progress not in (0, duration)):
            raise GameError("存档施工进度与状态不一致。")
        empty.buildings.append(building)
        ids.add(building.id)
    if world.next_id <= max(ids, default=0) or world.population > city_capacity(world):
        raise GameError("存档建筑编号或住房人口不一致。")
    if not all(isinstance(items, list) for items in (world.ledger,world.events,world.history)):
        raise GameError("存档账目或历史格式无效。")
    if any(not isinstance(item,dict) or type(item.get("amount")) is not int
           or type(item.get("day")) is not int or not 0 <= item["day"] <= world.day
           or item.get("category") not in ("construction","tax","maintenance")
           or not isinstance(item.get("label"),str) for item in world.ledger):
        raise GameError("存档账目无效。")
    if any(not isinstance(item,dict) or type(item.get("day")) is not int
           or not 0 <= item["day"] <= world.day
           or not all(isinstance(item.get(key),str) for key in ("title","text")) for item in world.events):
        raise GameError("存档事件无效。")
    fields=("day","population","cash","migration","revenue","upkeep","net")
    if any(not isinstance(item,dict) or not all(type(item.get(key)) is int for key in fields)
           or not 0 <= item["day"] <= world.day or item["population"]<0
           or item["net"] != item["revenue"]-item["upkeep"] for item in world.history):
        raise GameError("存档月报无效。")
    if world.cash != BALANCE["starting_cash"] + sum(item["amount"] for item in world.ledger):
        raise GameError("存档现金与账目不一致。")


def load_world(directory: Path):
    path = directory / "slot1.json"
    if not path.exists():
        raise GameError("还没有存档，请先保存城市。")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload["schema_version"] != SCHEMA_VERSION or payload["config_hash"] != config_hash():
            raise GameError("存档版本或配置已变化；请恢复原配置后读取。")
        world = World.from_dict(payload["world"])
        validate_world(world)
        return world
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        if isinstance(exc, GameError):
            raise
        raise GameError("存档损坏或内容不完整；当前城市未被替换。") from exc
