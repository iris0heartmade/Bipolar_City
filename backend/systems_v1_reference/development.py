"""发展层：把产能公式与企业决策接到可玩世界的月结上。

这一层解决的根本问题
--------------------
接入之前，城市的经济是**定值**：六行业每月固定发同样多的工资、
营业额永远是同一个数。所以无论玩家盖多少房、建多少店，城市都不会变。

设计稿 §4.1 的产能公式和 §9 的企业进退才是「发动机」。
本模块把这两台发动机接到投入产出表上，让月结真正吃进：

1. **产能利用率**：招不满人、缺原料、技能不足时营业额打折；
2. **企业规模**：扩张时行业人数与营业额一起长，退出时一起缩。

两条纪律
--------
1. **参考城不受影响**：所有函数都接受 ``None`` 表示「不启用」。
   P00 的参考城传 None，行为与接入前逐分一致，14 项基准不会漂移。
2. **只缩放、不发明**：每一项都能追溯到产能公式的哪个因子，
   或企业决策的哪条门槛。凭空造的数字一律不许进这里。
"""
from copy import deepcopy
from fractions import Fraction

from backend.config import INPUT_OUTPUT, REFERENCE_CITY
from backend.core.errors import GameError
from backend.systems.capacity import (CapacityFactors, labour_factor,
                                      resource_factor, safety_factor,
                                      skill_factor, worktime_factor)
from backend.systems.firm_decisions import (FirmHistory, decide_entry,
                                            decide_operation)

REFERENCE_POPULATION = REFERENCE_CITY["demographics"]["population"]


def _spread(total: int, weights: list) -> list[int]:
    """按权重把整数拆开，残差归入权重最大项。人数与金额都会因取整丢分。"""
    if not weights or total <= 0:
        return [0] * len(weights)
    weight_sum = sum(weights)
    parts = [int(total * weight / weight_sum) for weight in weights]
    parts[weights.index(max(weights))] += total - sum(parts)
    return parts


def capacity_factors(world=None, city_factors: CapacityFactors = None,
                     faith_share: Fraction = None) -> CapacityFactors:
    """算出本月六个产能因子。

    聚合阶段所有行业面对同一座城市：同一批劳动力、同一个信仰比例、
    同一套治安水平，所以六个行业的因子相同。
    逐店建模（P05）之后才会各店不同。
    """
    faith = (faith_share if faith_share is not None
             else Fraction(str(REFERENCE_CITY["religion"]["opening_faith"])))
    # F_worktime 只跟信仰比例有关，其余因子当前无城市级输入，一律为 1。
    # 教育（P07）、战略品（P06）、魔法防御（P07）落地时，在这里接入对应项。
    return CapacityFactors(worktime=worktime_factor(faith)) if city_factors is None else city_factors


def apply_capacity_to_input_output(input_output: dict,
                                   factors: CapacityFactors) -> dict:
    """把产能利用率作用到投入产出表的**对外收入来源**上。

    只缩放三类真正受产能影响的收入：
    - 出口（生产出来的才卖得出去）
    - 私人资本投入（企业有钱才投）
    - 游客消费（有东西可买才有人来）

    **不缩放**居民消费、市政采购、市政资本支出、业主维修：
    这三类是付款方的需求，不是企业的产出能力。
    企业产能不足时缺的那部分，应该表现为「卖不掉」而不是「居民不买了」——
    所以缺口由消费侧重分配机制承担，而不是在这里悄悄减收入。
    """
    if factors.product == 1:
        return input_output                     # 满负荷时原样返回，不做任何改动
    scaled = deepcopy(input_output)
    sources = scaled["firm_revenue_sources"]
    for key in ("exports", "private_capital", "tourist_spend"):
        sources[key] = _scale_cents(sources[key], factors.product)
    return scaled


def _scale_cents(amount_cr: int, factor: Fraction) -> int:
    """金额缩放，保持非零项至少 1 单位。

    缩到 0 会让引擎以为「本月没有这笔义务」，那比少收一点钱更糟。
    """
    if not amount_cr:
        return 0
    return max(1, int(round(amount_cr * float(factor))))


def sector_employee_targets(world, history: dict) -> tuple[dict, list]:
    """按企业进退规则算六行业本月目标人数，并给出玩家可读的理由。

    返回 (每行业目标人数, 决策说明列表)。
    只对**已有历史**的行业做进退判断——第一个月没有历史可看，一律维持现状。
    """
    from backend.systems.live_economy import labour_structure

    labour = labour_structure(world)
    config = deepcopy(REFERENCE_CITY)
    sectors = config["firms"]["sectors"]
    private_total = max(len(sectors),
                        round(labour["employed"] * 0.92))
    headcounts = _spread(private_total, [s["employees"] for s in sectors])

    targets, notes = {}, []
    for sector, headcount in zip(sectors, headcounts):
        record = history.get(sector["id"])
        if record is None or not record.utilisation:
            # 没有已结算的历史：本月维持按人口比推出的规模，不猜、不动
            targets[sector["id"]] = headcount
            continue
        decision = decide_operation(record, sector["id"], headcount,
                                    utilisation=Fraction(0), profit=0)
        targets[sector["id"]] = decision.target_employees
        if decision.action != "hold":
            notes.append(f"{sector['name']}：{decision.reason}")

    return targets, notes


def rebuild_config_for_targets(config: dict, targets: dict) -> dict:
    """按目标人数重写六行业配置：人数守恒，人均水平保持不变。

    这正是设计稿 §4.1 F_labor 想表达的事——
    招到人，产出就上去；招不到，产出就打折。单价一律不动。
    """
    rebuilt = deepcopy(config)
    sectors = rebuilt["firms"]["sectors"]
    for sector in sectors:
        headcount = targets.get(sector["id"], sector["employees"])
        if sector["employees"] <= 0:
            continue
        factor = headcount / sector["employees"]
        sector["employees"] = headcount
        sector["firms"] = max(1, int(round(sector["firms"] * factor)))
        for key in ("revenue", "input_cost", "commercial_rent", "depreciation", "fees"):
            sector[key] = max(1, int(round(sector[key] * factor)))
    rebuilt["firms"]["private_employees"] = sum(s["employees"] for s in sectors)
    return rebuilt


def firm_history_from_config(config: dict, previous: dict = None) -> dict[str, FirmHistory]:
    """从配置重建各行业历史。previous 传入时跨月延续，避免读档后判断断链。"""
    histories = dict(previous or {})
    for sector in config["firms"]["sectors"]:
        histories.setdefault(sector["id"], FirmHistory(sector["id"]))
    return histories


__all__ = [
    "apply_capacity_to_input_output", "capacity_factors", "firm_history_from_config",
    "rebuild_config_for_targets", "sector_employee_targets",
]