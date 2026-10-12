import asyncio
from unittest.mock import patch

from backend.core.clock import run_clock
from backend.core.service import GameService


def test_normal_commands_do_not_reset_partial_day():
    service=GameService();service.command({"type":"pause","paused":False})
    now=[0.0]

    async def sleep(interval):
        now[0]+=interval
        if now[0]==4:
            service.command({"type":"cancel"})
        if now[0]>=6:
            raise asyncio.CancelledError

    async def scenario():
        with patch("backend.core.clock.asyncio.sleep",sleep),patch("backend.core.clock.time.monotonic",lambda:now[0]):
            try:await run_clock(service,asyncio.Lock())
            except asyncio.CancelledError:pass

    asyncio.run(scenario())
    assert service.world.day==1
