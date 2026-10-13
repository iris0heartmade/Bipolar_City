from copy import deepcopy

import pytest

from backend.config import BALANCE, CATALOG
from backend.core.errors import GameError
from backend.core.service import GameService
from backend.core.simulation import advance_days
from backend.systems.commerce import commerce_summary, update_merchants
from backend.systems.finance import monthly_finance
from backend.systems.housing import assign_occupancy, housing_summary, maintenance_cost, sell_homes
from backend.systems.population import city_capacity, city_jobs
from test_simulation import legal_position


def complete_house(service,mode="for_sale"):
    x,y=legal_position(service.world)
    service.command({"type":"place_blueprint","definition_id":"row_house","x":x,"y":y,"housing_mode":mode})
    service.command({"type":"commit"})
    advance_days(service.world,CATALOG["row_house"]["duration"],manual=True)
    return service.world.buildings[-1]


def test_unsold_housing_is_asset_not_available_capacity_or_income():
    service=GameService();initial=service.world.cash;complete_house(service)
    assert service.world.cash==initial-CATALOG["row_house"]["cost"]
    assert city_capacity(service.world)==280
    assert housing_summary(service.world)["unsold_units"]==7
    assert housing_summary(service.world)["unsold_asset_value"]==CATALOG["row_house"]["cost"]
    assert not any(item["category"]=="housing_sale" for item in service.world.ledger)


def test_actual_sale_transfers_cash_reduces_inventory_and_opens_capacity():
    service=GameService();house=complete_house(service)
    cash,funds=service.world.cash,service.world.resident_funds
    count,revenue=sell_homes(service.world);assign_occupancy(service.world)
    assert count==2 and revenue==2*CATALOG["row_house"]["sale_price_per_unit"]
    assert service.world.cash-cash==funds-service.world.resident_funds==revenue
    assert house.sold_units==2 and house.owner_mode=="mixed"
    assert city_capacity(service.world)==288
    assert house.occupancy==8
    assert housing_summary(service.world)["unsold_units"]==5
    assert maintenance_cost(house)<CATALOG["row_house"]["maintenance"]


@pytest.mark.parametrize("funds,demand,expected",[(0,10,0),(119999,10,0),(120000,10,1),(10000000,0,0)])
def test_sales_require_real_budget_and_demand(funds,demand,expected):
    service=GameService();house=complete_house(service)
    service.world.resident_funds,service.world.buyer_demand=funds,demand
    sold,revenue=sell_homes(service.world)
    assert sold==expected and house.sold_units==expected
    assert service.world.resident_funds>=0 and service.world.buyer_demand>=0


def test_completed_sales_cannot_repeat_or_reclaim_private_units():
    service=GameService();house=complete_house(service)
    for _ in range(4):sell_homes(service.world)
    assert house.sold_units==7 and house.owner_mode=="private"
    cash=service.world.cash
    assert sell_homes(service.world)==(0,0) and service.world.cash==cash
    assert maintenance_cost(house)==0
    with pytest.raises(GameError,match="已有居民购买"):
        service.command({"type":"set_housing_mode","building_id":house.id,"housing_mode":"public"})
    totals=monthly_finance(service.world)
    assert totals["public_rent"]==0


def test_no_residents_means_no_homebuyers():
    service=GameService();complete_house(service);service.world.population=0
    assert sell_homes(service.world)==(0,0)


def test_public_rent_only_charges_real_occupants_and_has_a_payer():
    service=GameService();house=complete_house(service,"public")
    service.world.population=294;assign_occupancy(service.world)
    assert house.occupancy==14
    funds=service.world.resident_funds
    totals=monthly_finance(service.world)
    assert totals["public_rent"]==14*BALANCE["public_rent_per_person"]
    assert funds-service.world.resident_funds==totals["resident_tax"]+totals["public_rent"]
    assert maintenance_cost(house)==CATALOG["row_house"]["maintenance"]


def test_switch_public_to_sale_does_not_displace_residents():
    service=GameService();house=complete_house(service,"public")
    service.world.population=294;assign_occupancy(service.world);before=deepcopy(service.world.to_dict())
    with pytest.raises(GameError,match="替代住房"):
        service.command({"type":"set_housing_mode","building_id":house.id,"housing_mode":"for_sale"})
    assert service.world.to_dict()==before


def test_unsold_mode_switch_is_reversible_without_cash_arbitrage():
    service=GameService();house=complete_house(service)
    cash=service.world.cash
    for mode in ("public","for_sale","public"):
        service.command({"type":"set_housing_mode","building_id":house.id,"housing_mode":mode})
    assert service.world.cash==cash and city_capacity(service.world)==308


def test_merchants_enter_gradually_and_change_actual_jobs_and_taxes():
    service=GameService();world=service.world;market=next(b for b in world.buildings if b.definition_id=="market")
    before=city_jobs(world)
    entered,exited=update_merchants(world)
    assert (entered,exited)==(2,0) and market.merchants==4
    assert city_jobs(world)-before==2*CATALOG["market"]["jobs_per_merchant"]
    finance=monthly_finance(world)
    assert finance["business_tax"]==4*BALANCE["merchant_tax"]
    assert finance["commercial_rent"]==4*BALANCE["merchant_lease"]
    world.population=0
    assert update_merchants(world)==(0,1)
    assert commerce_summary(world)["merchants"]==3
    assert monthly_finance(world)["business_tax"]==0


def test_120_months_with_sales_restore_and_conservation(tmp_path):
    service=GameService(tmp_path);complete_house(service)
    advance_days(service.world,120*BALANCE["days_per_month"],manual=True)
    assert service.world.cash==service.world.opening_cash+sum(item["amount"] for item in service.world.ledger)
    assert sum(b.occupancy for b in service.world.buildings)==service.world.population
    assert service.world.resident_funds>=0
    service.command({"type":"save"});before=service.world.to_dict();service.command({"type":"load"})
    assert service.world.to_dict()==before
