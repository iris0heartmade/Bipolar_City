from backend.config import BALANCE, CATALOG
from backend.systems.housing import available_capacity


def city_capacity(world):
    return sum(available_capacity(b) for b in world.buildings)


def city_jobs(world):
    return BALANCE["base_jobs"] + sum(
        b.merchants*CATALOG[b.definition_id]["jobs_per_merchant"] if CATALOG[b.definition_id].get("merchant_slots")
        else CATALOG[b.definition_id]["jobs"] for b in world.buildings if b.status == "completed")


def migrate(world):
    target = min(city_capacity(world), city_jobs(world))
    delta = max(-BALANCE["migration_per_month"], min(BALANCE["migration_per_month"], target-world.population))
    world.population += delta
    return delta
