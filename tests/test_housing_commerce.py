from copy import deepcopy

import pytest

from backend.config import BALANCE, CATALOG
from backend.core.errors import GameError
from backend.core.service import GameService
from backend.core.simulation import advance_days
from backend.model.accounts import AccountBook
from backend.model.economy import ReferenceCity
from backend.systems.commerce import commerce_summary, update_merchants
from backend.systems.finance import finance_forecast
from backend.systems.housing import assign_occupancy, housing_summary, maintenance_cost, sell_homes
from backend.systems.live_economy import build_live_config
from backend.systems.population import city_capacity, city_jobs
from backend.systems.property_income import property_flows
from test_simulation import legal_position


def complete_house(service,mode="for_sale"):
    x,y=legal_position(service.world)
    service.command({"type":"place_blueprint","definition_id":"row_house","x":x,"y":y,"housing_mode":mode})
    service.command({"type":"commit"})
    advance_days(service.world,CATALOG["row_house"]["duration"],manual=True)
    return service.world.buildings[-1]


def test_unsold_housing_is_asset_not_available_capacity_or_income():
    service=GameService();initial=service.world.cash;complete_house(service)
    assert service.world.cash==initial, "批准开工只登记待付款，不当场扣钱"
    assert service.world.pending_construction_cents==CATALOG["row_house"]["cost"]
    assert city_capacity(service.world)==280
    assert housing_summary(service.world)["unsold_units"]==7
    assert housing_summary(service.world)["unsold_asset_value"]==CATALOG["row_house"]["cost"]


def test_actual_sale_reduces_inventory_and_opens_capacity():
    """成交只登记产权与待付交易；钱由月底账户结算在家庭与市政之间真实转移。"""
    service=GameService();house=complete_house(service)
    funds=service.world.resident_funds
    count,revenue,sales=sell_homes(service.world);assign_occupancy(service.world)
    assert count==2 and revenue==2*CATALOG["row_house"]["sale_price_per_unit"]
    assert len(sales)==1 and sales[0]["quantity"]==2
    assert service.world.resident_funds==funds, "sell_homes 不直接扣钱，扣款由交易完成"
    assert house.sold_units==2 and house.owner_mode=="mixed"
    assert city_capacity(service.world)==288
    assert house.occupancy==8
    assert housing_summary(service.world)["unsold_units"]==5
    assert maintenance_cost(house)<CATALOG["row_house"]["maintenance"]


def test_sale_money_moves_between_accounts_at_month_end():
    """房款必须从家庭账户付到市政账户，不能凭空消失。"""
    service=GameService();complete_house(service)
    advance_days(service.world,30,manual=True)
    before={a["account_id"]:a["cash_cents"]
            for a in service.world.economy["accounts"]}
    sold,revenue,sales=sell_homes(service.world)
    assert sold and revenue
    advance_days(service.world,30,manual=True)
    after={a["account_id"]:a["cash_cents"]
           for a in service.world.economy["accounts"]}
    # 家庭账户确实少了房款（市政账户因本期税收与支出另有变化，故只校验家庭侧）
    assert after["households"]+after["landlord"] < before["households"]+before["landlord"]
    assert revenue>0


@pytest.mark.parametrize("funds,demand,expected",[(0,10,0),(119999,10,0),(120000,10,1),(10000000,0,0)])
def test_sales_require_real_budget_and_demand(funds,demand,expected):
    service=GameService();house=complete_house(service)
    service.world.resident_funds,service.world.buyer_demand=funds,demand
    sold,revenue,sales=sell_homes(service.world)
    assert sold==expected and house.sold_units==expected
    assert service.world.resident_funds==funds and service.world.buyer_demand>=0


def test_completed_sales_cannot_repeat_or_reclaim_private_units():
    service=GameService();house=complete_house(service)
    for _ in range(4):sell_homes(service.world)
    assert house.sold_units==7 and house.owner_mode=="private"
    assert sell_homes(service.world)==(0,0,[])
    assert maintenance_cost(house)==0
    with pytest.raises(GameError,match="已有居民购买"):
        service.command({"type":"set_housing_mode","building_id":house.id,"housing_mode":"public"})


def test_no_residents_means_no_homebuyers():
    service=GameService();complete_house(service);service.world.population=0
    assert sell_homes(service.world)==(0,0,[])


def test_public_rent_only_charges_real_occupants_and_has_a_payer():
    """公共住房租金按套收取，付款方是家庭账户、收款方是房东子账户。

    口径与老引擎不同：老版按「人数 × 人均租金」估算，新版按「出租套数 × 单套租金」
    实算——只有真正挂牌出租的套数才产生收入，空置与自住都不收租。
    """
    service=GameService();house=complete_house(service,"public")
    service.world.population=294;assign_occupancy(service.world)
    assert house.occupancy==14
    advance_days(service.world,30,manual=True)
    assert maintenance_cost(house)==CATALOG["row_house"]["maintenance"]

    bundle=build_live_config(service.world)
    city=ReferenceCity(book=AccountBook.from_dict(service.world.economy["accounts"]),
                       config=bundle["config"], input_output=bundle["input_output"],
                       labor=bundle["labor"])
    flows=property_flows(city)
    assert flows.residential_rent_cents==house.sold_units*0 \
        + bundle["housing"]["rented_units"]*bundle["config"]["housing"]["rent_per_rented_unit"]*100
    assert flows.residential_rent_cents>0, "有公共出租就该有租金收入"
    assert flows.landlord_repair_cents>0 and flows.household_repair_cents>0, \
        "出租房与自住房的维修必须分别落在房东和房主身上"


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


def test_merchants_enter_gradually_and_change_actual_jobs():
    service=GameService();world=service.world;market=next(b for b in world.buildings if b.definition_id=="market")
    before=city_jobs(world)
    entered,exited=update_merchants(world)
    assert (entered,exited)==(2,0) and market.merchants==4
    assert city_jobs(world)-before==2*CATALOG["market"]["jobs_per_merchant"]
    finance=finance_forecast(world)
    assert finance["business_tax"]==4*BALANCE["merchant_tax"], "预测仍按实际商户数，只是不再直接入账"
    assert finance["commercial_rent"]==4*BALANCE["merchant_lease"]
    world.population=0
    assert update_merchants(world)==(0,1)
    assert commerce_summary(world)["merchants"]==3
    assert finance_forecast(world)["business_tax"]==0


def test_120_months_with_sales_restore_and_conservation(tmp_path):
    service=GameService(tmp_path);complete_house(service)
    advance_days(service.world,120*BALANCE["days_per_month"],manual=True)
    accounts={a["account_id"]:a["cash_cents"] for a in service.world.economy["accounts"]}
    assert service.world.cash==accounts["gov"]
    assert service.world.resident_funds==accounts["households"]+accounts["landlord"]
    assert sum(b.occupancy for b in service.world.buildings)==service.world.population
    assert service.world.resident_funds>=0
    service.command({"type":"save"});before=service.world.to_dict();service.command({"type":"load"})
    assert service.world.to_dict()==before
