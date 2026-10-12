"""单机世界服务；原子命令在副本成功后替换当前世界。"""
from copy import deepcopy
import logging
from pathlib import Path

from backend.config import BALANCE, CATALOG, SAVE_DIR, TEMPLATE
from backend.core.errors import GameError
from backend.core.simulation import add_event, advance_days
from backend.persistence.savegame import load_world, save_world
from backend.systems.construction import cancel_blueprints, commit_construction, place_blueprint
from backend.systems.generator import new_world
from backend.systems.population import city_capacity, city_jobs

logger = logging.getLogger(__name__)


class GameService:
    def __init__(self, save_dir: Path = SAVE_DIR):
        self.world = new_world(BALANCE["starting_seed"])
        self.save_dir = save_dir
        self.seen_requests = set()
        self.clock_epoch = 0

    def snapshot(self):
        world = self.world
        completed = [b for b in world.buildings if b.status == "completed"]
        blueprints = [b for b in world.buildings if b.status == "blueprint"]
        revenue = world.population * BALANCE["tax_per_resident"]
        upkeep = sum(CATALOG[b.definition_id]["maintenance"] for b in completed)
        return {"world":world.to_dict(),"catalog":list(CATALOG.values()),
                "city":{"name":TEMPLATE["name"],"subtitle":TEMPLATE["subtitle"]},
                "calendar":{"days_per_month":BALANCE["days_per_month"],"months_per_year":BALANCE["months_per_year"]},
                "summary":{"capacity":city_capacity(world),"jobs":city_jobs(world),
                           "revenue":revenue,"upkeep":upkeep,"net":revenue-upkeep,
                           "blueprint_cost":sum(CATALOG[b.definition_id]["cost"] for b in blueprints),
                           "blueprints":len(blueprints),"construction":sum(b.status in ("queued","constructing") for b in world.buildings)},
                "save_exists":(self.save_dir / "slot1.json").exists()}

    def command(self, command):
        token = command.get("request_id")
        if token and token in self.seen_requests:
            return self.snapshot()
        world = deepcopy(self.world)
        kind = command["type"]
        if kind == "place_blueprint":
            place_blueprint(world, command.get("definition_id"), command.get("x"), command.get("y"), command.get("rotation",0))
        elif kind == "commit":
            count = sum(b.status == "blueprint" for b in world.buildings)
            commit_construction(world)
            add_event(world, "工程已批准", "%d 座建筑进入施工队列。" % count)
        elif kind == "cancel":
            cancel_blueprints(world)
        elif kind == "pause":
            if type(command.get("paused")) is not bool:
                raise GameError("暂停参数无效。")
            world.paused = command["paused"]
        elif kind == "speed":
            if command.get("speed") not in (0.5, 1, 2):
                raise GameError("只支持 0.5、1、2 倍速。")
            world.speed = command["speed"]
        elif kind == "advance_month":
            if not world.paused:
                raise GameError("请先暂停，再手动推进一个月。")
            advance_days(world, BALANCE["days_per_month"], manual=True)
        elif kind == "save":
            save_world(world, self.save_dir)
            logger.info("City saved: day=%d, buildings=%d", world.day, len(world.buildings))
            return self.snapshot()
        elif kind == "load":
            world = load_world(self.save_dir)
            logger.info("City loaded: day=%d, buildings=%d", world.day, len(world.buildings))
        elif kind == "new_game":
            seed = command.get("seed", BALANCE["starting_seed"])
            if type(seed) is not int or not 0 <= seed <= 2147483647:
                raise GameError("种子须为 0—2147483647 的整数。")
            world = new_world(seed)
        else:
            raise GameError("不支持的操作。")
        if kind not in ("load", "new_game"):
            world.revision += 1
        if kind in ("load", "new_game") or (world.paused, world.speed) != (self.world.paused, self.world.speed):
            self.clock_epoch += 1
        self.world = world
        if token:
            self.seen_requests.add(token)
            if len(self.seen_requests) > 512:
                self.seen_requests = {token}
        return self.snapshot()
