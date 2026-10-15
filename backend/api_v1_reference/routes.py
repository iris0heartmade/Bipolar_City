import logging
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt

from backend.core.errors import GameError
from backend.systems.placement import validate_placement

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")


class Command(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["place_blueprint","commit","cancel","pause","speed","advance_month","save","load","new_game","set_housing_mode"]
    request_id: str | None = Field(default=None, max_length=80)
    definition_id: str | None = None
    x: StrictInt | None = None
    y: StrictInt | None = None
    rotation: StrictInt = 0
    paused: StrictBool | None = None
    speed: float | None = None
    seed: StrictInt | None = None
    housing_mode: Literal["public","for_sale"] | None = None
    building_id: StrictInt | None = None


@router.get("/state")
async def state(request: Request):
    async with request.app.state.lock:
        return request.app.state.game.snapshot()


@router.get("/placement")
async def placement(request: Request, definition_id: str, x: int, y: int, rotation: int = 0):
    async with request.app.state.lock:
        try:
            cells = validate_placement(request.app.state.game.world, definition_id, x, y, rotation)
            return {"valid":True,"cells":cells,"reason":"入口通路，地块可建。"}
        except GameError as exc:
            return {"valid":False,"cells":[],"reason":str(exc)}


@router.post("/commands")
async def commands(command: Command, request: Request):
    async with request.app.state.lock:
        try:
            return request.app.state.game.command(command.model_dump(exclude_none=True))
        except GameError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except OSError as exc:
            logger.error("Storage operation failed: type=%s, error=%s", command.type, exc)
            raise HTTPException(status_code=500, detail="无法读写存档，请检查 saves 目录权限和磁盘空间。") from exc
