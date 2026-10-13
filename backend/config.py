"""外部配置入口，不依赖执行时的工作目录。"""
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read_json(path: Path):
    # utf-8-sig 兼容 Windows 编辑器可能写入的 BOM，缺 BOM 时行为与 utf-8 相同。
    return json.loads(path.read_text(encoding="utf-8-sig"))


CATALOG = {item["id"]: item for item in read_json(ROOT / "data/buildings.json")}
BALANCE = read_json(ROOT / "data/balance.json")
TEMPLATE = read_json(ROOT / "data/templates/river_town.json")
SERVER = read_json(ROOT / "data/server.json")
SAVE_DIR = Path(os.environ.get("BIPOLE_SAVE_DIR", ROOT / "saves"))

# P00 经济参考场景：原始参数、投入产出流量与显式校准项分开存放。
ECONOMY_DIR = ROOT / "data/economy"
REFERENCE_CITY = read_json(ECONOMY_DIR / "reference_city_v1.json")
INPUT_OUTPUT = read_json(ECONOMY_DIR / "input_output_v1.json")
CALIBRATION = read_json(ECONOMY_DIR / "calibration_overrides.json")
CALENDAR = read_json(ECONOMY_DIR / "calendar_v1.json")
