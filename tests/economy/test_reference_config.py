"""P00：校验经济参考场景配置自洽。

这些测试只核对配置本身的算术与口径，不证明任何引擎功能已实现。
派生结果（利润、税额、结余、GVA）必须在测试中由原始参数算出，
不得从配置的派生字段读回来再断言自己。
"""
import pytest

from backend.config import CALIBRATION, INPUT_OUTPUT, REFERENCE_CITY

CITY = REFERENCE_CITY
IO = INPUT_OUTPUT
TARGETS = CALIBRATION["derived_targets"]
SECTORS = CITY["firms"]["sectors"]


def sector_wage(sector):
    return sector["employees"] * sector["wage_per_employee"]


def sector_profit(sector):
    """税前利润 = 营业额 - 采购 - 工资 - 商租 - 折旧 - 规费。"""
    return (sector["revenue"] - sector["input_cost"] - sector_wage(sector)
            - sector["commercial_rent"] - sector["depreciation"] - sector["fees"])


# --- 六行业聚合 ---------------------------------------------------------

def test_sector_totals_match_declared_aggregates():
    assert sum(s["firms"] for s in SECTORS) == CITY["firms"]["sector_count_derived"] == 86
    assert sum(s["revenue"] for s in SECTORS) == TARGETS["firm_revenue"] == 340000
    assert sum(s["input_cost"] for s in SECTORS) == TARGETS["firm_input_cost"] == 154000
    assert sum(s["commercial_rent"] for s in SECTORS) == TARGETS["commercial_rent"] == 9500
    assert sum(s["depreciation"] for s in SECTORS) == TARGETS["depreciation"] == 11000
    assert sum(s["fees"] for s in SECTORS) == TARGETS["business_fees"] == 1200


def test_sector_profit_recomputed_from_raw_inputs():
    """逐行业重算利润并求和；配置里没有存任何利润字段。"""
    assert [sector_profit(s) for s in SECTORS] == [5700, 10100, 13800, 5340, 4160, 3300]
    assert sum(sector_profit(s) for s in SECTORS) == TARGETS["profit_before_tax"] == 42400


def test_private_wage_bill_matches_employment():
    private_wages = sum(sector_wage(s) for s in SECTORS)
    assert private_wages == TARGETS["wages_private"] == 121900
    assert private_wages + CITY["public_sector"]["wage_bill"] == TARGETS["wages_total"] == 136300


def test_employment_identity_links_firms_and_public_sector():
    """私企雇员 + 公共雇员 = 就业人数；否则岗位与人口恒等式无法闭合。"""
    demo = CITY["demographics"]
    assert sum(s["employees"] for s in SECTORS) == 1380
    assert sum(s["employees"] for s in SECTORS) + CITY["public_sector"]["employees"] == demo["employed"] == 1500
    assert demo["employed"] + demo["unemployed"] + demo["not_in_labour_force"] == demo["population"] == 3000
    assert demo["employed"] + demo["unemployed"] == demo["labour_force_derived"]
    assert demo["population"] / demo["households"] == demo["household_size_derived"]


# --- 营业额与投入来源 ---------------------------------------------------

def test_revenue_has_eight_funded_sources():
    sources = dict(IO["firm_revenue_sources"])
    sources.pop("note")
    assert len(sources) == 8
    assert sum(sources.values()) == TARGETS["firm_revenue"] == 340000


def test_enterprise_input_splits_into_local_and_import():
    split = dict(IO["firm_input_split"])
    split.pop("note")
    assert split["local_intermediate"] + split["imports"] == TARGETS["firm_input_cost"]
    assert split["local_intermediate"] == IO["firm_revenue_sources"]["firm_intermediate"]


def test_trade_lines_reconcile_with_totals_and_resource_flows():
    imports = sum(line["value"] for line in IO["trade"]["imports"])
    exports = sum(line["value"] for line in IO["trade"]["exports"])
    assert imports == TARGETS["imports"] == 68000
    assert exports == TARGETS["exports"] == 88200

    resources = {r["id"]: r for r in CITY["resources"]}
    for line in IO["trade"]["imports"]:
        if "qty" in line:
            assert line["qty"] * line["unit_price"] == line["value"]
            assert line["qty"] == resources[line["commodity"]]["imported"]
    for line in IO["trade"]["exports"]:
        if "qty" in line:
            assert line["qty"] * line["unit_price"] == line["value"]
            assert line["qty"] == resources[line["commodity"]]["exported"]


def test_strategic_stock_rolls_forward_without_creating_inventory():
    for res in CITY["resources"]:
        closing = (res["opening"] + res["produced"] + res["imported"]
                   - res["used"] - res["exported"])
        assert closing == res["closing"], res["id"]
        assert res["unit_price"] > 0 and res["opening"] >= 0


# --- 公共部门 ---------------------------------------------------------

def test_public_posts_match_headcount_and_wage_bill():
    public = CITY["public_sector"]
    assert sum(p["staff"] for p in public["posts"]) == public["employees"] == 120
    assert sum(p["spend"] for p in public["posts"]) == public["wage_bill"] == 14400
    for post in public["posts"]:
        assert post["staff"] * post["wage"] == post["spend"], post["id"]


def test_public_procurement_matches_reference_and_is_not_double_counted():
    procurement = CITY["public_sector"]["procurement"]
    assert sum(p["spend"] for p in procurement) == 8600
    assert any(p["id"] == "magic_material" and p["spend"] == 1100 for p in procurement), \
        "防御/魔法材料 1100 已含在公共采购内，不得另设一笔费用"


def test_school_seats_and_coverage():
    education = CITY["education"]
    seats = education["seats_per_teacher"] * CITY["public_sector"]["posts"][0]["staff"]
    assert seats == 720
    assert seats / education["school_age_population"] == pytest.approx(0.8)


# --- 住房 -------------------------------------------------------------

def test_housing_units_and_rent_reconcile():
    housing = CITY["housing"]
    assert housing["rented_units"] + housing["owner_occupied_units"] + housing["vacant_units"] == housing["total_units"] == 800
    assert housing["rent_per_rented_unit"] * housing["rented_units"] == housing["residential_rent_derived"] == 28200
    assert TARGETS["residential_rent"] == housing["residential_rent_derived"]
    assert housing["landlord_identity"] == "household", "房东是家庭财产身份，不得另计人口"


# --- 税收与市政派生 ----------------------------------------------------

def test_profit_tax_uses_positive_profit_only():
    fiscal = CITY["fiscal"]
    assert sum(sector_profit(s) for s in SECTORS) * fiscal["profit_tax_rate"] == pytest.approx(TARGETS["profit_tax"])
    assert TARGETS["profit_before_tax"] - TARGETS["profit_tax"] == TARGETS["profit_after_tax"]
    assert fiscal["profit_tax_rate_range"][0] == 0.0, "亏损不得产生负税"


def test_income_and_property_tax_recomputed_from_bases():
    fiscal, housing, religion = CITY["fiscal"], CITY["housing"], CITY["religion"]
    gross = sum(IO["resident_flows"]["gross_income_sources"].values())
    assert gross == TARGETS["resident_gross_income"] == 173300
    assert gross * fiscal["income_tax_rate"] == pytest.approx(TARGETS["income_tax"])

    relief = (fiscal["property_tax_monthly_rate"] * housing["assessed_value"]
              * (1 - fiscal["property_relief_religion_factor"] * religion["opening_faith"]))
    assert relief == pytest.approx(TARGETS["property_tax"])
    assert fiscal["income_tax_rate_range"][0] <= fiscal["income_tax_rate"] <= fiscal["income_tax_rate_range"][1]
    assert fiscal["property_tax_rate_range"][0] <= fiscal["property_tax_monthly_rate"] <= fiscal["property_tax_rate_range"][1]


def test_municipal_budget_closes_on_actual_payments():
    inflows = dict(IO["municipal_flows"]["inflows"])
    outflows = dict(IO["municipal_flows"]["outflows"])
    assert sum(inflows.values()) == TARGETS["municipal_income"] == 28817
    assert sum(outflows.values()) == TARGETS["municipal_spending"] == 28300
    assert sum(inflows.values()) - sum(outflows.values()) == TARGETS["municipal_surplus"] == 517


def test_firm_cash_change_excludes_depreciation():
    """折旧是账面成本，不再扣钱包；现金只减真实支付项。"""
    outflows = dict(IO["firm_flows"]["outflows"])
    assert sum(outflows.values()) == 322688
    assert TARGETS["firm_revenue"] - sum(outflows.values()) == TARGETS["firm_cash_change"] == 17312
    assert "depreciation" not in outflows
    retained = TARGETS["profit_after_tax"] - TARGETS["dividends"]
    assert retained == TARGETS["retained_profit"] == 21312
    assert retained + TARGETS["depreciation"] - outflows["capital_purchase"] == TARGETS["firm_cash_change"]


def test_dividend_payout_uses_rational_reference_fixture():
    """0.4288 量化后是 15999.3856；参考 fixture 用有理数保证首月恰为 16000。"""
    convention = CALIBRATION["algorithm_conventions"]["dividend_payout"]
    assert convention["rational_form"] is True
    payout = TARGETS["profit_after_tax"] * 16000 / TARGETS["profit_after_tax"]
    assert round(payout) == TARGETS["dividends"] == 16000
    assert 0.4288 * TARGETS["profit_after_tax"] != TARGETS["dividends"], \
        "0.4288 不满足精确基准，普通演化配置使用它时必须重算预期"


# --- 居民、教会与守恒 --------------------------------------------------

def test_resident_budget_closes_from_disposable_income():
    flows = IO["resident_flows"]
    disposable = (flows["gross_income_derived"] - TARGETS["income_tax"] - TARGETS["property_tax"]
                  + flows["inflows"]["relief"])
    assert disposable == TARGETS["resident_disposable_income"] == 157131
    spent = flows["outflows"]["consumption"] + flows["outflows"]["residential_rent"] + flows["outflows"]["donation"]
    assert disposable - spent == TARGETS["resident_cash_change"] == 8431


def test_church_uses_complete_formula_and_neutral_has_no_subsidy():
    church = dict(IO["church_flows"])
    subsidy = church["inflows"]["official_subsidy"]
    assert subsidy == 0, "宗教中立档不得产生官方扶持费"
    assert church["inflows"]["resident_donation"] - church["outflows"]["donation_to_city"] == TARGETS["church_reserve_change"] == 840
    neutral = next(t for t in CITY["religion"]["policy_tiers"] if t["tier"] == 0)
    assert neutral["subsidy"] == 0
    assert 1200 * CITY["religion"]["opening_faith"] == church["outflows"]["donation_to_city"] == 660


def test_city_net_cash_matches_external_net_flow():
    identity = (TARGETS["resident_cash_change"] + TARGETS["firm_cash_change"]
                + TARGETS["municipal_surplus"] + TARGETS["church_reserve_change"])
    assert identity == TARGETS["city_net_cash_change"] == 27100

    external = (TARGETS["exports"] + TARGETS["tourist_spend"] - TARGETS["imports"]
                + TARGETS["central_grant"] - TARGETS["external_interest"])
    assert external == TARGETS["external_net_cash_change"] == 27100
    assert identity == external, "本地钱包变化必须等于与非本地账户的实际净收付款"


def test_gva_production_components_sum_to_target():
    gva = IO["gva"]
    assert sum(gva["components"].values()) == gva["method_production_derived"] == TARGETS["gva"] == 211900
    private = (TARGETS["wages_private"] + TARGETS["depreciation"] + TARGETS["profit_tax"]
               + TARGETS["business_fees"] + TARGETS["profit_after_tax"])
    assert private == gva["components"]["private_firms"] == 176500
    assert gva["components"]["property_and_landlords"] == 21000


# --- 配置卫生：派生结果不得回写配置 --------------------------------------

def test_config_does_not_store_derived_results_as_inputs():
    """利润/税额/结余/GVA 必须由引擎算出；配置只保留原始参数和测试断言目标。"""
    for sector in SECTORS:
        for forbidden in ("profit", "profit_before_tax", "profit_after_tax", "net"):
            assert forbidden not in sector, f"{sector['id']} 不应存储派生字段 {forbidden}"
    assert "profit_before_tax" not in CITY["firms"]
    assert "surplus" not in CITY["fiscal"] and "gva" not in CITY["fiscal"]
    assert "profit_before_tax" in TARGETS, "派生目标只存在于 derived_targets（测试断言用）"


def test_assumptions_are_declared_with_reasons():
    assumptions = CALIBRATION["assumptions"]
    assert assumptions, "未给出原始输入的项必须显式登记为校准假设"
    ids = [a["id"] for a in assumptions]
    assert len(ids) == len(set(ids)), "假设编号不得重复"
    for item in assumptions:
        assert item["reason"].strip(), f"{item['id']} 缺少理由"
        assert item["status"] in {"calibration", "derived", "pending"}
        assert item["verify_in"].strip(), f"{item['id']} 缺少验证阶段"


def test_pending_assumptions_block_downstream_claims():
    """尚未补齐的项必须标 pending，不能悄悄按已确认数据使用。"""
    pending = {a["id"] for a in CALIBRATION["assumptions"] if a["status"] == "pending"}
    assert pending == {"A-05", "A-06", "A-07"}, "P00/P01 阶段不应新增未登记的缺口"


def test_opening_cash_is_stock_not_first_month_revenue():
    opening = CALIBRATION["opening_balances"]
    assert opening["city_government"] == CITY["opening_cash"]["city_government"] == 100000
    assert opening["church"] == CITY["opening_cash"]["church"] == 20000
    sector_ids = {entry["sector"] for entry in opening["firms"]}
    assert sector_ids == {s["id"] for s in SECTORS}
    for entry in opening["firms"]:
        sector = next(s for s in SECTORS if s["id"] == entry["sector"])
        monthly_fixed = sector_wage(sector) + sector["commercial_rent"] + sector["fees"]
        assert entry["opening_cash"] == monthly_fixed * 2, entry["sector"]
    assert opening["households_total"] == TARGETS["resident_disposable_income"], \
        "家庭期初现金取一个月可支配收入，避免用未到账工资虚构预算"


def test_currency_and_calendar_are_explicit():
    assert CITY["currency"]["code"] == "CR"
    assert CITY["currency"]["minor_units_per_major"] == 100
    assert CITY["calendar"] == {"days_per_month": 30, "months_per_year": 12}
    assert CITY["map"]["width_cells"] == CITY["map"]["height_cells"] == 64
    assert CITY["map"]["meters_per_cell"] == 20