"""地图完整占地与入口规则，供预览、命令及存档核对共用。"""
from backend.config import CATALOG, TEMPLATE

def _generate_terrain(template: dict, seed: int = 20251008) -> list[list[str]]:
    """按 river_town.json 生成 28x28 terrain：河岸、桥梁、道路、草原。"""
    width, height = template['width'], template['height']
    river_start_x = template['river_start_x']
    river_bend_y = template['river_bend_y']
    river_bend_offset = template['river_bend_offset']
    road_columns = template['road_columns']
    road_rows = template['road_rows']
    bridge_rows = template['bridge_rows']

    terrain = [['grass'] * width for _ in range(height)]
    # 河流：river_start_x 开始，到 river_bend_y 处弯 river_bend_offset 个格
    river_x = river_start_x
    for y in range(height):
        terrain[y][river_x] = 'water'
        if y == river_bend_y:
            river_x += river_bend_offset
            river_x = max(0, min(width - 1, river_x))
            terrain[y][river_x] = 'water'
    # 道路网格
    for rx in road_columns:
        for y in range(height):
            if terrain[y][rx] != 'water':
                terrain[y][rx] = 'road'
    for ry in road_rows:
        for x in range(width):
            if terrain[ry][x] != 'water':
                terrain[ry][x] = 'road'
    # 桥梁：道路与河流交汇处
    for ry in bridge_rows:
        for x in range(width):
            if terrain[ry][x] == 'road':
                # 与河流相邻的 road 改成 bridge
                for dx in (-1, 0, 1):
                    nx = x + dx
                    if 0 <= nx < width and terrain[ry][nx] == 'water':
                        terrain[ry][nx] = 'bridge'
                        terrain[ry][x] = 'bridge'
    return terrain


def _rotate(x, y, width, height, rotation):
    for _ in range(rotation):
        x, y = height - 1 - y, x
        width, height = height, width
    return x, y


def _cells(definition, x, y, rotation):
    return {(x + dx, y + dy) for dx, dy in
            (_rotate(a, b, definition['width'], definition['height'], rotation)
             for a in range(definition['width']) for b in range(definition['height']))}


def validate_placement(buildings, definition_id, x, y, rotation=0, exclude_id=None):
    if definition_id not in CATALOG:
        raise ValueError('未知建筑类型')
    definition = CATALOG[definition_id]
    if not definition.get('buildable', True) and exclude_id is None:
        raise ValueError('此建筑不可建造')
    if any(type(v) is not int for v in (x, y, rotation)) or not 0 <= rotation <= 3:
        raise ValueError('坐标和朝向必须是合法整数')
    terrain = _generate_terrain(TEMPLATE)
    cells = _cells(definition, x, y, rotation)
    if any(not (0 <= a < len(terrain[0]) and 0 <= b < len(terrain)) for a, b in cells):
        raise ValueError('建筑完整占地超出地图')
    if any(terrain[b][a] != 'grass' for a, b in cells):
        raise ValueError('建筑完整占地必须是草地，不能覆盖道路、桥梁或河流')
    occupied = set()
    for building in buildings:
        if building['id'] != exclude_id:
            occupied |= _cells(CATALOG[building['definition_id']], building['x'], building['y'], building['rotation'])
    if cells & occupied:
        raise ValueError('建筑占地与已有建筑重叠')
    ex, ey = _rotate(*definition['entry'], definition['width'], definition['height'], rotation)
    ex, ey = x + ex, y + ey
    if not (0 <= ey < len(terrain) and 0 <= ex < len(terrain[0])) or terrain[ey][ex] not in ('road', 'bridge'):
        raise ValueError('黄色入口必须连接道路或桥梁')

