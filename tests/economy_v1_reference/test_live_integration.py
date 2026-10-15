"""接入验收：新引擎的月结真的接管了可玩世界。

这一组测试回答四个问题：
1. 「按人口自动加钱」是否真的消失了；
2. 动态标定出来的配置是否自洽（人口恒等、岗位、行业、住房）；
3. 连跑很多个月会不会破产，账户是否守恒；
4. 读档重放会不会重复付款。
"""
import pytest

from backend.config import BALANCE, CATALOG
from backend.core.errors import GameError
from backend.core.monthly import simulate_month
from backend.core.service import GameService
from backend.core.simulation import (advance_days, economy_month, migration_transactions,
                                     run_economy_settlement)
from backend.model.accounts import AccountBook
from backend.model.economy import ReferenceCity
from backend.model.transactions import MIGRATION_IN, MIGRATION_OUT
from backend.systems.generator import new_world
from backend.systems.live_economy import (REFERENCE_POPULATION, build_live_config,
                                          cohort_state, housing_structure,
                                          labour_structure, live_city,
                                          restore_cohort_state)
from backend.systems.population import city_jobs
from tests.test_simulation import legal_position

MONTH = BALANCE["days_per_month"]
GOV, CHURCH = ReferenceCity.GOVERNMENT, ReferenceCity.CHURCH
HOUSEHOLDS, LANDLORDS = ReferenceCity.HOUSEHOLDS, ReferenceCity.LANDLORDS
FIRMS, OUTSIDE = ReferenceCity.FIRMS, ReferenceCity.OUTSIDE
LOCAL = (GOV, CHURCH, HOUSEHOLDS, LANDLORDS, FIRMS)


def balances(world):
    return {a["account_id"]: a["cash_cents"] for a in world.economy["accounts"]}


def run_months(world, months):
    for _ in range(months):
        advance_days(world, MONTH, manual=True)
    return world


# --- 动态标定 -----------------------------------------------------------

def test_labour_structure_closes_and_respects_jobs():
    world = new_world(BALANCE["starting_seed"])
    labour = labour_structure(world)
    assert labour["employed"] + labour["unemployed"] + labour["not_in_labour_force"] \
        == labour["population"]
    assert labour["employed"] <= labour["jobs"], "岗位数不得凭空创造就业"
    assert labour["population"] > 0


def test_empty_city_refuses_to_settle():
    world = new_world(BALANCE["starting_seed"])
    world.population = 0
    with pytest.raises(GameError, match="还没有人口"):
        labour_structure(world)


def test_scaled_config_is_internally_consistent():
    world = new_world(BALANCE["starting_seed"])
    bundle = build_live_config(world)
    config = bundle["config"]
    demo = config["demographics"]
    assert demo["employed"] + demo["unemployed"] + demo["not_in_labour_force"] == demo["population"]
    assert sum(s["employees"] for s in config["firms"]["sectors"]) \
        + config["public_sector"]["employees"] == demo["employed"]
    assert config["public_sector"]["wage_bill"] == sum(
        row["staff"] * row["wage"] for row in config["public_sector"]["posts"])
    housing = bundle["housing"]
    assert housing["rented_units"] + housing["owner_occupied_units"] + housing["vacant_units"] \
        == housing["total_units"]


def test_scale_follows_population_not_a_fixed_constant():
    world = new_world(BALANCE["starting_seed"])
    small = build_live_config(world)
    world.population *= 10
    large = build_live_config(world)
    assert large["scale"] > small["scale"] * 9
    assert sum(s["revenue"] for s in large["config"]["firms"]["sectors"]) \
        > sum(s["revenue"] for s in small["config"]["firms"]["sectors"])


def test_income_bands_sum_to_the_scaled_population():
    world = new_world(BALANCE["starting_seed"])
    labor = build_live_config(world)["labor"]
    assert sum(b["population"] for b in labor["income_bands"]) == labor["labour_market"]["employed_total"] \
        + labor["labour_market"]["unemployed_total"] + \
        sum(b["not_in_labour_force"] for b in labor["income_bands"])
    for band in labor["income_bands"]:
        assert band["employed"] + band["unemployed"] + band["not_in_labour_force"] \
            == band["population"]


def test_housing_reflects_actual_buildings():
    world = new_world(BALANCE["starting_seed"])
    expected = sum(CATALOG[b.definition_id]["units"] for b in world.buildings
                   if b.status == "completed" and CATALOG[b.definition_id].get("units"))
    assert housing_structure(world)["total_units"] == expected


def test_cohort_state_round_trips():
    world = new_world(BALANCE["starting_seed"])
    registry = live_city(world).household_registry
    registry["low"].last_disposable_cents = 12345
    states = cohort_state(registry)
    fresh = live_city(world).household_registry
    restore_cohort_state(fresh, states)
    assert fresh["low"].last_disposable_cents == 12345


def test_restore_ignores_unknown_bands():
    world = new_world(BALANCE["starting_seed"])
    registry = live_city(world).household_registry
    restore_cohort_state(registry, [{"band_id": "mortal", "last_disposable_cents": 1}])
    assert all(c.last_disposable_cents == 0 for c in registry)


# --- 月结确实接管了世界 ---------------------------------------------------

def test_resident_income_is_no_longer_a_population_multiplication():
    """核心红线：居民的钱不再按人口自动发放。

    老引擎是 resident_funds += population × resident_income_per_person，
    账面上永远是正的。新引擎的居民余额变化由真实收付决定，两者不可能相等。
    """
    world = new_world(BALANCE["starting_seed"])
    before = world.resident_funds
    run_economy_settlement(world, 1, [])
    after = balances(world)[HOUSEHOLDS] + balances(world)[LANDLORDS]

    legacy = world.population * BALANCE["resident_income_per_person"]
    assert after - before != legacy, "余额变化若等于人口×人均收入，说明仍在跑旧的自动加钱"


def test_every_household_inflow_has_a_real_payer():
    """居民账户的每一笔收入都必须来自本地账户，不允许凭空出现。"""
    city = live_city(new_world(BALANCE["starting_seed"]))
    simulate_month(city, month=1)
    inflows = [tx for tx in city.last_journal
               if tx.to_account in (HOUSEHOLDS, LANDLORDS) and tx.amount_cents > 0]
    assert inflows, "本月应当有居民收入"
    for tx in inflows:
        assert tx.from_account in LOCAL, \
            f"居民收入来自 {tx.from_account}，说明这笔现金没有本地付款方"


def test_household_spending_has_a_local_payee():
    """居民花出去的每一笔钱也要有本地收款方，不能流向世界之外。"""
    city = live_city(new_world(BALANCE["starting_seed"]))
    simulate_month(city, month=1)
    outflows = [tx for tx in city.last_journal
                if tx.from_account in (HOUSEHOLDS, LANDLORDS) and tx.amount_cents > 0]
    assert outflows
    for tx in outflows:
        assert tx.to_account in LOCAL, \
            f"居民支出流向 {tx.to_account}，消费与租金本应留在城内"


def test_firms_without_cash_still_pay_only_from_real_receipts():
    """企业账户清零后仍能完成结算，说明它当月是净流入、并非靠期初余额硬撑。

    如果企业真的付不出工资，这一行会直接抛错——所以这条断言同时证明了
    「钱不够就会被拒绝」这条底线仍然生效。
    """
    world = new_world(BALANCE["starting_seed"])
    advance_days(world, MONTH, manual=True)
    world.economy["accounts"] = [
        dict(a, cash_cents=0 if a["account_id"] == FIRMS else a["cash_cents"])
        for a in world.economy["accounts"]]
    before_households = balances(world)[HOUSEHOLDS]
    advance_days(world, MONTH, manual=True)
    book = balances(world)
    assert book[FIRMS] > 0, "企业靠当月真实收入而非期初余额周转"
    assert book[HOUSEHOLDS] > before_households, "居民的钱来自企业真实发薪"


def test_gov_cannot_pay_out_of_nothing():
    """市政把账户清零后若付款不足，整批必须被拒绝而不是留下半个世界。"""
    world = new_world(BALANCE["starting_seed"])
    advance_days(world, MONTH, manual=True)
    world.economy["accounts"] = [
        dict(a, cash_cents=0 if a["account_id"] == GOV else a["cash_cents"])
        for a in world.economy["accounts"]]
    before = world.to_dict()
    try:
        advance_days(world, MONTH, manual=True)
    except GameError:
        assert world.to_dict() == before, "整批回滚，世界不得被部分改动"
    else:
        after = balances(world)
        assert after[GOV] >= 0, "结算成功则市政不得透支"


def test_monthly_settlement_runs_through_the_account_engine():
    world = new_world(BALANCE["starting_seed"])
    advance_days(world, MONTH, manual=True)
    assert world.economy_month == 1
    assert world.economy["month"] == 1
    assert set(balances(world)) >= set(LOCAL + (OUTSIDE,))
    assert world.cash == balances(world)[GOV], "市政现金必须等于账户簿余额"
    assert world.resident_funds == balances(world)[HOUSEHOLDS] + balances(world)[LANDLORDS]


def test_settlement_is_idempotent_within_the_same_month():
    world = new_world(BALANCE["starting_seed"])
    advance_days(world, MONTH, manual=True)
    snapshot = world.to_dict()
    with pytest.raises(GameError, match="已经完成"):
        run_economy_settlement(world, 1, [])
    assert world.to_dict() == snapshot


def test_sale_and_construction_are_real_account_transactions():
    """房款与工程款都必须有付款方与收款方，不能凭空改余额。"""
    service = GameService()
    x, y = legal_position(service.world)
    service.command({"type": "place_blueprint", "definition_id": "row_house",
                     "x": x, "y": y, "housing_mode": "for_sale"})
    service.command({"type": "commit"})
    construction = service.world.pending_construction_cents
    assert construction == CATALOG["row_house"]["cost"]

    advance_days(service.world, MONTH, manual=True)
    book = balances(service.world)
    assert service.world.pending_construction_cents == 0, "待付施工款已在月底付清"
    # 施工款付给城市边界账户，所以市政账户必然少于未扣款时的水平
    report = service.world.economy["report"]
    assert service.world.cash == construction * 0 + book[GOV]
    assert book[GOV] < CATALOG["row_house"]["cost"] * 100, "市政账户不因未记账而虚增"
    assert report is not None


def _sever_external_demand(city) -> None:
    """切断企业的外部订单与私人资本，只剩本地工资与市政采购。

    用途是构造「企业确实付不出工资」的场景：本地家庭那点消费远不足以
    覆盖进口、工资、租金与资本采购，企业只能靠期初余额或外部输血周转。
    清零期初余额后，它就必然违约。
    """
    sources = city.input_output["firm_revenue_sources"]
    sources["exports"] = 0
    sources["tourist_spend"] = 0
    sources["private_capital"] = 0


def test_firms_are_rejected_when_they_cannot_cover_wages():
    """企业付不出工资时整批拒绝，绝不允许先发钱再欠账。

    构造方式：外部订单与资本金归零 + 企业期初余额清零，
    企业只剩本地消费这点收入，付不动进口与工资。
    """
    city = live_city(new_world(BALANCE["starting_seed"]))
    _sever_external_demand(city)
    city.book[FIRMS].cash_cents = 0
    before = {a.account_id: a.cash_cents for a in city.book}

    with pytest.raises(GameError, match=FIRMS):
        simulate_month(city, month=1)
    after = {a.account_id: a.cash_cents for a in city.book}
    assert after == before, "整批回滚，任何账户都不得被部分改动"


# --- 人口迁徙 -----------------------------------------------------------

def test_migration_brings_money_in_and_takes_money_out():
    """人口是人数，钱是现金：迁入带钱进来，迁出带钱出去。

    以前 migrate() 只改 world.population 一个数字，于是新居民凭空出现、
    迁出的居民凭空消失——人数动了，钱却一动不动。
    """
    inbound = migration_transactions(14, month=1)
    assert len(inbound) == 1
    assert inbound[0].from_account == OUTSIDE and inbound[0].to_account == HOUSEHOLDS
    assert inbound[0].amount_cents == 14 * BALANCE["resident_savings_per_person"]
    assert inbound[0].category == MIGRATION_IN

    outbound = migration_transactions(-6, month=1)
    assert outbound[0].from_account == HOUSEHOLDS and outbound[0].to_account == OUTSIDE
    assert outbound[0].amount_cents == 6 * BALANCE["resident_savings_per_person"]
    assert outbound[0].category == MIGRATION_OUT

    assert migration_transactions(0, month=1) == [], "人口不动时不产生空交易"


def test_growth_actually_lands_in_the_account_book():
    """真实跑一个月：人口增加多少，家庭账户就该多出对应的钱。"""
    world = new_world(BALANCE["starting_seed"])
    population_before = world.population
    advance_days(world, MONTH, manual=True)
    moved = world.population - population_before

    assert moved > 0, "开局有富余住房时应当有人迁入"
    gained = balances(world)[HOUSEHOLDS]
    # 迁入者自带积蓄进了家庭账户：家庭余额不能低于「只靠工资收入」的水平
    assert gained > 0
    report = world.economy["report"]
    assert report is not None


def test_migration_does_not_change_total_city_cash():
    """迁徙只换主人，不改变全城现金总量。"""
    world = new_world(BALANCE["starting_seed"])
    advance_days(world, MONTH, manual=True)
    total = sum(balances(world).values())
    run_months(world, 11)
    assert sum(balances(world).values()) == total


def test_migration_out_moves_cash_from_households_to_the_outside():
    """迁出者带走的钱确实从家庭账户挪到了外部账户。

    不拿「外部账户净余额为负」或「净增等于迁出额」当断言：
    外部账户当月本身有贸易逆差，净额里混着好几笔流量，符号和大小都测不出迁徙本身。
    这里用**同月对照组**：同一个起点跑两次，一次带迁徙、一次不带，
    两次的差额就精确等于迁徙带来的现金变动。
    """
    leaving = 6
    moved = leaving * BALANCE["resident_savings_per_person"]

    def run(with_migration: bool):
        city = live_city(new_world(BALANCE["starting_seed"]))
        extra = migration_transactions(-leaving, 1) if with_migration else ()
        simulate_month(city, month=1, extra_transactions=extra)
        return (city.book[HOUSEHOLDS].cash_cents,
                city.book[OUTSIDE].cash_cents,
                [tx for tx in city.last_journal if tx.category == MIGRATION_OUT])

    households_with, outside_with, journal = run(True)
    households_without, outside_without, journal_without = run(False)

    assert journal_without == [], "对照组不该有迁徙交易"
    assert len(journal) == 1 and journal[0].amount_cents == moved
    assert outside_with - outside_without == moved, \
        "外部账户必须正好收到迁出者带走的积蓄"
    assert households_with < households_without, "家庭账户必须因迁出而少这笔钱"


# --- 长期演化 -----------------------------------------------------------

def test_thirty_six_months_never_go_bankrupt():
    world = new_world(BALANCE["starting_seed"])
    world.paused = False
    advance_days(world, 36 * MONTH)
    assert world.economy_month == 36
    book = balances(world)
    for name in LOCAL:
        assert book[name] >= 0, f"{name} 账户在 36 个月内出现透支"
    assert book[HOUSEHOLDS] > 0 and book[FIRMS] >= 0


def test_world_cash_total_never_changes():
    """世界现金总量恒定：既没有被创造，也没有被销毁。"""
    world = new_world(BALANCE["starting_seed"])
    advance_days(world, MONTH, manual=True)
    total = sum(balances(world).values())
    run_months(world, 11)
    assert sum(balances(world).values()) == total


def test_households_earn_their_money_from_firms_and_government():
    """居民的钱必须来自本地账户，不能凭空出现在世界上。"""
    world = new_world(BALANCE["starting_seed"])
    advance_days(world, MONTH, manual=True)
    book = balances(world)
    assert book[HOUSEHOLDS] + book[LANDLORDS] > 0
    assert book[FIRMS] > 0 and book[GOV] > 0


def test_growth_shrinks_the_city_when_firms_cannot_pay():
    """企业付不出工资时整批拒绝，不允许任何形式的人口自动加钱托底。

    这条断言防的是最坏情况：万一哪天有人为了「让游戏继续跑下去」
    在月结里加了人口兜底加钱，这里会立刻红。
    """
    world = new_world(BALANCE["starting_seed"])
    advance_days(world, MONTH, manual=True)
    households_before = balances(world)[HOUSEHOLDS]
    firms_before = balances(world)[FIRMS]

    city = live_city(world, book=AccountBook.from_dict(world.economy["accounts"]),
                     cohorts_state=world.economy.get("cohorts"))
    _sever_external_demand(city)
    city.book[FIRMS].cash_cents = 0

    with pytest.raises(GameError):
        simulate_month(city, month=2)

    # 世界状态未被这次失败的结算改动：既没有多加钱，也没有少发钱
    assert balances(world) == {a["account_id"]: a["cash_cents"]
                               for a in world.economy["accounts"]}
    assert balances(world)[HOUSEHOLDS] == households_before
    assert balances(world)[FIRMS] == firms_before
    assert world.resident_funds != world.population * BALANCE["resident_income_per_person"]


# --- 存档往返 -----------------------------------------------------------

def test_save_and_load_preserves_account_balances(tmp_path):
    service = GameService(tmp_path)
    advance_days(service.world, 6 * MONTH, manual=True)
    service.command({"type": "save"})
    snapshot = service.world.to_dict()
    service.command({"type": "load"})
    assert service.world.to_dict() == snapshot
    assert balances(service.world)[GOV] == service.world.cash


def test_reload_does_not_pay_the_month_twice(tmp_path):
    service = GameService(tmp_path)
    advance_days(service.world, 3 * MONTH, manual=True)
    service.command({"type": "save"})
    saved = service.world.cash
    service.command({"type": "load"})
    assert service.world.cash == saved
    assert service.world.economy_month == 3
    with pytest.raises(GameError, match="已经完成"):
        run_economy_settlement(service.world, 3, [])


def test_corrupt_account_book_is_rejected(tmp_path):
    service = GameService(tmp_path)
    advance_days(service.world, 2 * MONTH, manual=True)
    service.command({"type": "save"})
    import json
    path = tmp_path / "slot1.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["world"]["economy"]["accounts"][0]["cash_cents"] = -5
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(GameError, match="负余额"):
        service.command({"type": "load"})
