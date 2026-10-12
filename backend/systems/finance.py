from backend.config import BALANCE, CATALOG


def record(world, category, amount, label):
    world.cash += amount
    world.ledger.append({"day":world.day, "category":category, "amount":amount, "label":label})


def monthly_finance(world):
    revenue = world.population * BALANCE["tax_per_resident"]
    upkeep = sum(CATALOG[b.definition_id]["maintenance"] for b in world.buildings if b.status == "completed")
    record(world, "tax", revenue, "居民与地方经济税收（初版简化）")
    record(world, "maintenance", -upkeep, "市政持有建筑维护")
    return revenue, upkeep
