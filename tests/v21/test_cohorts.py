"""V2.1 阶段 2 居民群体验收测试（2026-10-15）。

【验收清单】（V2.1 §1.4 + §14.2 + §6.4）
- [x] h=0..7 八群体完整（NONE + 7 行业归属）
- [x] 八群体 P_h 严格匹配 V2.1 §14.2：
  NONE=70, AGR=480, IND=1120, SRV=1050, CHU=40, BNK=45, MAG=90, GOV=105
- [x] 八群体 E_h 严格匹配 V2.1 §14.2：
  NONE=0, AGR=240, IND=610, SRV=530, CHU=20, BNK=20, MAG=30, GOV=50
- [x] sum P_h = 3,000（V2.1 §14.1）
- [x] sum E_h = 1,500（V2.1 §14.1）
- [x] sum P_h - sum E_h = 1,500（非就业）
- [x] 严禁 P_h == E_h：各群体 P_h >= E_h（群体中可有失业居民）
- [x] h=0 群体 P=70 不等于失业总量 150（V2.1 §14.2 明确"不要求"）
- [x] h=1..7 与 IndustryId 1:1 对应（CohortIndustry.industry_id）
- [x] PIT 8% 通用（PERSONAL_INCOME_TAX_RATE = 0.08）
- [x] AGR 工资免 PIT（V2.1 §6.4）
- [x] 其它行业工资不免 PIT
- [x] 股息/财产/其它收入不免 PIT
- [x] 期初群体储蓄 300,000 CR 按权重分配（最大余数法，逐群体非负）
- [x] JSON 配置加载并字段一致
"""
import pytest

from backend.systems.v21.cohorts import (
    CohortIndustry, CohortDefinition, INDUSTRIAL_CITY_COHORT_DEFINITIONS,
    TOTAL_POPULATION_N, TOTAL_EMPLOYMENT_E, TOTAL_LABOR_FORCE_F, TOTAL_UNEMPLOYED_U,
    TOTAL_NON_LABOR_FORCE, TOTAL_INITIAL_SAVINGS_CR,
    PERSONAL_INCOME_TAX_RATE, AGRICULTURAL_WAGE_PIT_EXEMPT,
    total_population, total_employment, total_non_employed,
    is_pit_exempt, compute_pit_cents, compute_pit_cr,
    split_initial_savings,
)
from backend.systems.v21.industries import IndustryId
from backend.systems.v21.loader import load_cohorts_config, ECONOMY_V2_DIR, read_json


# ===== CohortIndustry 枚举 =====

def test_cohort_industry_count():
    """阶段 2：8 个群体（h=0..7），V2.1 §1.4。"""
    assert len(CohortIndustry) == 8


def test_cohort_industry_values():
    """阶段 2：群体值 0..7 严格匹配。"""
    assert [c.value for c in CohortIndustry] == [0, 1, 2, 3, 4, 5, 6, 7]


def test_cohort_industry_industry_id_mapping():
    """阶段 2：h=1..7 与 IndustryId 1:1 对应。"""
    assert CohortIndustry.NONE.industry_id is None
    assert CohortIndustry.AGR.industry_id == IndustryId.AGR
    assert CohortIndustry.IND.industry_id == IndustryId.IND
    assert CohortIndustry.SRV.industry_id == IndustryId.SRV
    assert CohortIndustry.CHU.industry_id == IndustryId.CHU
    assert CohortIndustry.BNK.industry_id == IndustryId.BNK
    assert CohortIndustry.MAG.industry_id == IndustryId.MAG
    assert CohortIndustry.GOV.industry_id == IndustryId.GOV


# ===== 八群体配置严格匹配 V2.1 §14.2 =====

@pytest.mark.parametrize("cohort_id, P_h, E_h", [
    (CohortIndustry.NONE, 70, 0),
    (CohortIndustry.AGR, 480, 240),
    (CohortIndustry.IND, 1_120, 610),
    (CohortIndustry.SRV, 1_050, 530),
    (CohortIndustry.CHU, 40, 20),
    (CohortIndustry.BNK, 45, 20),
    (CohortIndustry.MAG, 90, 30),
    (CohortIndustry.GOV, 105, 50),
])
def test_industrial_city_cohort_definitions(cohort_id, P_h, E_h):
    """阶段 2：八群体 P_h、E_h 严格匹配 V2.1 §14.2。"""
    d = INDUSTRIAL_CITY_COHORT_DEFINITIONS[cohort_id]
    assert d.population_P_h == P_h
    assert d.employees_E_h == E_h


def test_total_population_matches_v21_spec():
    """阶段 2：sum P_h = 3,000（V2.1 §14.1）。"""
    assert total_population() == 3_000
    assert TOTAL_POPULATION_N == 3_000


def test_total_employment_matches_v21_spec():
    """阶段 2：sum E_h = 1,500（V2.1 §14.1）。"""
    assert total_employment() == 1_500
    assert TOTAL_EMPLOYMENT_E == 1_500


def test_total_non_employed():
    """阶段 2：sum (P_h - E_h) = N - E = 1,500（非就业）。"""
    assert total_non_employed() == 1_500


def test_total_unemployed_150():
    """阶段 2：F - E = 1,650 - 1,500 = 150（失业总量）。"""
    assert TOTAL_UNEMPLOYED_U == 150


def test_total_labor_force_1650():
    """阶段 2：F = floor(0.55 * 3000) = 1,650。"""
    assert TOTAL_LABOR_FORCE_F == 1_650


# ===== 关键约束：P_h >= E_h =====

def test_no_P_h_equals_E_h():
    """阶段 2：严禁 P_h == E_h（V2.1 §1.4）。"""
    # NONE 群体 P=70 E=0 也不等于（P != E）—— 满足"严禁等于"约束
    for cid, d in INDUSTRIAL_CITY_COHORT_DEFINITIONS.items():
        assert d.population_P_h != d.employees_E_h, \
            f"群体 {cid.name} P_h == E_h 违反 V2.1 §1.4 严禁等于约束"


def test_P_h_ge_E_h():
    """阶段 2：所有群体 P_h >= E_h（失业/非就业包含在 P_h 中）。"""
    for cid, d in INDUSTRIAL_CITY_COHORT_DEFINITIONS.items():
        assert d.population_P_h >= d.employees_E_h, \
            f"群体 {cid.name} P_h={d.population_P_h} < E_h={d.employees_E_h}"


def test_h0_not_equal_to_unemployment_150():
    """阶段 2：h=0 群体 70 人 ≠ 失业总量 150（V2.1 §14.2 明确"不要求"）。"""
    h0 = INDUSTRIAL_CITY_COHORT_DEFINITIONS[CohortIndustry.NONE]
    assert h0.population_P_h == 70
    assert h0.population_P_h != TOTAL_UNEMPLOYED_U


def test_agricultural_cohort_has_unemployed():
    """阶段 2：AGR 群体 480 人有 240 非就业（含失业）。"""
    agr = INDUSTRIAL_CITY_COHORT_DEFINITIONS[CohortIndustry.AGR]
    assert agr.non_employed == 240
    assert agr.has_unemployed is True


# ===== CohortDefinition 数据类属性 =====

def test_cohort_definition_is_frozen():
    """阶段 2：CohortDefinition 是 frozen dataclass。"""
    d = INDUSTRIAL_CITY_COHORT_DEFINITIONS[CohortIndustry.IND]
    with pytest.raises(Exception):  # FrozenInstanceError
        d.population_P_h = 999  # type: ignore[misc]


def test_cohort_definition_has_required_fields():
    """阶段 2：CohortDefinition 字段完整。"""
    d = INDUSTRIAL_CITY_COHORT_DEFINITIONS[CohortIndustry.IND]
    expected_fields = {'cohort_id', 'name_cn', 'industry_id', 'population_P_h', 'employees_E_h', 'notes'}
    actual_fields = set(d.__dataclass_fields__.keys())  # type: ignore[attr-defined]
    assert expected_fields.issubset(actual_fields)


# ===== PIT 个人所得税（V2.1 §6.4） =====

def test_personal_income_tax_rate_is_8_percent():
    """阶段 2：通用 PIT 税率 = 8%。"""
    assert PERSONAL_INCOME_TAX_RATE == 0.08


def test_agricultural_wage_pit_exempt():
    """阶段 2：农业（AGR）工资免 PIT。"""
    assert AGRICULTURAL_WAGE_PIT_EXEMPT is True


def test_pit_exempt_agr_wage():
    """阶段 2：AGR 工资免 PIT。"""
    assert is_pit_exempt(IndustryId.AGR, 'wage') is True


def test_pit_exempt_others_wage_not_exempt():
    """阶段 2：其它行业（IND/SRV/CHU/BNK/MAG/GOV）工资不免 PIT。"""
    for iid in [IndustryId.IND, IndustryId.SRV, IndustryId.CHU, IndustryId.BNK, IndustryId.MAG, IndustryId.GOV]:
        assert is_pit_exempt(iid, 'wage') is False, f"{iid} 工资不应免 PIT"


def test_pit_agr_dividend_not_exempt():
    """阶段 2：AGR 股息不免 PIT（仅工资免）。"""
    assert is_pit_exempt(IndustryId.AGR, 'dividend') is False


def test_pit_agr_property_not_exempt():
    """阶段 2：AGR 财产收入不免 PIT。"""
    assert is_pit_exempt(IndustryId.AGR, 'property') is False


def test_pit_agr_other_not_exempt():
    """阶段 2：AGR 其它收入不免 PIT。"""
    assert is_pit_exempt(IndustryId.AGR, 'other') is False


def test_pit_industry_none_wage_not_exempt():
    """阶段 2：None industry（h=0 居民）工资不免 PIT。"""
    assert is_pit_exempt(None, 'wage') is False


def test_compute_pit_agr_wage_zero():
    """阶段 2：AGR 工资 PIT = 0（免）。"""
    assert compute_pit_cents(taxable_income_cents=100_000, industry_id=IndustryId.AGR, income_kind='wage') == 0


def test_compute_pit_industry_wage_8_percent():
    """阶段 2：IND 工资 PIT = 8%。"""
    # 100 CR = 10,000 分，8% = 800 分 = 8 CR
    assert compute_pit_cents(taxable_income_cents=10_000, industry_id=IndustryId.IND, income_kind='wage') == 800


def test_compute_pit_dividend_8_percent():
    """阶段 2：股息 PIT = 8%（任何行业）。"""
    assert compute_pit_cents(taxable_income_cents=20_000, industry_id=IndustryId.AGR, income_kind='dividend') == 1_600


def test_compute_pit_negative_income_zero():
    """阶段 2：负数收入 PIT = 0（亏损不退税）。"""
    assert compute_pit_cents(taxable_income_cents=-1_000, industry_id=IndustryId.IND, income_kind='wage') == 0


def test_compute_pit_zero_income_zero():
    """阶段 2：零收入 PIT = 0。"""
    assert compute_pit_cents(taxable_income_cents=0, industry_id=IndustryId.IND, income_kind='wage') == 0


def test_compute_pit_cr_basic():
    """阶段 2：compute_pit_cr 整数 CR 接口。"""
    # 100 CR IND 工资 PIT = 8 CR
    assert compute_pit_cr(taxable_income_cr=100, industry_id=IndustryId.IND, income_kind='wage') == 8
    # 100 CR AGR 工资 PIT = 0 CR（免）
    assert compute_pit_cr(taxable_income_cr=100, industry_id=IndustryId.AGR, income_kind='wage') == 0


def test_compute_pit_industrial_city_total_wage_pit():
    """阶段 2：工业新城首月工资 PIT（AGR 工资免税；其它行业 8%）。

    各行业工资总额 = 14,400 + 54,900 + 53,000 + 1,500 + 2,000 + 2,700 + 3,750 = 132,250 CR
    AGR 工资 = 14,400 CR（免 PIT = 0）
    其它 6 行业 = 132,250 - 14,400 = 117,850 CR × 8% = 9,428 CR = 942,800 分

    注：V2.1 §14.7 PIT = 12,388 CR = 1,238,800 分（含股息 16,000 + 净房产 21,000）
    本测试只验证工资 PIT 部分。
    """
    # 工资 PIT = (132,250 - 14,400) * 0.08 = 117,850 * 0.08 = 9,428 CR
    wage_bill = {
        IndustryId.AGR: 14_400,
        IndustryId.IND: 54_900,
        IndustryId.SRV: 53_000,
        IndustryId.CHU: 1_500,
        IndustryId.BNK: 2_000,
        IndustryId.MAG: 2_700,
        IndustryId.GOV: 3_750,
    }
    total_pit_cr = sum(
        compute_pit_cr(taxable_income_cr=w, industry_id=iid, income_kind='wage')
        for iid, w in wage_bill.items()
    )
    # AGR 免 PIT：0；其它 6 行业：(117,850) × 0.08 = 9,428
    assert total_pit_cr == 9_428


# ===== 期初群体储蓄分配（V2.1 §14.2） =====

def test_total_initial_savings_300000():
    """阶段 2：期初群体储蓄合计 = 300,000 CR。"""
    assert TOTAL_INITIAL_SAVINGS_CR == 300_000


def test_split_initial_savings_basic():
    """阶段 2：split_initial_savings 按权重分配。"""
    weights = {
        CohortIndustry.AGR: 1.0,
        CohortIndustry.IND: 1.0,
    }
    result = split_initial_savings(weights)
    assert sum(result.values()) == 300_000
    # 两个等权重：150,000 / 150,000
    assert result[CohortIndustry.AGR] == 150_000
    assert result[CohortIndustry.IND] == 150_000


def test_split_initial_savings_uneven_weights():
    """阶段 2：不等权重分配（最大余数法）。"""
    weights = {
        CohortIndustry.AGR: 1.0,
        CohortIndustry.IND: 2.0,
    }
    result = split_initial_savings(weights)
    # sum = 300,000；3 等分：100,000 / 200,000
    assert sum(result.values()) == 300_000
    assert result[CohortIndustry.AGR] == 100_000
    assert result[CohortIndustry.IND] == 200_000


def test_split_initial_savings_8_cohorts():
    """阶段 2：八群体按 P_h 权重分配（首月储蓄建议）。"""
    weights = {cid: d.population_P_h for cid, d in INDUSTRIAL_CITY_COHORT_DEFINITIONS.items()}
    result = split_initial_savings(weights)
    assert sum(result.values()) == 300_000
    # 每人 100 CR
    for cid, savings in result.items():
        d = INDUSTRIAL_CITY_COHORT_DEFINITIONS[cid]
        expected = d.population_P_h * 100
        assert savings == expected, f"群体 {cid.name} 期望 {expected} 实际 {savings}"


def test_split_initial_savings_non_negative():
    """阶段 2：所有群体储蓄非负（V2.1 §14.2 强制）。"""
    weights = {cid: d.population_P_h for cid, d in INDUSTRIAL_CITY_COHORT_DEFINITIONS.items()}
    result = split_initial_savings(weights)
    for cid, v in result.items():
        assert v >= 0


def test_split_initial_savings_zero_weight_raises():
    """阶段 2：权重之和为 0 抛 ValueError。"""
    with pytest.raises(ValueError):
        split_initial_savings({CohortIndustry.AGR: 0, CohortIndustry.IND: 0})


# ===== JSON 加载器 =====

def test_cohorts_json_file_exists():
    """阶段 2：data/economy_v2/cohorts.json 存在。"""
    assert (ECONOMY_V2_DIR / 'cohorts.json').is_file()


def test_load_cohorts_config_basic():
    """阶段 2：load_cohorts_config 返回 _meta + demographics + pit_rules + initial_savings + cohorts。"""
    config = load_cohorts_config()
    assert config['_meta']['version'] == '2.1.0'
    assert config['_meta']['archetype'] == 'industrial_city'
    assert 'demographics' in config
    assert 'pit_rules' in config
    assert 'initial_savings' in config
    assert 'cohorts' in config


def test_load_cohorts_config_demographics():
    """阶段 2：demographics 字段匹配 V2.1 §14.1。"""
    config = load_cohorts_config()
    d = config['demographics']
    assert d['total_population_N'] == 3000
    assert d['total_labor_force_F'] == 1650
    assert d['total_employment_E'] == 1500
    assert d['total_unemployed_U'] == 150


def test_load_cohorts_config_pit_rules():
    """阶段 2：pit_rules 字段匹配 V2.1 §6.4。"""
    config = load_cohorts_config()
    p = config['pit_rules']
    assert p['personal_income_tax_rate'] == 0.08
    assert p['agricultural_wage_pit_exempt'] is True


def test_load_cohorts_config_cohorts_count():
    """阶段 2：cohorts 含 8 群体。"""
    config = load_cohorts_config()
    assert len(config['cohorts']) == 8


def test_load_cohorts_config_matches_python_defs():
    """阶段 2：JSON 全部 8 群体字段与 Python INDUSTRIAL_CITY_COHORT_DEFINITIONS 一致。"""
    config = load_cohorts_config()
    for cid, py_def in INDUSTRIAL_CITY_COHORT_DEFINITIONS.items():
        json_def = config['cohorts'][cid.name]
        assert json_def['name_cn'] == py_def.name_cn
        assert json_def['industry_id'] == (py_def.industry_id.value if py_def.industry_id else None)
        assert json_def['population_P_h'] == py_def.population_P_h
        assert json_def['employees_E_h'] == py_def.employees_E_h


def test_load_cohorts_config_bom_compatible():
    """阶段 2：read_json 兼容 UTF-8 BOM。"""
    raw = read_json(ECONOMY_V2_DIR / 'cohorts.json')
    assert raw['_meta']['version'] == '2.1.0'


def test_load_cohorts_config_missing_file():
    """阶段 2：缺失文件抛 FileNotFoundError。"""
    import pathlib
    with pytest.raises(FileNotFoundError):
        load_cohorts_config(path=pathlib.Path('/nonexistent/cohorts.json'))


# ===== v21 包整体可被 import =====

def test_v21_package_imports_cohorts():
    """阶段 2：v21 包整体可被 import，导出阶段 0+1+2 全部符号。"""
    import backend.systems.v21 as v21
    assert v21.CohortIndustry is CohortIndustry
    assert v21.CohortDefinition is CohortDefinition
    assert v21.INDUSTRIAL_CITY_COHORT_DEFINITIONS is INDUSTRIAL_CITY_COHORT_DEFINITIONS
    assert v21.PERSONAL_INCOME_TAX_RATE == 0.08
    assert v21.AGRICULTURAL_WAGE_PIT_EXEMPT is True
    assert callable(v21.is_pit_exempt)
    assert callable(v21.compute_pit_cents)
    assert callable(v21.compute_pit_cr)
    assert callable(v21.split_initial_savings)
    assert callable(v21.total_population)
    assert callable(v21.total_employment)
    assert callable(v21.total_non_employed)
    assert callable(v21.load_cohorts_config)
