"""集市按消费需求渐进入驻；市长提供铺位，商户自主运营。"""
from backend.config import BALANCE, CATALOG


def market_buildings(world):
    return [b for b in world.buildings if b.status == "completed" and CATALOG[b.definition_id].get("merchant_slots")]


def update_merchants(world):
    demand = world.population // BALANCE["residents_per_merchant"]
    entries, exits = 0, 0
    entry_budget, exit_budget = BALANCE["merchant_entries_per_month"], BALANCE["merchant_exits_per_month"]
    for building in market_buildings(world):
        target = min(CATALOG[building.definition_id]["merchant_slots"], demand)
        demand -= target
        if target > building.merchants:
            change = min(target-building.merchants, entry_budget)
            building.merchants += change
            entries += change
            entry_budget -= change
        elif target < building.merchants:
            change = min(building.merchants-target, exit_budget)
            building.merchants -= change
            exits += change
            exit_budget -= change
    return entries, exits


def commerce_summary(world):
    markets = market_buildings(world)
    return {"merchants":sum(b.merchants for b in markets),
            "slots":sum(CATALOG[b.definition_id]["merchant_slots"] for b in markets),
            "demand":world.population // BALANCE["residents_per_merchant"]}
