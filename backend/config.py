"""应用基础设施配置入口（不依赖执行时的工作目录）。

【V2.1 阶段 0，2026-10-15】移除 V1.0 经济 JSON 引用（reference_city_v1.json、
input_output_v1.json、calibration_overrides.json、calendar_v1.json、
labor_consumption_v1.json）。这些 JSON 已封存到 `data/economy_v1_reference/`，
V1.0 应用代码（`backend/*_v1_reference/`）如需引用应从封存目录加载。

V2.1 重建后新增经济配置会走 `data/economy_v2/*.json`（阶段 1 行业体系完成后开始）。
"""
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
