"""AI 市长偏好配置：每座 NPC 城市独立持有。

参数分层：这是**城市级**配置，不与世界级/国家级参数混淆。
玩家城市与 NPC 城市**各自独立**的数据：scenario、人口、就业、政策、税率。
"""
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from backend.config import ROOT


WELFARE_STANCES = ('lavish', 'normal', 'austere')
DEFAULT_INDUSTRY_FOCUS = {i: 1.0 for i in
                         ('AGR', 'IND', 'SRV', 'CHU', 'BNK', 'MAG', 'GOV')}


@dataclass(frozen=True)
class MayorPreferences:
    """单 NPC 城市 AI 市长的决策偏好。

    每座城市一个实例，配置不会跨城共享。
    """
    city_id: str
    archetype: str
    industry_focus: dict = field(default_factory=lambda: dict(DEFAULT_INDUSTRY_FOCUS))
    tax_rate_preference: float = 0.10
    welfare_stance: str = 'normal'
    expansion_drive: float = 0.5
    risk_tolerance: float = 0.5
    subsidy_priority: tuple = ()
    purchase_urgency: float = 0.5
    target_unemployment_rate: float = 0.20
    target_wage_arrears_ratio: float = 0.5
    min_cash_months: int = 3


def _validate(prefs: MayorPreferences) -> MayorPreferences:
    if not 0 <= prefs.tax_rate_preference <= 0.5:
        raise ValueError(f'{prefs.city_id} 税率偏好 {prefs.tax_rate_preference} 越界 [0, 0.5]')
    if prefs.welfare_stance not in WELFARE_STANCES:
        raise ValueError(f'{prefs.city_id} 福利倾向必须是 {WELFARE_STANCES} 之一')
    for k, v in prefs.industry_focus.items():
        if k not in DEFAULT_INDUSTRY_FOCUS:
            raise ValueError(f'{prefs.city_id} 行业偏好含未知行业 {k}')
        if not isinstance(v, (int, float)) or v < 0:
            raise ValueError(f'{prefs.city_id} 行业 {k} 偏好 {v} 非法')
    return prefs


def _load_profiles() -> dict[str, MayorPreferences]:
    path = Path(ROOT / 'data' / 'npc_mayor_profiles.json')
    if not path.exists():
        return {}
    config = json.loads(path.read_text(encoding='utf-8-sig'))
    out: dict[str, MayorPreferences] = {}
    for entry in config.get('profiles', []):
        prefs = MayorPreferences(
            city_id=entry['city_id'],
            archetype=entry.get('archetype', ''),
            industry_focus={**DEFAULT_INDUSTRY_FOCUS, **entry.get('industry_focus', {})},
            tax_rate_preference=float(entry.get('tax_rate_preference', 0.10)),
            welfare_stance=entry.get('welfare_stance', 'normal'),
            expansion_drive=float(entry.get('expansion_drive', 0.5)),
            risk_tolerance=float(entry.get('risk_tolerance', 0.5)),
            subsidy_priority=tuple(entry.get('subsidy_priority', ())),
            purchase_urgency=float(entry.get('purchase_urgency', 0.5)),
            target_unemployment_rate=float(entry.get('target_unemployment_rate', 0.20)),
            target_wage_arrears_ratio=float(entry.get('target_wage_arrears_ratio', 0.5)),
            min_cash_months=int(entry.get('min_cash_months', 3)),
        )
        out[entry['city_id']] = _validate(prefs)
    return out


_DEFAULT_PROFILES_CACHE: Optional[dict] = None


def default_preferences() -> dict[str, MayorPreferences]:
    """读取 data/npc_mayor_profiles.json 并构造 MayorPreferences 字典（按 city_id 索引）。"""
    global _DEFAULT_PROFILES_CACHE
    if _DEFAULT_PROFILES_CACHE is None:
        _DEFAULT_PROFILES_CACHE = _load_profiles()
    return dict(_DEFAULT_PROFILES_CACHE)


def get_preferences(city_id: str) -> MayorPreferences:
    """按 city_id 取出对应 NPC 城市的 AI 偏好；找不到返回默认配置。"""
    profiles = default_preferences()
    if city_id in profiles:
        return profiles[city_id]
    return MayorPreferences(city_id=city_id, archetype='')


def reset_cache_for_tests():
    global _DEFAULT_PROFILES_CACHE
    _DEFAULT_PROFILES_CACHE = None