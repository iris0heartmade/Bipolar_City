"""本地单槽存档，原子替换；恢复失败不触碰当前世界。"""
import hashlib
import json
import os
import shutil
from pathlib import Path

from backend.config import BALANCE, CATALOG, TEMPLATE
from backend.core.errors import GameError
from backend.model.world import SCHEMA_VERSION, World
from backend.model.economy import ReferenceCity
from backend.systems.placement import validate_placement
from backend.systems.population import city_capacity
from backend.systems.housing import assign_occupancy
from backend.persistence.migrations import migrate_v1, migrate_v2, migrate_v3
from backend.persistence.property_validation import validate_property

GOV, CHURCH = ReferenceCity.GOVERNMENT, ReferenceCity.CHURCH
HOUSEHOLDS, LANDLORDS = ReferenceCity.HOUSEHOLDS, ReferenceCity.LANDLORDS
FIRMS, OUTSIDE = ReferenceCity.FIRMS, ReferenceCity.OUTSIDE


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


def _validate_economy(world):
    """账户簿自洽性：余额非负、账户齐全、展示口径与账本对得上。"""
    economy = world.economy
    if type(world.economy_month) is not int or world.economy_month < 0:
        raise GameError("存档的经济结算月份无效。")
    if type(world.pending_construction_cents) is not int \
            or world.pending_construction_cents < 0:
        raise GameError("存档的待付施工款无效。")
    if economy is None:
        if world.economy_month:
            raise GameError("存档缺少账户簿却记录了结算月份。")
        return
    if world.economy_month != economy.get("month"):
        raise GameError("存档的账户簿与结算月份不一致。")
    accounts = economy.get("accounts")
    if not isinstance(accounts, list) or not accounts:
        raise GameError("存档账户簿格式无效。")
    ids = set()
    for account in accounts:
        if not isinstance(account, dict) or type(account.get("cash_cents")) is not int \
                or type(account.get("reserved_cents")) is not int:
            raise GameError("存档账户余额无效。")
        if account["cash_cents"] < 0 and account.get("city_id") is not None:
            raise GameError("存档中的本地账户出现负余额。")
        ids.add(account.get("account_id"))
    for required in (GOV, CHURCH, HOUSEHOLDS, LANDLORDS, FIRMS, OUTSIDE):
        if required not in ids:
            raise GameError("存档账户簿缺少必要账户。")
    balances = {a["account_id"]: a["cash_cents"] for a in accounts}
    if world.cash != balances[GOV]:
        raise GameError("存档的市政现金与账户簿不一致。")
    if world.resident_funds != balances[HOUSEHOLDS] + balances[LANDLORDS]:
        raise GameError("存档的居民资金与账户簿不一致。")


def validate_world(world):
    if world.schema_version != SCHEMA_VERSION or world.template_id != TEMPLATE["id"]:
        raise GameError("存档的世界版本或地图不兼容。")
    if type(world.generator_version) is not int or world.generator_version != 1:
        raise GameError("存档生成版本不兼容。")
    for value in (world.seed, world.day, world.cash, world.population, world.next_id, world.revision,
                  world.opening_cash, world.resident_funds, world.buyer_demand):
        if type(value) is not int:
            raise GameError("存档包含非法数值。")
    if min(world.day,world.population,world.revision,world.resident_funds,world.buyer_demand) < 0:
        raise GameError("存档时间或人口无效。")
    _validate_economy(world)
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
        if type(building.progress) is not int or building.progress < 0:
            raise GameError("存档建筑进度或产权无效。")
        validate_placement(empty, building.definition_id, building.x, building.y, building.rotation)
        validate_property(building)
        duration = CATALOG[building.definition_id]["duration"]
        if (building.status in ("blueprint", "queued") and building.progress != 0
                or building.status == "constructing" and not 0 < building.progress < duration
                or building.status == "completed" and building.progress not in (0, duration)):
            raise GameError("存档施工进度与状态不一致。")
        empty.buildings.append(building)
        ids.add(building.id)
    if world.next_id <= max(ids, default=0) or world.population > city_capacity(world):
        raise GameError("存档建筑编号或住房人口不一致。")
    if sum(b.occupancy for b in world.buildings) != world.population:
        raise GameError("存档各住宅入住人数与总人口不一致。")
    if not all(isinstance(items, list) for items in (world.ledger,world.events,world.history)):
        raise GameError("存档账目或历史格式无效。")
    if any(not isinstance(item,dict) or type(item.get("amount")) is not int
           or type(item.get("day")) is not int or not 0 <= item["day"] <= world.day
           or item.get("category") not in ("construction","tax","maintenance","housing_sale","public_rent","business_tax","commercial_rent")
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
    if world.economy is None:
        # 尚未结算过任何月份（新建城市或刚迁移的老存档）：
        # 现金仍由旧账目线性推得，这条关系必须成立，否则说明账目被改过。
        if world.cash != world.opening_cash + sum(item["amount"] for item in world.ledger):
            raise GameError("存档现金与账目不一致。")


def load_world(directory: Path):
    path = directory / "slot1.json"
    if not path.exists():
        raise GameError("还没有存档，请先保存城市。")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        version = payload["schema_version"]
        migrated = None
        if version == 1:
            world = World.from_dict(migrate_v1(payload))
            migrated = 1
            assign_occupancy(world)
        elif version == 2:
            world = World.from_dict(migrate_v2(payload["world"]))
            migrated = 2
        elif version == 3:
            world = World.from_dict(migrate_v3(payload["world"]))
            migrated = 3
        else:
            if version != SCHEMA_VERSION or payload["config_hash"] != config_hash():
                raise GameError("存档版本或配置已变化；请恢复原配置后读取。")
            world = World.from_dict(payload["world"])
        validate_world(world)
        if migrated == 1:
            world.events.append({"day":world.day,"title":"旧城市印册已迁移",
                                 "text":"保留旧版时间、现金、施工与市政住房产权；居民购房余款及商户字段已初始化。原存档未覆盖。"})
            world.events = world.events[-BALANCE["event_limit"]:]
        elif migrated == 2:
            world.events.append({"day":world.day,"title":"经济账本已接入",
                                 "text":"居民收入改为企业与市政真实转付，不再按人口自动加钱；"
                                        "原有市政现金与居民资金原样并入账户。原存档未覆盖。"})
            world.events = world.events[-BALANCE["event_limit"]:]
        return world
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        if isinstance(exc, GameError):
            raise
        raise GameError("存档损坏或内容不完整；当前城市未被替换。") from exc
