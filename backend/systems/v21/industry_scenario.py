"""行业实验场景、中文参数和确定性整数分配。"""
from copy import deepcopy
import json
import math
from pathlib import Path

from backend.systems.v21.industry_model import load_parameters, validate_parameters

ROOT = Path(__file__).resolve().parents[3]


def read_config(name):
    return json.loads((ROOT / 'data/economy_v2' / name).read_text(encoding='utf-8'))


def default_scenario():
    config = read_config('industry_lab.json')
    config['model'] = load_parameters()
    return config


def split_integer(total, weights):
    """按权重最大余数分配；整数分和人数均不丢失。"""
    keys = sorted(weights, key=str)
    denominator = sum(weights.values())
    if total <= 0 or denominator <= 0:
        return {k: 0 for k in keys}
    raw = {k: total * weights[k] / denominator for k in keys}
    result = {k: math.floor(raw[k]) for k in keys}
    for k in sorted(keys, key=lambda k: (-(raw[k] - result[k]), str(k)))[:total-sum(result.values())]:
        result[k] += 1
    return result


def validate_scenario(scenario):
    base = read_config('industry_lab.json')
    if set(scenario['sectors']) != set(base['sectors']):
        raise ValueError('必须包含完整七行业')
    validate_parameters(scenario['model'])
    for control in base['controls']:
        section, key = control['section'], control['key']
        groups = scenario['sectors'].values() if section == 'sector' else [control_group(scenario, section)]
        for group in groups:
            value = group[key]
            if type(value) not in (int, float) or not math.isfinite(value) or not control['min'] <= value <= control['max']:
                raise ValueError(f"{control['label']}须在{control['min']}—{control['max']}之间")
    # 非可调常量须保持场景定义，导入方案不能绕过资金/人口来源规则。
    allowed_city = {c['key'] for c in base['controls'] if c['section'] == 'city'}
    for key, value in base['city'].items():
        if key not in allowed_city and scenario['city'].get(key) != value:
            raise ValueError(f'当前版本不能修改城市结构参数：{key}')
    allowed_sector = {c['key'] for c in base['controls'] if c['section'] == 'sector'}
    for industry, sector in scenario['sectors'].items():
        for key, value in base['sectors'][industry].items():
            if key not in allowed_sector and sector.get(key) != value:
                raise ValueError(f'当前版本不能修改行业结构参数：{industry}.{key}')
    for key in ('household_seller_weights', 'household_rent_share', 'domestic_supplier_weights',
                'government_supplier_weights', 'version'):
        if scenario.get(key) != base[key]:
            raise ValueError(f'当前版本不能修改结算结构：{key}')


def control_group(scenario, section, industry='IND'):
    if section == 'city':
        return scenario['city']
    if section == 'sector':
        return scenario['sectors'][industry]
    if section == 'weights':
        return scenario['model']['attractiveness']['weights']
    return scenario['model']['labor_flow' if section == 'flow' else section]


def change_control(scenario, section, key, value, industry='IND'):
    """GUI和游戏共用参数校验；调一个权重时其余两项按比例联动。"""
    candidate = deepcopy(scenario)
    meta = next((c for c in read_config('industry_lab.json')['controls']
                 if c['section'] == section and c['key'] == key), None)
    if meta is None or type(value) not in (int, float) or not math.isfinite(value) or not meta['min'] <= value <= meta['max']:
        raise ValueError('参数不存在或超过允许范围')
    group = control_group(candidate, section, industry)
    if section == 'weights':
        rest = sum(v for k, v in group.items() if k != key)
        for other in group:
            if other != key:
                group[other] = (1-value)*group[other]/rest if rest else (1-value)/(len(group)-1)
    group[key] = value
    # 联动权重允许落到原单滑块推荐区间之外，数学上仍须非负且总和为1。
    validate_scenario(candidate)
    return candidate


def simulate(scenario, months):
    from backend.systems.v21.industry_economy import IndustryEconomy
    validate_scenario(scenario)
    if type(months) is not int or not 1 <= months <= scenario['city']['max_months']:
        raise ValueError('推演月数须为1—120整数')
    city = IndustryEconomy(deepcopy(scenario))
    return [city.advance() for _ in range(months)]
