"""启动：python -m backend.app；仅监听本机，不用于公网部署。"""
import asyncio
from contextlib import asynccontextmanager
import logging
import os

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend.industry_api import IndustryGame, router
from backend.config import ROOT, SERVER
from backend.logging_setup import setup_logging

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app):
    app.state.industry_game = IndustryGame()
    app.state.lock = asyncio.Lock()
    logger.info('行业经营服务启动，月份由玩家操作推进')
    try:
        yield
    finally:
        logger.info('行业经营服务停止 month=%d', app.state.industry_game.city.month)


app = FastAPI(title="BIPOLE 行业经营", version="2.1.1", lifespan=lifespan)
app.include_router(router)
app.mount("/static", StaticFiles(directory=ROOT / "frontend"), name="static")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(ROOT / "frontend/industry.html")


@app.get('/industry', include_in_schema=False)
def industry():
    return FileResponse(ROOT / 'frontend/industry.html')


if __name__ == "__main__":
    import uvicorn
    setup_logging()
    port = int(os.environ.get("BIPOLE_PORT", SERVER["port"]))
    logger.info("BIPOLE starting at http://%s:%d", SERVER["host"], port)
    uvicorn.run(app, host=SERVER["host"], port=port, log_config=None)
