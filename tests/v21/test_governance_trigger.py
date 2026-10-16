"""M24 经济触发器单元测试。"""
from backend.systems.v21.governance_trigger import (
    EconomicTrigger,
    TriggerConfig,
    load_governance_triggers,
)


def _report(unemployed=100, gov_cash=10_000_00, arrears=0, population=3000, labor=2400,
            month=1):
    return {
        'month': month, 'unemployed': unemployed, 'labor_force': labor,
        'government_cash_cents': gov_cash, 'wage_arrears_cents': arrears,
        'total_population': population,
    }


def test_load_triggers_default():
    cfg = load_governance_triggers()
    assert 'unemployment_crisis' in cfg
    assert 'government_bankruptcy' in cfg
    assert cfg['government_bankruptcy'].severity == 'grace_period'
    assert cfg['unemployment_crisis'].grace_months == 3
    assert cfg['population_collapse'].severity == 'immediate'
    assert cfg['civil_unrest_multi'].type == 'multi'


def test_normal_report_not_trigger():
    trig = EconomicTrigger()
    assert trig.evaluate(_report()) is False
    assert trig.is_game_over is False
    assert trig.reasons == []


def test_unemployment_crisis_grace_period():
    trig = EconomicTrigger()
    for _ in range(2):
        assert trig.evaluate(_report(unemployed=720, labor=2400)) is False  # 30%
    assert trig.evaluate(_report(unemployed=720, labor=2400)) is True  # 第 3 月触发
    assert any('失业率危机' in r for r in trig.reasons)


def test_unemployment_recover_resets():
    trig = EconomicTrigger()
    trig.evaluate(_report(unemployed=720))  # streak 1
    trig.evaluate(_report(unemployed=720))  # streak 2
    assert trig.streak['unemployment_crisis'] == 2
    trig.evaluate(_report(unemployed=10))  # 恢复
    assert trig.streak['unemployment_crisis'] == 0
    assert trig.evaluate(_report(unemployed=10)) is False


def test_government_bankruptcy_grace_3():
    trig = EconomicTrigger()
    assert trig.evaluate(_report(gov_cash=-2_000_00)) is False  # grace 1/3
    assert trig.evaluate(_report(gov_cash=-2_000_00)) is False  # grace 2/3
    assert trig.evaluate(_report(gov_cash=-2_000_00)) is True   # grace 3/3 → game over
    assert any('政府现金破产' in r for r in trig.reasons)


def test_wage_arrears_runaway_grace_2():
    trig = EconomicTrigger()
    assert trig.evaluate(_report(arrears=10_000_00)) is False  # grace 1/2
    assert trig.evaluate(_report(arrears=10_000_00)) is True   # grace 2/2
    assert any('累计欠薪' in r for r in trig.reasons)


def test_population_collapse_immediate():
    trig = EconomicTrigger()
    trig.evaluate(_report(population=3000))  # 基准月
    assert trig.evaluate(_report(population=2400)) is True  # -20% 立即触发
    assert any('人口锐减' in r for r in trig.reasons)


def test_civil_unrest_multi_immediate():
    trig = EconomicTrigger()
    # 失业（≥30%）+ 欠薪（≥50000 CR）同月 breach → multi immediate
    assert trig.evaluate(_report(unemployed=720, arrears=10_000_00)) is True
    reasons = trig.reasons
    assert any('失业' in r for r in reasons)
    assert any('欠薪' in r for r in reasons)


def test_to_dict_from_dict_roundtrip():
    trig = EconomicTrigger()
    trig.evaluate(_report(gov_cash=-2_000_00))  # grace 1/3
    trig.evaluate(_report(gov_cash=-2_000_00))  # grace 2/3
    trig.evaluate(_report(gov_cash=-2_000_00))  # grace 3/3 → game over
    data = trig.to_dict()
    assert data['is_game_over'] is True
    assert data['reasons']
    trig2 = EconomicTrigger.from_dict(data)
    assert trig2.is_game_over is True
    assert trig2.reasons == trig.reasons


def test_evaluate_after_game_over_returns_true():
    trig = EconomicTrigger()
    trig.evaluate(_report(gov_cash=-2_000_00))
    trig.evaluate(_report(gov_cash=-2_000_00))
    trig.evaluate(_report(gov_cash=-2_000_00))
    assert trig.is_game_over
    # 再 evaluate 仍然返回 True
    assert trig.evaluate(_report(gov_cash=10_000_00)) is True


def test_reasons_accumulate_across_triggers():
    trig = EconomicTrigger()
    # 第 1 月：失业 breach（streak 1/3）+ 破产 breach（streak 1/2）
    trig.evaluate(_report(unemployed=720, gov_cash=-2_000_00))
    assert not trig.is_game_over
    # 第 2 月：两个 streak 都 +1，都还不到阈值
    assert not trig.evaluate(_report(unemployed=720, gov_cash=-2_000_00))
    # 第 3 月：破产 grace 达 2/2 触发，失业还差 1 月
    assert trig.evaluate(_report(unemployed=720, gov_cash=-2_000_00)) is True
    assert any('政府现金破产' in r for r in trig.reasons)


def test_missing_metric_zeroes_streak():
    trig = EconomicTrigger()
    trig.evaluate(_report(unemployed=720))
    assert trig.streak['unemployment_crisis'] == 1
    # 报告里没有 unemployed 字段 → streak 重置为 0
    trig.evaluate({'month': 2, 'labor_force': 2400})
    assert trig.streak['unemployment_crisis'] == 0


def test_comparison_operators():
    assert EconomicTrigger._compare(5, '>=', 5) is True
    assert EconomicTrigger._compare(5, '>=', 6) is False
    assert EconomicTrigger._compare(5, '<=', 5) is True
    assert EconomicTrigger._compare(5, '<=', 4) is False
    assert EconomicTrigger._compare(5, '>', 4) is True
    assert EconomicTrigger._compare(5, '<', 6) is True
    assert EconomicTrigger._compare(5, '==', 5) is True