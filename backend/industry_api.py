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
    type: Literal['advance', 'set_parameter', 'new_game', 'save', 'load']
    request_id: str | None = Field(default=None, max_length=80)
    months: StrictInt = Field(default=1, ge=1, le=12)
    section: str | None = None
    key: str | None = None
    value: float | None = None
    industry: str = 'IND'


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
