"""P00：月/季/年三层周期节律配置自洽。

本文件验证周期定义与原始设计稿一致，且季度、年度事件不会污染首月基准。
原始输入未提出季节变化，因此这里不测试任何季节系数。
"""
import pytest

from backend.config import CALENDAR, REFERENCE_CITY

CYCLE = CALENDAR


# --- 三层尺度 ---------------------------------------------------------

def test_time_scales_are_divisible_into_each_other():
    scales = CYCLE["time_scales"]
    assert scales["month"]["days"] == 30
    assert scales["month"]["months_per_scale"] == 1
    assert scales["quarter"]["months"] % scales["month"]["months_per_scale"] == 0
    assert scales["year"]["months"] % scales["quarter"]["months"] == 0
    assert scales["year"]["months"] == 12 and scales["quarter"]["months"] == 3
    assert scales["term"]["years"] == 4


def test_calendar_agrees_with_reference_city_config():
    calendar = REFERENCE_CITY["calendar"]
    assert calendar["days_per_month"] == CYCLE["time_scales"]["month"]["days"] == 30
    assert calendar["months_per_year"] == CYCLE["time_scales"]["year"]["months"] == 12


# --- 月度结算 ---------------------------------------------------------

def test_monthly_cycle_runs_exactly_one_settlement_on_the_last_day():
    monthly = CYCLE["monthly_cycle"]
    assert monthly["settlement_day"] == 30
    assert monthly["settlement_day"] == CYCLE["time_scales"]["month"]["days"], \
        "月结日必须与月末重合，不得在月内重复结算"


def test_monthly_events_are_sequenced_without_gaps():
    seqs = [event["seq"] for event in CYCLE["monthly_cycle"]["events"]]
    assert seqs == list(range(1, len(seqs) + 1)), "月度步骤必须从 1 连续编号，不能跳号或重复"


def test_final_monthly_step_is_commit():
    """最后一步必须是会计断言与提交，中间步骤不得自行提交。"""
    events = CYCLE["monthly_cycle"]["events"]
    assert events[-1]["id"] == "commit"
    assert events[-1]["cash"] is False, "提交步骤本身不产生现金流"
    assert sum(1 for e in events if e["id"] == "commit") == 1


def test_cash_bearing_steps_reference_the_settlement_layer():
    """产生现金的步骤必须归属于 M02 清算或明确的主体结算，不得凭空加钱。"""
    cash_steps = [e for e in CYCLE["monthly_cycle"]["events"] if e["cash"]]
    assert cash_steps, "月度循环必须有实际结算步骤"
    for event in cash_steps:
        assert event["module"], f"{event['id']} 未标注所属模块"
        assert "M02" in event["module"] or event["module"] in {"M05", "M06", "M09", "M10", "M11", "M14"}, \
            f"{event['id']} 标记为产生现金，却未经过清算层或明确的主体结算模块"


def test_monthly_evaluations_respect_documented_limits():
    evals = CYCLE["monthly_cycle"]["monthly_evaluations"]
    assert evals["price_adjust"]["limit"] == 0.05
    assert evals["wage_adjust"]["limit"] == 0.03
    assert evals["rent_adjust"]["limit"] == 0.03
    assert evals["exit_check"]["unpayable_months"] == 3
    assert evals["exit_check"]["loss_months"] == 6


# --- 季度 -------------------------------------------------------------

def test_quarter_policy_cooldown_spans_two_quarters():
    cooldown = CYCLE["quarterly_cycle"]["policy_cooldown"]
    assert cooldown["months"] == REFERENCE_CITY["fiscal"]["policy_cooldown_months"] == 6
    assert cooldown["months"] == CYCLE["time_scales"]["quarter"]["months"] * cooldown["quarters"] == 6
    assert cooldown["quarters"] == 2, "季度开会不等于可任意改政策，受 6 月冷却限制"


def test_policy_limits_match_fiscal_config():
    limits = {item["policy"]: item for item in CYCLE["quarterly_cycle"]["policy_cooldown"]["limits"]}
    fiscal = REFERENCE_CITY["fiscal"]

    assert limits["所得税"]["range"] == fiscal["income_tax_rate_range"]
    assert limits["所得税"]["step_pp"] == fiscal["income_tax_step_pp"]
    assert limits["正利润税"]["range"] == fiscal["profit_tax_rate_range"]
    assert limits["正利润税"]["step_pp"] == fiscal["profit_tax_step_pp"]
    assert limits["房产税月率"]["range"] == fiscal["property_tax_rate_range"]


def test_religion_policy_tier_range_matches_reference():
    limits = {item["policy"]: item for item in CYCLE["quarterly_cycle"]["policy_cooldown"]["limits"]}
    tiers = [t["tier"] for t in REFERENCE_CITY["religion"]["policy_tiers"]]
    assert limits["宗教政策档位"]["range"] == [min(tiers), max(tiers)] == [-2, 2]


# --- 年度 -------------------------------------------------------------

def test_yearly_borrowing_cap_needs_a_full_year_of_history():
    event = next(e for e in CYCLE["yearly_cycle"]["events"] if e["id"] == "borrowing_cap")
    assert "12" in event["formula"]
    assert event["formula"] == "过去 12 个月平均月税收 × 6"
    assert CYCLE["yearly_cycle"]["months"] == 12, "年度周期长度必须等于借款上限所需的回溯窗口"
    assert REFERENCE_CITY["fiscal"]["central_grant"] > 0


def test_yearly_events_are_distinct_and_named():
    ids = [event["id"] for event in CYCLE["yearly_cycle"]["events"]]
    assert len(ids) == len(set(ids))
    for event in CYCLE["yearly_cycle"]["events"]:
        assert event["name"] and event["detail"], event["id"]
        assert event["module"], event["id"]


def test_long_cycles_align_with_scenario_length():
    terms = {item["id"]: item for item in CYCLE["long_cycles"]}
    assert terms["final_review"]["years"] == 20, "二十年评价见 M19"
    assert terms["term_review"]["years"] == 4, "约四年任期考核见 M18"
    assert terms["final_review"]["years"] % terms["term_review"]["years"] == 0


# --- 首月基准保护 ------------------------------------------------------

def test_first_month_triggers_no_quarter_or_yearly_event():
    """第 1 月只跑月度结算，年度与季度事件不得改写首月现金流。"""
    guards = CYCLE["reference_month_guard"]["rules"]
    assert any("第 1 月不触发" in rule for rule in guards)

    monthly = CYCLE["monthly_cycle"]["events"]
    assert all(not event["id"].startswith(("annual", "npc", "borrowing", "debt", "council", "policy_window"))
               for event in monthly), "季度与年度事件不得混入月度结算步骤"


def test_guard_declares_single_monthly_settlement_and_daily_clock_scope():
    rules = CYCLE["reference_month_guard"]["rules"]
    assert any("每月第 30 日" in rule or "月内不产生第二次" in rule for rule in rules)
    assert any("日时钟只推进施工" in rule for rule in rules)


def test_no_seasonal_system_was_invented():
    """原始输入未提出季节变化，配置里就不该凭空出现季节字段。"""
    text = str(CYCLE)
    for word in ("spring", "summer", "autumn", "winter", "season"):
        assert word not in text.lower(), f"配置出现了未经原始输入确认的季节字段：{word}"
    assert "季节" in CYCLE["note"], "须显式记录未采用季节的理由"