# 校长修正 M24 实施计划 — 经济触发器与市长下台

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落实 2026-10-16 校长修正中的 M24：每月初报告极度异常 → 宽限期 / 直接触发 → 市长下台事件 → 游戏结束。**触发器只读不写，不影响经济守恒。**

**设计文档**：[校长修正-2026-10-16](../../03_设计规范/游戏最终设计要点与校长修正-2026-10-16.md)

---

## 任务清单

- [ ] A1 `data/governance_triggers.json` + `EconomicTrigger` 类
- [ ] A2 `IndustryEconomy` 集成触发器（advance 末尾检查 + 置 game_over）
- [ ] A3 `IndustryGame` 集成（advance 循环检测 game_over + snapshot 暴露）
- [ ] A4 REST 端点改造（state 含 is_game_over；commands.advance 在 game_over 时 409）
- [ ] A5 WorldGame 联动（玩家下台 → NPC AI 冻结 + /api/world/state 暴露）
- [ ] A6 前端：行业经营页红色横幅 + 推进按钮置灰；/world 玩家城灰色
- [ ] B1 单元测试：每条触发规则 + grace_period + multi_trigger
- [ ] B2 集成测试：构造触发场景 + REST 409
- [ ] B3 端到端验收：浏览器触发场景看到红色横幅
- [ ] C1 设计文档 M24 + CHANGELOG + README

---

## A1 数据契约 + 触发器类

### A1.1 新文件 `data/governance_triggers.json`

```jsonc
{
  "_comment": "经济触发器阈值与宽限期。每月从 last_report 读取指标，"
              "单项持续多月极度异常进入宽限期（grace_period）；"
              "多项同时触发直接下台（immediate）。"
              "校长修正 2026-10-16：经济崩溃 = 玩家责任；"
              "触发器只读不写，不影响经济守恒。",
  "triggers": {
    "unemployment_crisis": {
      "metric": "unemployment_rate",
      "comparison": ">=",
      "threshold": 0.30,
      "grace_months": 3,
      "severity": "grace_period",
      "label": "失业率危机"
    },
    "government_bankruptcy": {
      "metric": "government_cash_cents",
      "comparison": "<=",
      "threshold": -1000000,
      "grace_months": 2,
      "severity": "grace_period",
      "label": "政府现金破产"
    },
    "wage_arrears_runaway": {
      "metric": "wage_arrears_cents",
      "comparison": ">=",
      "threshold": 5000000,
      "grace_months": 2,
      "severity": "grace_period",
      "label": "累计欠薪失控"
    },
    "population_collapse": {
      "metric": "population_change_rate",
      "comparison": "<=",
      "threshold": -0.20,
      "grace_months": 0,
      "severity": "immediate",
      "label": "人口锐减"
    },
    "civil_unrest_multi": {
      "type": "multi",
      "all_of": ["unemployment_crisis", "wage_arrears_runaway"],
      "severity": "immediate",
      "label": "失业 + 欠薪同时爆发"
    }
  }
}
```

### A1.2 新文件 `backend/systems/v21/governance_trigger.py`

```python
"""M24 经济触发器：月初报告独立判定器。"""
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import json


@dataclass
class TriggerConfig:
    """单条触发规则。"""
    key: str
    metric: str  # 指标名（unemployment_rate / government_cash_cents 等）
    comparison: str  # >= / <= / > / < / ==
    threshold: float
    grace_months: int  # 0 = immediate
    severity: str  # grace_period / immediate
    label: str
    # multi 类型字段
    type: str = 'single'
    all_of: list[str] = field(default_factory=list)


def load_governance_triggers(config_path=None):
    """读取 data/governance_triggers.json。"""
    if config_path is None:
        config_path = Path(__file__).resolve().parents[3] / 'data' / 'governance_triggers.json'
    raw = json.loads(Path(config_path).read_text(encoding='utf-8'))
    triggers = {}
    for key, cfg in raw['triggers'].items():
        triggers[key] = TriggerConfig(key=key, **cfg)
    return triggers


class EconomicTrigger:
    """触发器状态机。每个 trigger 跟踪连续触发月份数。"""
    def __init__(self, config=None):
        self.config = config or load_governance_triggers()
        # { trigger_key: consecutive_months_breached }
        self._streak = {key: 0 for key in self.config}
        self._prev_report = None  # 上一个 last_report（用于 population_change_rate 等变化类指标）
        self._game_over = False
        self._reasons = []  # 触发游戏结束的原因列表

    def evaluate(self, current_report):
        """读取 last_report，更新 streak；若 game_over 触发则返回 True。"""
        if self._game_over:
            return True  # 已经结束，继续返回 True
        newly_breached = set()
        for key, cfg in self.config.items():
            if cfg.type == 'multi':
                continue  # multi 在 single 评估后处理
            value = self._read_metric(current_report)
            if value is None:
                continue
            if self._compare(value, cfg.comparison, cfg.threshold):
                self._streak[key] += 1
                if cfg.severity == 'immediate' or self._streak[key] >= cfg.grace_months:
                    newly_breached.add(key)
            else:
                self._streak[key] = 0  # 重置 streak

        # multi 触发器：所有 all_of 都 breach 立即触发
        for key, cfg in self.config.items():
            if cfg.type != 'multi':
                continue
            if all(s in newly_breached for s in cfg.all_of):
                newly_breached.add(key)

        self._prev_report = current_report
        if newly_breached:
            self._game_over = True
            self._reasons = [self.config[k].label for k in newly_breached]
        return self._game_over

    def _read_metric(self, report):
        """从 last_report 读取指标；处理 population_change_rate 等派生指标。"""
        if 'unemployment_rate' in report:
            return report['unemployment_rate']
        # 派生：unemployment_rate = unemployed / labor_force
        if 'unemployment_rate' not in report and 'unemployed' in report and 'labor_force' in report:
            labor = report['labor_force'] or 1
            return report['unemployed'] / labor
        if 'government_cash_cents' in report:
            return report['government_cash_cents']
        if 'wage_arrears_cents' in report:
            return report['wage_arrears_cents']
        if 'population_change_rate' in report:
            return report['population_change_rate']
        if 'population_change_rate' not in report and self._prev_report:
            prev_pop = self._prev_report.get('total_population', self._prev_report.get('population'))
            curr_pop = report.get('total_population', report.get('population'))
            if prev_pop:
                return (curr_pop - prev_pop) / prev_pop
        return None

    @staticmethod
    def _compare(value, op, threshold):
        if op == '>=': return value >= threshold
        if op == '<=': return value <= threshold
        if op == '>':  return value >  threshold
        if op == '<':  return value <  threshold
        if op == '==': return value == threshold
        raise ValueError(f'不支持的比较运算符 {op}')

    @property
    def is_game_over(self):
        return self._game_over

    @property
    def reasons(self):
        return list(self._reasons)

    @property
    def streak(self):
        return dict(self._streak)

    def to_dict(self):
        return {
            'is_game_over': self._game_over,
            'reasons': list(self._reasons),
            'streak': dict(self._streak),
        }

    @classmethod
    def from_dict(cls, data):
        inst = cls.__new__(cls)
        inst.config = load_governance_triggers()
        inst._streak = dict(data.get('streak', {key: 0 for key in inst.config}))
        inst._prev_report = None
        inst._game_over = bool(data.get('is_game_over', False))
        inst._reasons = list(data.get('reasons', []))
        return inst
```

### A1.3 测试 `tests/v21/test_governance_trigger.py`

```python
"""M24 经济触发器单元测试。"""
import pytest
from backend.systems.v21.governance_trigger import (
    EconomicTrigger, load_governance_triggers, TriggerConfig
)


def _report(unemployed=100, gov_cash=10000_00, arrears=0, population=3000, labor=2400):
    """构造一个正常 last_report。"""
    return {
        'month': 1, 'unemployed': unemployed, 'labor_force': labor,
        'government_cash_cents': gov_cash, 'wage_arrears_cents': arrears,
        'total_population': population,
    }


def test_load_triggers_default():
    cfg = load_governance_triggers()
    assert 'unemployment_crisis' in cfg
    assert cfg['government_bankruptcy'].severity == 'grace_period'


def test_normal_report_not_trigger():
    trig = EconomicTrigger()
    assert trig.evaluate(_report()) is False
    assert trig.is_game_over is False


def test_unemployment_crisis_grace_period():
    trig = EconomicTrigger()
    # 失业率 30% 触发，grace_months=3
    for _ in range(2):
        assert trig.evaluate(_report(unemployed=720, labor=2400)) is False  # 30%
    assert trig.evaluate(_report(unemployed=720, labor=2400)) is True  # 第 3 月触发


def test_unemployment_recover_resets():
    trig = EconomicTrigger()
    trig.evaluate(_report(unemployed=720))  # 触发
    trig.evaluate(_report(unemployed=720))  # 触发
    assert trig.evaluate(_report(unemployed=10)) is False  # 恢复
    assert trig.streak['unemployment_crisis'] == 0


def test_government_bankruptcy_immediate_after_grace():
    trig = EconomicTrigger()
    assert trig.evaluate(_report(gov_cash=-2_000_00)) is False  # grace 1/2
    assert trig.evaluate(_report(gov_cash=-2_000_00)) is True   # grace 2/2 触发


def test_wage_arrears_runaway():
    trig = EconomicTrigger()
    assert trig.evaluate(_report(arrears=10_000_00)) is False
    assert trig.evaluate(_report(arrears=10_000_00)) is True  # grace 2/2


def test_population_collapse_immediate():
    trig = EconomicTrigger()
    # 第一月是基准，第二月锐减 20% 立即触发
    trig.evaluate(_report(population=3000))
    assert trig.evaluate(_report(population=2400)) is True
    assert '人口锐减' in trig.reasons[0]


def test_civil_unrest_multi_immediate():
    trig = EconomicTrigger()
    # 失业 + 欠薪同时 breach → immediate（第 1 期就触发）
    assert trig.evaluate(_report(unemployed=720, arrears=10_000_00)) is True
    assert any('失业' in r for r in trig.reasons)


def test_to_dict_from_dict_roundtrip():
    trig = EconomicTrigger()
    trig.evaluate(_report(gov_cash=-2_000_00))  # grace 1/2
    trig.evaluate(_report(gov_cash=-2_000_00))  # grace 2/2 → game over
    data = trig.to_dict()
    assert data['is_game_over'] is True
    trig2 = EconomicTrigger.from_dict(data)
    assert trig2.is_game_over is True
    assert trig2.reasons == trig.reasons


def test_reasons_accumulate():
    trig = EconomicTrigger()
    trig.evaluate(_report(unemployed=720, gov_cash=-2_000_00))  # 失业 grace 1/3 + 破产 grace 1/2
    assert trig.evaluate(_report(unemployed=720, gov_cash=-2_000_00)) is False  # 都不达 grace 阈值
    trig.evaluate(_report(unemployed=720, gov_cash=-2_000_00))  # 第 3 月破产触发 + 第 3 月失业触发
    assert trig.is_game_over is True
    assert len(trig.reasons) >= 1
```

---

## A2 IndustryEconomy 集成

### A2.1 修改 `backend/systems/v21/industry_economy.py`

在 `__init__` 增加：
```python
from backend.systems.v21.governance_trigger import EconomicTrigger, load_governance_triggers
self._trigger = EconomicTrigger(load_governance_triggers())
```

在 `advance()` / `_advance()` 末尾（`return report` 之前）增加：
```python
# M24：触发器只读检查
self._trigger.evaluate(self.last_report)
```

在 `to_dict()` / `from_dict()` 序列化触发器状态：
```python
# to_dict
'_trigger': self._trigger.to_dict() if self._trigger else None

# from_dict
self._trigger = EconomicTrigger.from_dict(data['_trigger']) if data.get('_trigger') else EconomicTrigger(load_governance_triggers())
```

### A2.2 测试 `tests/v21/test_governance_integration.py`

```python
"""M24 与 IndustryEconomy 集成测试。"""
import pytest
from backend.systems.v21.industry_economy import IndustryEconomy


def test_industryeconomy_advance_triggers_gameover(monkeypatch):
    """构造极端参数使 advance 触发 game_over。"""
    eco = IndustryEconomy()
    # 强制修改 scenario 把失业率/政府现金调到触发线
    eco.scenario['city']['living_cost_cr'] = 200  # 高生活成本
    # 直接 mutate 触发状态：跳过经济复杂路径，直接调触发器
    eco._trigger._streak['unemployment_crisis'] = 2  # 已经 2 月 grace
    # 第 3 次 advance 触发（构造极端失业场景）
    # ...
    pass  # 见实际测试
```

---

## A3 IndustryGame 集成

### A3.1 修改 `backend/industry_api.py`

在 `IndustryGame.__init__` 增加：
```python
self._game_over = False  # 来自 IndustryEconomy._trigger 同步
```

修改 `_apply` 的 advance 分支：
```python
elif kind == 'advance':
    months_run = 0
    for _ in range(command['months']):
        report = city.advance()
        history = getattr(city, 'report_history', [])
        history.append(report)
        city.report_history = history[-city.scenario['city']['max_months']:]
        months_run += 1
        if city._trigger.is_game_over:
            self._game_over = True
            break
```

修改 `snapshot()`：
```python
def snapshot(self):
    base = {...}  # 现有
    base['is_game_over'] = self._game_over
    base['game_over_reasons'] = self.city._trigger.reasons
    base['trigger_streak'] = self.city._trigger.streak
    return base
```

---

## A4 REST 端点

### A4.1 修改 `backend/industry_api.py` 的 `commands` 端点

```python
@router.post('/commands')
async def commands(command: Command, request: Request):
    async with request.app.state.lock:
        game = request.app.state.industry_game
        if game._game_over and command.type == 'advance':
            raise HTTPException(status_code=409, detail={
                'error': 'game_over',
                'reasons': game.city._trigger.reasons,
            })
        # ... 现有逻辑
```

---

## A5 WorldGame 联动

### A5.1 修改 `backend/systems/v21/world_game.py`

```python
class WorldGame:
    def advance(self):
        # 玩家城市 IndustryGame 已先调用 .command('advance')，那里已经检测触发器。
        # 这里只判断是否还需要驱动 NPC
        if self.player_game._game_over:
            self._world_frozen = True
            return  # 玩家下台 → 冻结 NPC
        # ... 现有 NPC advance
```

`/api/world/state` 加 `is_player_game_over` 字段。

---

## A6 前端

### A6.1 修改 `frontend/industry.html`

顶部增加：
```html
<div id="gameover-banner" class="hidden" role="alert">
  ⚠ 市长下台：<span id="gameover-reasons"></span>
  <button onclick="window.location.reload()">重新开局</button>
</div>
```

### A6.2 修改 `frontend/js/industry.js`

`fetchState()` 后检查 `data.is_game_over`，若 true 则显示横幅 + 禁用推进按钮。

### A6.3 修改 `frontend/world.html` + `frontend/js/world_map.js`

玩家城金色光晕在玩家下台时变灰色（CSS class `player-defeated`）。

---

## B1-B3 测试

按 A1.3、A2.2 的测试清单 + 一个 Playwright 端到端：
1. 启动临时后端
2. 通过 API 强制 mutate 触发器 streak
3. 浏览器加载 /industry
4. 校验红色横幅出现 + 推进按钮 disabled

---

## C1 文档

- 新增 `docs/03_设计规范/经济触发器与市长下台-M24-2026-10-16.md`
- CHANGELOG [未发布] 加 M24 条目
- README 启动部分加"游戏结束"说明