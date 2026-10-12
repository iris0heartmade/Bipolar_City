"""服务端唯一时间循环；改变暂停/倍率时清空未完成日的累积。"""
import asyncio
import logging
import time

from backend.config import BALANCE
from backend.core.simulation import advance_days

logger = logging.getLogger(__name__)


async def run_clock(service, lock):
    previous = time.monotonic()
    elapsed, health_at, started = 0.0, previous, previous
    mode = None
    logger.info("Simulation clock started: seconds_per_day=%s", BALANCE["seconds_per_day"])
    try:
        while True:
            await asyncio.sleep(0.25)
            now = time.monotonic()
            async with lock:
                world = service.world
                current_mode = (service.clock_epoch, world.paused, world.speed)
                # Only time-control changes and world restoration reset a partial day.
                if current_mode != mode:
                    elapsed = 0
                    mode = current_mode
                if not world.paused:
                    elapsed += (now-previous) * world.speed
                    days = int(elapsed / BALANCE["seconds_per_day"])
                    if days:
                        advance_days(world, days)
                        elapsed -= days * BALANCE["seconds_per_day"]
                if now-health_at >= 60:
                    logger.info("HEALTH | uptime=%.0fs | day=%d | buildings=%d | paused=%s",
                                now-started,world.day,len(world.buildings),world.paused)
                    health_at = now
            previous = now
    finally:
        logger.info("Simulation clock stopped: uptime=%.1fs, day=%d", time.monotonic()-started, service.world.day)
