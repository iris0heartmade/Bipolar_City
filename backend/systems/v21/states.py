"""V2.1 阶段 9 补：完整 CityState + SectorState 聚合（§1.2 + §1.3）。

【2026-10-15】Bipole 城市聚合经济系统理论说明书 V2.1 §1.2 CityState + §1.3 SectorState。

【V2.1 §1.2 CityState 完整 24 字段】
population_total, labor_force, employed, cohort_population[8],
cohort_savings[8], sector_state[7], housing_capacity_people,
housing_building_capacity, government_account, property_pass_through_account,
church_reserve, city_commodity_stock[g], outstanding_shipments,
faith_share, service_capacity, effective_security, threat_level,
government_policy, completed_buildings, construction_queue,
K, livability_L, last_month_report。

【V2.1 §1.3 SectorState 完整 20 字段】
industry_id, building_count_by_type, job_capacity_J,
sustainable_job_capacity_Jop, employment_E, wage_per_worker_w,
operating_cash_M, base_capacity_per_good[g], planned_output[g],
produced_output[g], paid_input[g], cash_revenue_R, cash_operating_cost,
depreciation, pre_tax_profit, tax, after_tax_profit, dividend, debt,
production_efficiency, special_points_output[g]。

【V2.1 阶段 9 补范围】
本模块承载：
- CityState 完整聚合 dataclass（24 字段）
- SectorState 完整聚合 dataclass（20 字段）
- 通过 sector_id_of/industry_id 工厂支持 ID 解析
"""
from dataclasses import dataclass, field
from typing import Optional

from backend.systems.v21.industries import IndustryId


# ===== V2.1 §1.3 SectorState =====

@dataclass
class SectorState:
    """V2.1 §1.3 SectorState（以行业总量而非单企业为准）。

    字段（20 个）：
    - industry_id：行业 ID
    - building_count_by_type：各建筑类型数量 dict
    - job_capacity_J：基础岗位上限（来自建筑）
    - sustainable_job_capacity_Jop：可持续支付岗位（§5.2）
    - employment_E：在职人数
    - wage_per_worker_w：单人月工资（整数分）
    - operating_cash_M：经营现金（整数分）
    - base_capacity_per_good：基础月产能 dict[good_id, qty]
    - planned_output：计划产出 dict[good_id, qty]
    - produced_output：实际产出 dict[good_id, qty]
    - paid_input：实际消耗 dict[good_id, qty]
    - cash_revenue_R：实际销售收款
    - cash_operating_cost：实际运营支出
    - depreciation：折旧（只影响会计利润）
    - pre_tax_profit：税前利润
    - tax：行业税
    - after_tax_profit：税后利润
    - dividend：分红（进居民）
    - debt：未偿贷款本金
    - production_efficiency：生产效率（V2.1 §5.4 eta）
    - special_points_output：特殊点数产出 dict[good_id, qty]
    """
    industry_id: str
    # 基础配置
    building_count_by_type: dict[str, int] = field(default_factory=dict)
    job_capacity_J: int = 0
    sustainable_job_capacity_Jop: int = 0
    # 实时数据
    employment_E: int = 0
    wage_per_worker_w: int = 0
    operating_cash_M: int = 0
    # 投入产出
    base_capacity_per_good: dict[str, float] = field(default_factory=dict)
    planned_output: dict[str, float] = field(default_factory=dict)
    produced_output: dict[str, float] = field(default_factory=dict)
    paid_input: dict[str, float] = field(default_factory=dict)
    # 财务
    cash_revenue_R: int = 0
    cash_operating_cost: int = 0
    depreciation: int = 0
    pre_tax_profit: int = 0
    tax: int = 0
    after_tax_profit: int = 0
    dividend: int = 0
    debt: int = 0
    production_efficiency: float = 1.0
    special_points_output: dict[str, float] = field(default_factory=dict)


# ===== V2.1 §1.2 CityState =====

@dataclass
class CityState:
    """V2.1 §1.2 CityState 完整聚合（24 字段）。

    字段：
    - city_id：城市 ID
    - population_total N：总人口
    - labor_force F：劳动力
    - employed E：在职
    - cohort_population[8]：八群体人口 P_h
    - cohort_savings[8]：八群体储蓄 S_h
    - sector_state[7]：七行业状态（用 dict 按 industry_id 索引）
    - housing_capacity_people：可入住住房人数
    - housing_building_capacity：建筑可容纳人数（不含农场附属）
    - government_account：政府账户 ID
    - property_pass_through_account：PROP 通道 ID
    - church_reserve：教会储备
    - city_commodity_stock[g]：城市商品库存 dict
    - outstanding_shipments：在途订单（V2.1 §4.4）
    - faith_share：信徒比
    - service_capacity：已运营公共服务容量
    - effective_security：有效安全系数 s（V2.1 §10.5）
    - threat_level：威胁等级
    - government_policy：当前政府政策 dict
    - completed_buildings：当月完工建筑
    - construction_queue：建筑队列
    - K：承载力（V2.1 §10.6）
    - livability_L：宜居度（V2.1 §11.1）
    - last_month_report：上月报告
    """
    city_id: str
    # 人口
    population_total: int = 0
    labor_force: int = 0
    employed: int = 0
    # 群体
    cohort_population: dict[int, int] = field(default_factory=dict)   # h → P_h
    cohort_savings: dict[int, int] = field(default_factory=dict)      # h → S_h
    # 行业
    sector_state: dict[str, SectorState] = field(default_factory=dict)  # industry_id → SectorState
    # 建筑
    housing_capacity_people: int = 0
    housing_building_capacity: int = 0
    # 账户
    government_account: str = 'government'
    property_pass_through_account: str = 'property_pass'
    church_reserve: int = 0
    # 库存
    city_commodity_stock: dict[str, float] = field(default_factory=dict)
    outstanding_shipments: list = field(default_factory=list)
    # 政策/环境
    faith_share: float = 0.55
    service_capacity: float = 0.0
    effective_security: float = 0.95
    threat_level: float = 0.0
    government_policy: dict = field(default_factory=dict)
    completed_buildings: list = field(default_factory=list)
    construction_queue: list = field(default_factory=list)
    # 派生
    K: int = 0
    livability_L: float = 0.0
    last_month_report: dict = field(default_factory=dict)

    def get_sector(self, industry_id: str) -> Optional[SectorState]:
        """按 industry_id 取行业状态。"""
        return self.sector_state.get(industry_id)

    def add_sector(self, sector: SectorState) -> None:
        """注册一个行业状态。"""
        self.sector_state[sector.industry_id] = sector

    def get_cohort_population(self, h: int) -> int:
        """取第 h 群体人口。"""
        return self.cohort_population.get(h, 0)

    def total_employment(self) -> int:
        """七行业在职合计（§1.3 + §1.2 交叉）。"""
        return sum(s.employment_E for s in self.sector_state.values())
