"""游戏行业经营接口；与桌面实验室共用IndustryEconomy。"""
import json
import logging
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from backend.config import SAVE_DIR
from backend.systems.v21.industry_economy import IndustryEconomy
from backend.systems.v21.industry_scenario import change_control

logger = logging.getLogger(__name__)
router = APIRouter(prefix='/api/industry')


class IndustryGame:
    def __init__(self, save_dir=SAVE_DIR):
        self.city = IndustryEconomy()
        self.save_dir = save_dir
        self.requests = {}

    def snapshot(self):
        return {'month': self.city.month, 'scenario': self.city.scenario,
                'employment': self.city.employment(), 'cash': self.city.cash,
                'ocean_cash': getattr(self.city, 'ocean_cash', 0),
                'active_policies': {f'{t}|{k}': v for (t, k), v in self.city._active_policies.items()},
                'latest': self.city.last_report,
                'save_exists': (self.save_dir/'industry-slot.json').exists()}

    def command(self, command):
        token = command.get('request_id')
        signature = json.dumps({k: v for k, v in command.items() if k != 'request_id'}, sort_keys=True)
        if token and token in self.requests:
            if self.requests[token] != signature:
                raise ValueError('同一操作编号不能重复用于不同命令')
            return self.snapshot()
        kind = command['type']
        if kind == 'advance':
            # 多月也作为一个事务，任何一月失败则保留命令前状态。
            candidate = IndustryEconomy.from_dict(self.city.to_dict())
            for _ in range(command.get('months', 1)):
                candidate.advance()
            self.city = candidate
        elif kind == 'set_parameter':
            candidate = change_control(self.city.scenario, command['section'], command['key'],
                                       command['value'], command.get('industry', 'IND'))
            self.city.scenario = candidate
            self.city.living_cost_cents = self.city.cr(candidate['city']['living_cost_cr']*self.city.cpi)
        elif kind == 'purchase_external':
            # 市政手动采购：现金 + 原料按需一次性买进；现金不足整笔被拒。
            cash_cr = float(command.get('cash_cr', 0) or 0)
            raw_units = float(command.get('raw_units', 0) or 0)
            from_account = command.get('from_account', 'GOV')
            note = command.get('note', '市政手动采购')
            paid_cash, paid_raw, reason = self.city.purchase_external(
                cash_cents=int(round(cash_cr*100)),
                raw_units=raw_units, from_account=from_account, note=note)
            if reason and reason != 'empty':
                raise ValueError(f'市政采购被拒：{reason}')
        elif kind == 'mayor_command':
            ok, reason, _ = self.city.mayor_command(
                kind=command['mayor_kind'],
                industry=command.get('industry'),
                cash_cr=float(command.get('cash_cr', 0) or 0),
                people=int(command.get('people', 0) or 0),
                months=int(command.get('policy_months', 1) or 1),
                note=command.get('note', ''))
            if not ok:
                raise ValueError(f'市政命令被拒：{reason}')
        elif kind == 'new_game':
            self.city = IndustryEconomy()
        elif kind == 'save':
            self.save_dir.mkdir(parents=True, exist_ok=True)
            path = self.save_dir/'industry-slot.json'
            temporary = path.with_suffix('.tmp')
            temporary.write_text(json.dumps(self.city.to_dict(), ensure_ascii=False), encoding='utf-8')
            temporary.replace(path)
            logger.info('行业城市已保存 month=%d', self.city.month)
        elif kind == 'load':
            data = json.loads((self.save_dir/'industry-slot.json').read_text(encoding='utf-8'))
            candidate = IndustryEconomy.from_dict(data)
            self.city = candidate
            logger.info('行业城市已恢复 month=%d', self.city.month)
        else:
            raise ValueError('不支持的行业操作')
        if token:
            self.requests[token] = signature
            if len(self.requests) > 512:
                self.requests.pop(next(iter(self.requests)))
        return self.snapshot()


class Command(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    type: Literal['advance', 'set_parameter', 'new_game', 'save', 'load',
                  'purchase_external', 'mayor_command']
    request_id: str | None = Field(default=None, max_length=80)
    months: StrictInt = Field(default=1, ge=1, le=12)
    section: str | None = None
    key: str | None = None
    value: float | None = None
    industry: str = 'IND'
    cash_cr: float = 0
    raw_units: float = 0
    from_account: str = 'GOV'
    note: str | None = None
    mayor_kind: str | None = None
    people: StrictInt = Field(default=0, ge=0, le=10000)
    policy_months: StrictInt = Field(default=1, ge=1, le=120)


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
