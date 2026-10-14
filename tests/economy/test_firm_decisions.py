"""企业进退决策验收：设计稿 §9.1 的入驻门槛与 §4.7 的退出门槛。

这一组测试回答五个问题：
1. 入驻的四道门槛是否真的按设计稿生效（利润率、流动资金、历史窗口）；
2. 预测是否只用过去、绝不用未来；
3. 退出门槛是否足够保守（不会因一个月亏损就清盘）；
4. 扩张是否要求持续满负荷而非单月繁荣；
5. 每条决策是否都带玩家看得懂的理由。
"""
from fractions import Fraction

import pytest

from backend.core.errors import GameError
from backend.systems.firm_decisions import (ENTRY_MARGIN_MIN, EXIT_CASH_STREAK,
                                            EXIT_LOSS_STREAK, EXPANSION_UTILISATION_MIN,
                                            FirmDecision, FirmHistory,
                                            decide_entry, decide_operation,
                                            expected_margin)


def fed(months, utilisation=Fraction(8, 10), profit=1000, shortfall=False):
    """喂给企业若干个月历史。"""
    history = FirmHistory("test")
    for _ in range(months):
        history.record(utilisation, profit, shortfall)
    return history


# --- 入驻门槛（§9.1）----------------------------------------------------

def test_low_margin_blocks_entry():
    """利润率不达 6% 门槛就没人来开店。

    收入 10000、投入 5000、固定支出 4900 → 利润率 1%，明确低于门槛。
    """
    decision = decide_entry(fed(3), "retail", target_employees=10,
                            revenue=10000, fixed_cost=4900, input_cost=5000,
                            working_cash=100000)
    assert decision.action == FirmDecision.HOLD
    assert "门槛" in decision.reason


def test_margin_threshold_is_six_percent():
    """门槛常量本身要能算出正确百分比。"""
    assert expected_margin(10000, 4900, 5000) == Fraction(1, 100)
    assert expected_margin(10000, 4800, 5000) == Fraction(2, 100)


def test_thin_margin_blocks_entry():
    """刚好卡在门槛下方也要挡住——边界要严。"""
    # 收入 10000，投入 5000，固定 4800 → 利润率 2%
    decision = decide_entry(fed(3), "retail", 10, 10000, 4800, 5000, 100000)
    assert decision.action == FirmDecision.HOLD


def test_thick_margin_and_cash_allows_entry():
    decision = decide_entry(fed(3), "retail", 10, 10000, 1000, 1000, 100000)
    assert decision.action == FirmDecision.ENTER
    assert decision.target_employees == 10


def test_thin_liquidity_blocks_entry():
    """流动资金必须覆盖 1.5 个月固定支出。"""
    decision = decide_entry(fed(3), "retail", 10, 10000, 1000, 1000,
                            working_cash=1400)      # 1.5 个月需要 1500
    assert decision.action == FirmDecision.HOLD
    assert "流动资金" in decision.reason


def test_liquidity_boundary_allows_entry():
    decision = decide_entry(fed(3), "retail", 10, 10000, 1000, 1000, working_cash=1500)
    assert decision.action == FirmDecision.ENTER


def test_zero_revenue_never_claims_a_margin():
    decision = decide_entry(fed(3), "retail", 10, 0, 0, 0, 100000)
    assert decision.action == FirmDecision.HOLD
    assert expected_margin(0, 500, 500) == 0


def test_entry_rejects_impossible_headcount():
    with pytest.raises(GameError):
        decide_entry(fed(3), "retail", 0, 10000, 1000, 1000, 100000)


# --- 预测只用过去 -------------------------------------------------------

def test_forecast_never_peeks_into_the_future():
    """设计稿明令不允许预知未来，所以短期历史也只能用于入驻门槛的粗判。"""
    history = fed(1)
    decision = decide_operation(history, "retail", 10,
                                utilisation=Fraction(99, 100), profit=99999)
    # 只有 1 个月历史时，本月利用率不能单独把平均拉到扩张线以上
    assert decision.action != FirmDecision.ENTER


def test_sustained_full_load_triggers_expansion():
    """连续满负荷才扩张。"""
    decision = decide_operation(fed(3, utilisation=Fraction(95, 100)), "retail", 10,
                                utilisation=Fraction(95, 100), profit=5000)
    assert decision.action == FirmDecision.ENTER
    assert decision.target_employees == 20
    assert "扩张" in decision.reason


def test_one_good_month_does_not_trigger_expansion():
    """只有本月好、上月差，不足以扩张——这是设计稿的保守设定。"""
    history = FirmHistory("retail")
    history.record(Fraction(30, 100), 500)      # 上月很差
    history.record(Fraction(30, 100), 500)      # 上上月也很差
    decision = decide_operation(history, "retail", 10,
                                utilisation=Fraction(99, 100), profit=9999)
    assert decision.action != FirmDecision.ENTER


def test_underutilised_sector_contracts():
    decision = decide_operation(fed(3, utilisation=Fraction(20, 100), profit=100),
                                "retail", 10, utilisation=Fraction(20, 100), profit=100)
    assert decision.action == FirmDecision.CONTRACT
    assert decision.target_employees < 10


def test_middle_utilisation_holds_steady():
    decision = decide_operation(fed(3, utilisation=Fraction(70, 100)), "retail", 10,
                                utilisation=Fraction(70, 100), profit=500)
    assert decision.action == FirmDecision.HOLD
    assert decision.target_employees == 10


# --- 退出门槛（§4.7）----------------------------------------------------

def test_single_loss_month_never_closes_a_firm():
    """§4.7 明令「不在一个偶然亏损月立即清盘」。"""
    history = fed(2, profit=1000)
    decision = decide_operation(history, "retail", 10,
                                utilisation=Fraction(70, 100), profit=-500)
    assert decision.action != FirmDecision.EXIT


def test_three_months_without_cash_triggers_exit():
    """决策看的是本月之前已结算的 3 个月，所以要先有 3 个月前科。"""
    decision = decide_operation(fed(3, shortfall=True), "retail", 10,
                                utilisation=Fraction(70, 100), profit=100,
                                cash_shortfall=True)
    assert decision.action == FirmDecision.EXIT
    assert decision.target_employees == 0
    assert "退出" in decision.reason


def test_two_short_months_are_not_enough_to_exit():
    """只欠费两个月不该清盘——门槛是 3 个月。"""
    decision = decide_operation(fed(2, shortfall=True), "retail", 10,
                                utilisation=Fraction(70, 100), profit=100,
                                cash_shortfall=True)
    assert decision.action != FirmDecision.EXIT


def test_six_months_of_losses_triggers_exit():
    decision = decide_operation(fed(6, profit=-100), "retail", 10,
                                utilisation=Fraction(70, 100), profit=-100)
    assert decision.action == FirmDecision.EXIT


def test_five_loss_months_are_not_enough_to_exit():
    """连亏 5 个月还没到 6 个月的门槛，不该退出。"""
    decision = decide_operation(fed(5, profit=-100), "retail", 10,
                                utilisation=Fraction(70, 100), profit=-100)
    assert decision.action != FirmDecision.EXIT


def test_recovering_streak_resets_the_exit_countdown():
    """停发工资两个月后恢复，不该继续累计到退出。"""
    history = FirmHistory("retail")
    history.record(Fraction(70, 100), 100, True)
    history.record(Fraction(70, 100), 100, True)
    history.record(Fraction(70, 100), 100, False)     # 恢复
    decision = decide_operation(history, "retail", 10,
                                utilisation=Fraction(70, 100), profit=100)
    assert decision.action != FirmDecision.EXIT


def test_operation_rejects_negative_headcount():
    with pytest.raises(GameError):
        decide_operation(fed(3), "retail", -1, Fraction(1), 0)


# --- 历史窗口管理 -------------------------------------------------------

def test_history_window_is_bounded():
    """再长的历史也不能把内存撑爆。"""
    history = FirmHistory("retail")
    for _ in range(200):
        history.record(Fraction(70, 100), 100)
    assert len(history.pre_tax_profit) <= max(EXIT_LOSS_STREAK, EXIT_CASH_STREAK, 3)


def test_every_decision_explains_itself():
    """每条决策都必须有面向玩家的中文理由，不能只吐一个枚举值。"""
    cases = [
        decide_entry(fed(3), "retail", 10, 10000, 9000, 500, 100000),
        decide_entry(fed(3), "retail", 10, 10000, 1000, 1000, 10),
        decide_operation(fed(3, utilisation=Fraction(95, 100)), "retail", 10,
                         utilisation=Fraction(95, 100), profit=5000),
        decide_operation(fed(3, utilisation=Fraction(20, 100)), "retail", 10,
                         utilisation=Fraction(20, 100), profit=100),
        decide_operation(fed(3), "retail", 10, utilisation=Fraction(70, 100), profit=100),
    ]
    for decision in cases:
        assert decision.reason.strip()
        assert decision.sector_id == "retail"


def test_thresholds_match_the_specification():
    """门槛必须与设计稿一致，改动就要连同文档一起改。"""
    assert ENTRY_MARGIN_MIN == Fraction(6, 100)
    assert EXPANSION_UTILISATION_MIN == Fraction(85, 100)
    assert EXIT_CASH_STREAK == 3
    assert EXIT_LOSS_STREAK == 6