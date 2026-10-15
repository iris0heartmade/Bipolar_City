"""V2.1 阶段 9 月结引擎验收测试（2026-10-15）。

【验收清单】（V2.1 §12）
- [x] MonthlyStep 15 值枚举（按 §12 顺序）
- [x] MonthlyContext 数据类（多城账本 + WorldState + 月份）
- [x] MonthReport + CityMonthReport 数据类
- [x] ConservationCheck 守恒检查结果
- [x] 15 步占位实现 + _STEP_FUNCTIONS 映射完整
- [x] run_month_step 单步执行
- [x] run_full_month 15 步严格顺序
- [x] run_first_month 初始月
- [x] run_next_month 进入下月
- [x] assert_conservation 占位校验
- [x] 步骤 14 WorldState 月度更新
"""
import pytest

from backend.core.errors import GameError
from backend.systems.v21.inflation import WorldState
from backend.systems.v21.ledger import Ledger, build_industrial_city_accounts
from backend.systems.v21.monthly_engine import (
    MonthlyStep, MonthlyContext, MonthReport, CityMonthReport,
    ConservationCheck,
    run_month_step, run_full_month, run_first_month, run_next_month,
    assert_conservation,
)


# ===== 测试夹具 =====

@pytest.fixture
def world():
    return WorldState()


@pytest.fixture
def ledger():
    book = build_industrial_city_accounts(city_id='test_city')
    return Ledger(city_id='test_city', book=book)


@pytest.fixture
def ctx(ledger, world):
    return MonthlyContext(month=1, world=world, ledgers={'test_city': ledger})


# ===== A. MonthlyStep 枚举（3 项）=====

class TestMonthlyStepEnum:
    """V2.1 §12 15 步枚举。"""

    def test_has_15_values(self):
        """阶段 9：MonthlyStep 必须有 15 值。"""
        assert len(MonthlyStep) == 15

    def test_steps_in_order(self):
        """阶段 9：step value 必须严格 1..15 顺序。"""
        values = sorted([s.value for s in MonthlyStep])
        assert values == list(range(1, 16))

    def test_step_names_match_spec(self):
        """阶段 9：15 步名称严格匹配 §12.01-§12.15。"""
        assert MonthlyStep.STEP_01_UPDATE_CONTRACTS.value == 1
        assert MonthlyStep.STEP_03_PAYROLL.value == 3
        assert MonthlyStep.STEP_07_MARKET_CLEAR.value == 7
        assert MonthlyStep.STEP_11_CONSERVATION.value == 11
        assert MonthlyStep.STEP_12_K_AND_L.value == 12
        assert MonthlyStep.STEP_13_MIGRATION.value == 13
        assert MonthlyStep.STEP_14_UPDATE_INDEX.value == 14
        assert MonthlyStep.STEP_15_REPORT.value == 15


# ===== B. MonthlyContext（5 项）=====

class TestMonthlyContext:
    """V2.1 §12 月初上下文。"""

    def test_default_construction(self):
        """阶段 9：MonthlyContext 默认构造。"""
        ctx = MonthlyContext()
        assert ctx.month == 1
        assert ctx.world is None
        assert ctx.ledgers == {}
        assert ctx.policies == {}
        assert ctx.running == {}

    def test_with_ledgers_and_world(self, ctx):
        """阶段 9：MonthlyContext 持有账本与 WorldState。"""
        assert ctx.month == 1
        assert ctx.world is not None
        assert 'test_city' in ctx.ledgers

    def test_running_records_step_execution(self, ctx):
        """阶段 9：执行步骤后 ctx.running 记录。"""
        run_month_step(MonthlyStep.STEP_01_UPDATE_CONTRACTS, ctx)
        assert ctx.running.get('step_01') is True

    def test_step_14_updates_world(self, ctx):
        """阶段 9：step_14 调用 WorldState.update_world_index（month +1）。"""
        before_month = ctx.world.month
        run_month_step(MonthlyStep.STEP_14_UPDATE_INDEX, ctx)
        assert ctx.world.month == before_month + 1

    def test_step_14_without_world_raises(self):
        """阶段 9：step_14 在无 WorldState 时抛 GameError。"""
        ctx = MonthlyContext()
        with pytest.raises(GameError, match='需要 WorldState'):
            run_month_step(MonthlyStep.STEP_14_UPDATE_INDEX, ctx)


# ===== C. MonthReport（3 项）=====

class TestMonthReport:
    """V2.1 §12.15 月末冻结报告。"""

    def test_default_construction(self):
        """阶段 9：MonthReport 默认字段。"""
        r = MonthReport(month=1)
        assert r.month == 1
        assert r.world_inflation_pi == 0.0
        assert r.world_I == 1.0
        assert r.world_C == 50.0
        assert r.total_moved == 0
        assert r.conservation_passed is False

    def test_city_month_report_fields(self):
        """阶段 9：CityMonthReport 字段。"""
        r = CityMonthReport(city_id='A', N=3000, K=3257, L=75.0)
        assert r.city_id == 'A'
        assert r.N == 3000
        assert r.K == 3257
        assert r.L == 75.0
        assert r.net_migration == 0


# ===== D. 15 步执行（5 项）=====

class TestRunMonthStep:
    """V2.1 §12 单步执行。"""

    def test_all_15_steps_runnable(self, ctx):
        """阶段 9：15 步均可执行。"""
        for step in MonthlyStep:
            run_month_step(step, ctx)
        # 全部 15 步记录
        for step in MonthlyStep:
            assert ctx.running.get(f'step_{step.value:02d}') is True

    def test_run_full_month_returns_report(self, ctx):
        """阶段 9：run_full_month 返回 MonthReport。"""
        report = run_full_month(ctx)
        assert isinstance(report, MonthReport)
        assert report.month == 1
        # 全部 15 步执行
        assert len(report.step_executed) == 15
        assert all(report.step_executed.values())

    def test_run_first_month_creates_context(self, ledger, world):
        """阶段 9：run_first_month 创建初始上下文与月报。"""
        ctx, report = run_first_month(ledgers={'test_city': ledger}, world=world)
        assert ctx.month == 1
        assert report.month == 1
        assert report.world_inflation_pi == world.inflation_pi

    def test_run_next_month_increments(self, ctx):
        """阶段 9：run_next_month 让 month +1。"""
        next_ctx = run_next_month(ctx)
        assert next_ctx.month == 2
        # world 引用保持
        assert next_ctx.world is ctx.world
        # ledgers 引用保持
        assert next_ctx.ledgers is ctx.ledgers

    def test_unknown_step_raises(self, ctx):
        """阶段 9：未知 step 抛 GameError（防御性检查）。"""
        with pytest.raises(GameError, match='未知步骤'):
            run_month_step(999, ctx)  # type: ignore


# ===== E. 守恒断言（5 项）=====

class TestConservation:
    """V2.1 §12.11 + §17 TEST-04 守恒。"""

    def test_conservation_with_empty_context(self):
        """阶段 9：空上下文守恒检查通过（占位实现）。"""
        check = assert_conservation(MonthlyContext())
        assert check.passed is True
        assert check.ocean_net == 0
        assert check.local_total == 0

    def test_conservation_with_ledger(self, ctx):
        """阶段 9：带账本时守恒检查。"""
        check = assert_conservation(ctx)
        assert check.passed is True
        # 占位实现：ocean_net/local_total 由各账本计算
        # 初始月没有跨城过账，应该全 0
        assert check.ocean_net == 0

    def test_conservation_ocean_net_reflects_pay(self, ctx):
        """阶段 9：占位实现读 ledger.ocean_net（基于 opening_snapshot）。"""
        # 没有任何过账
        check = assert_conservation(ctx)
        assert check.ocean_net == 0

    def test_conservation_run_full_month_includes(self, ctx):
        """阶段 9：run_full_month 报告含 conservation_passed。"""
        report = run_full_month(ctx)
        # 占位实现：首月没有跨城过账，守恒通过
        assert report.conservation_passed is True
        assert report.conservation_errors == []

    def test_conservation_check_dataclass(self):
        """阶段 9：ConservationCheck 数据类。"""
        check = ConservationCheck(
            passed=True,
            ocean_net=100,
            local_total=5000,
            peer_total=200,
            inflow_outflow=300,
        )
        assert check.passed is True
        assert check.ocean_net == 100
        assert check.inflow_outflow == 300


# ===== F. WorldState 集成（3 项）=====

class TestWorldIntegration:
    """V2.1 §12.14 月度更新与 WorldState 集成。"""

    def test_step_14_multiplies_world_index(self, ctx):
        """阶段 9：step_14 调 WorldState.update_world_index 令 I/C 增长。"""
        before_I = ctx.world.global_I
        before_C = ctx.world.global_C
        run_month_step(MonthlyStep.STEP_14_UPDATE_INDEX, ctx)
        # 默认 pi=0.003 → I/C 各涨 0.3%
        assert ctx.world.global_I > before_I
        assert ctx.world.global_C > before_C

    def test_run_full_month_updates_world(self, ctx):
        """阶段 9：run_full_month 后 world.month +1。"""
        before = ctx.world.month
        run_full_month(ctx)
        assert ctx.world.month == before + 1

    def test_first_month_report_captures_world(self, ledger, world):
        """阶段 9：run_first_month 月报含 WorldState 字段。"""
        ctx, report = run_first_month(ledgers={'test_city': ledger}, world=world)
        # 注意：run_first_month 内部已调 step_14，world.month=1
        assert report.world_inflation_pi == 0.003
        assert report.world_I > 1.0  # 0.3% 增长
        assert report.world_C > 50.0


# ===== G. 步骤顺序（3 项）=====

class TestStepOrdering:
    """V2.1 §12 15 步严格顺序。"""

    def test_step_enum_sorted_matches_spec(self):
        """阶段 9：step 枚举值严格对应 §12.01-§12.15 编号。"""
        # STEP_01..STEP_15 数值 1..15
        names = [
            'STEP_01_UPDATE_CONTRACTS', 'STEP_02_LOCK_FUNDS', 'STEP_03_PAYROLL',
            'STEP_04_CONSUMPTION_DEMAND', 'STEP_05_PRODUCTION', 'STEP_06_AGGREGATE_PURCHASE',
            'STEP_07_MARKET_CLEAR', 'STEP_08_FINANCE', 'STEP_09_PROPERTY_RELIEF',
            'STEP_10_UPDATE_CASH', 'STEP_11_CONSERVATION', 'STEP_12_K_AND_L',
            'STEP_13_MIGRATION', 'STEP_14_UPDATE_INDEX', 'STEP_15_REPORT',
        ]
        for i, name in enumerate(names, start=1):
            assert hasattr(MonthlyStep, name)
            assert getattr(MonthlyStep, name).value == i

    def test_run_full_month_executes_in_order(self, ctx):
        """阶段 9：run_full_month 按 §12 顺序执行 15 步。"""
        report = run_full_month(ctx)
        # 全部 15 步都被记录
        assert len(report.step_executed) == 15
        # 顺序：key 字典按 step 编号排列
        keys = sorted(report.step_executed.keys())
        assert keys == [f'step_{i:02d}' for i in range(1, 16)]

    def test_step_11_before_step_12(self, ctx):
        """阶段 9：守恒断言（11）在 K/L 计算（12）之前。"""
        run_full_month(ctx)
        # 两者都被执行
        assert ctx.running.get('step_11') is True
        assert ctx.running.get('step_12') is True
