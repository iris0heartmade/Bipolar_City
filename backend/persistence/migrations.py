"""只迁移已知 0.1.0 格式，不猜测未知配置的财务语义。"""
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
                buyer_demand=BALANCE["initial_buyer_demand"])
    return data
