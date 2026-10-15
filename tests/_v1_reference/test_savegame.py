import json

import pytest

from backend.core.errors import GameError
from backend.core.service import GameService
from backend.core.simulation import advance_days
from backend.persistence.savegame import load_world, save_world
from test_simulation import place


def test_save_restore_and_continue_matches_uninterrupted(tmp_path):
    service=GameService(tmp_path);place(service,"apartment",1);service.command({"type":"commit"})
    advance_days(service.world,7,manual=True);save_world(service.world,tmp_path)
    restored=load_world(tmp_path)
    assert restored.to_dict()==service.world.to_dict()
    advance_days(restored,120,manual=True);advance_days(service.world,120,manual=True)
    assert restored.to_dict()==service.world.to_dict()


def test_backup_and_save_does_not_mutate_world(tmp_path):
    service=GameService(tmp_path);before=service.world.to_dict();service.command({"type":"save"})
    assert service.world.to_dict()==before
    service.command({"type":"advance_month"});service.command({"type":"save"})
    assert json.loads((tmp_path/"slot1.bak").read_text(encoding="utf-8"))["world"]==before


@pytest.mark.parametrize("damage",["broken_json","version","cash","overlap","float","missing","history","events","progress"])
def test_bad_save_keeps_current_city(tmp_path,damage):
    service=GameService(tmp_path);service.command({"type":"save"})
    path=tmp_path/"slot1.json";data=json.loads(path.read_text(encoding="utf-8"))
    if damage=="version":data["schema_version"]=999
    elif damage=="cash":data["world"]["cash"]+=1
    elif damage=="overlap":data["world"]["buildings"][1]["x"]=data["world"]["buildings"][0]["x"];data["world"]["buildings"][1]["y"]=data["world"]["buildings"][0]["y"]
    elif damage=="float":data["world"]["population"]=1.5
    elif damage=="missing":del data["world"]["terrain"]
    elif damage=="history":data["world"]["history"]=[{"population":"wrong"}]
    elif damage=="events":data["world"]["events"]=None
    elif damage=="progress":data["world"]["buildings"][0]["progress"]=-5
    path.write_text("{" if damage=="broken_json" else json.dumps(data),encoding="utf-8")
    before=service.world.to_dict()
    with pytest.raises(GameError):service.command({"type":"load"})
    assert service.world.to_dict()==before


def test_missing_slot_has_clear_error(tmp_path):
    with pytest.raises(GameError,match="没有存档"):load_world(tmp_path)
