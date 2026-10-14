"""金额为整数分币，建筑容量不等于实际居民数量。

v3 起接入 P01—P03 的账户清算引擎：市政与居民不再共用「按人口自动加钱」，
而是各自持有账户余额，每月由新引擎跑一次真实收付。economy 保存账户簿快照，
economy_month 记录已结算到第几个月，读档后从下一个月继续。
"""
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

SCHEMA_VERSION = 4


@dataclass
class Building:
    id: int
    definition_id: str
    x: int
    y: int
    rotation: int = 0
    status: str = "blueprint"
    progress: int = 0
    owner_mode: str = "municipal"
    housing_mode: str = "public"
    sold_units: int = 0
    occupancy: int = 0
    merchants: int = 0


@dataclass
class World:
    seed: int
    terrain: list[list[str]]
    buildings: list[Building]
    cash: int
    population: int
    schema_version: int = SCHEMA_VERSION
    template_id: str = "river_town"
    generator_version: int = 1
    day: int = 0
    paused: bool = True
    speed: float = 1
    next_id: int = 1
    revision: int = 0
    ledger: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    history: list[dict[str, int]] = field(default_factory=list)
    opening_cash: int = 0
    resident_funds: int = 0
    buyer_demand: int = 0
    economy: Optional[dict] = None      # 账户簿快照（AccountBook.to_dict 的结构）
    economy_month: int = 0              # 已结算到第几个月，读档后从 +1 继续
    pending_construction_cents: int = 0 # 本月已批准但尚未入账的施工款
    # 产能层开关（设计稿 §4.1 Qcap 与 §9.1 企业进退）。
    # False 时月结走定值口径，行为与接入前逐分一致；True 时企业规模按经营历史进退。
    development_enabled: bool = True

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        payload = dict(data)
        payload["buildings"] = [Building(**item) for item in payload["buildings"]]
        return cls(**payload)
