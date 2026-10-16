"""稳定性结论必须来自正式运行入口逐月执行。"""
from backend.systems.v21.calibrator import CalibrationReport, step_09_stability
from backend.systems.v21.industry_economy import IndustryEconomy
from backend.systems.v21.runtime_validation import validate_runtime


def test_runtime_validation_really_advances_every_month(monkeypatch):
    calls = []
    original = IndustryEconomy.advance

    def counted(city):
        result = original(city)
        calls.append(city.month)
        return result

    monkeypatch.setattr(IndustryEconomy, 'advance', counted)
    result = validate_runtime(12)
    assert calls == list(range(1, 13))
    assert result['months_executed'] == 12
    assert all(r['cash_residual_cents'] == 0 for r in result['history'])


def test_unmapped_city_cannot_claim_runtime_stability():
    report = CalibrationReport(archetype='agricultural_town', seed=0,
                               N=2400, K=2800, L=75, unemployment_rate=.09)
    assert step_09_stability(report, 120) is False
    assert report.runtime_evidence['120']['months_executed'] == 0
