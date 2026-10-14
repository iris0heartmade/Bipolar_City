"""单一固定日步长；渲染帧率不影响经营结算。

月结走 P01—P03 的账户清算引擎（DEC-12）：每月第 30 天唯一结算一次，
居民的钱来自企业与市政的真实转付，不再有「按人口自动加钱」。
日步长只负责推动施工这类不产生经济流量的进度。
"""
from math import ceil

from backend.config import BALANCE
from backend.core.errors import GameError
from backend.core.monthly import simulate_month
from backend.model.accounts import AccountBook
from backend.model.economy import ReferenceCity
from backend.model.transactions import (CONSTRUCTION, HOUSING_SALE, MIGRATION_IN,
                                       MIGRATION_OUT, Transaction)
from backend.systems.commerce import update_merchants
from backend.systems.construction import advance_construction
from backend.systems.housing import assign_occupancy, sell_homes
from backend.systems.live_economy import cohort_state, live_city
from backend.systems.population import city_capacity, migrate


def add_event(world, title, text):
    world.events.append({"day":world.day, "title":title, "text":text})
    world.events = world.events[-BALANCE["event_limit"]:]


def economy_month(world) -> int:
    """当前世界所处的经济月份。第 30 天完成第一次月结，所以先用整除。"""
    return world.day // BALANCE["days_per_month"]


def housing_sales(sales, month: int) -> list[Transaction]:
    """把住宅成交明细变成账户间交易：家庭付款给市政，产权随之转移。

    每栋楼一笔，来源稳定可追溯，因此同一次成交重复提交也不会收两次钱。
    """
    return [Transaction(from_account=ReferenceCity.HOUSEHOLDS,
                        to_account=ReferenceCity.GOVERNMENT,
                        amount_cents=sale["payment_cents"], category=HOUSING_SALE,
                        source_ref=f"housing-sale-{sale['building_id']}", month=month)
            for sale in sales if sale["payment_cents"] > 0]


def construction_transaction(world, month: int) -> list[Transaction]:
    """本月施工款：市政账户付给施工方。

    施工方在本城账户体系里还没有实体（P05 逐店拆分时才建），
    所以收款方记为城市边界账户——钱确实出去了，只是暂时记在世界其余部分。
    """
    amount = world.pending_construction_cents
    return [Transaction(from_account=ReferenceCity.GOVERNMENT,
                        to_account=ReferenceCity.OUTSIDE, amount_cents=amount,
                        category=CONSTRUCTION, source_ref="construction", month=month)] \
        if amount > 0 else []


def migration_transactions(delta: int, month: int) -> list[Transaction]:
    """本月人口变动的资金往来：迁入的人自带积蓄，迁出的人带走积蓄。

    人口是**人数**，钱是**现金**，两者必须各自说清来历。
    以前migrate() 只改 world.population 一个数字，于是新迁入的居民凭空出现、
    迁出的居民凭空消失——人数动了，钱却一动不动，全城现金口径与人口口径从此对不上。

    规则：每人按 resident_savings_per_person 计价（与开局储蓄同一口径）。
    - 迁入：外部世界付钱给家庭账户（MIGRATION_IN），钱有出处。
    - 迁出：家庭账户付钱给外部世界（MIGRATION_OUT），钱有去处，且受实际现金约束。
    两条都是真实收付，因此全城现金总量不变，只是换了个主人。
    """
    if delta == 0:
        return []
    amount = abs(delta) * BALANCE["resident_savings_per_person"]
    if delta > 0:
        return [Transaction(from_account=ReferenceCity.OUTSIDE,
                            to_account=ReferenceCity.HOUSEHOLDS,
                            amount_cents=amount, category=MIGRATION_IN,
                            source_ref="migration-in", month=month)]
    return [Transaction(from_account=ReferenceCity.HOUSEHOLDS,
                        to_account=ReferenceCity.OUTSIDE,
                        amount_cents=amount, category=MIGRATION_OUT,
                        source_ref="migration-out", month=month)]


def run_economy_settlement(world, month: int, sales, migration_delta: int = 0) -> dict:
    """跑一次真实收付的月结，把账户余额写回世界。

    幂等由 economy_month 保证：同一个月只结算一次，读档重放不会再付第二遍钱。
    """
    if world.economy_month >= month:
        raise GameError("本月经济结算已经完成，请等待下一个月。")

    book = AccountBook.from_dict(world.economy["accounts"]) if world.economy else None
    city = live_city(world, book=book,
                     cohorts_state=world.economy.get("cohorts") if world.economy else None)
    extra = (housing_sales(sales, month)
             + construction_transaction(world, month)
             + migration_transactions(migration_delta, month))
    report = simulate_month(city, month=month, extra_transactions=extra)

    world.economy = {"accounts": city.book.to_dict(), "month": month,
                     "cohorts": cohort_state(city.household_registry),
                     "report": report.to_dict()}
    world.economy_month = month
    world.pending_construction_cents = 0
    # 旧字段降级为展示口径：市政现金与家庭可用资金，不再参与任何结算
    world.cash = city.book[ReferenceCity.GOVERNMENT].cash_cents
    world.resident_funds = (city.book[ReferenceCity.HOUSEHOLDS].cash_cents
                            + city.book[ReferenceCity.LANDLORDS].cash_cents)
    return report


def advance_days(world, days, manual=False):
    if world.paused and not manual:
        return
    for _ in range(days):
        world.day += 1
        completed = advance_construction(world)
        if completed:
            add_event(world, "新建筑落成", "、".join(completed) + "已竣工。")
        if world.day % BALANCE["days_per_month"] == 0:
            entries, exits = update_merchants(world)
            delta = migrate(world)
            world.buyer_demand += ceil(max(0, delta) / BALANCE["household_size"])
            sold, sales_revenue, sales = sell_homes(world)
            assign_occupancy(world)
            report = run_economy_settlement(world, economy_month(world), sales, delta)
            world.history.append({
                "day":world.day, "population":world.population, "cash":world.cash,
                "migration":delta, "revenue":report.municipal_income_cents,
                "upkeep":report.municipal_spending_cents,
                "net":report.municipal_surplus_cents,
                "housing_sales":sold, "sales_revenue":sales_revenue,
                "public_rent":report.property_tax_cents,
                "business_tax":report.profit_tax_cents,
                "commercial_rent":report.municipal_spending_cents})
            world.history = world.history[-BALANCE["history_limit"]:]
            add_event(world, "城市月报",
                      "人口变化 %+d 人，市政结余 %.2f CR，家庭净增 %.2f CR。" % (
                          delta, report.municipal_surplus_cents / 100,
                          report.resident_cash_change_cents / 100))
            if sold:
                add_event(world, "住宅印契已交付",
                          "%d 户住宅成交，回笼 %.2f CR；私人部分停止市政收租与维护。" % (
                              sold, sales_revenue / 100))
            if entries or exits:
                add_event(world, "集市商户变动",
                          "入驻 %d 家，退出 %d 家；岗位与地方税随实际经营变化。" % (entries, exits))
            if world.population >= city_capacity(world):
                add_event(world, "住房已满", "新增住房可接纳更多居民；请同时关注岗位。")
        world.revision += 1