"""外部配置入口，不依赖执行时的工作目录。"""
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


CATALOG = {item["id"]: item for item in read_json(ROOT / "data/buildings.json")}
BALANCE = read_json(ROOT / "data/balance.json")
TEMPLATE = read_json(ROOT / "data/templates/river_town.json")
SERVER = read_json(ROOT / "data/server.json")
SAVE_DIR = Path(os.environ.get("BIPOLE_SAVE_DIR", ROOT / "saves"))
