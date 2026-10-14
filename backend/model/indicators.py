"""经济指标：设计稿 §12 的 MonthReport 字段，外加结构诊断与风险预警。

三类指标
--------
``core``     月度核心指标，玩家最关心的十几个数
``structure`` 结构诊断，回答「钱到底在哪、产能用到几成」
``risk``     风险预警，回答「还能撑几个月、哪里要出事」

每个指标带：
- 单位与显示精度（金额是分，展示时换算成 CR）
- 一句话说明，避免看数字猜含义
- 合理区间，超出就在 GUI 里标黄
"""
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class Indicator:
    """一个可展示的经济指标。"""

    key: str
    label: str            # 中文名，GUI 直接显示
    unit: str             # "CR" / "人" / "%" / "月" / ""（无量纲）
    decimals: int         # 展示精度
    category: str         # core / structure / risk
    description: str      # 一句话说明
    healthy: tuple = ()   # (低, 高) 健康区间；留空表示不判断
    higher_is_better: bool = True

    def format(self, value) -> str:
        if value is None:
            return "—"
        if self.unit == "CR":
            return f"{value / 100:,.{self.decimals}f}"
        if self.unit == "%":
            return f"{value * 100:.{self.decimals}f}%"
        if self.unit == "":
            return f"{value:.{self.decimals}f}"
        return f"{value:,.{self.decimals}f}"

    def status(self, value) -> str:
        """ok / warn / bad。GUI 用颜色区分。

        健康区间支持**单边**：``(0, None)`` 表示「不得为负」，
        ``(None, 0.10)`` 表示「不得高于 10%」。两边都不为空时是双侧区间。

        区间一律按**显示单位**书写：百分比写 0.10 表示 10%，
        CR 写 20000 表示 20,000 CR。引擎吐出来的金额却是「分」，
        所以这里必须先换算再比——否则企业所得税 12,001 CR
        会拿原始的 1,200,100 去撞 20000 的上限，
        于是一个完全正常的数被标成红色告警。
        """
        if not self.healthy or value is None:
            return "ok"
        low, high = self.healthy
        if self.unit == "CR":
            value = value / 100
        if (low is None or value >= low) and (high is None or value <= high):
            return "ok"
        if high is not None and value > high:
            return "bad" if high > 0 and value > high * 1.2 else "warn"
        if low is not None and value < low:
            return "bad" if low > 0 and value < low * 0.8 else "warn"
        return "warn"


def _ind(key, label, unit, decimals, category, desc,
         healthy=(), higher_is_better=True):
    """构造一个指标。顺序按「标识、名称、单位、精度、分类、说明」排列。"""
    return Indicator(key=key, label=label, unit=unit, decimals=decimals,
                     category=category, description=desc, healthy=healthy,
                     higher_is_better=higher_is_better)


INDICATORS: list[Indicator] = [
    # --- 人口与就业 ---
    _ind("population", "总人口", "人", 0, "core", "就业＋失业＋非劳动力，三项之和恒等于总人口",
         healthy=(1, None), higher_is_better=True),
    _ind("employed", "就业人口", "人", 0, "core", "本月有工作的劳动者"),
    _ind("employment_rate", "就业率", "%", 1, "core", "就业人口 ÷ 劳动年龄人口",
         healthy=(0.85, 1.0)),
    _ind("unemployment_rate", "失业率", "%", 1, "core", "失业人口 ÷ 劳动力",
         healthy=(0.0, 0.10), higher_is_better=False),
    _ind("migration_net", "净迁入", "人", 0, "core", "本月迁入减迁出"),
    _ind("vacant_homes", "空置住房", "套", 0, "core", "空着没人住的住宅",
         healthy=(0, 60), higher_is_better=False),

    # --- 收入 ---
    _ind("avg_wage_employed", "就业者平均工资", "CR", 2, "core",
         "工资总额 ÷ 就业人口，不是除以总人口"),
    _ind("avg_income_gross_per_capita", "人均税前收入", "CR", 2, "core",
         "居民全部税前收入 ÷ 总人口"),
    _ind("avg_income_disposable_per_capita", "人均可支配收入", "CR", 2, "core",
         "扣掉两税与必付支出后，居民真正能花的钱"),
    _ind("household_cash", "家庭现金", "CR", 0, "structure", "家庭账户余额合计"),
    _ind("household_savings_rate", "家庭储蓄率", "%", 1, "structure",
         "本月收入减去消费后的结余占比", healthy=(0.0, 0.40)),
    _ind("poverty_rate", "贫困率", "%", 1, "core",
         "可支配收入低于最低生活成本的收入层人口占比",
         healthy=(0.0, 0.15), higher_is_better=False),

    # --- 企业 ---
    _ind("firm_sales", "企业营业额", "CR", 0, "core", "企业收到的全部货款"),
    _ind("firm_pre_tax_profit", "企业税前利润", "CR", 0, "core", "扣完全部成本后"),
    _ind("firm_after_tax_profit", "企业税后利润", "CR", 0, "core", "缴税之后"),
    _ind("firm_profit_margin", "企业利润率", "%", 2, "structure",
         "税前利润 ÷ 营业额", healthy=(0.03, 0.20)),
    _ind("firm_cash", "企业现金", "CR", 0, "structure", "企业账户余额"),
    _ind("private_firm_count", "私营企业数", "家", 0, "structure", "当前在营企业数量"),
    _ind("capacity_utilisation", "产能利用率", "%", 1, "core",
         "实际售出 ÷ 满负荷产能。设计稿 §4.2 的 80% 就是这个口径，低于 85% 企业不会扩张",
         healthy=(0.55, 0.95)),
    _ind("capacity_readiness", "产能就绪度", "%", 1, "structure",
         "人力/技能/资源/服务/安全/工时六个因子的乘积，代表「产能能做到多少」；"
         "它不等于利用率——工厂具备满载能力不代表有人下单"),
    _ind("firm_cash_runway_months", "企业现金可撑", "月", 1, "risk",
         "按当前月支出，企业还能发几个月工资", healthy=(1.0, None),
         higher_is_better=True),

    # --- 财政 ---
    _ind("firm_profit_tax", "企业所得税", "CR", 0, "core",
         "当月企业缴纳的所得税。只对正利润征收，亏损月份必然为 0（设计稿 §2.2）",
         healthy=(0, 20000)),
    _ind("license_fee", "营业许可规费", "CR", 0, "structure",
         "企业交给市政的营业许可与行政规费，设计稿 §2.4 的 1200 CR 基准"),
    _ind("municipal_revenue", "市政收入", "CR", 0, "core", "税收、规费、中央拨款"),
    _ind("municipal_expense", "市政支出", "CR", 0, "core", "工资、采购、利息、建设"),
    _ind("fiscal_balance", "财政结余", "CR", 0, "core", "收入减支出",
         healthy=(0, None)),
    _ind("municipal_cash", "市政现金", "CR", 0, "core", "账户里实际有多少钱"),
    _ind("municipal_debt", "市政债务", "CR", 0, "core", "欠银行的本金"),
    _ind("credit_score", "城市信用", "", 2, "core",
         "税收稳定性、现金流、负债率与还款记录的综合分，范围 −1~1",
         healthy=(-0.2, 1.0)),
    _ind("loan_interest_rate", "当前贷款利率", "%", 2, "structure",
         "信用越好利率越低", healthy=(0.0, 0.03), higher_is_better=False),
    _ind("fiscal_runway_months", "财政可撑", "月", 1, "risk",
         "按当前赤字，市政还能撑几个月。财政有盈余时显示 999，表示「不会亏到破产」",
         healthy=(3.0, None)),

    # --- 对外 ---
    _ind("total_exports", "出口", "CR", 0, "core", "卖给外地与其他城市的货"),
    _ind("total_imports", "进口", "CR", 0, "core", "从外地买进来的投入"),
    _ind("export_import_ratio", "进出口比", "", 2, "structure",
         "出口 ÷ 进口。大于 1 才有净流入", healthy=(0.8, 3.0)),
    _ind("net_external_flow", "对外净流量", "CR", 0, "core",
         "出口＋拨款−进口−利息，决定城市整体现金增减", healthy=(0, None)),
    _ind("trade_balance_warning", "贸易预警", "", 0, "risk",
         "长期逆差会抽干城市现金", healthy=(0.6, 3.0)),
    _ind("strategic_stock_total", "战略品库存", "", 0, "structure",
         "粮煤钢魔材的库存合计（折算价值）"),

    # --- 增加值 ---
    _ind("gva", "城市增加值 GVA", "CR", 0, "core",
         "城市真正新创造的价值，不含中间交易重复计算"),

    # --- 结构与风险 ---
    _ind("faith_share", "信仰比例", "%", 1, "core",
         "影响免税、工时、工资容忍与教会捐赠四个因子",
         healthy=(0.0, 1.0)),
    _ind("church_cash", "教会储备", "CR", 0, "structure", "教会账户余额"),
    _ind("public_staff", "公共部门人数", "人", 0, "structure", "市政雇员总数"),
    _ind("school_coverage", "学校覆盖率", "%", 1, "structure",
         "入学人数 ÷ 学龄人口。影响 12 个月后的技能供给",
         healthy=(0.0, 1.0)),
    _ind("effective_magic_risk", "魔物风险", "%", 2, "risk",
         "期望袭击强度。魔法师驻地可压制", healthy=(0.0, 0.10),
         higher_is_better=False),
    _ind("accounting_residual_cents", "会计残差", "分", 0, "core",
         "本地现金变化与外部净流量的差，必须恒为 0"),
    _ind("unpaid_cents", "本月欠付", "CR", 0, "risk",
         "账户付不起而被截断的付款总额。0 表示本月所有收付都结清",
         healthy=(0, None), higher_is_better=False),
    _ind("total_city_cash", "全城现金", "CR", 0, "core",
         "所有本地账户余额合计，既不创造也不销毁"),
]

BY_KEY = {i.key: i for i in INDICATORS}

CATEGORY_LABELS = {"core": "核心指标", "structure": "结构诊断", "risk": "风险预警"}


def indicator(key: str) -> Indicator:
    if key not in BY_KEY:
        raise KeyError(f"未定义的经济指标：{key}")
    return BY_KEY[key]


def by_category(category: str) -> list[Indicator]:
    return [i for i in INDICATORS if i.category == category]


__all__ = ["BY_KEY", "CATEGORY_LABELS", "INDICATORS", "Indicator",
           "by_category", "indicator"]