"""接入层：把可玩世界（老引擎）的实际规模翻译成新引擎认识的标定配置。

为什么需要这一层
----------------
P00—P03 的参考城是**定值**的：3000 人、1380 名私企员工、800 套住房、136300 工资。
而玩家实际玩的是一座从小镇长起来的城市，开局只有一百来人，还在动态变化。
把 3000 人的账本直接套到 168 人的镇上，六行业每月固定发 136300 CR 工资，开局即破产。

所以这里按**人口比**统一缩放：单价（税率、工资单价、租金单价）一律不动，
人数与总量按比例缩放。这样利润率、税率、储蓄率等一切比例关系都保持参考城的比例，
而绝对金额跟着城市规模走。

三条纪律
--------
1. 只做缩放，不发明新数值。每一项都能追溯到参考城的哪个字段。
2. 缩放后的配置必须自洽：人口三项恒等、就业不超过岗位、
   企业损益与投入产出表对得上、住房套数与出租/自住/空置相加等于总数。
3. 缩不动的情况一律报错，绝不静默取整成 0。金额缩到 0 会让引擎以为「本月没有这笔义务」。
"""
from copy import deepcopy

from backend.config import BALANCE, CATALOG, INPUT_OUTPUT, LABOR_CONSUMPTION, REFERENCE_CITY
from backend.core.errors import GameError
from backend.model.economy import ReferenceCity, cr_to_cents
from backend.systems.fiscal import CHURCH_DONATION_BASE_CR

REFERENCE_POPULATION = REFERENCE_CITY["demographics"]["population"]
REFERENCE_PRIVATE_SHARE = 0.92      # 1380/1500 私企岗位占比
REFERENCE_UNEMPLOYED_RATE = 0.10   # 150/1500 失业率
REFERENCE_EMPLOYED_RATE = 0.50     # 1500/3000 就业人口占比
REFERENCE_ASSESSED_PER_UNIT = (REFERENCE_CITY["housing"]["assessed_value"]
                               / REFERENCE_CITY["housing"]["total_units"])


def _spread(total: int, weights: list) -> list[int]:
    """按权重把整数 total 拆成同样长度的列表，残差归入权重最大的一项。

    金额与人数都会因为取整丢分。丢在哪一项必须写明，
    不能让每一项都少一点然后指望调用方去猜差额跑哪去了。
    """
    if not weights:
        return []
    weight_sum = sum(weights)
    if weight_sum <= 0 or total <= 0:
        return [0] * len(weights)
    parts = [int(total * weight / weight_sum) for weight in weights]
    parts[weights.index(max(weights))] += total - sum(parts)
    return parts


def _scale(amount, factor: float) -> int:
    """金额缩放，非零项至少保留 1 单位。"""
    return max(1, int(round(amount * factor))) if amount else 0


def labour_structure(world) -> dict:
    """按实际人口与岗位推导就业结构，保证三项恒等且就业不超过岗位。"""
    population = world.population
    if population <= 0:
        raise GameError("城市还没有人口，无法进行经济结算。")

    from backend.systems.population import city_jobs
    jobs = city_jobs(world)
    employed = min(jobs, max(1, round(population * REFERENCE_EMPLOYED_RATE)))
    unemployed = min(population - employed, round(employed * REFERENCE_UNEMPLOYED_RATE))
    not_in_labour_force = population - employed - unemployed

    if employed + unemployed + not_in_labour_force != population:
        raise GameError("推导出的就业结构不闭合，拒绝结算。")
    return {"population": population,
            "households": max(1, population // BALANCE["household_size"]),
            "employed": employed, "unemployed": unemployed,
            "not_in_labour_force": not_in_labour_force, "jobs": jobs}


def housing_structure(world) -> dict:
    """按实际建成住宅推导套数、出租/自住/空置与评估值。"""
    total_units = rented_units = owner_occupied = 0
    for building in world.buildings:
        definition = CATALOG[building.definition_id]
        if building.status != "completed" or not definition.get("units"):
            continue
        units = definition["units"]
        total_units += units
        if building.housing_mode == "public":
            rented_units += units
        else:
            owner_occupied += min(building.sold_units, units)

    # 住宅是强制项：没有住宅的城市收不到房产税，房产统计也无从谈起
    if total_units <= 0:
        total_units = rented_units = 1
    owner_occupied = min(owner_occupied, max(0, total_units - rented_units))
    return {"total_units": total_units, "rented_units": rented_units,
            "owner_occupied_units": owner_occupied,
            "vacant_units": max(0, total_units - rented_units - owner_occupied),
            "assessed_value": max(1, round(total_units * REFERENCE_ASSESSED_PER_UNIT))}


def _scale_sectors(config, private_total: int) -> None:
    """按实际就业人数重写六行业，员工总数守恒，人均水平保持不变。"""
    sectors = config["firms"]["sectors"]
    headcounts = _spread(private_total, [s["employees"] for s in sectors])
    for sector, headcount in zip(sectors, headcounts):
        factor = headcount / sector["employees"] if sector["employees"] else 0
        sector["firms"] = _scale(sector["firms"], factor)
        sector["employees"] = headcount
        # wage_per_employee 是单价，不随城市规模改变
        for key in ("revenue", "input_cost", "commercial_rent", "depreciation", "fees"):
            sector[key] = _scale(sector[key], factor)


def _scale_public_sector(config, public_total: int) -> None:
    """公共部门按实际公共岗位缩放；工资总额等于各岗位之和。"""
    public = config["public_sector"]
    staff_counts = _spread(public_total, [row["staff"] for row in public["posts"]])
    for row, staff in zip(public["posts"], staff_counts):
        factor = staff / row["staff"] if row["staff"] else 0
        row["staff"] = staff
        row["spend"] = _scale(row["spend"], factor)
    public["employees"] = public_total
    public["wage_bill"] = sum(row["staff"] * row["wage"] for row in public["posts"])

    # 采购跟着公共部门规模走，用工资总额作缩放锚点
    factor = public["wage_bill"] / REFERENCE_CITY["public_sector"]["wage_bill"]
    for row in public["procurement"]:
        row["spend"] = _scale(row["spend"], factor)


def _scale_demographics(config, labour: dict) -> None:
    demo = config["demographics"]
    demo.update({"population": labour["population"], "households": labour["households"],
                 "employed": labour["employed"], "unemployed": labour["unemployed"],
                 "not_in_labour_force": labour["not_in_labour_force"],
                 "labour_force_derived": labour["employed"] + labour["unemployed"]})


def _scale_housing(config, housing: dict, scale: float) -> None:
    """住房按实际建成套数重写。

    维修费按产权拆成两笔：出租房部分走房东账户，自住房部分由房主本人付。
    参考城全部住宅都有租赁市场，landlord_share 等于全额，因此基准不受影响。
    """
    block = config["housing"]
    total_units = housing["total_units"]
    repair_total = _scale(block["owner_repair_spend"], scale)
    landlord_share = _scale(repair_total, housing["rented_units"] / total_units) \
        if total_units else repair_total

    block.update({"total_units": total_units,
                  "rented_units": housing["rented_units"],
                  "owner_occupied_units": housing["owner_occupied_units"],
                  "vacant_units": housing["vacant_units"],
                  "assessed_value": housing["assessed_value"],
                  "owner_repair_spend": repair_total,
                  "owner_repair_rented": min(landlord_share, repair_total),
                  "residential_rent_derived": housing["rented_units"]
                  * block["rent_per_rented_unit"]})


def _scale_fiscal(config, scale: float) -> None:
    """市政收支随规模缩放；税率与贷款月息保持不变。"""
    for key in ("central_grant", "relief_payments", "interest_paid", "municipal_capital_spend"):
        config["fiscal"][key] = _scale(config["fiscal"][key], scale)
    config["finance"]["government_loan_principal"] = _scale(
        config["finance"]["government_loan_principal"], scale)
    config["religion"]["resident_donation"] = _scale(
        config["religion"]["resident_donation"], scale)
    config["religion"]["donation_to_city_base_cr"] = _scale(
        config["religion"].get("donation_to_city_base_cr", CHURCH_DONATION_BASE_CR), scale)


def _scale_input_output(config, scale: float) -> dict:
    """投入产出表按人口比缩放；战略品明细与总额同比例缩放，两者始终对得上。

    市政采购、资本支出与业主维修不按人口比缩放，而是直接取 config 的实际值：
    月报口径用的是 config 求和，实际付款用的是这张表，两边必须逐分相等。
    否则市政支出报表会和账户里真实付出的钱差一个数，看着像结余，实际已经亏了。
    """
    io = deepcopy(INPUT_OUTPUT)
    for key in ("household_consumption", "private_capital", "exports", "tourist_spend",
                "firm_intermediate"):
        io["firm_revenue_sources"][key] = _scale(io["firm_revenue_sources"][key], scale)
    io["firm_revenue_sources"]["municipal_procurement"] = sum(
        row["spend"] for row in config["public_sector"]["procurement"])
    io["firm_revenue_sources"]["municipal_capital"] = config["fiscal"]["municipal_capital_spend"]
    io["firm_revenue_sources"]["owner_repair_spend"] = config["housing"]["owner_repair_spend"]

    for key in ("local_intermediate", "imports"):
        io["firm_input_split"][key] = _scale(io["firm_input_split"][key], scale)
    for key in ("tourist_spend", "external_interest_paid"):
        io["trade"][key] = _scale(io["trade"][key], scale)
    io["trade"]["central_grant"] = config["fiscal"]["central_grant"]

    for direction in ("imports", "exports"):
        for line in io["trade"][direction]:
            if "value" in line:
                line["value"] = _scale(line["value"], scale)
            if "qty" in line:
                line["qty"] = _scale(line["qty"], scale)
    return io


def _scale_labour(config, labour: dict, scale: float) -> dict:
    """收入层按实际人口重写：三层人数守恒，层间比例与参考城一致。"""
    labor = deepcopy(LABOR_CONSUMPTION)
    reference_bands = LABOR_CONSUMPTION["income_bands"]

    band_population = _spread(labour["population"], [b["population"] for b in reference_bands])
    band_households = _spread(labour["households"], [b["households"] for b in reference_bands])
    band_employed = _spread(labour["employed"], [b["employed"] for b in reference_bands])
    band_unemployed = _spread(labour["unemployed"], [b["unemployed"] for b in reference_bands])

    for index, band in enumerate(labor["income_bands"]):
        band["population"] = band_population[index]
        band["households"] = band_households[index]
        band["employed"] = band_employed[index]
        band["unemployed"] = band_unemployed[index]
        band["not_in_labour_force"] = max(
            0, band["population"] - band["employed"] - band["unemployed"])
        band["wage_income"] = _scale(band["wage_income"], scale)
        band["asset_income"] = _scale(band["asset_income"], scale)

    labor["consumption"]["month_1_reference_total"] = _scale(
        LABOR_CONSUMPTION["consumption"]["month_1_reference_total"], scale)
    labor["labour_market"].update({
        "private_post_total": sum(s["employees"] for s in config["firms"]["sectors"]),
        "public_post_total": config["public_sector"]["employees"],
        "employed_total": labour["employed"], "unemployed_total": labour["unemployed"]})
    return labor


def _opening_balances(world, config) -> dict:
    """期初余额。全部以**分**计价：老引擎的钱本来就是分，再乘一次就凭空放大 100 倍。"""
    firms = []
    for sector in config["firms"]["sectors"]:
        monthly_fixed = (sector["employees"] * sector["wage_per_employee"]
                         + sector["commercial_rent"] + sector["fees"])
        firms.append({"sector": sector["id"], "opening_cash": monthly_fixed * 2})
    return {
        "city_government": world.cash,
        "church": cr_to_cents(_scale(REFERENCE_CITY["opening_cash"]["church"],
                                     config["_scale"])),
        "households_total": world.resident_funds,
        "firms": firms,
    }


def build_live_config(world) -> dict:
    """生成新引擎所需的四份配置：参考配置、投入产出表、收入层、期初余额。"""
    labour = labour_structure(world)
    scale = labour["population"] / REFERENCE_POPULATION
    housing = housing_structure(world)

    private_total = max(len(REFERENCE_CITY["firms"]["sectors"]),
                        round(labour["employed"] * REFERENCE_PRIVATE_SHARE))
    public_total = max(1, labour["employed"] - private_total)

    config = deepcopy(REFERENCE_CITY)
    _scale_demographics(config, labour)
    _scale_sectors(config, private_total)
    _scale_public_sector(config, public_total)
    _scale_housing(config, housing, scale)
    _scale_fiscal(config, scale)
    config["_scale"] = scale

    return {"config": config,
            "input_output": _scale_input_output(config, scale),
            "labor": _scale_labour(config, labour, scale),
            "opening_balances": _opening_balances(world, config),
            "labour": labour, "housing": housing, "scale": scale}


def cohort_state(registry) -> list[dict]:
    """导出各收入层的上期可支配收入，供下个月重建登记表时接续。

    收入层是聚合统计对象，每次月结都要按当前人口重新生成；
    但「上期赚了多少」是跨月的反馈链，丢了消费就会归零。
    """
    return [{"band_id": cohort.band_id,
             "last_disposable_cents": cohort.last_disposable_cents}
            for cohort in registry]


def restore_cohort_state(registry, states) -> None:
    """把导出的上期可支配收入接回新建的收入层；层不存在或结构变了就跳过。"""
    known = {cohort.band_id: cohort for cohort in registry}
    for state in states or ():
        cohort = known.get(state.get("band_id"))
        if cohort is not None:
            cohort.last_disposable_cents = int(state.get("last_disposable_cents", 0))


def live_city(world, book=None, cohorts_state=None) -> ReferenceCity:
    """按当前世界状态装配一个可跑月结的新引擎城市。

    opening_balances 一律按分传入；book 传入时不会重开账户，存档余额原样沿用。
    """
    bundle = build_live_config(world)
    city = ReferenceCity(city_id="live_city", config=bundle["config"], book=book,
                         input_output=bundle["input_output"], labor=bundle["labor"],
                         opening_balances=bundle["opening_balances"],
                         opening_in_cents=True)
    restore_cohort_state(city.household_registry, cohorts_state)
    return city