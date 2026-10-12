import random

from backend.config import BALANCE, TEMPLATE
from backend.model.world import Building, World
from backend.systems.placement import validate_placement


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
    candidates = list(TEMPLATE["house_candidates"])
    random.Random(seed).shuffle(candidates)
    placements = [{"definition_id":"row_house", "x":x, "y":y, "rotation":0}
                  for x,y in candidates[:TEMPLATE["initial_houses"]]] + TEMPLATE["landmarks"]
    for item in placements:
        validate_placement(world, **item)
        world.buildings.append(Building(world.next_id, **item, status="completed"))
        world.next_id += 1
    world.events.append({"day":0, "title":"欢迎来到河湾镇", "text":"城镇已暂停。规划你的第一栋住宅，开启新的城市故事。"})
    return world
