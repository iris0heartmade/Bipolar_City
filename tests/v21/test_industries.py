"""V2.1 阶段 1 行业体系验收测试（2026-10-15）。

【验收清单】（V2.1 §2.1）
- [x] 七行业 ID 完整：AGR/IND/SRV/CHU/BNK/MAG/GOV
- [x] 工资级 A=100/B=90/C=75/D=60 CR/在职人/月
- [x] 税级 A=15/B=12/C=8/无=0% 正利润税率
- [x] 行业岗位上限匹配 V2.1 §2.1：
  AGR=300 / IND=1500 / SRV=600 / CHU=100 / BNK=100 / MAG=400 / GOV=50
- [x] 行业初始就业匹配 V2.1 §2.1 工业新城基准：
  AGR=240 / IND=610 / SRV=530 / CHU=20 / BNK=20 / MAG=30 / GOV=50
- [x] INDUSTRY_DEFINITIONS 全部 7 行业有定义
- [x] 工资总额（W_total）= 132,250 CR（V2.1 §2.2）
- [x] monthly_wage_bill_cents 整数分计算
- [x] JSON 配置加载并字段一致
- [x] GOV 行业税率 0%（对自身征税无意义）
"""
import json
from pathlib import Path

import pytest

from backend.systems.v21.industries import (
    WageTier, TaxTier, IndustryId, IndustryDefinition, INDUSTRY_DEFINITIONS,
    WAGE_TIER_CR, TAX_TIER_RATE,
    wage_cr_per_worker, tax_rate, monthly_wage_bill_cents,
)
from backend.systems.v21.loader import (
    load_industries_config, ECONOMY_V2_DIR, read_json,
)


# ===== WageTier 工资级 =====

def test_wage_tier_cr_mapping():
    """阶段 1：WAGE_TIER_CR 严格匹配 V2.1 §2.2。"""
    assert WAGE_TIER_CR[WageTier.A] == 100
    assert WAGE_TIER_CR[WageTier.B] == 90
    assert WAGE_TIER_CR[WageTier.C] == 75
    assert WAGE_TIER_CR[WageTier.D] == 60


def test_wage_tier_count():
    """阶段 1：4 个工资级（A/B/C/D），符合 V2.1 §2.2。"""
    assert len(WageTier) == 4
    assert {t.value for t in WageTier} == {'A', 'B', 'C', 'D'}


# ===== TaxTier 税级 =====

def test_tax_tier_rate_mapping():
    """阶段 1：TAX_TIER_RATE 严格匹配 V2.1 §2.3。"""
    assert TAX_TIER_RATE[TaxTier.A] == 0.15
    assert TAX_TIER_RATE[TaxTier.B] == 0.12
    assert TAX_TIER_RATE[TaxTier.C] == 0.08
    assert TAX_TIER_RATE[TaxTier.NONE] == 0.0


def test_tax_tier_count():
    """阶段 1：4 个税级（A/B/C/无），符合 V2.1 §2.3。"""
    assert len(TaxTier) == 4
    assert {t.value for t in TaxTier} == {'A', 'B', 'C', 'none'}


# ===== IndustryId 行业 ID =====

def test_industry_id_count():
    """阶段 1：7 个行业（V2.1 §2.1）。"""
    assert len(IndustryId) == 7


def test_industry_id_values():
    """阶段 1：行业 ID 集合精确匹配。"""
    assert {i.value for i in IndustryId} == {'AGR', 'IND', 'SRV', 'CHU', 'BNK', 'MAG', 'GOV'}


# ===== INDUSTRY_DEFINITIONS 行业定义 =====

def test_industry_definitions_count():
    """阶段 1：INDUSTRY_DEFINITIONS 含 7 行业。"""
    assert len(INDUSTRY_DEFINITIONS) == 7
    for iid in IndustryId:
        assert iid in INDUSTRY_DEFINITIONS


def test_industry_definitions_agr():
    """阶段 1：AGR 农业 — 300岗位 D工资 无税 240初始。"""
    d = INDUSTRY_DEFINITIONS[IndustryId.AGR]
    assert d.name_cn == '农业'
    assert d.job_capacity_max == 300
    assert d.wage_tier == WageTier.D
    assert d.wage_cr == 60
    assert d.tax_tier == TaxTier.NONE
    assert d.tax_rate == 0.0
    assert d.initial_employees_industrial_city == 240


def test_industry_definitions_ind():
    """阶段 1：IND 工业 — 1500岗位 B工资 B税 610初始。"""
    d = INDUSTRY_DEFINITIONS[IndustryId.IND]
    assert d.name_cn == '工业'
    assert d.job_capacity_max == 1500
    assert d.wage_tier == WageTier.B
    assert d.wage_cr == 90
    assert d.tax_tier == TaxTier.B
    assert d.tax_rate == 0.12
    assert d.initial_employees_industrial_city == 610


def test_industry_definitions_srv():
    """阶段 1：SRV 服务业 — 600岗位 A工资 A税 530初始。"""
    d = INDUSTRY_DEFINITIONS[IndustryId.SRV]
    assert d.name_cn == '服务业/商业'
    assert d.job_capacity_max == 600
    assert d.wage_tier == WageTier.A
    assert d.wage_cr == 100
    assert d.tax_tier == TaxTier.A
    assert d.tax_rate == 0.15
    assert d.initial_employees_industrial_city == 530


def test_industry_definitions_chu():
    """阶段 1：CHU 教会 — 100岗位 C工资 无税 20初始。"""
    d = INDUSTRY_DEFINITIONS[IndustryId.CHU]
    assert d.name_cn == '教会'
    assert d.job_capacity_max == 100
    assert d.wage_tier == WageTier.C
    assert d.wage_cr == 75
    assert d.tax_tier == TaxTier.NONE
    assert d.tax_rate == 0.0
    assert d.initial_employees_industrial_city == 20


def test_industry_definitions_bnk():
    """阶段 1：BNK 银行 — 100岗位 A工资 B税 20初始（V1.0 缺失行业）。"""
    d = INDUSTRY_DEFINITIONS[IndustryId.BNK]
    assert d.name_cn == '银行'
    assert d.job_capacity_max == 100
    assert d.wage_tier == WageTier.A
    assert d.wage_cr == 100
    assert d.tax_tier == TaxTier.B
    assert d.tax_rate == 0.12
    assert d.initial_employees_industrial_city == 20


def test_industry_definitions_mag():
    """阶段 1：MAG 魔法学院 — 400岗位 B工资 C税 30初始（V1.0 缺失行业）。"""
    d = INDUSTRY_DEFINITIONS[IndustryId.MAG]
    assert d.name_cn == '魔法学院'
    assert d.job_capacity_max == 400
    assert d.wage_tier == WageTier.B
    assert d.wage_cr == 90
    assert d.tax_tier == TaxTier.C
    assert d.tax_rate == 0.08
    assert d.initial_employees_industrial_city == 30


def test_industry_definitions_gov():
    """阶段 1：GOV 公务员 — 50岗位 C工资 C税(实际 0%) 50初始。"""
    d = INDUSTRY_DEFINITIONS[IndustryId.GOV]
    assert d.name_cn == '公务员'
    assert d.job_capacity_max == 50
    assert d.wage_tier == WageTier.C
    assert d.wage_cr == 75
    # 手稿税级为 C，但实际税率 0%（V2.1 §2.1 注：对自身征税无意义）
    assert d.tax_tier == TaxTier.C
    assert d.tax_rate == 0.0
    assert d.initial_employees_industrial_city == 50


def test_total_job_capacity_max():
    """阶段 1：行业岗位上限之和 = 3050（V2.1 §2.1 注）。"""
    total = sum(d.job_capacity_max for d in INDUSTRY_DEFINITIONS.values())
    assert total == 3050


def test_total_initial_employees_industrial_city():
    """阶段 1：工业新城首月就业合计 = 1500（V2.1 §2.1）。"""
    total = sum(d.initial_employees_industrial_city for d in INDUSTRY_DEFINITIONS.values())
    assert total == 1500


def test_initial_wage_bill_total():
    """阶段 1：首月工资总额 = 132,250 CR（V2.1 §2.2 首月数字）。"""
    total = sum(
        d.wage_cr * d.initial_employees_industrial_city
        for d in INDUSTRY_DEFINITIONS.values()
    )
    # 14,400 + 54,900 + 53,000 + 1,500 + 2,000 + 2,700 + 3,750 = 132,250
    assert total == 132_250


# ===== 工具函数 =====

def test_wage_cr_per_worker():
    """阶段 1：wage_cr_per_worker(industry_id) 返回 CR/人/月。"""
    assert wage_cr_per_worker(IndustryId.AGR) == 60
    assert wage_cr_per_worker(IndustryId.IND) == 90
    assert wage_cr_per_worker(IndustryId.SRV) == 100
    assert wage_cr_per_worker(IndustryId.CHU) == 75
    assert wage_cr_per_worker(IndustryId.BNK) == 100
    assert wage_cr_per_worker(IndustryId.MAG) == 90
    assert wage_cr_per_worker(IndustryId.GOV) == 75


def test_tax_rate_per_industry():
    """阶段 1：tax_rate(industry_id) 返回正利润税率。"""
    assert tax_rate(IndustryId.AGR) == 0.0   # 无税
    assert tax_rate(IndustryId.IND) == 0.12
    assert tax_rate(IndustryId.SRV) == 0.15
    assert tax_rate(IndustryId.CHU) == 0.0   # 无税
    assert tax_rate(IndustryId.BNK) == 0.12
    assert tax_rate(IndustryId.MAG) == 0.08
    assert tax_rate(IndustryId.GOV) == 0.0   # 实际 0%


def test_monthly_wage_bill_cents():
    """阶段 1：monthly_wage_bill_cents(industry, employees) 返回整数分。"""
    # IND 工业 610 人 × 90 CR × 100 分 = 5,490,000 分
    assert monthly_wage_bill_cents(IndustryId.IND, 610) == 610 * 90 * 100
    # GOV 50 人 × 75 CR × 100 分
    assert monthly_wage_bill_cents(IndustryId.GOV, 50) == 50 * 75 * 100
    # 0 员工
    assert monthly_wage_bill_cents(IndustryId.SRV, 0) == 0


def test_monthly_wage_bill_cents_industrial_city_total():
    """阶段 1：工业新城首月工资总额（分）= 13,225,000。"""
    total_cents = sum(
        monthly_wage_bill_cents(iid, d.initial_employees_industrial_city)
        for iid, d in INDUSTRY_DEFINITIONS.items()
    )
    assert total_cents == 132_250 * 100  # 13,225,000 分


# ===== JSON 加载器 =====

def test_industries_json_file_exists():
    """阶段 1：data/economy_v2/industries.json 存在。"""
    assert (ECONOMY_V2_DIR / 'industries.json').is_file()


def test_load_industries_config_basic():
    """阶段 1：load_industries_config 返回含 _meta + industries 的 dict。"""
    config = load_industries_config()
    assert '_meta' in config
    assert 'industries' in config
    assert config['_meta']['version'] == '2.1.0'
    assert config['_meta']['currency'] == 'CR'


def test_load_industries_config_count():
    """阶段 1：JSON 含 7 行业。"""
    config = load_industries_config()
    assert len(config['industries']) == 7


def test_load_industries_config_agr():
    """阶段 1：JSON AGR 与 Python INDUSTRY_DEFINITIONS 一致。"""
    config = load_industries_config()
    agr = config['industries']['AGR']
    py_def = INDUSTRY_DEFINITIONS[IndustryId.AGR]
    assert agr['name_cn'] == py_def.name_cn
    assert agr['job_capacity_max'] == py_def.job_capacity_max
    assert agr['wage_tier'] == py_def.wage_tier.value
    assert agr['tax_tier'] == py_def.tax_tier.value
    assert agr['initial_employees_industrial_city'] == py_def.initial_employees_industrial_city


def test_load_industries_config_all_match():
    """阶段 1：JSON 全部 7 行业与 Python INDUSTRY_DEFINITIONS 字段一致。"""
    config = load_industries_config()
    for iid in IndustryId:
        json_def = config['industries'][iid.value]
        py_def = INDUSTRY_DEFINITIONS[iid]
        assert json_def['name_cn'] == py_def.name_cn
        assert json_def['job_capacity_max'] == py_def.job_capacity_max
        assert json_def['wage_tier'] == py_def.wage_tier.value
        assert json_def['tax_tier'] == py_def.tax_tier.value
        assert json_def['initial_employees_industrial_city'] == py_def.initial_employees_industrial_city


def test_load_industries_config_bom_compatible():
    """阶段 1：read_json 兼容 UTF-8 BOM。"""
    # 直接调用 read_json 验证 BOM 处理
    raw = read_json(ECONOMY_V2_DIR / 'industries.json')
    assert raw is not None
    assert raw['_meta']['version'] == '2.1.0'


def test_load_industries_config_missing_file():
    """阶段 1：缺失文件抛 FileNotFoundError。"""
    with pytest.raises(FileNotFoundError):
        load_industries_config(path=Path('/nonexistent/industries.json'))


# ===== IndustryDefinition 数据类属性 =====

def test_industry_definition_is_frozen():
    """阶段 1：IndustryDefinition 是 frozen dataclass，不可变。"""
    d = INDUSTRY_DEFINITIONS[IndustryId.AGR]
    with pytest.raises(Exception):  # FrozenInstanceError
        d.wage_cr = 999  # type: ignore[misc]


def test_industry_definition_has_all_fields():
    """阶段 1：IndustryDefinition 字段完整。"""
    d = INDUSTRY_DEFINITIONS[IndustryId.IND]
    expected_fields = {
        'industry_id', 'name_cn', 'name_en', 'job_capacity_max',
        'wage_tier', 'wage_cr', 'tax_tier', 'tax_rate',
        'initial_employees_industrial_city', 'notes',
    }
    actual_fields = set(d.__dataclass_fields__.keys())  # type: ignore[attr-defined]
    assert expected_fields.issubset(actual_fields)


# ===== v21 包整体可被 import =====

def test_v21_package_imports_industries():
    """阶段 1：v21 包整体可被 import，导出行业体系全部符号。"""
    import backend.systems.v21 as v21
    assert v21.WageTier is WageTier
    assert v21.TaxTier is TaxTier
    assert v21.IndustryId is IndustryId
    assert v21.INDUSTRY_DEFINITIONS is INDUSTRY_DEFINITIONS
    assert v21.WAGE_TIER_CR == WAGE_TIER_CR
    assert v21.TAX_TIER_RATE == TAX_TIER_RATE
    assert callable(v21.wage_cr_per_worker)
    assert callable(v21.tax_rate)
    assert callable(v21.monthly_wage_bill_cents)
    assert callable(v21.load_industries_config)
