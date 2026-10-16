"""桌面实验会话：配置、当前城市与未干预基准分别保存。"""
from copy import deepcopy
import json
import math
from functools import lru_cache
from pathlib import Path
import tempfile

from backend.systems.v21.industry_economy import IndustryEconomy
from backend.systems.v21.industry_scenario import change_control, default_scenario, validate_scenario
from backend.systems.v21.industry_state import validate_report


def write_json_atomic(path, payload):
    """编码成功后再原子替换文件，失败不覆盖已有存档。"""
    encoded = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False)
    destination = Path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=destination.parent,
                                         prefix=destination.name + '.', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(encoded)
            stream.flush()
        temporary.replace(destination)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


@lru_cache(maxsize=1)
def _report_shape():
    """从正式内核取得完整月报结构，避免桌面维护一份逐渐失效的字段清单。"""
    return IndustryEconomy().advance()


def _validate_report_shape(value, template, path='月报'):
    """同时检查完整字段及可渲染类型；JSON把元组转成列表是合法往返。"""
    if isinstance(template, dict):
        if not isinstance(value, dict) or not template.keys() <= value.keys():
            raise ValueError(f'{path}缺少完整字段')
        for key, child in template.items():
            _validate_report_shape(value[key], child, f'{path}.{key}')
    elif isinstance(template, (list, tuple)):
        if not isinstance(value, (list, tuple)):
            raise ValueError(f'{path}必须是列表')
        # 流水、短缺等列表包含多种记录结构，交由正式月报校验器核对。
        if template and not isinstance(template[0], dict):
            for child in value:
                _validate_report_shape(child, template[0], path)
    elif type(template) in (int, float):
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError(f'{path}必须是有限数字')
    elif not isinstance(value, type(template)):
        raise ValueError(f'{path}类型无效')


def validate_lab_report(report, keys):
    validate_report(report, set(keys))
    _validate_report_shape(report, _report_shape())
    for sector in report['sectors'].values():
        if any(key not in ('building', 'cash', 'recurring_finance', 'inputs')
               for key in sector['capacity_details']['bottlenecks']):
            raise ValueError('月报行业容纳短板类型无效')


class LabSession:
    FORMAT = 'industry-lab-session-1'

    def __init__(self, scenario=None):
        self.scenario = deepcopy(scenario if scenario is not None else default_scenario())
        validate_scenario(self.scenario)
        self.city = IndustryEconomy(self.scenario)
        self.baseline_city = IndustryEconomy(default_scenario())
        self.history, self.baseline, self.commands = [], [], []

    def advance(self, months=1):
        if type(months) is not int or not 1 <= months <= 120:
            raise ValueError('推进月数须为1—120整数')
        candidate = deepcopy(self)
        for _ in range(months):
            candidate.history.append(candidate.city.advance())
            candidate.baseline.append(candidate.baseline_city.advance())
        self.__dict__.update(candidate.__dict__)

    def set_parameter(self, section, key, value, industry='IND'):
        scenario = change_control(self.scenario, section, key, value, industry)
        if scenario == self.scenario:
            return
        candidate = deepcopy(self.city)
        candidate.scenario = deepcopy(scenario)
        candidate.household_size = float(scenario['city']['household_consumption_size'])
        candidate.living_cost_cents = max(1, round(candidate.cr(scenario['city']['living_cost_cr']) * candidate.cpi))
        candidate.assert_invariants()
        self.city, self.scenario = candidate, scenario
        self.commands.append({'month': candidate.month, 'kind': 'set_parameter',
                              'section': section, 'key': key, 'value': value, 'industry': industry})

    def command(self, kind, **parameters):
        for key in ('cash_cr', 'raw_units'):
            value = parameters.get(key, 0)
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1e12:
                raise ValueError('金额和原料必须是非负有限数字')
        for key, default, low, high in (('people', 0, 0, 10000), ('months', 1, 1, 120),
                                         ('cash_cents', 0, 0, 10**14)):
            value = parameters.get(key, default)
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f'{key}必须是{low}—{high}整数')
        candidate = deepcopy(self.city)
        if kind == 'purchase_external':
            _, _, reason = candidate.purchase_external(**parameters)
            if reason and reason != 'empty':
                raise ValueError(reason)
        else:
            ok, reason, _ = candidate.mayor_command(kind=kind, **parameters)
            if not ok:
                raise ValueError(reason)
        candidate.assert_invariants()
        self.city = candidate
        self.scenario = deepcopy(candidate.scenario)
        self.commands.append({'month': candidate.month, 'kind': kind, **deepcopy(parameters)})

    def to_dict(self):
        return json.loads(json.dumps({'format': self.FORMAT, 'scenario': self.scenario,
                         'city': self.city.to_dict(), 'baseline_city': self.baseline_city.to_dict(),
                         'history': self.history, 'baseline': self.baseline, 'commands': self.commands}, allow_nan=False))

    @classmethod
    def from_dict(cls, payload):
        if not isinstance(payload, dict) or payload.get('format') != cls.FORMAT:
            raise ValueError('请选择实验运行存档；方案配置不是运行状态')
        try:
            result = cls(payload['scenario'])
            result.city = IndustryEconomy.from_dict(payload['city'])
            result.baseline_city = IndustryEconomy.from_dict(payload['baseline_city'])
            result.history = deepcopy(payload['history'])
            result.baseline = deepcopy(payload['baseline'])
            result.commands = deepcopy(payload['commands'])
            if result.city.scenario != result.scenario or result.city.month != result.baseline_city.month:
                raise ValueError('存档配置或基准月份不一致')
            for history, city in ((result.history, result.city), (result.baseline, result.baseline_city)):
                if not isinstance(history, list) or len(history) != city.month:
                    raise ValueError('逐月历史不完整')
                for report in history:
                    validate_lab_report(report, city.keys)
                if any(report['month'] != month for month, report in enumerate(history, 1)):
                    raise ValueError('历史月份不连续')
                if history and history[-1] != city.last_report:
                    raise ValueError('历史与当前城市不一致')
            if result.baseline_city.scenario != default_scenario():
                raise ValueError('基准配置被改写')
            if not isinstance(result.commands, list) or any(
                    not isinstance(command, dict) or type(command.get('month')) is not int
                    or not 0 <= command['month'] <= result.city.month
                    or not isinstance(command.get('kind'), str) for command in result.commands):
                raise ValueError('指令历史格式无效')
            json.dumps(result.commands, allow_nan=False)
            return result
        except (KeyError, TypeError) as exc:
            raise ValueError('实验运行存档缺少有效字段') from exc
