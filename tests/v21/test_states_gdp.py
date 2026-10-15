"""V2.1 阶段 9 补 §1.2+§1.3+§13 状态聚合 + GDP 验收测试（2026-10-15）。

【验收清单】
- [x] CityState 24 字段（§1.2）
- [x] SectorState 20 字段（§1.3）
- [x] GDP/GVA 报告（§13）
- [x] 行业 GVA 公式
"""
import pytest

from backend.systems.v21.states import SectorState, CityState
from backend.systems.v21.gdp import (
    GVAComponents, compute_industry_GVA_cents,
    CityGDPReport, compute_city_GDP_report,
    loan_principal_does_not_count_in_GDP,
)


# ===== A. SectorState（3 项）=====

class TestSectorState:
    """V2.1 §1.3 SectorState 20 字段。"""

    def test_default_construction(self):
        """阶段 9 补：默认构造。"""
        s = SectorState(industry_id='AGR')
        assert s.industry_id == 'AGR'
        assert s.job_capacity_J == 0
        assert s.employment_E == 0
        assert s.operating_cash_M == 0

    def test_with_real_data(self):
        """阶段 9 补：填入实际数据。"""
        s = SectorState(
            industry_id='IND',
            job_capacity_J=1500,
            sustainable_job_capacity_Jop=610,
            employment_E=610,
            wage_per_worker_w=90_00,           # 90 CR = 9,000 分
            operating_cash_M=250_000_00,        # 250,000 CR
            base_capacity_per_good={'STEEL': 1000.0},
            cash_revenue_R=190_000_00,
            pre_tax_profit=18_500_00,
        )
        assert s.industry_id == 'IND'
        assert s.job_capacity_J == 1500
        assert s.employment_E == 610

    def test_full_20_fields(self):
        """阶段 9 补：20 字段全部可显式赋值。"""
        s = SectorState(
            industry_id='SRV',
            building_count_by_type={'shop': 50},
            job_capacity_J=600,
            sustainable_job_capacity_Jop=530,
            employment_E=530,
            wage_per_worker_w=100_00,
            operating_cash_M=170_000_00,
            base_capacity_per_good={'GOODS': 500.0},
            planned_output={'GOODS': 500.0},
            produced_output={'GOODS': 450.0},
            paid_input={'STEEL': 100.0},
            cash_revenue_R=110_000_00,
            cash_operating_cost=99_500_00,
            depreciation=1_500_00,
            pre_tax_profit=6_000_00,
            tax=900_00,
            after_tax_profit=5_100_00,
            dividend=3_000_00,
            debt=0,
            production_efficiency=0.9,
            special_points_output={'CIVIC': 50.0},
        )
        assert s.employment_E == 530
        assert s.produced_output == {'GOODS': 450.0}


# ===== B. CityState（4 项）=====

class TestCityState:
    """V2.1 §1.2 CityState 24 字段。"""

    def test_default_construction(self):
        """阶段 9 补：默认构造。"""
        c = CityState(city_id='A')
        assert c.city_id == 'A'
        assert c.population_total == 0
        assert c.faith_share == 0.55
        assert c.effective_security == 0.95

    def test_get_sector(self):
        """阶段 9 补：get_sector 按 industry_id 索引。"""
        c = CityState(city_id='A')
        c.add_sector(SectorState(industry_id='AGR'))
        sector = c.get_sector('AGR')
        assert sector is not None
        assert sector.industry_id == 'AGR'

    def test_total_employment(self):
        """阶段 9 补：七行业在职合计。"""
        c = CityState(city_id='A')
        c.add_sector(SectorState(industry_id='AGR', employment_E=240))
        c.add_sector(SectorState(industry_id='IND', employment_E=610))
        c.add_sector(SectorState(industry_id='SRV', employment_E=530))
        assert c.total_employment() == 1380

    def test_get_cohort_population(self):
        """阶段 9 补：get_cohort_population 取 h 群体。"""
        c = CityState(city_id='A', cohort_population={0: 70, 1: 480})
        assert c.get_cohort_population(0) == 70
        assert c.get_cohort_population(1) == 480
        assert c.get_cohort_population(5) == 0  # 默认 0


# ===== C. GVA + GDP 报告（4 项）=====

class TestGVA:
    """V2.1 §13 GVA = 销售 - 中间品/服务 - 商业租金。"""

    def test_industry_gva_basic(self):
        """阶段 9 补：行业 GVA 计算。"""
        comp = GVAComponents(
            industry_sales_cents=190_000_00,            # 190,000 CR
            intermediate_purchase_cents=100_000_00,     # 100,000
            commercial_rent_paid_cents=5_100_00,        # 5,100
        )
        # 190,000 - 100,000 - 5,100 = 84,900
        assert comp.GVA_cents() == 84_900_00
        assert compute_industry_GVA_cents(comp) == 84_900_00

    def test_loan_principal_not_in_gdp(self):
        """阶段 9 补：§13 金融资产本金买卖不计 GDP。"""
        # 100 万本金 = 0 计入 GDP
        assert loan_principal_does_not_count_in_GDP(1_000_000_00) == 0


class TestCityGDPReport:
    """V2.1 §13 城市级 GDP 报告。"""

    def test_basic_report(self):
        """阶段 9 补：基本 GDP 报告生成。"""
        report = compute_city_GDP_report(
            city_id='industrial_city', month=1,
            industry_gvas=[80_000_00, 60_000_00],
            industry_sales=[190_000_00, 110_000_00],
            total_wages_cents=128_500_00,    # §14.3 五行业工资合计
            total_employment=1500,           # §14.1
            population_N=3000,
            per_capita_total_income_cr=50.0,
            per_capita_disposable_income_cr=42.0,
            public_service_value_cents=10_000_00,
        )
        assert report.city_id == 'industrial_city'
        assert report.total_industry_GVA_cents == 140_000_00
        assert report.nominal_GDP_cents() == 150_000_00  # GVA + 公共服务

    def test_per_capita_wage(self):
        """阶段 9 补：在职人均工资 = 总工资/实际在职人数。"""
        report = compute_city_GDP_report(
            city_id='A', month=1,
            industry_gvas=[], industry_sales=[],
            total_wages_cents=100_000_00,  # 100,000 CR = 10,000,000 分
            total_employment=100,
            population_N=300,
            per_capita_total_income_cr=0.0,
            per_capita_disposable_income_cr=0.0,
        )
        # 100,000 / 100 = 1,000 CR/人
        assert abs(report.per_capita_wage_cr - 1_000.0) < 1e-6
