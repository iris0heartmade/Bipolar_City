"""启动：python -m backend.app；仅监听本机，不用于公网部署。"""
import asyncio
from contextlib import asynccontextmanager
import logging
import os

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend.industry_api import IndustryGame, router as industry_router
from backend.game_api import router as game_router
from backend.world_api import router as world_router
from backend.config import ROOT, SERVER
from backend.logging_setup import setup_logging

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app):
    app.state.industry_game = IndustryGame()
    app.state.lock = asyncio.Lock()
    logger.info('BIPOLE 启动：地图游戏 + 行业经营共用 IndustryGame')
    try:
        yield
    finally:
        logger.info('BIPOLE 停止 month=%d', app.state.industry_game.city.month)


app = FastAPI(title="BIPOLE 城市纪事", version="2.1.2", lifespan=lifespan)
# 行业经营（原 industry.html 图表 webui）路由
app.include_router(industry_router)
# 地图游戏（index.html 等距地图 + 房屋建造视觉）路由
app.include_router(game_router)
# 世界地图（/world 独立页面 + 5 NPC 城市 + AI 市长 + 脱敏视图）路由
app.include_router(world_router)
app.mount("/static", StaticFiles(directory=ROOT / "frontend"), name="static")


@app.get("/", include_in_schema=False)
def index():
    """首页 = 真游戏（地图 + 房屋建造视觉，接入新经济内核）。"""
    return FileResponse(ROOT / "frontend/index.html")


@app.get('/industry', include_in_schema=False)
def industry():
    """行业经营子页面（图表 webui，阶段 14+15）。"""
    return FileResponse(ROOT / 'frontend/industry.html')


@app.get('/world', include_in_schema=False)
def world():
    """世界地图独立页面（5 NPC 城市节点 + 关系 + 脱敏视图）。"""
    return FileResponse(ROOT / 'frontend/world.html')


if __name__ == "__main__":
    import uvicorn
    setup_logging()
    port = int(os.environ.get("BIPOLE_PORT", SERVER["port"]))
    logger.info("BIPOLE starting at http://%s:%d", SERVER["host"], port)
    uvicorn.run(app, host=SERVER["host"], port=port, log_config=None)
