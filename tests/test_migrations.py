import json

import pytest

from backend.core.errors import GameError
from backend.core.service import GameService
from backend.persistence.migrations import LEGACY
from backend.persistence.savegame import load_world


def v1_payload():
    world=GameService().world.to_dict()
    for field in ("opening_cash","resident_funds","buyer_demand"):world.pop(field)
    world["schema_version"]=1
    for building in world["buildings"]:
        for field in ("housing_mode","sold_units","occupancy","merchants"):building.pop(field)
        building["owner_mode"]="municipal"
    return {"schema_version":1,"config_hash":LEGACY["config_hash"],"world":world}


def test_known_v1_migrates_without_changing_cash_time_or_source(tmp_path):
    payload=v1_payload();path=tmp_path/"slot1.json";path.write_text(json.dumps(payload),encoding="utf-8")
    original=path.read_bytes();world=load_world(tmp_path)
    assert world.schema_version==3 and world.cash==payload["world"]["cash"] and world.day==0
    assert all(b.owner_mode=="municipal" for b in world.buildings)
    assert sum(b.occupancy for b in world.buildings)==world.population
    assert world.resident_funds>0
    assert world.economy is None and world.economy_month==0, "旧存档没有账户簿，首次月结才建账"
    assert path.read_bytes()==original


def test_known_v2_migrates_and_keeps_cash_and_funds(tmp_path):
    """2.x 是刚接入前的最后一版：现金与居民资金必须原样保留。"""
    service=GameService(tmp_path);service.command({"type":"save"})
    path=tmp_path/"slot1.json";payload=json.loads(path.read_text(encoding="utf-8"))
    payload["schema_version"]=2
    for key in ("economy","economy_month","pending_construction_cents"):payload["world"].pop(key,None)
    payload["world"]["schema_version"]=2
    path.write_text(json.dumps(payload),encoding="utf-8")

    world=load_world(tmp_path)
    assert world.schema_version==3
    assert world.cash==payload["world"]["cash"]
    assert world.resident_funds==payload["world"]["resident_funds"]
    assert world.economy is None and world.economy_month==0
    assert world.events[-1]["title"]=="经济账本已接入"


def test_unknown_v1_configuration_is_rejected(tmp_path):
    payload=v1_payload();payload["config_hash"]="unknown"
    (tmp_path/"slot1.json").write_text(json.dumps(payload),encoding="utf-8")
    with pytest.raises(GameError,match="配置未知"):load_world(tmp_path)


@pytest.mark.parametrize("field,value",[("sold_units",999),("owner_mode","municipal"),("occupancy",999),("merchants",100),("resident_funds",-1)])
def test_v2_corrupt_property_or_funds_keeps_world(tmp_path,field,value):
    service=GameService(tmp_path);service.command({"type":"save"})
    path=tmp_path/"slot1.json";payload=json.loads(path.read_text(encoding="utf-8"))
    if field=="resident_funds":payload["world"][field]=value
    else:payload["world"]["buildings"][0][field]=value
    path.write_text(json.dumps(payload),encoding="utf-8");before=service.world.to_dict()
    with pytest.raises(GameError):service.command({"type":"load"})
    assert service.world.to_dict()==before
