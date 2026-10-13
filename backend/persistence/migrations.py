"""只迁移已知格式，不猜测未知配置的财务语义。

v1 → v2 补齐购房与商户字段；v2 → v3 接入账户清算引擎。
v2→v3 的迁移最关键：旧存档里居民的钱是「按人口自动加钱」攒出来的，
没有账户结构。迁移时按当前人口与住房重算一次期初余额建账，
原有市政现金与居民资金原样搬进对应账户，一分不改。
"""
from copy import deepcopy

from backend.config import BALANCE, ROOT, read_json
from backend.core.errors import GameError
from backend.model.world import SCHEMA_VERSION

LEGACY = read_json(ROOT / "data/legacy/v1_manifest.json")


def migrate_v1(payload):
    if payload.get("config_hash") != LEGACY["config_hash"]:
        raise GameError("旧存档配置未知，无法安全迁移；原存档未修改。")
    data = deepcopy(payload["world"])
    if data.get("schema_version") != 1:
        raise GameError("存档内外版本不一致。")
    fields = {"id","definition_id","x","y","rotation","status","progress","owner_mode"}
    for building in data["buildings"]:
        if set(building)-fields or building.get("owner_mode") != "municipal":
            raise GameError("旧存档包含未知产权信息。")
        building.update(housing_mode="public",sold_units=0,occupancy=0,merchants=0)
    data.update(schema_version=SCHEMA_VERSION,opening_cash=LEGACY["balance"]["starting_cash"],
                resident_funds=data["population"]*BALANCE["resident_savings_per_person"],
                buyer_demand=BALANCE["initial_buyer_demand"],
                economy=None,economy_month=0,pending_construction_cents=0)
    return data


def migrate_v2(data):
    """2.x → 3：接入账户引擎，尚未结算过任何月份。"""
    data = deepcopy(data)
    if data.get("schema_version") != 2:
        raise GameError("存档内外版本不一致。")
    data.update(schema_version=SCHEMA_VERSION, economy=None, economy_month=0,
                pending_construction_cents=0)
    return data
