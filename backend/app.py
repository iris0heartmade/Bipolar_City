"""启动：python -m backend.app；仅监听本机，不用于公网部署。"""
import asyncio
from contextlib import asynccontextmanager, suppress
import logging
import os

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend.api.routes import router
from backend.config import ROOT, SERVER
from backend.core.clock import run_clock
from backend.core.service import GameService
from backend.logging_setup import setup_logging

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app):
    app.state.game = GameService()
    app.state.lock = asyncio.Lock()
    task = asyncio.create_task(run_clock(app.state.game, app.state.lock))
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


app = FastAPI(title="BIPOLE 城市模拟", version="0.2.0", lifespan=lifespan)
app.include_router(router)
app.mount("/static", StaticFiles(directory=ROOT / "frontend"), name="static")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(ROOT / "frontend/index.html")


if __name__ == "__main__":
    import uvicorn
    setup_logging()
    port = int(os.environ.get("BIPOLE_PORT", SERVER["port"]))
    logger.info("BIPOLE starting at http://%s:%d", SERVER["host"], port)
    uvicorn.run(app, host=SERVER["host"], port=port, log_config=None)
