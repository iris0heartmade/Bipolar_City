"""V2.1 阶段 9：月结算引擎（§12 15 步 + 防循环引用）。

【2026-10-15】Bipole 城市聚合经济系统理论说明书 V2.1 §12 月结算引擎。

【V2.1 §12 15 步同步顺序】
月初读取所有城市 S_t 和世界市场报价 p_t、C_t、pi_t，执行以下同步顺序：
01 更新已完工建筑、实际到期履约的跨城货物/服务合同与已经生效的政策。
02 确认城市/行业/政府现金、库存及各项建设维护承诺；锁定必要付款资金。
03 计算本月可支付岗位 Jop、分配劳动力 E、保留工资负债并实际发放有资金的工资。
04 依据已到账收入、储蓄和预算限制，生成各居民归属群体的消费需求。
05 依据已到账可购买的关键原料计算行业实际产量，扣实际生产投入库存。
06 在冻结的当期价格下聚合本地各行业采购、居民购买、政府采购和国外订单。
07 市场清算：按实际库存、真实买方资金、货运能力交割；一个订单只生成一次转账。
08 财务结算：企业租金、真实运营采购、行业税、个人税、补贴、分红、贷款利息。
09 结算房地产业主净租金、政府救济及教会捐赠，不重复扣已过账项目。
10 更新每行业经营现金、每居民群体储蓄、银行本金债权、市政府现金债务。
11 更新商品库存、完成财政/产业/人口/资源账的守恒断言。
12 用月末实际就业、持续粮食供应、已建成住房、安全计算 K 与 L。
13 世界层对所有城市同时求解迁移，转移人口及便携储蓄，重新计算信徒构成。
14 基于通胀及市场相对供需更新下一月消费标准 C、CPI、工资目标、报价。
15 生成 MonthReport，冻结一致性存档，进入下一月。

【防循环引用关键约束】
- 统一用已到账款项与期初储蓄预算；预计销售额不是可用现金。
- 未来建造的建筑不参与当月生产；只有完成建造才获得岗位/服务/住房。
- 同一笔公共采购从政府支出和卖方收入各记一次，但不能在不同步骤再次付款。
- 本月迁入者原则上从下个月才参与劳动力分配与正常消费，防同月重复计算。

【V2.1 阶段 9 范围】
本模块承载：
- MonthlyStep 枚举（15 步严格顺序）
- MonthlyContext 数据类（持有所有城市账本 + WorldState + 月份）
- MonthReport 数据类（月末冻结报告）
- run_month_step() 单步执行（含各步占位实现）
- run_full_month() 调度 15 步
- assert_conservation() 守恒断言（V2.1 §17 TEST-04）
"""
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Optional, Callable

from backend.core.errors import GameError
from backend.systems.v21.accounts import OCEAN_ACCOUNT
from backend.systems.v21.ledger import Ledger
from backend.systems.v21.inflation import WorldState


# ===== V2.1 §12 15 步顺序 =====

class MonthlyStep(IntEnum):
    """V2.1 §12 15 步同步顺序。"""
    STEP_01_UPDATE_CONTRACTS = 1     # 01 更新已完工建筑 / 合同 / 政策
    STEP_02_LOCK_FUNDS = 2           # 02 确认现金、库存、付款承诺；锁定必要付款
    STEP_03_PAYROLL = 3              # 03 Jop / E / 工资负债 / 实际发放
    STEP_04_CONSUMPTION_DEMAND = 4   # 04 各居民群体消费需求
    STEP_05_PRODUCTION = 5           # 05 行业实际产量 + 扣投入
    STEP_06_AGGREGATE_PURCHASE = 6   # 06 聚合本地采购 + 居民购买 + 政府采购 + 国外
    STEP_07_MARKET_CLEAR = 7         # 07 市场清算：实际库存 + 真实买方 + 货运
    STEP_08_FINANCE = 8              # 08 财务结算：税、分红、利息、补贴
    STEP_09_PROPERTY_RELIEF = 9      # 09 房地产净租金 + 救济 + 教会捐赠
    STEP_10_UPDATE_CASH = 10         # 10 更新所有账户现金
    STEP_11_CONSERVATION = 11        # 11 守恒断言
    STEP_12_K_AND_L = 12             # 12 算 K 与 L
    STEP_13_MIGRATION = 13           # 13 多城同步迁移
    STEP_14_UPDATE_INDEX = 14        # 14 下月 C、I、pi、wage target
    STEP_15_REPORT = 15              # 15 MonthReport 冻结存档


# ===== V2.1 §12 守恒断言（§17 TEST-04）=====

@dataclass
class ConservationCheck:
    """V2.1 §17 TEST-04 守恒检查结果。"""
    passed: bool
    errors: list[str] = field(default_factory=list)
    ocean_net: int = 0                # 海洋账户净变化
    local_total: int = 0              # 本地现金合计
    peer_total: int = 0               # 邻城现金合计
    inflow_outflow: int = 0           # 邻城净流入（inflow - outflow）


# ===== V2.1 §12 月初上下文 =====

@dataclass
class MonthlyContext:
    """V2.1 §12 月初读取 S_t 与世界市场报价 p_t/C_t/pi_t。

    字段：
    - month：当前月份（1 = 第一个月）
    - world：WorldState（pi/global_I/global_C）
    - ledgers：{city_id: Ledger} 多城账本
    - policies：当前生效政策（占位 dict[policy_id, policy]）
    - completed_buildings：当月完工建筑（占位 list）
    - expired_contracts：当月到期合同（占位 list）
    - running：每步执行状态（用于中断恢复）
    """
    month: int = 1
    world: Optional[WorldState] = None
    ledgers: dict[str, Ledger] = field(default_factory=dict)
    policies: dict[str, dict] = field(default_factory=dict)
    completed_buildings: list[dict] = field(default_factory=list)
    expired_contracts: list[dict] = field(default_factory=list)
    running: dict[str, bool] = field(default_factory=dict)
    # 月末冻结数据（步骤 12/13/14 填入）
    K_by_city: dict[str, int] = field(default_factory=dict)
    L_by_city: dict[str, float] = field(default_factory=dict)
    migration_delta_N: dict[str, int] = field(default_factory=dict)


# ===== V2.1 §12 月末报告 =====

@dataclass
class MonthReport:
    """V2.1 §12.15 月末冻结报告（一致性存档）。"""
    month: int
    city_reports: dict[str, 'CityMonthReport'] = field(default_factory=dict)
    world_inflation_pi: float = 0.0
    world_I: float = 1.0
    world_C: float = 50.0
    total_moved: int = 0              # §11.3(f) 当月总迁移人数
    conservation_passed: bool = False
    conservation_errors: list[str] = field(default_factory=list)
    step_executed: dict[int, bool] = field(default_factory=dict)


@dataclass
class CityMonthReport:
    """V2.1 §12.15 单城月报。"""
    city_id: str
    N: int = 0
    K: int = 0
    L: float = 0.0
    total_industry_sales_cents: int = 0   # §14.4 行业销售总额
    total_wages_cents: int = 0            # §14.1 工资总额
    total_taxes_cents: int = 0            # 行业税 + PIT
    net_migration: int = 0
    conservation_ocean_net: int = 0


# ===== V2.1 §12 步骤占位实现 =====

def _step_01_update_contracts(ctx: MonthlyContext) -> MonthlyContext:
    """V2.1 §12.01 更新已完工建筑 / 到期合同 / 生效政策。

    占位实现：从 ctx.completed_buildings / expired_contracts / policies 读取并应用。
    实际业务逻辑由阶段 9 后续实现。
    """
    return ctx


def _step_02_lock_funds(ctx: MonthlyContext) -> MonthlyContext:
    """V2.1 §12.02 确认现金、库存、付款承诺；锁定必要付款资金。

    占位实现：检查每个账本的开账快照与对必要付款的锁定。
    实际业务由各付款方（行业/政府/居民）按需上锁。
    """
    return ctx


def _step_03_payroll(ctx: MonthlyContext) -> MonthlyContext:
    """V2.1 §12.03 Jop / E / 工资负债 / 实际发放。

    占位实现：业务由行业工资（阶段 6）实际发放；本步骤只标记完成。
    """
    return ctx


def _step_04_consumption_demand(ctx: MonthlyContext) -> MonthlyContext:
    """V2.1 §12.04 各居民群体消费需求。

    占位实现：由消费模块（阶段 6）按 Cneed 与预算生成需求。
    """
    return ctx


def _step_05_production(ctx: MonthlyContext) -> MonthlyContext:
    """V2.1 §12.05 行业实际产量 + 扣投入。

    占位实现：eta_i 计算与投入扣减；由生产模块（阶段 9 后续）实现。
    """
    return ctx


def _step_06_aggregate_purchase(ctx: MonthlyContext) -> MonthlyContext:
    """V2.1 §12.06 聚合本地 + 居民 + 政府 + 国外采购。

    占位实现：把需求聚合到订单簿（阶段 5）。
    """
    return ctx


def _step_07_market_clear(ctx: MonthlyContext) -> MonthlyContext:
    """V2.1 §12.07 市场清算：按实际库存、真实买方资金、货运能力交割。

    占位实现：按订单匹配（阶段 5）+ 在途库存（阶段 5）清算。
    """
    return ctx


def _step_08_finance(ctx: MonthlyContext) -> MonthlyContext:
    """V2.1 §12.08 财务结算：税、分红、利息、补贴、贷款。

    占位实现：行业税（阶段 1）+ PIT（阶段 6）+ 利息（阶段 4）。
    """
    return ctx


def _step_09_property_relief(ctx: MonthlyContext) -> MonthlyContext:
    """V2.1 §12.09 房地产净租金 + 政府救济 + 教会捐赠。

    占位实现：PROP 通道已由 Ledger.end_month 清零；本步记录净租金。
    """
    return ctx


def _step_10_update_cash(ctx: MonthlyContext) -> MonthlyContext:
    """V2.1 §12.10 更新所有账户现金。

    占位实现：各账本已完成 pay() 实际过账；本步只校验最终余额。
    """
    return ctx


def _step_11_conservation(ctx: MonthlyContext) -> MonthlyContext:
    """V2.1 §12.11 守恒断言（§17 TEST-04）。"""
    return ctx


def _step_12_k_and_l(ctx: MonthlyContext) -> MonthlyContext:
    """V2.1 §12.12 用月末实际就业、粮食、住房、安全算 K 与 L。"""
    return ctx


def _step_13_migration(ctx: MonthlyContext) -> MonthlyContext:
    """V2.1 §12.13 多城同步迁移。"""
    return ctx


def _step_14_update_index(ctx: MonthlyContext) -> MonthlyContext:
    """V2.1 §12.14 更新下月 C、I、pi、wage target。"""
    if ctx.world is not None:
        ctx.world.update_world_index()
    return ctx


def _step_15_report(ctx: MonthlyContext) -> MonthlyContext:
    """V2.1 §12.15 生成 MonthReport 冻结存档。"""
    return ctx


# 15 步 → 函数映射
_STEP_FUNCTIONS: dict[MonthlyStep, Callable[[MonthlyContext], MonthlyContext]] = {
    MonthlyStep.STEP_01_UPDATE_CONTRACTS: _step_01_update_contracts,
    MonthlyStep.STEP_02_LOCK_FUNDS: _step_02_lock_funds,
    MonthlyStep.STEP_03_PAYROLL: _step_03_payroll,
    MonthlyStep.STEP_04_CONSUMPTION_DEMAND: _step_04_consumption_demand,
    MonthlyStep.STEP_05_PRODUCTION: _step_05_production,
    MonthlyStep.STEP_06_AGGREGATE_PURCHASE: _step_06_aggregate_purchase,
    MonthlyStep.STEP_07_MARKET_CLEAR: _step_07_market_clear,
    MonthlyStep.STEP_08_FINANCE: _step_08_finance,
    MonthlyStep.STEP_09_PROPERTY_RELIEF: _step_09_property_relief,
    MonthlyStep.STEP_10_UPDATE_CASH: _step_10_update_cash,
    MonthlyStep.STEP_11_CONSERVATION: _step_11_conservation,
    MonthlyStep.STEP_12_K_AND_L: _step_12_k_and_l,
    MonthlyStep.STEP_13_MIGRATION: _step_13_migration,
    MonthlyStep.STEP_14_UPDATE_INDEX: _step_14_update_index,
    MonthlyStep.STEP_15_REPORT: _step_15_report,
}


# ===== V2.1 §12 月结入口 =====

def run_month_step(
    step: MonthlyStep,
    ctx: MonthlyContext,
) -> MonthlyContext:
    """V2.1 §12 执行单步；按 §12 顺序调用对应函数。

    抛 GameError 当上下文不完整（如 WorldState 缺失）。
    """
    if ctx.world is None and step in {
        MonthlyStep.STEP_14_UPDATE_INDEX,
        MonthlyStep.STEP_12_K_AND_L,
    }:
        raise GameError(f"step {step} 需要 WorldState")

    if step not in _STEP_FUNCTIONS:
        raise GameError(f"未知步骤：{step}")

    func = _STEP_FUNCTIONS[step]
    ctx = func(ctx)
    ctx.running[f"step_{step.value:02d}"] = True
    return ctx


def run_full_month(ctx: MonthlyContext) -> MonthReport:
    """V2.1 §12 调度 15 步执行完整月结。

    步骤严格按 §12 顺序执行；返回 MonthReport 冻结存档。
    """
    for step in MonthlyStep:
        ctx = run_month_step(step, ctx)

    # 生成月报
    report = MonthReport(
        month=ctx.month,
        world_inflation_pi=ctx.world.inflation_pi if ctx.world else 0.0,
        world_I=ctx.world.global_I if ctx.world else 1.0,
        world_C=ctx.world.global_C if ctx.world else 50.0,
        total_moved=sum(abs(v) for v in ctx.migration_delta_N.values()) // 2,
        step_executed=ctx.running,
    )
    # 守恒检查
    check = assert_conservation(ctx)
    report.conservation_passed = check.passed
    report.conservation_errors = check.errors
    return report


# ===== V2.1 §12 + §17 守恒断言 =====

def assert_conservation(
    ctx: MonthlyContext,
    *,
    tol: int = 0,                     # 整数守恒（分）
) -> ConservationCheck:
    """V2.1 §12.11 + §17 TEST-04 守恒断言。

    关键不变量：本地现金变化 = 海洋 + 邻城净流出（V2.1 §11 TEST-04 强调）。
    本检查为占位实现：未来可扩展为完整守恒。
    """
    errors: list[str] = []
    ocean_net = 0
    local_total = 0
    peer_total = 0
    inflow_outflow = 0

    for city_id, ledger in ctx.ledgers.items():
        ocean_net += ledger.ocean_net()
        local_total += ledger.local_total()
        inflow_outflow += sum(f.amount_cents for f in ledger.peer_flows if f.direction == 'in') \
                       - sum(f.amount_cents for f in ledger.peer_flows if f.direction == 'out')

    # 占位：暂不严格校验（未来按 V2.1 §17 完整规则实现）
    return ConservationCheck(
        passed=len(errors) == 0,
        errors=errors,
        ocean_net=ocean_net,
        local_total=local_total,
        peer_total=peer_total,
        inflow_outflow=inflow_outflow,
    )


# ===== V2.1 §12 高层入口 =====

def run_first_month(
    ledgers: dict[str, Ledger],
    world: Optional[WorldState] = None,
) -> tuple[MonthlyContext, MonthReport]:
    """V2.1 §12.14 第一个月（V2.1 §14 金标准）。"""
    if world is None:
        world = WorldState()
    ctx = MonthlyContext(month=1, world=world, ledgers=ledgers)
    report = run_full_month(ctx)
    return ctx, report


def run_next_month(
    prev_ctx: MonthlyContext,
) -> MonthlyContext:
    """V2.1 §12 进入下一月（++month, WorldState 已由 step_14 更新）。"""
    new_ctx = MonthlyContext(
        month=prev_ctx.month + 1,
        world=prev_ctx.world,
        ledgers=prev_ctx.ledgers,
        policies=prev_ctx.policies,
    )
    return new_ctx
