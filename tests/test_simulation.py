from copy import deepcopy

import pytest

from backend.config import BALANCE, CATALOG
from backend.core.errors import GameError
from backend.core.service import GameService
from backend.core.simulation import advance_days
from backend.systems.generator import new_world
from backend.systems.placement import entry_cell, footprint, validate_placement
from backend.systems.population import city_capacity


def legal_position(world, definition_id="row_house", rotation=0):
    for y in range(len(world.terrain)):
        for x in range(len(world.terrain[0])):
            try:
                validate_placement(world, definition_id, x, y, rotation)
                return x, y
            except GameError:
                continue
    raise AssertionError("测试地图缺少合法位置")


def place(service, definition_id="row_house", rotation=0):
    x,y=legal_position(service.world, definition_id, rotation)
    service.command({"type":"place_blueprint","definition_id":definition_id,"x":x,"y":y,"rotation":rotation})
    return service.world.buildings[-1]


def test_seed_is_reproducible_and_changes_houses():
    a,b,c=new_world(11),new_world(11),new_world(12)
    assert a.to_dict()==b.to_dict()
    assert a.terrain==c.terrain
    assert [(x.x,x.y) for x in a.buildings]!=[(x.x,x.y) for x in c.buildings]


@pytest.mark.parametrize("seed", [0,1,20261012,2147483647])
def test_generation_valid_and_non_overlapping(seed):
    world=new_world(seed)
    occupied=set()
    for b in world.buildings:
        cells=footprint(CATALOG[b.definition_id],b.x,b.y,b.rotation)
        assert not occupied.intersection(cells)
        assert all(world.terrain[y][x]=="grass" for x,y in cells)
        occupied.update(cells)
    assert world.population<=city_capacity(world)


@pytest.mark.parametrize("rotation,dimensions,entry",[(0,(2,3),(0,3)),(1,(3,2),(-1,0)),(2,(2,3),(1,-1)),(3,(3,2),(3,1))])
def test_non_square_rotation(rotation,dimensions,entry):
    definition=CATALOG["apartment"]
    cells=footprint(definition,0,0,rotation)
    assert len(set(cells))==6
    assert (max(x for x,y in cells)+1,max(y for x,y in cells)+1)==dimensions
    assert entry_cell(definition,0,0,rotation)==entry


@pytest.mark.parametrize("x,y,r",[(-1,5,0),(28,0,0),(23,7,0),(8,8,0),(0,0,0),(1.2,8,0),(True,8,0),(3,8,4)])
def test_invalid_placement_does_not_change_world(x,y,r):
    service=GameService();before=service.world.to_dict()
    with pytest.raises(GameError):
        service.command({"type":"place_blueprint","definition_id":"row_house","x":x,"y":y,"rotation":r})
    assert service.world.to_dict()==before


def test_blueprint_overlap_rejected_and_free():
    service=GameService();cash=service.world.cash;b=place(service)
    assert service.world.cash==cash
    before=service.world.to_dict()
    with pytest.raises(GameError):
        service.command({"type":"place_blueprint","definition_id":b.definition_id,"x":b.x,"y":b.y,"rotation":b.rotation})
    assert service.world.to_dict()==before
    service.command({"type":"cancel"})
    assert service.world.cash==cash


def test_commit_atomic_and_no_double_charge():
    """施工款先记成待付，月底由市政账户真实支付；重复提交不得收两次。"""
    service=GameService();place(service);place(service,"apartment",1);cash=service.world.cash
    cost=CATALOG["row_house"]["cost"]+CATALOG["apartment"]["cost"]
    service.command({"type":"commit","request_id":"unique-commit"})
    assert service.world.pending_construction_cents==cost
    assert service.world.cash==cash, "批准开工本身不扣钱，付款在月底随账户结算完成"
    service.command({"type":"commit","request_id":"unique-commit"})
    assert service.world.pending_construction_cents==cost, "重复提交不得二次计入待付款"
    with pytest.raises(GameError):service.command({"type":"commit"})
    assert service.world.pending_construction_cents==cost

    advance_days(service.world,30,manual=True)
    assert service.world.pending_construction_cents==0
    report=service.world.economy["report"]
    assert service.world.cash==cash+report["municipal_surplus_cents"]-cost, \
        "市政账户＝期初现金＋本月财政结余−已付施工款"


def test_insufficient_funds_keeps_all_blueprints():
    service=GameService();place(service);place(service);service.world.cash=1
    before=service.world.to_dict()
    with pytest.raises(GameError):service.command({"type":"commit"})
    assert service.world.to_dict()==before


def test_pause_construction_completion_once_and_housing_growth():
    service=GameService();b=place(service);capacity=city_capacity(service.world)
    service.command({"type":"commit"});world=service.world
    advance_days(world,30)
    assert world.day==0 and world.buildings[-1].progress==0
    world.paused=False;advance_days(world,14)
    assert world.buildings[-1].status=="completed"
    assert city_capacity(world)==capacity+CATALOG[b.definition_id]["capacity"]
    advance_days(world,16)
    assert world.population==BALANCE["initial_population"]+BALANCE["migration_per_month"]
    advance_days(world,30)
    assert city_capacity(world)==capacity+CATALOG[b.definition_id]["capacity"]


def test_120_months_accounting_and_capacity_without_ui():
    """连跑 120 个月：月结全部由账户引擎完成，旧账目不再被写入。"""
    service=GameService();world=service.world;world.paused=False
    advance_days(world,120*BALANCE["days_per_month"])
    assert world.population==min(city_capacity(world),service.snapshot()["summary"]["jobs"])
    assert world.economy_month==120
    accounts={a["account_id"]:a["cash_cents"] for a in world.economy["accounts"]}
    assert world.cash==accounts["gov"], "市政现金必须等于账户簿里的市政余额"
    assert world.resident_funds==accounts["households"]+accounts["landlord"]
    for name in ("gov","church","households","firms"):
        assert accounts[name]>=0, f"{name} 账户不得透支"
    assert world.ledger==[], "结算已由账户引擎承担，旧账目不再重复记录"
    assert len(world.history)==BALANCE["history_limit"]
    assert len(world.events)<=BALANCE["event_limit"]


def test_manual_month_requires_pause_and_preserves_pause():
    service=GameService();service.command({"type":"advance_month"})
    assert service.world.day==30 and service.world.paused
    service.command({"type":"pause","paused":False});before=deepcopy(service.world.to_dict())
    with pytest.raises(GameError):service.command({"type":"advance_month"})
    assert service.world.to_dict()==before
