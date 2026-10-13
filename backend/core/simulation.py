"""单一固定日步长；渲染帧率不影响经营结算。"""
from backend.config import BALANCE
from backend.systems.construction import advance_construction
from backend.systems.finance import monthly_finance
from backend.systems.population import city_capacity, migrate
from backend.systems.housing import assign_occupancy, sell_homes
from backend.systems.commerce import update_merchants
from math import ceil


def add_event(world, title, text):
    world.events.append({"day":world.day, "title":title, "text":text})
    world.events = world.events[-BALANCE["event_limit"]:]


def advance_days(world, days, manual=False):
    if world.paused and not manual:
        return
    for _ in range(days):
        world.day += 1
        completed = advance_construction(world)
        if completed:
            add_event(world, "新建筑落成", "、".join(completed) + "已竣工。")
        if world.day % BALANCE["days_per_month"] == 0:
            world.resident_funds += world.population * BALANCE["resident_income_per_person"]
            entries, exits = update_merchants(world)
            delta = migrate(world)
            world.buyer_demand += ceil(max(0, delta) / BALANCE["household_size"])
            sold, sales_revenue = sell_homes(world)
            assign_occupancy(world)
            finance = monthly_finance(world)
            revenue, upkeep = finance["revenue"] + sales_revenue, finance["upkeep"]
            world.history.append({"day":world.day,"population":world.population,"cash":world.cash,
                                  "migration":delta,"revenue":revenue,"upkeep":upkeep,"net":revenue-upkeep,
                                  "housing_sales":sold,"sales_revenue":sales_revenue,
                                  "public_rent":finance["public_rent"],"business_tax":finance["business_tax"],
                                  "commercial_rent":finance["commercial_rent"]})
            world.history = world.history[-BALANCE["history_limit"]:]
            add_event(world, "城市月报", "人口变化 %+d 人，月度结余 %.2f 银镑。" % (delta,(revenue-upkeep)/100))
            if sold:
                add_event(world, "住宅印契已交付", "%d 户住宅成交，回笼 %.2f 银镑；私人部分停止市政收租与维护。" % (sold,sales_revenue/100))
            if entries or exits:
                add_event(world, "集市商户变动", "入驻 %d 家，退出 %d 家；岗位与地方税随实际经营变化。" % (entries,exits))
            if world.population >= city_capacity(world):
                add_event(world, "住房已满", "新增住房可接纳更多居民；请同时关注岗位。")
        world.revision += 1
