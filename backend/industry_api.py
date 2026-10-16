"""游戏行业经营接口；与桌面实验室共用IndustryEconomy。"""
import json
import logging
from copy import deepcopy
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator

from backend.config import SAVE_DIR
from backend.systems.v21.industry_economy import IndustryEconomy
from backend.systems.v21.industry_scenario import change_control

logger = logging.getLogger(__name__)
router = APIRouter(prefix='/api/industry')


class IndustryGame:
    def __init__(self, save_dir=SAVE_DIR):
        self.city = IndustryEconomy()
        self.save_dir = Path(save_dir)
        self.requests = {}

    def snapshot(self):
        return deepcopy({'month': self.city.month, 'scenario': self.city.scenario,
                'employment': self.city.employment(), 'cash': self.city.cash,
                'population': sum(self.city.population.values()), 'labor_force': self.city.labor_force,
                'government_raw_units': self.city.government_raw_units,
                'government_raw_resale_cents': self.city.cumulative_government_raw_resale_cents,
                'ocean_cash': getattr(self.city, 'ocean_cash', 0),
                'active_policies': {f'{t}|{k}': v for (t, k), v in self.city._active_policies.items()},
                'recent_mayor_actions': list(self.city._last_policy_actions),
                'history': getattr(self.city, 'report_history', []),
                'latest': self.city.last_report,
                'save_exists': (self.save_dir/'industry-slot.json').exists()})

    def is_replay(self, command):
        """地图和行业入口共用操作编号，失败的操作不会占用编号。"""
        token = command.get('request_id')
        signature = json.dumps({k: v for k, v in command.items() if k != 'request_id'},
                               sort_keys=True, allow_nan=False)
        if token and token in self.requests:
            if self.requests[token] != signature:
                raise ValueError('同一操作编号不能重复用于不同命令')
            return True
        return False

    def remember_request(self, command):
        token = command.get('request_id')
        if token:
            self.requests[token] = json.dumps(
                {k: v for k, v in command.items() if k != 'request_id'},
                sort_keys=True, allow_nan=False)
            if len(self.requests) > 512:
                self.requests.pop(next(iter(self.requests)))

    def execute_once(self, command, operation):
        """在隔离副本执行一次动作；核对成功才更换正式城市。"""
        if self.is_replay(command):
            return self.snapshot()
        candidate = deepcopy(self.city)
        operation(candidate)
        candidate.assert_invariants()
        self.city = candidate
        self.remember_request(command)
        return self.snapshot()

    def command(self, command):
        # 直接调用与HTTP使用同一输入约束，不能通过另一入口绕过金额检查。
        command = Command.model_validate(command).model_dump(exclude_none=True)
        if self.is_replay(command):
            return self.snapshot()
        kind = command['type']
        if kind == 'save':
            self.city.assert_invariants()
            encoded = json.dumps(self.city.to_dict(), ensure_ascii=False, allow_nan=False)
            self.save_dir.mkdir(parents=True, exist_ok=True)
            path = self.save_dir/'industry-slot.json'
            temporary = path.with_suffix('.tmp')
            temporary.write_text(encoded, encoding='utf-8')
            if path.exists():
                backup = path.with_suffix('.bak')
                backup_tmp = backup.with_suffix('.bak.tmp')
                backup_tmp.write_bytes(path.read_bytes())
                backup_tmp.replace(backup)
            temporary.replace(path)
            self.remember_request(command)
            logger.info('行业城市已保存 month=%d', self.city.month)
        elif kind == 'load':
            data = json.loads((self.save_dir/'industry-slot.json').read_text(encoding='utf-8'))
            candidate = IndustryEconomy.from_dict(data)
            self.city = candidate
            self.remember_request(command)
            logger.info('行业城市已恢复 month=%d', self.city.month)
        else:
            return self.execute_once(command, lambda city: self._apply(city, command))
        return self.snapshot()

    @staticmethod
    def _apply(city, command):
        kind = command['type']
        if kind == 'advance':
            for _ in range(command['months']):
                report = city.advance()
                # 月报保存在运行存档中，网页刷新/读取不会丢掉趋势证据。
                history = getattr(city, 'report_history', [])
                history.append(report)
                city.report_history = history[-city.scenario['city']['max_months']:]
        elif kind == 'set_parameter':
            city.scenario = change_control(city.scenario, command.get('section'), command.get('key'),
                                           command.get('value'), command['industry'])
            city.living_cost_cents = city.cr(city.scenario['city']['living_cost_cr']*city.cpi)
            city.household_size = float(city.scenario['city']['household_consumption_size'])
        elif kind == 'purchase_external':
            _, _, reason = city.purchase_external(
                cash_cents=city.cr(command['cash_cr']), raw_units=command['raw_units'],
                from_account='GOV', note=command.get('note', '市政采购'))
            if reason:
                raise ValueError(f'市政采购被拒：{reason}')
        elif kind == 'mayor_command':
            ok, reason, _ = city.mayor_command(
                kind=command.get('mayor_kind'), industry=command['industry'],
                cash_cr=command['cash_cr'], people=command['people'],
                months=command['policy_months'], note=command.get('note', ''))
            if not ok:
                raise ValueError(f'市政命令被拒：{reason}')
        elif kind == 'new_game':
            replacement = IndustryEconomy()
            replacement.scenario['_seed'] = command['seed']
            city.__dict__.clear()
            city.__dict__.update(replacement.__dict__)
        else:
            raise ValueError('不支持的行业操作')


class Command(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False, validate_default=True)
    type: Literal['advance', 'set_parameter', 'new_game', 'save', 'load',
                  'purchase_external', 'mayor_command']
    request_id: str | None = Field(default=None, max_length=80)
    months: StrictInt = Field(default=1, ge=1, le=120)
    seed: StrictInt = Field(default=20251008, ge=0, le=2**32-1)
    section: str | None = None
    key: str | None = None
    value: float | None = None
    industry: str = 'IND'
    cash_cr: float = Field(default=0, ge=0, le=1e12)
    raw_units: float = Field(default=0, ge=0, le=1e12)
    from_account: Literal['GOV'] = 'GOV'
    note: str | None = None
    mayor_kind: str | None = None
    people: StrictInt = Field(default=0, ge=0, le=10000)
    policy_months: StrictInt = Field(default=1, ge=1, le=120)

    @field_validator('cash_cr', 'raw_units', 'value', mode='before')
    @classmethod
    def numeric_input(cls, value):
        if value is not None and type(value) not in (int, float):
            raise ValueError('金额、数量和参数必须是数字，不能用布尔值或文本代替')
        return value


@router.get('/state')
async def state(request: Request):
    async with request.app.state.lock:
        return request.app.state.industry_game.snapshot()


@router.post('/commands')
async def commands(command: Command, request: Request):
    async with request.app.state.lock:
        try:
            return request.app.state.industry_game.command(command.model_dump(exclude_none=True))
        except (ValueError, KeyError, TypeError) as exc:
            raise HTTPException(status_code=400, detail=f'操作未生效：{exc}') from exc
        except OSError as exc:
            logger.error('行业存档读写失败 operation=%s error=%s', command.type, exc)
            raise HTTPException(status_code=500, detail='无法读写行业存档，原城市未被替换。') from exc
        except AssertionError as exc:
            logger.exception('行业结算断言失败，未提交状态 operation=%s', command.type)
            raise HTTPException(status_code=500, detail='经济核对失败，本次推进未生效。请检查日志。') from exc
