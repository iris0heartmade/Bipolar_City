import random

from backend.config import BALANCE, CATALOG, TEMPLATE
from backend.model.world import Building, World
from backend.systems.placement import validate_placement
from backend.systems.housing import assign_occupancy


def new_world(seed):
    terrain = []
    for y in range(TEMPLATE["height"]):
        river_x = TEMPLATE["river_start_x"] + (TEMPLATE["river_bend_offset"] if y >= TEMPLATE["river_bend_y"] else 0)
        row = ["water" if x >= river_x else "grass" for x in range(TEMPLATE["width"])]
        for x in range(TEMPLATE["width"]):
            if y in TEMPLATE["road_rows"] or x in TEMPLATE["road_columns"]:
                if row[x] == "grass":
                    row[x] = "road"
                elif y in TEMPLATE["bridge_rows"]:
                    row[x] = "bridge"
        terrain.append(row)
    world = World(seed, terrain, [], BALANCE["starting_cash"], BALANCE["initial_population"])
    world.opening_cash = world.cash
    world.resident_funds = world.population * BALANCE["resident_savings_per_person"]
    world.buyer_demand = BALANCE["initial_buyer_demand"]
    candidates = list(TEMPLATE["house_candidates"])
    random.Random(seed).shuffle(candidates)
    placements = [{"definition_id":"row_house", "x":x, "y":y, "rotation":0}
                  for x,y in candidates[:TEMPLATE["initial_houses"]]] + TEMPLATE["landmarks"]
    for item in placements:
        validate_placement(world, **item)
        building = Building(world.next_id, **item, status="completed")
        if building.definition_id == "row_house":
            building.sold_units = CATALOG[building.definition_id]["units"]
            building.owner_mode, building.housing_mode = "private", "private"
        if building.definition_id == "market":
            building.merchants = BALANCE["initial_merchants"]
        world.buildings.append(building)
        world.next_id += 1
    assign_occupancy(world)
    world.events.append({"day":0, "title":"市政印契已交接", "text":"魔导灯照亮河湾。规划公共住房或待售住宅，接续这座城市的故事。"})
    return world
