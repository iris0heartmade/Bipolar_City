"""单一固定日步长；渲染帧率不影响经营结算。"""
from backend.config import BALANCE
from backend.systems.construction import advance_construction
from backend.systems.finance import monthly_finance
from backend.systems.population import city_capacity, migrate


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
            delta = migrate(world)
            revenue, upkeep = monthly_finance(world)
            world.history.append({"day":world.day,"population":world.population,"cash":world.cash,
                                  "migration":delta,"revenue":revenue,"upkeep":upkeep,"net":revenue-upkeep})
            world.history = world.history[-BALANCE["history_limit"]:]
            add_event(world, "城市月报", "人口变化 %+d 人，月度结余 %.2f 银镑。" % (delta,(revenue-upkeep)/100))
            if world.population >= city_capacity(world):
                add_event(world, "住房已满", "新增住房可接纳更多居民；请同时关注岗位。")
        world.revision += 1
