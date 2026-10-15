"""V2.1 阶段 1：行业体系数据类。

【2026-10-15】Bipole 城市聚合经济系统理论说明书 V2.1 §2.1 七行业重构。

V1.0 时期 6 行业（农业/轻工/重矿/零售/物流/服务）已封存到
`backend/systems_v1_reference/`。V2.1 重构为 7 行业：
- AGR 农业 300岗位上限 / D工资(60) / 无税 / 240初始就业
- IND 工业 1500岗位上限 / B工资(90) / B税(12%) / 610初始就业
- SRV 服务业/商业 600岗位上限 / A工资(100) / A税(15%) / 530初始就业
- CHU 教会 100岗位上限 / C工资(75) / 无税 / 20初始就业
- BNK 银行 100岗位上限 / A工资(100) / B税(12%) / 20初始就业（V1.0 缺失）
- MAG 魔法学院 400岗位上限 / B工资(90) / C税(8%) / 30初始就业（V1.0 缺失）
- GOV 公务员 50岗位上限 / C工资(75) / C税(0% 实际) / 50初始就业

【V2.1 阶段 1 范围】本模块仅承载 V2.1 §2.1 静态定义（行业 ID、工资级、税级、
岗位上限、初始就业）。完整 SectorState 运行时数据类（V2.1 §1.3）由
V2.1 阶段 2 居民群体完成后定义；工资发放逻辑在阶段 6 通胀与工资；
经营生产逻辑在阶段 9 月结引擎。
"""
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class WageTier(str, Enum):
    """工资级 A/B/C/D，对应 100/90/75/60 CR/在职人/月（V2.1 §2.2）。"""
    A = 'A'  # 100 CR
    B = 'B'  # 90 CR
    C = 'C'  # 75 CR
    D = 'D'  # 60 CR


class TaxTier(str, Enum):
    """行业税级 A/B/C/无，对应 15/12/8/0% 正利润税率（V2.1 §2.3）。

    GOV 行业税级手稿为 C，但实际为 0%（V2.1 §2.1 注：公务员向自身缴税无意义）。
    """
    A = 'A'           # 15%
    B = 'B'           # 12%
    C = 'C'           # 8%
    NONE = 'none'     # 0%（亏损或免税行业）


# V2.1 §2.2 工资级 → CR/人/月
WAGE_TIER_CR: dict[WageTier, int] = {
    WageTier.A: 100,
    WageTier.B: 90,
    WageTier.C: 75,
    WageTier.D: 60,
}

# V2.1 §2.3 税级 → 正利润税率
TAX_TIER_RATE: dict[TaxTier, float] = {
    TaxTier.A: 0.15,
    TaxTier.B: 0.12,
    TaxTier.C: 0.08,
    TaxTier.NONE: 0.0,
}


class IndustryId(str, Enum):
    """V2.1 §2.1 七行业 ID。"""
    AGR = 'AGR'   # 农业
    IND = 'IND'   # 工业
    SRV = 'SRV'   # 服务业/商业
    CHU = 'CHU'   # 教会
    BNK = 'BNK'   # 银行（V1.0 缺失，V2.1 全新）
    MAG = 'MAG'   # 魔法学院（V1.0 缺失，V2.1 全新）
    GOV = 'GOV'   # 公务员


@dataclass(frozen=True)
class IndustryDefinition:
    """行业静态定义（V2.1 §2.1）。

    注：frozen=True 防止运行时被修改；如需动态调参，请通过 EconomyParameters
    （V2.1 阶段 6 后实现）传入 override。
    """
    industry_id: IndustryId
    name_cn: str                                  # 中文显示名
    name_en: str                                  # 英文名（兼容老引用）
    job_capacity_max: int                        # 基础阶段理论岗位上限
    wage_tier: WageTier
    wage_cr: int                                 # 工资 CR/在职人/月（冗余字段，便于直接读）
    tax_tier: TaxTier
    tax_rate: float                              # 实际税率（冗余字段，0.0~0.15）
    initial_employees_industrial_city: int       # 工业新城首月初始就业
    notes: str = ''                              # 备注


def _make_industry(
    industry_id: IndustryId,
    name_cn: str,
    name_en: str,
    job_capacity_max: int,
    wage_tier: WageTier,
    tax_tier: TaxTier,
    initial_employees: int,
    notes: str = '',
    tax_rate_override: Optional[float] = None,
) -> IndustryDefinition:
    """工厂函数：自动从 WageTier/TaxTier 推导 wage_cr 和 tax_rate。

    tax_rate_override：少数行业（如 GOV 公务员）的实际税率与手稿税级不符，
    可通过此参数显式覆盖。当前唯一用例：GOV 公务员手稿 tax_tier=C（8%）但
    实际 tax_rate=0%（对自身征税无意义，V2.1 §2.1 注）。
    """
    wage_cr = WAGE_TIER_CR[wage_tier]
    tax_rate = tax_rate_override if tax_rate_override is not None else TAX_TIER_RATE[tax_tier]
    return IndustryDefinition(
        industry_id=industry_id,
        name_cn=name_cn,
        name_en=name_en,
        job_capacity_max=job_capacity_max,
        wage_tier=wage_tier,
        wage_cr=wage_cr,
        tax_tier=tax_tier,
        tax_rate=tax_rate,
        initial_employees_industrial_city=initial_employees,
        notes=notes,
    )


# V2.1 §2.1 七行业标准定义
INDUSTRY_DEFINITIONS: dict[IndustryId, IndustryDefinition] = {
    IndustryId.AGR: _make_industry(
        industry_id=IndustryId.AGR, name_cn='农业', name_en='Agriculture',
        job_capacity_max=300, wage_tier=WageTier.D, tax_tier=TaxTier.NONE,
        initial_employees=240,
        notes='粮食与农业原料；低工资；行业税免征；农业工资税减免；农场附属住房',
    ),
    IndustryId.IND: _make_industry(
        industry_id=IndustryId.IND, name_cn='工业', name_en='Industry',
        job_capacity_max=1500, wage_tier=WageTier.B, tax_tier=TaxTier.B,
        initial_employees=610,
        notes='材料及制造业商品；科技点数；少量市政点数；受原料与能源影响',
    ),
    IndustryId.SRV: _make_industry(
        industry_id=IndustryId.SRV, name_cn='服务业/商业', name_en='Services',
        job_capacity_max=600, wage_tier=WageTier.A, tax_tier=TaxTier.A,
        initial_employees=530,
        notes='零售、物流、生活服务的聚合；商业渠道；可销售多种居民服务',
    ),
    IndustryId.CHU: _make_industry(
        industry_id=IndustryId.CHU, name_cn='教会', name_en='Church',
        job_capacity_max=100, wage_tier=WageTier.C, tax_tier=TaxTier.NONE,
        initial_employees=20,
        notes='信徒捐助、宗教服务、信仰点数；保留信徒比例四种既定经济效应',
    ),
    IndustryId.BNK: _make_industry(
        industry_id=IndustryId.BNK, name_cn='银行', name_en='Banking',
        job_capacity_max=100, wage_tier=WageTier.A, tax_tier=TaxTier.B,
        initial_employees=20,
        notes='金融服务、贷款利息与费用；经营现金、债权/债务必须分开；最简贷款基金制',
    ),
    IndustryId.MAG: _make_industry(
        industry_id=IndustryId.MAG, name_cn='魔法学院', name_en='Magic Academy',
        job_capacity_max=400, wage_tier=WageTier.B, tax_tier=TaxTier.C,
        initial_employees=30,
        notes='教育/魔法服务、魔法点数、少量科技点数；可向本地/异地提供安全服务',
    ),
    IndustryId.GOV: _make_industry(
        industry_id=IndustryId.GOV, name_cn='公务员', name_en='Government',
        job_capacity_max=50, wage_tier=WageTier.C, tax_tier=TaxTier.C,
        initial_employees=50,
        notes='公共行政、市政点数；人员工资由政府支付，不作为盈利行业；'
              '实际行业税=0%（对自身征税无意义，V2.1 §2.1 注）',
        tax_rate_override=0.0,   # GOV 手稿税级 C(8%) 但实际 0%
    ),
}


def wage_cr_per_worker(industry_id: IndustryId) -> int:
    """返回该行业每在职人每月工资 CR。"""
    return INDUSTRY_DEFINITIONS[industry_id].wage_cr


def tax_rate(industry_id: IndustryId) -> float:
    """返回该行业正利润税率（0.0~0.15）。亏损行业不征税（V2.1 §7.2）。"""
    return INDUSTRY_DEFINITIONS[industry_id].tax_rate


def monthly_wage_bill_cents(industry_id: IndustryId, employees: int) -> int:
    """计算某行业当月工资总额（整数分）。

    employees: 实际在职人数
    返回：employees * wage_cr * 100（分）
    """
    wage_cr = wage_cr_per_worker(industry_id)
    return employees * wage_cr * 100
