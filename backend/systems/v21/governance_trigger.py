"""M24 经济触发器：月初报告独立判定器。

设计要点（校长修正 2026-10-16）：
- 触发器只读 last_report，不写经济数据，不影响经济守恒。
- 单项持续多月极度异常进入宽限期（grace_period）。
- 多项同时触发 / 关键单项 → 直接下台（immediate）。
- 阈值与宽限期外置为 data/governance_triggers.json。

模块边界：
- 触发器状态序列化在 IndustryEconomy._trigger（to_dict/from_dict）。
- IndustryGame 在 advance 循环里读取 city._trigger.is_game_over 决定是否继续。
- WorldGame 在玩家下台后冻结 NPC AI。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def _default_config_path() -> Path:
    return Path(__file__).resolve().parents[3] / 'data' / 'governance_triggers.json'


@dataclass
class TriggerConfig:
    """单条触发规则。"""
    key: str
    metric: str = ''
    comparison: str = '>='
    threshold: float = 0.0
    grace_months: int = 0
    severity: str = 'grace_period'
    label: str = ''
    type: str = 'single'  # single / multi
    all_of: list[str] = field(default_factory=list)


def load_governance_triggers(config_path: Path | None = None) -> dict[str, TriggerConfig]:
    """读取 data/governance_triggers.json → {key: TriggerConfig}。"""
    if config_path is None:
        config_path = _default_config_path()
    raw = json.loads(Path(config_path).read_text(encoding='utf-8'))
    triggers: dict[str, TriggerConfig] = {}
    for key, cfg in raw['triggers'].items():
        triggers[key] = TriggerConfig(
            key=key,
            metric=cfg.get('metric', ''),
            comparison=cfg.get('comparison', '>='),
            threshold=cfg.get('threshold', 0.0),
            grace_months=cfg.get('grace_months', 0),
            severity=cfg.get('severity', 'grace_period'),
            label=cfg.get('label', key),
            type=cfg.get('type', 'single'),
            all_of=list(cfg.get('all_of', [])),
        )
    return triggers


class EconomicTrigger:
    """触发器状态机。连续触发月份数累计到 grace_months 才算 game_over。"""

    def __init__(self, config: dict[str, TriggerConfig] | None = None):
        self.config: dict[str, TriggerConfig] = config or load_governance_triggers()
        self._streak: dict[str, int] = {key: 0 for key in self.config}
        self._prev_report: dict[str, Any] | None = None
        self._game_over: bool = False
        self._reasons: list[str] = []

    def evaluate(self, current_report: dict[str, Any]) -> bool:
        """读取 last_report，更新 streak；若 game_over 触发则返回 True。

        返回 True 后再调用 evaluate 直接返回 True（保持 game_over 状态）。
        """
        if self._game_over:
            return True
        newly_breached: set[str] = set()
        for key, cfg in self.config.items():
            if cfg.type == 'multi':
                continue  # multi 在 single 评估后处理
            value = self._read_metric_for(cfg.metric, current_report)
            if value is None:
                self._streak[key] = 0
                continue
            if self._compare(value, cfg.comparison, cfg.threshold):
                self._streak[key] += 1
                if cfg.severity == 'immediate' or self._streak[key] >= cfg.grace_months:
                    newly_breached.add(key)
            else:
                self._streak[key] = 0

        for key, cfg in self.config.items():
            if cfg.type != 'multi':
                continue
            # multi 类型：只要当月 all_of 都 breach（streak >= 1）就立即触发
            if all(self._streak.get(s, 0) >= 1 for s in cfg.all_of):
                newly_breached.add(key)

        self._prev_report = dict(current_report)
        if newly_breached:
            self._game_over = True
            self._reasons = [self.config[k].label for k in sorted(newly_breached)]
        return self._game_over

    def _read_metric_for(self, metric: str, report: dict[str, Any]):
        """按 cfg.metric 名路由到对应读取路径。"""
        if metric == 'unemployment_rate':
            if 'unemployment_rate' in report and report['unemployment_rate'] is not None:
                return report['unemployment_rate']
            labor = report.get('labor_force') or 0
            if labor and 'unemployed' in report:
                return report['unemployed'] / labor
            return None
        if metric == 'government_cash_cents':
            return report.get('government_cash_cents')
        if metric == 'wage_arrears_cents':
            return report.get('wage_arrears_cents')
        if metric == 'population_change_rate':
            if 'population_change_rate' in report and report['population_change_rate'] is not None:
                return report['population_change_rate']
            curr_pop = report.get('total_population', report.get('population'))
            if self._prev_report and curr_pop is not None:
                prev_pop = self._prev_report.get('total_population', self._prev_report.get('population'))
                if prev_pop:
                    return (curr_pop - prev_pop) / prev_pop
            return None
        return report.get(metric)

    @staticmethod
    def _compare(value: float, op: str, threshold: float) -> bool:
        if op == '>=': return value >= threshold
        if op == '<=': return value <= threshold
        if op == '>':  return value >  threshold
        if op == '<':  return value <  threshold
        if op == '==': return value == threshold
        raise ValueError(f'不支持的比较运算符 {op}')

    @property
    def is_game_over(self) -> bool:
        return self._game_over

    @property
    def reasons(self) -> list[str]:
        return list(self._reasons)

    @property
    def streak(self) -> dict[str, int]:
        return dict(self._streak)

    def to_dict(self) -> dict[str, Any]:
        return {
            'is_game_over': self._game_over,
            'reasons': list(self._reasons),
            'streak': dict(self._streak),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> 'EconomicTrigger':
        inst = cls.__new__(cls)
        inst.config = load_governance_triggers()
        inst._streak = dict(data.get('streak', {key: 0 for key in inst.config}))
        inst._prev_report = None
        inst._game_over = bool(data.get('is_game_over', False))
        inst._reasons = list(data.get('reasons', []))
        return inst

    def __eq__(self, other):
        if not isinstance(other, EconomicTrigger):
            return NotImplemented
        return (self._game_over == other._game_over
                and self._reasons == other._reasons
                and self._streak == other._streak)

    def __hash__(self):
        return hash((self._game_over, tuple(self._reasons), tuple(sorted(self._streak.items()))))