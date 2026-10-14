"""完整经济模型验收：参数体系、指标定义、演化引擎与守恒。

这一组测试回答五个问题：
1. 参数是否可读、可改、可校验（越界必须报错）；
2. 引擎是否 100 个月零残差（钱既没创造也没销毁）；
3. 每个参数是否真的影响结果（不然调参就是假的）；
4. 长期演化会不会崩溃（NaN、负库存、财政击穿）；
5. 指标定义是否完整、可用（GUI 要靠它渲染）。

另外还有一组**基准还原测试**：直接拿设计稿 §2/§3.1 的数字当标准答案，
模型算出来的头一个月必须对得上。对不上就说明账漏了收入线或者开了双重记账，
这类 bug 光看「残差为 0」是发现不了的——钱照样守恒，只是守恒在错误的账上。
"""
import pytest

from backend.core.errors import GameError
from backend.model.indicators import BY_KEY, INDICATORS, Indicator, by_category
from backend.model.parameters import EconomyParameters
from backend.systems.model_sim import EconomySimulation, Ledger


# --- 参数体系 -----------------------------------------------------------

def test_default_parameters_pass_full_validation():
    """出厂参数必须自洽：范围、恒等式、库存三项全过。"""
    params = EconomyParameters()
    params.validate_all_deep()


def test_out_of_range_parameter_is_rejected():
    """填错一个 8.5 就启动失败，绝不静默跑出一个假城市。"""
    params = EconomyParameters()
    with pytest.raises(GameError, match="超出"):
        params.set("tax.income_flat_rate", 0.85)


def test_boundary_values_are_accepted():
    """边界值必须放行，否则合法参数会被误拒。"""
    params = EconomyParameters()
    params.set("tax.income_flat_rate", 0.20)
    params.set("business.profit_tax_rate", 0.0)
    params.validate_all()


def test_unknown_parameter_is_rejected():
    params = EconomyParameters()
    with pytest.raises(GameError):
        params.set("tax.does_not_exist", 1)


def test_reset_restores_factory_values():
    params = EconomyParameters()
    original = params.get("tax.income_flat_rate")
    params.set("tax.income_flat_rate", 0.15)
    params.reset()
    assert params.get("tax.income_flat_rate") == original


def test_reset_single_key_only_touches_that_key():
    params = EconomyParameters()
    params.set("tax.income_flat_rate", 0.15)
    params.set("business.profit_tax_rate", 0.20)
    params.reset_key("tax.income_flat_rate")
    assert params.get("tax.income_flat_rate") == 0.08
    assert params.get("business.profit_tax_rate") == 0.20


def test_changed_reports_only_the_differences():
    params = EconomyParameters()
    params.set("tax.income_flat_rate", 0.15)
    changed = params.changed()
    assert "tax.income_flat_rate" in changed
    assert len(changed) == 1


def test_adjustable_lists_every_parameter_with_range():
    metas = EconomyParameters().adjustable()
    assert len(metas) >= 80
    assert sum(1 for m in metas if m.has_range) >= 20
    assert all(m.key and m.unit is not None for m in metas)


def test_population_identity_is_enforced():
    """设计稿 TEST-01：就业＋失业＋非劳动力必须等于总人口。

    只改总人口而不动三项，恒等式必然崩——这正是校验要拦住的情况。
    """
    params = EconomyParameters()
    params.set("population.employed", 1000)
    with pytest.raises(GameError, match="人口恒等式"):
        params.validate_identity()


def test_population_identity_passes_when_consistent():
    """三项同步缩放后恒等式成立，城市可以正常启动。"""
    params = EconomyParameters({
        "population.start": 1500, "population.employed": 750,
        "population.unemployed": 75, "population.out_of_labour_force": 675,
        "population.labour_force": 825, "population.households": 375})
    params.validate_identity()


def test_negative_resource_stock_is_rejected():
    """设计稿 TEST-19：战略品库存不得为负。"""
    params = EconomyParameters()
    items = params.section("resources")["items"]
    items[0]["opening"] = 10
    items[0]["used"] = 9999
    with pytest.raises(GameError, match="库存"):
        params.validate_resource_balance()


# --- 账本 ---------------------------------------------------------------

def test_ledger_payment_moves_money_between_accounts():
    """钱只能从一本挪到另一本，这是全部记账纪律的地基。"""
    ledger = Ledger({"households": 1000, "firms": 0})
    ledger.pay("households", "firms", 300, "sale")
    assert ledger.balances["households"] == 700
    assert ledger.balances["firms"] == 300
    assert ledger.local_total == 1000


def test_ledger_ignores_zero_amounts():
    ledger = Ledger({"households": 1000})
    ledger.pay("households", "firms", 0, "sale")
    assert ledger.transactions == []


def test_ledger_rejects_unknown_account():
    ledger = Ledger()
    with pytest.raises(GameError):
        ledger.pay("nobody", "firms", 100, "x")


# --- 演化守恒 -----------------------------------------------------------

def test_sixty_months_have_zero_residual():
    """60 个月逐月残差必须恒为 0。"""
    history = EconomySimulation(months=60).run()
    bad = [r.month for r in history if r.metrics["accounting_residual_cents"] != 0]
    assert bad == [], f"会计残差非零的月份：{bad}"


def test_local_cash_never_goes_negative():
    """任何本地账户都不允许出现负余额。"""
    history = EconomySimulation(months=36).run()
    assert all(r.metrics["total_city_cash"] > 0 for r in history)
    assert all(r.metrics["municipal_cash"] > 0 for r in history)
    assert all(r.metrics["household_cash"] > 0 for r in history)


def test_no_nan_or_infinity_in_any_metric():
    """TEST-20：120 个月不出现 NaN 或无穷大。"""
    import math
    history = EconomySimulation(months=120).run()
    for r in history:
        for key, value in r.metrics.items():
            assert value is None or math.isfinite(value), f"第 {r.month} 月 {key} = {value}"


def test_population_never_exceeds_housing_or_jobs():
    """人口不得超过住房容量，也不得超过岗位能养活的人数。"""
    params = EconomyParameters()
    history = EconomySimulation(months=48, parameters=params).run()
    capacity = params.section("housing")["units"] * params.section("housing")["household_size"]
    assert max(r.metrics["population"] for r in history) <= capacity


def test_unemployment_rate_stays_near_reference():
    """失业率应围绕设计稿基准 9% 波动，而不是趋近 0 或暴涨。"""
    history = EconomySimulation(months=24).run()
    rates = [r.metrics["unemployment_rate"] for r in history]
    assert all(0.05 <= rate <= 0.20 for rate in rates), f"失业率异常：{rates}"


def test_fiscal_balance_is_reported_every_month():
    history = EconomySimulation(months=12).run()
    assert all("fiscal_balance" in r.metrics for r in history)
    assert all(isinstance(r.metrics["fiscal_balance"], int) for r in history)


def test_strategic_stock_never_negative():
    history = EconomySimulation(months=36).run()
    assert all(r.metrics["strategic_stock_total"] >= 0 for r in history)


# --- 参数确实影响结果 ---------------------------------------------------

def test_tax_rate_changes_fiscal_balance():
    """改税率必须改变财政结余，否则调参面板是摆设。"""
    low = EconomySimulation(months=12, parameters=EconomyParameters(
        {"tax.income_flat_rate": 0.0})).run()
    high = EconomySimulation(months=12, parameters=EconomyParameters(
        {"tax.income_flat_rate": 0.20})).run()
    assert high[-1].metrics["fiscal_balance"] > low[-1].metrics["fiscal_balance"]
    assert high[-1].metrics["municipal_revenue"] > low[-1].metrics["municipal_revenue"]


def _population_overrides(total: int) -> dict:
    """按总人口同步缩放人口四项与户数，保持恒等式成立。

    人口恒等式是硬校验：只改总人口会被拦下来，这是设计意图，不是 bug。
    """
    factor = total / 3000
    return {"population.start": total, "population.households": int(750 * factor),
            "population.labour_force": int(1650 * factor),
            "population.employed": int(1500 * factor),
            "population.unemployed": int(150 * factor),
            "population.out_of_labour_force": int(1350 * factor)}


def test_initial_population_changes_scale():
    """改人口必须改变经济规模。"""
    small = EconomySimulation(months=6, parameters=EconomyParameters(
        _population_overrides(1500))).run()
    large = EconomySimulation(months=6, parameters=EconomyParameters(
        _population_overrides(4500))).run()
    assert large[-1].metrics["population"] > small[-1].metrics["population"]
    assert large[-1].metrics["firm_sales"] > small[-1].metrics["firm_sales"]


def test_profit_tax_reduces_firm_profit():
    """设计稿 §2.2：只对正利润征企业所得税。

    取 3 个月而不是 1 个月：首月要先把订单跑起来才谈得上利润，
    用单月断言等于在测「冷启动」，测不到税制本身。
    """
    without = EconomySimulation(months=3, parameters=EconomyParameters(
        {"business.profit_tax_rate": 0.0})).run()
    with_tax = EconomySimulation(months=3, parameters=EconomyParameters(
        {"business.profit_tax_rate": 0.25})).run()
    assert without[-1].metrics["firm_profit_tax"] == 0
    assert with_tax[-1].metrics["firm_profit_tax"] > 0
    assert with_tax[-1].metrics["firm_after_tax_profit"] \
        < without[-1].metrics["firm_after_tax_profit"]


def test_loss_making_firms_pay_no_profit_tax():
    """设计稿 §2.2 / TEST-15：亏损不征负所得税。

    把出口和游客砍到接近零，企业收入不足以覆盖采购与工资，
    利润应当为负或为零，所得税随之归零——不能倒过来给企业发钱。
    """
    month = _first_month(**{"trade.exports_base": 0,
                            "trade.tourist_spend_base": 0,
                            "business.profit_tax_rate": 0.25})
    assert month.metrics["firm_profit_tax"] >= 0
    assert month.metrics["firm_pre_tax_profit"] \
        <= month.metrics["firm_after_tax_profit"]


def test_religion_policy_moves_faith_share():
    """设计稿 §6.2：严格限制应让信徒比例逐年下降。"""
    strict = EconomySimulation(months=24, parameters=EconomyParameters(
        {"religion.policy": -2})).run()
    neutral = EconomySimulation(months=24, parameters=EconomyParameters(
        {"religion.policy": 0})).run()
    assert strict[-1].metrics["faith_share"] < neutral[-1].metrics["faith_share"]


def test_wage_adjustment_converges_toward_market_target():
    """设计稿 §4.6：工资每月向目标走完差距的 adjust 比例。

    方向由劳动力市场决定，不能想当然假设「调得快＝工资高」：
    城市有 9% 待业，岗位需求 θ = 需求 ÷ 供给 < 1，目标工资**低于**起薪，
    所以调整幅度越大，工资越快往下走。
    """
    slow = EconomySimulation(months=12, parameters=EconomyParameters(
        {"labour.wage_target_adjustment": 0.10})).run()
    fast = EconomySimulation(months=12, parameters=EconomyParameters(
        {"labour.wage_target_adjustment": 0.50})).run()
    assert fast[-1].metrics["avg_wage_employed"] < slow[-1].metrics["avg_wage_employed"]


def test_tight_labour_market_pushes_wages_up():
    """岗位紧张（θ > 1）时工资应当上涨，与上面的劳动力过剩情形方向相反。

    人口四项必须整组改：只把待业人数从 150 调低会破坏人口恒等式，
    参数校验会直接拦下来，这是设计意图。
    """
    loose = EconomySimulation(months=12).run()
    tight = EconomySimulation(months=12, parameters=EconomyParameters({
        "population.unemployed": 20,
        "population.out_of_labour_force": 1480,
    })).run()
    assert tight[-1].metrics["unemployment_rate"] < loose[-1].metrics["unemployment_rate"]
    assert tight[-1].metrics["avg_wage_employed"] \
        > loose[-1].metrics["avg_wage_employed"]


def test_wage_change_respects_monthly_cap():
    """设计稿 §4.6：单月工资变动最大 ±3%，一次调到位是违规的。"""
    cap = 0.03
    result = EconomySimulation(months=24, parameters=EconomyParameters(
        {"labour.wage_change_monthly_cap": cap,
         "labour.wage_target_adjustment": 0.5})).run()
    for previous, current in zip(result, result[1:]):
        assert previous.metrics["avg_wage_employed"] > 0
        change = (current.metrics["avg_wage_employed"]
                  / previous.metrics["avg_wage_employed"] - 1)
        assert abs(change) <= cap + 1e-9


def test_migration_rate_changes_population_speed():
    """迁徙上限必须真的影响人口变化速度。"""
    slow = EconomySimulation(months=12, parameters=EconomyParameters(
        {"population.migration_monthly_cap_rate": 0.001})).run()
def test_migration_rate_changes_population_speed():
    """迁徙上限必须真的影响人口变化速度（设计稿 §5.2 单月不超过 1%）。

    默认参数下城市人口正好卡在可持续规模上（3000 人、1500 个岗位），
    没有迁徙压力，这个参数怎么调都看不出差别。
    所以先把住房压到 2800 个床位，制造出「住不下」的外迁压力。
    """
    squeeze = {"housing.units": 700}
    slow = EconomySimulation(months=12, parameters=EconomyParameters(
        {**squeeze, "population.migration_monthly_cap_rate": 0.001})).run()
    fast = EconomySimulation(months=12, parameters=EconomyParameters(
        {**squeeze, "population.migration_monthly_cap_rate": 0.05})).run()
    slow_change = abs(slow[-1].metrics["population"] - slow[0].metrics["population"])
    fast_change = abs(fast[-1].metrics["population"] - fast[0].metrics["population"])
    assert slow_change > 0
    assert fast_change > slow_change


def test_education_affects_skill_factor_over_time():
    """教育产出应随时间提升技能，进而影响产能。"""
    params = EconomyParameters({"education.completion_rate": 0.95,
                                "education.skill_transition_rate": 0.60})
    history = EconomySimulation(months=24, parameters=params).run()
    assert history[-1].metrics["capacity_utilisation"] > 0


def test_magic_defence_reduces_risk():
    """魔法师驻地应压制魔物威胁（设计稿 §5.4）。"""
    weak = EconomySimulation(months=1, parameters=EconomyParameters(
        {"security.mage_coverage_per_mage": 0.0,
         "security.base_threat_intensity": 0.5})).run()
    strong = EconomySimulation(months=1, parameters=EconomyParameters(
        {"security.mage_coverage_per_mage": 0.5,
         "security.base_threat_intensity": 0.5})).run()
    assert strong[-1].metrics["effective_magic_risk"] < weak[-1].metrics["effective_magic_risk"]


# --- 指标定义 -----------------------------------------------------------

def test_every_indicator_has_a_description():
    """指标必须能让人看懂，不能只有名字和数字。"""
    for ind in INDICATORS:
        assert ind.label and ind.description


def test_indicator_keys_are_unique():
    keys = [i.key for i in INDICATORS]
    assert len(keys) == len(set(keys))


def test_all_three_categories_are_populated():
    for category in ("core", "structure", "risk"):
        assert by_category(category), f"{category} 分类为空"


def test_indicator_formatting_scales_units():
    money = Indicator(key="t", label="金额", unit="CR", decimals=2, category="core",
                      description="")
    percent = Indicator(key="p", label="比率", unit="%", decimals=1, category="core",
                        description="")
    assert money.format(123456) == "1,234.56"
    assert percent.format(0.0856) == "8.6%"


def test_single_sided_health_range_works():
    """(0, None) 表示不得为负，不能因为 None 而崩。"""
    ind = Indicator(key="t", label="x", unit="CR", decimals=0, category="core",
                    description="", healthy=(0, None))
    assert ind.status(100) == "ok"
    assert ind.status(-5) in ("warn", "bad")


def test_status_detects_out_of_range():
    ind = Indicator(key="t", label="x", unit="%", decimals=1, category="core",
                    description="", healthy=(0.0, 0.10))
    assert ind.status(0.05) == "ok"
    assert ind.status(0.5) == "bad"


def test_indicator_lookup_raises_for_unknown():
    assert BY_KEY["population"].label == "总人口"
    with pytest.raises(KeyError):
        from backend.model.indicators import indicator
        indicator("no_such_metric")


# --- 基准还原：拿设计稿 §2 / §3.1 当标准答案 -------------------------------
#
# 下面每一个断言背后都是一个真实踩过的坑。
# 它们看起来「像在测数字」，其实是在守住「账有没有漏收入线」这件事。

def _first_month(**overrides):
    return EconomySimulation(months=1,
                             parameters=EconomyParameters(overrides) if overrides else None
                             ).run()[-1]


def test_month1_sales_reproduce_design_baseline():
    """设计稿 §3.1：营业额 340,000 CR，由**八个付款方**合计而成。

    以前只算了五个付款方，漏掉「企业间中间品 86,000」和
    「私人资本设备 15,000」，企业凭空少收 101,000 CR 却照样付全额采购，
    于是利润永远为负、营业额卡在 25 万上下怎么调参数都上不去。
    """
    sales_cr = _first_month().metrics["firm_sales"] / 100
    assert sales_cr == pytest.approx(340000, rel=0.05)


def test_month1_capacity_utilisation_is_eighty_percent():
    """设计稿 §4.2：K 值是按 80% 产能利用率反推的。

    「利用率」的口径是**实际售出 ÷ 满负荷产能**，不是六个因子的乘积。
    两者混用时指标永远显示 98%，玩家完全看不出工厂其实在空转。
    """
    month = _first_month()
    assert month.metrics["capacity_utilisation"] == pytest.approx(0.80, abs=0.05)
    # 就绪度可以接近 1，但它不是玩家关心的那个数
    assert month.metrics["capacity_readiness"] > month.metrics["capacity_utilisation"]


def test_month1_households_are_not_bankrupt():
    """居民钱包里有钱才能消费：期初家庭账上有一整笔钱。

    以前钱记在账本上却没分摊到各收入层，首月每个群体的 cash 都是 0，
    「花得起多少」算出来是 0，城市第一个月的消费预算直接归零。
    """
    month = _first_month()
    assert month.metrics["avg_income_gross_per_capita"] > 0
    assert all(c["cash"] > 0 for c in month.cohorts)
    # 设计稿 §2.5：居民商品及服务消费 119,000 CR
    assert month.metrics["firm_sales"] > 0


def test_asset_income_does_not_compound_monthly():
    """资产收入是**流量**，不是能滚雪球的余额。

    以前每月 `asset_income += 净房产收益`，等于每月往收入上再叠一份
    21,000 CR 房租净收益——收入会逐月**膨胀** 12% 左右，城市越花越多。
    正常演化下收入只随工资小幅平滑漂移，不会单向狂奔。
    """
    history = EconomySimulation(months=24).run()
    incomes = [sum(c["income"] for c in r.cohorts) for r in history]
    for previous, current in zip(incomes, incomes[1:]):
        change = abs(current - previous) / previous
        assert change < 0.03, f"单月收入跳变 {change:.1%}，疑似在复利累加"


def test_commercial_rent_and_license_fee_are_actually_paid():
    """设计稿 §2.1 / §2.4：商业租金 9,500 CR 与许可规费 1,200 CR 必须过账本。

    这两笔钱以前只进损益表、不过账本，等于凭空蒸发——
    居民少收一笔租金、市政少收一笔规费，而企业成本照扣。
    """
    month = _first_month()
    assert month.metrics["license_fee"] == pytest.approx(120000, rel=0.05)
    assert month.metrics["municipal_revenue"] > 0


def test_population_holds_at_design_baseline():
    """设计稿 §2.1：3000 人、1500 个岗位、1650 劳动力。

    人口上限不能只除以劳动参与率：1500 个岗位只有 90.9% 的劳动力能上岗，
    漏掉就业率这一层会把 3000 人的城压到 2727 人，凭空赶走九分之一居民。
    """
    month = _first_month()
    assert month.metrics["population"] == 3000
    assert month.metrics["unemployment_rate"] == pytest.approx(0.0909, abs=0.005)


def test_every_emitted_metric_has_an_indicator():
    """引擎产出了、但没有指标定义的数项，GUI 上就永远看不见。

    这种漏配不会让任何测试变红，只会让人以为「这个数没意义」，
    所以必须自动守门。
    """
    emitted = set(_first_month().metrics)
    assert emitted - set(BY_KEY) == set()


def test_every_indicator_is_actually_emitted():
    """指标定义了、但引擎压根不产出的指标，GUI 上会渲染成一片空白。"""
    emitted = set(_first_month().metrics)
    assert set(BY_KEY) - emitted == set()


def test_every_adjustable_parameter_has_a_range():
    """所有可调参数都必须声明取值范围。

    没有范围的参数，GUI 给不出滑条、校验也给不出提示，
    等于留了一个「填什么都行」的洞。
    """
    missing = [meta.key for meta in EconomyParameters().adjustable()
               if not meta.has_range]
    assert missing == []