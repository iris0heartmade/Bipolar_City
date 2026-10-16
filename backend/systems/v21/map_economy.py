"""地图资产月结：建设付款、真实能力与维护均进入同一经济账本。"""
from backend.config import ROOT, read_json

CONFIG = read_json(ROOT / 'data/building_economy.json')
BUILDINGS = CONFIG['buildings']


def housing_capacity(city):
    return CONFIG['housing_base'] + sum(
        BUILDINGS[b['definition_id']]['housing_added']
        for b in city.scenario.get('_map_buildings', [])
        if b.get('player_built') and b['status'] == 'completed')


def change_capacity(city, building, direction):
    effect = BUILDINGS[building['definition_id']]
    industry, jobs = effect['industry'], effect['jobs_added']
    if industry and jobs:
        before = city.jobs(industry)
        after = city.add_industry_capacity(industry, direction * jobs)
        # 同比例扩张生产设施及满负荷原料需求，避免新增岗位仅稀释原有产能。
        city.qbar[industry] *= after / before
        city.full_raw[industry] *= after / before


def advance_buildings(city):
    """在下一次月结、劳动力与生产计算之前完工；同月开始承担维护。"""
    events = []
    for building in city.scenario.get('_map_buildings', []):
        if not building.get('player_built'):
            continue
        effect = BUILDINGS[building['definition_id']]
        if building['status'] == 'constructing':
            change_capacity(city, building, 1)
            building.update(status='completed', progress=1, completed_month=city.month + 1)
            events.append({'kind': 'completed', 'building_id': building['id']})
        due = effect['maintenance_cents'] + building.get('maintenance_due_cents', 0)
        paid = min(city.cash['GOV'], due)
        if paid:
            city.cash['GOV'] -= paid
            city.cash['IND'] += paid
            city._record_tx({'from': 'GOV', 'to': 'IND', 'amount_cents': paid,
                             'kind': 'building_maintenance', 'building_id': building['id']})
        building['maintenance_due_cents'] = due - paid
        events.append({'kind': 'maintenance', 'building_id': building['id'],
                       'paid_cents': paid, 'outstanding_cents': due - paid})
    city.housing_capacity = housing_capacity(city)
    city.map_month_report = events


def assert_map_invariants(city):
    """拒绝不完整或被篡改的地图存档；只检查，不修写运行状态。"""
    from backend.systems.v21.map_geometry import validate_placement
    from backend.systems.v21.industry_scenario import read_config
    import math
    buildings = city.scenario.get('_map_buildings')
    if buildings is None:
        if getattr(city, 'housing_capacity', CONFIG['housing_base']) != CONFIG['housing_base']:
            raise ValueError('无新增地图资产时住房容量必须等于开局底座')
        return
    if not isinstance(buildings, list):
        raise ValueError('地图建筑必须是列表')
    ids = []
    for building in buildings:
        required = {'id', 'definition_id', 'x', 'y', 'rotation', 'status'}
        if not isinstance(building, dict) or not required <= building.keys():
            raise ValueError('地图建筑字段不完整')
        token = building['id']
        if not isinstance(token, str) or not token.startswith('h') or not token[1:].isdigit():
            raise ValueError('地图建筑编号无效')
        ids.append(token)
        if building['definition_id'] not in BUILDINGS or building['status'] not in ('constructing', 'completed'):
            raise ValueError('地图建筑类型或施工状态无效')
        if building.get('player_built'):
            for key in ('started_month', 'maintenance_due_cents'):
                if type(building.get(key)) is not int or building[key] < 0:
                    raise ValueError('地图施工月份或维护欠款无效')
            if building['started_month'] > city.month:
                raise ValueError('地图施工月份晚于当前月份')
        elif building['status'] != 'completed':
            raise ValueError('开局建筑状态无效')
        validate_placement(buildings, building['definition_id'], building['x'], building['y'],
                           building['rotation'], token)
    if len(ids) != len(set(ids)):
        raise ValueError('地图建筑编号重复')
    if sum(b['definition_id'] == 'town_hall' for b in buildings) != 1:
        raise ValueError('地图必须保留唯一市政厅')
    if getattr(city, 'housing_capacity', CONFIG['housing_base']) != housing_capacity(city):
        raise ValueError('地图住房容量与已完工资产不一致')
    if '_map_next_id' in city.scenario:
        next_id = city.scenario['_map_next_id']
        if type(next_id) is not int or next_id <= max((int(token[1:]) for token in ids), default=0):
            raise ValueError('地图下一个建筑编号无效')

    baseline = read_config('industries.json')['industries']
    capacity_base = city.scenario.get('_map_capacity_base')
    if any(b.get('player_built') for b in buildings) and not isinstance(capacity_base, dict):
        raise ValueError('地图建筑缺少原始生产设施记录')
    for industry in city.keys:
        contribution = sum(BUILDINGS[b['definition_id']]['jobs_added'] for b in buildings
                           if b.get('player_built') and b['status'] == 'completed'
                           and BUILDINGS[b['definition_id']]['industry'] == industry)
        civil_contribution = city.funded_civil_staff_plan if industry == 'GOV' else 0
        expected_jobs = baseline[industry]['job_capacity_max'] + contribution + civil_contribution
        if city.jobs(industry) != expected_jobs:
            raise ValueError('地图行业岗位与已完工资产不一致')
        if capacity_base is None:
            continue
        base = capacity_base.get(industry)
        if not isinstance(base, dict) or not {'jobs', 'qbar', 'full_raw'} <= base.keys():
            raise ValueError('地图原始生产设施记录不完整')
        if base['jobs'] != baseline[industry]['job_capacity_max']:
            raise ValueError('地图原始岗位容量与开局定义不一致')
        for key, actual in (('qbar', city.qbar[industry]), ('full_raw', city.full_raw[industry])):
            value = base[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ValueError('地图原始生产设施参数无效')
            if not math.isclose(actual, value * expected_jobs / base['jobs'], rel_tol=1e-9, abs_tol=1e-8):
                raise ValueError('地图产能与已完工资产不一致')
