"""V2.1 阶段 9 补：GDP/GVA 报告（§13）。

【2026-10-15】Bipole 城市聚合经济系统理论说明书 V2.1 §13 全城 GDP/GVA 及经济报告口径。

【V2.1 §13 关键约束】
- 居民工资总额 = sum paid_wages；在职人均工资 = 总工资/实际在职人数
- 人均总收入 = 全部居民当月实际税前收入/N
- 人均可支配收入按实缴税额扣减
- 行业营业额 = 实际商品服务销售（中间交易重复计入总营业额，不能当 GDP）
- 增加值 GVA ≈ 各行业商品服务销售 - 其购买的中间品/服务 - 商业租金
- 物业出租、公共部门非市场服务按一致口径计入
- 金融资产本金买卖不计 GDP
- 更严格 GDP 必须与最终消费+投资+政府消费+净出口的支出法核对
- MVP 可暂不显示精确 GDP，但资金守恒和库存守恒不能因此简化掉

【V2.1 阶段 9 补范围】
本模块承载：
- GVAComponents 数据类
- compute_industry_GVA() 行业 GVA
- compute_city_GDP() 城市级 GDP 报告
- compute_GDP_per_capita() 人均
- compute_loan_principal_not_in_GDP() §13 金融资产本金买卖不计
"""
from dataclasses import dataclass, field


# ===== V2.1 §13 GVA 组件 =====

@dataclass(frozen=True)
class GVAComponents:
    """V2.1 §13 行业 GVA = 销售 - 中间品/服务 - 商业租金（按一致口径）。"""
    industry_sales_cents: int = 0          # R_i 实际销售
    intermediate_purchase_cents: int = 0   # 中间品/服务采购
    commercial_rent_paid_cents: int = 0    # 商业租金

    def GVA_cents(self) -> int:
        """V2.1 §13 行业 GVA = 销售 - 中间品/服务 - 商业租金。"""
        return (
            self.industry_sales_cents
            - self.intermediate_purchase_cents
            - self.commercial_rent_paid_cents
        )


def compute_industry_GVA_cents(comp: GVAComponents) -> int:
    """V2.1 §13 短工具：行业 GVA。"""
    return comp.GVA_cents()


# ===== V2.1 §13 城市级 GDP 报告 =====

@dataclass(frozen=True)
class CityGDPReport:
    """V2.1 §13 城市级 GDP/GVA 报告。"""
    city_id: str
    month: int
    total_industry_GVA_cents: int = 0           # Σ行业 GVA
    total_industry_sales_cents: int = 0         # Σ行业销售（含中间交易，不当 GDP）
    total_wages_cents: int = 0                  # 居民工资总额
    total_employment: int = 0                    # 在职人数
    per_capita_total_income_cr: float = 0.0      # 人均总收入
    per_capita_disposable_income_cr: float = 0.0 # 人均可支配
    per_capita_wage_cr: float = 0.0              # 在职人均工资
    new_loan_principal_cents: int = 0            # V2.1 §13 本金买卖不计 GDP（仅显示）
    public_service_value_cents: int = 0          # 物业出租 + 公共部门非市场服务（一致口径）

    def nominal_GDP_cents(self) -> int:
        """V2.1 §13 严格 GDP = GVA + 公共部门非市场服务（不含金融资产本金）。"""
        return self.total_industry_GVA_cents + self.public_service_value_cents


def compute_city_GDP_report(
    city_id: str,
    month: int,
    *,
    industry_gvas: list[int],
    industry_sales: list[int],
    total_wages_cents: int,
    total_employment: int,
    population_N: int,
    per_capita_total_income_cr: float,
    per_capita_disposable_income_cr: float,
    new_loan_principal_cents: int = 0,
    public_service_value_cents: int = 0,
) -> CityGDPReport:
    """V2.1 §13 城市级 GDP 报告聚合。"""
    per_capita_wage = (total_wages_cents / 100.0 / max(total_employment, 1)) if total_employment > 0 else 0.0
    return CityGDPReport(
        city_id=city_id, month=month,
        total_industry_GVA_cents=sum(industry_gvas),
        total_industry_sales_cents=sum(industry_sales),
        total_wages_cents=total_wages_cents,
        total_employment=total_employment,
        per_capita_total_income_cr=per_capita_total_income_cr,
        per_capita_disposable_income_cr=per_capita_disposable_income_cr,
        per_capita_wage_cr=per_capita_wage,
        new_loan_principal_cents=new_loan_principal_cents,
        public_service_value_cents=public_service_value_cents,
    )


# ===== V2.1 §13 金融资产本金买卖不计 GDP =====

def loan_principal_does_not_count_in_GDP(principal_cents: int) -> int:
    """V2.1 §13 金融资产本金买卖不计 GDP。

    返回 0；本金仅作为过账（§8.1 新贷款本金不计入 GDP 和营业收入）。
    此函数作为占位标记，未来可在 GDP 报告里显式扣减。
    """
    return 0
