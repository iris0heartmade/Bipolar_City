"""预览与提交共用整数格校验；道路须连接初始主路。"""
from collections import deque

from backend.config import CATALOG, TEMPLATE
from backend.core.errors import GameError


def rotate_point(x, y, width, height, rotation):
    for _ in range(rotation):
        x, y = height - 1 - y, x
        width, height = height, width
    return x, y


def footprint(definition, x, y, rotation):
    return [(x + dx, y + dy) for a in range(definition["width"])
            for b in range(definition["height"])
            for dx, dy in [rotate_point(a, b, definition["width"], definition["height"], rotation)]]


def entry_cell(definition, x, y, rotation):
    dx, dy = rotate_point(*definition["entry"], definition["width"], definition["height"], rotation)
    return x + dx, y + dy


def connected_roads(terrain):
    origin = (TEMPLATE["road_columns"][0], TEMPLATE["road_rows"][0])
    queue, visited = deque([origin]), {origin}
    while queue:
        x, y = queue.popleft()
        for nx, ny in [(x-1,y),(x+1,y),(x,y-1),(x,y+1)]:
            if (0 <= ny < len(terrain) and 0 <= nx < len(terrain[0])
                    and terrain[ny][nx] in ("road", "bridge") and (nx,ny) not in visited):
                visited.add((nx,ny))
                queue.append((nx,ny))
    return visited


def validate_placement(world, definition_id, x, y, rotation=0):
    if any(type(v) is not int for v in (x, y, rotation)) or rotation not in range(4):
        raise GameError("位置必须为整数格，朝向必须为 0—3。")
    if definition_id not in CATALOG:
        raise GameError("没有这种建筑。")
    definition = CATALOG[definition_id]
    cells = footprint(definition, x, y, rotation)
    occupied = {cell for building in world.buildings
                for cell in footprint(CATALOG[building.definition_id], building.x, building.y, building.rotation)}
    for cx, cy in cells:
        if not (0 <= cy < len(world.terrain) and 0 <= cx < len(world.terrain[0])):
            raise GameError("建筑超出了城市边界。")
        if world.terrain[cy][cx] != "grass":
            raise GameError("这里是河流或道路，请选择空地。")
        if (cx, cy) in occupied:
            raise GameError("这里已有建筑或规划蓝图。")
    if entry_cell(definition, x, y, rotation) not in connected_roads(world.terrain):
        raise GameError("入口须紧邻已连接主路的道路；可按 R 旋转。")
    return cells
