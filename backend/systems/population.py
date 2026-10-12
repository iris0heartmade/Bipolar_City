from backend.config import BALANCE, CATALOG


def city_capacity(world):
    return sum(CATALOG[b.definition_id]["capacity"] for b in world.buildings if b.status == "completed")


def city_jobs(world):
    return BALANCE["base_jobs"] + sum(CATALOG[b.definition_id]["jobs"] for b in world.buildings if b.status == "completed")


def migrate(world):
    target = min(city_capacity(world), city_jobs(world))
    delta = max(-BALANCE["migration_per_month"], min(BALANCE["migration_per_month"], target-world.population))
    world.population += delta
    return delta
