"""政府与居民现金区分，房屋销售不重复收租。"""
from backend.config import BALANCE
from backend.systems.commerce import commerce_summary
from backend.systems.housing import housing_summary, maintenance_cost


def record(world, category, amount, label):
    if not amount:
        return
    world.cash += amount
    world.ledger.append({"day":world.day, "category":category, "amount":amount, "label":label})


def finance_forecast(world):
    active = min(commerce_summary(world)["merchants"], world.population // BALANCE["residents_per_merchant"])
    resident_tax = world.population * BALANCE["tax_per_resident"]
    business_tax = active * BALANCE["merchant_tax"]
    public_rent = housing_summary(world)["public_residents"] * BALANCE["public_rent_per_person"]
    commercial_rent = active * BALANCE["merchant_lease"]
    upkeep = sum(maintenance_cost(b) for b in world.buildings)
    revenue = resident_tax + business_tax + public_rent + commercial_rent
    return {"resident_tax":resident_tax,"business_tax":business_tax,"public_rent":public_rent,
            "commercial_rent":commercial_rent,"revenue":revenue,"upkeep":upkeep,"net":revenue-upkeep}


def monthly_finance(world):
    totals = finance_forecast(world)
    for category, label in [("resident_tax","居民地方税"),("public_rent","公共住房实际租金")]:
        totals[category] = min(world.resident_funds, totals[category])
        world.resident_funds -= totals[category]
        record(world, "tax" if category == "resident_tax" else category, totals[category], label)
    record(world, "business_tax", totals["business_tax"], "营业商户地方税")
    record(world, "commercial_rent", totals["commercial_rent"], "市政集市铺位租金")
    record(world, "maintenance", -totals["upkeep"], "市政持有部分维护")
    totals["revenue"] = sum(totals[key] for key in ("resident_tax","business_tax","public_rent","commercial_rent"))
    totals["net"] = totals["revenue"] - totals["upkeep"]
    return totals
