"""经济模型参数与经济指标。

这一层是「参数」与「代码」的边界：
参数全部来自 ``data/economy/model_parameters_v1.json``，代码里不写死任何可调数值。
GUI 调参工具改的就是这份参数的副本，跑的是同一套模型。

为什么需要它
------------
此前数值分散在四五个配置文件里（参考城、投入产出表、劳动力、平衡表），
要改一个税率得翻三处文件，且没有任何范围校验——填错一个 8.5 引擎照跑不误。

这里把散落的参数收敛成一份带**取值范围、单位、设计稿出处**的参数表，
载入时逐项校验，越界直接报错并说明设计稿允许的范围。
"""
from copy import deepcopy
from dataclasses import dataclass
import math
from typing import Any

from backend.config import ROOT, read_json
from backend.core.errors import GameError

PARAMETER_FILE = ROOT / "data/economy/model_parameters_v1.json"
_RAW = read_json(PARAMETER_FILE)

#: 参数表中带 ``*_range`` 的键，载入时按范围校验
RANGE_SUFFIX = "_range"
# 组内声明「必须取整」的参数清单，见 model_parameters_v1.json 的 _integer_keys
INTEGER_SUFFIX = "_integer_keys"
# 组内声明「中文显示名」，见 model_parameters_v1.json 的 _labels
LABEL_SUFFIX = "_labels"


def _floor(value: Any) -> Any:
    """整数参数的有效值：向下取整。

    向下取整而不是四舍五入——拖出 2999.9 时四舍五入会变成 3000，
    等于凭空给城市多生 0.1 个人；向下取整只会少，不会凭空多。
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return value
    return int(math.floor(value))


def _path_of(key_path: str) -> Any:
    """按 ``a.b.c`` 取嵌套值；取不到就报错，不返回 None 让上层误用。"""
    node: Any = _RAW
    for part in key_path.split("."):
        if not isinstance(node, dict) or part not in node:
            raise GameError(f"参数 {key_path} 不存在，请检查参数表。")
        node = node[part]
    return node


@dataclass(frozen=True)
class ParameterMeta:
    """一个参数的元信息：取值、允许范围、单位、整数性、出处。

    ``integral`` 表示这个参数的业务值**必须取整**：人数、户数、套房数、
    企业数，以及金额（CR 是最小货币单位，1 CR = 100 分）。
    滑块按像素取整时可能拖出 3000.37 这种值，
    对「总人口」来说它没有业务含义，必须折算成有效值再进入模型。
    """

    key: str
    value: Any
    low: float | None
    high: float | None
    unit: str
    integral: bool = False
    label: str = ""          # 中文显示名；界面显示这个，代码里仍用 key

    @property
    def display_name(self) -> str:
        """界面显示名。没配中文名时退回 key，不让界面开天窗。"""
        return self.label or self.key.split(".", 1)[-1]

    @property
    def has_range(self) -> bool:
        return self.low is not None and self.high is not None

    @property
    def effective(self) -> Any:
        """当前取值的有效值（整数参数向下取整）。"""
        return self.effective_of(self.value)

    def effective_of(self, raw: Any) -> Any:
        """把任意取值折算成有效值。

        整数参数一律**向下取整**，而不是四舍五入：
        拖出 3000.37 个人时，按 3000 人开算才不会凭空多出 0.37 个居民；
        四舍五入到 3000 也对，但拖出 2999.9 时会变成 3000，
        比实际凭空多出 0.1 个人——向上取整会让人口凭空增长。
        """
        if not self.integral or not isinstance(raw, (int, float)):
            return raw
        if isinstance(raw, bool):
            return raw
        return int(math.floor(raw))

    def clamp(self) -> Any:
        """把值夹回允许范围。GUI 拖过头时用这个兜底。"""
        if not self.has_range or not isinstance(self.value, (int, float)):
            return self.value
        return max(self.low, min(self.high, self.value))

    def validate(self) -> None:
        """越界直接报错。宁可启动失败，也不要静默跑出一个假城市。"""
        if not self.has_range:
            return
        if not isinstance(self.value, (int, float)):
            return
        if not (self.low <= self.value <= self.high):
            raise GameError(
                f"参数 {self.key} = {self.value} 超出允许范围 "
                f"[{self.low}, {self.high}]。请按设计稿的取值域调整。")

    def to_dict(self) -> dict[str, Any]:
        return {"key": self.key, "value": self.value, "low": self.low,
                "high": self.high, "unit": self.unit, "integral": self.integral,
                "label": self.label}


def _unit_of(group: str, key: str) -> str:
    """从组名推断单位。金额是 CR，人数是「人」，比率无量纲。

    注意有例外：``market.choice_beta_price`` 是「价格敏感系数」，
    它只是名字里带 price ���个系数，不是金额。
    早先按 ``_price`` 后缀判成 CR，界面上一个系数旁边写着「CR·整」，
    会让人以为拖它会改成交价。所以系数类单独走一条规则。
    """
    if key.startswith(("choice_beta_", "wage_reserve_alpha_")):
        return "系数"
    if key.endswith(("_rate", "_ratio", "_share", "_efficiency", "_cap_rate",
                     "_multiplier", "_strength", "_k", "_beta_", "_mean",
                     "_floor", "_exponent", "_intensity", "_index",
                     "_adjustment", "_window", "_pp")):
        return "比例"
    if key.endswith(("_cents", "_price", "_cash", "_debt", "_value", "_income",
                     "_revenue", "_spend", "_rent", "_transfer", "_payout",
                     "_capex", "_welfare", "_grant", "_fees", "_interest",
                     "_deposit", "_wage", "_cost", "_base", "_total")):
        return "CR"
    if key.endswith(("_months", "_month", "_people", "_staff", "_seats", "_firms",
                     "_households", "_units", "_ticks", "_scale", "_points",
                     "_years", "_policy")):
        return "个"
    if key.endswith(("_days", "_years", "_seconds")):
        return "天"
    return ""


class EconomyParameters:
    """一份可改的参数集。

    用法::

        params = EconomyParameters()
        params.set("tax.income_flat_rate", 0.12)      # 调税率
        params.set("city.start_cash", 500000)          # 调初始资金
        city = build_reference_city(params)            # 按参数装配城市
    """

    def __init__(self, overrides: dict | None = None):
        self._values = deepcopy(_RAW)
        self._original = deepcopy(_RAW)
        self._overrides = dict(overrides or {})
        for key, value in self._overrides.items():
            self.set(key, value, validate=False)
        self.validate_all()

    # --- 读取 ---------------------------------------------------------

    def get(self, key: str, default: Any = None) -> Any:
        node: Any = self._values
        for part in key.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def section(self, name: str) -> dict:
        value = self.get(name)
        if not isinstance(value, dict):
            raise GameError(f"参数分组 {name} 不存在或不是字典。")
        return value

    # --- 修改 ---------------------------------------------------------

    def set(self, key: str, value: Any, validate: bool = True) -> None:
        """写入一个参数并（可选）校验范围。

        整数参数会先折算成**有效值**再写入：传 3000.37 人进来，
        存进去的是 3000。这样模型里永远不会出现半个人，
        界面显示的值和实际生效的值也必然一致。
        """
        parts = key.split(".")
        node = self._values
        for part in parts[:-1]:
            if not isinstance(node.get(part), dict):
                raise GameError(f"参数 {key} 的上级 {part} 不是分组。")
            node = node[part]
        if parts[-1] not in node:
            raise GameError(f"参数 {key} 不存在，无法设置。")
        group = parts[0]
        if len(parts) == 2:
            integer_keys = node.get(INTEGER_SUFFIX)
            if isinstance(integer_keys, list) and parts[-1] in integer_keys:
                value = _floor(value)
        node[parts[-1]] = value
        if validate:
            self._validate_key(key)

    def reset(self) -> None:
        """恢复出厂参数。GUI 的「重置」按钮走这里。"""
        self._values = deepcopy(self._original)

    def reset_key(self, key: str) -> None:
        """只恢复一个参数，便于逐项对比。"""
        original = self._original
        for part in key.split("."):
            original = original[part]
        self.set(key, original)

    # --- 校验 ---------------------------------------------------------

    def _validate_key(self, key: str) -> None:
        parts = key.split(".")
        group = parts[0]
        name = parts[-1]
        # 范围写在同组的 ``name_range`` 上
        if not isinstance(self._values.get(group), dict):
            return
        range_value = self._values[group].get(name + RANGE_SUFFIX)
        if not isinstance(range_value, list) or len(range_value) != 2:
            return
        value = self._values[group][name]
        if not isinstance(value, (int, float)):
            return
        low, high = range_value
        if not (low <= value <= high):
            raise GameError(
                f"参数 {key} = {value} 超出设计稿允许范围 [{low}, {high}]。")

    def validate_all(self) -> None:
        """逐组校验所有带范围的参数。"""
        for group, values in self._values.items():
            if not isinstance(values, dict):
                continue
            for name, value in values.items():
                if name.endswith(RANGE_SUFFIX):
                    continue
                range_value = values.get(name + RANGE_SUFFIX)
                if not isinstance(range_value, list) or len(range_value) != 2:
                    continue
                if not isinstance(value, (int, float)):
                    continue
                low, high = range_value
                if not (low <= value <= high):
                    raise GameError(
                        f"参数 {group}.{name} = {value} 超出设计稿允许范围 "
                        f"[{low}, {high}]。")

    def validate_identity(self) -> None:
        """人口恒等式（设计稿 TEST-01）：就业＋失业＋非劳动力＝总人口。"""
        demo = self.section("population")
        total = (demo["employed"] + demo["unemployed"] + demo["out_of_labour_force"])
        if total != demo["start"]:
            raise GameError(
                f"人口恒等式不成立：{demo['employed']}+{demo['unemployed']}"
                f"+{demo['out_of_labour_force']} = {total}，应为 {demo['start']}。")

    def validate_resource_balance(self) -> None:
        """战略品期末库存必须非负（设计稿 TEST-19）。"""
        for item in self.section("resources")["items"]:
            closing = (item["opening"] + item["produced"] + item["imported"]
                       - item["used"] - item["exported"])
            if closing < 0:
                raise GameError(
                    f"战略品 {item['name']} 期末库存为 {closing}，不得为负。"
                    f"请调整产出、进口或用量。")

    def validate_all_deep(self) -> None:
        """范围 + 恒等式 + 库存，一次性全查。"""
        self.validate_all()
        self.validate_identity()
        self.validate_resource_balance()

    # --- 元信息（GUI 用）----------------------------------------------

    def adjustable(self) -> list[ParameterMeta]:
        """列出所有可调参数及其范围。GUI 的滑块就是按这个生成的。

        顺序按各组 ``_labels`` 的**声明顺序**，也就是业务重要性：
        人口组先显示总人口、再显示就业，而不是按英文 key 字母序
        把 employed 顶到第一位、start 挤在中间。名字一翻成中文，
        字母序就更显得莫名其妙了。
        """
        metas: list[ParameterMeta] = []
        for group, values in self._values.items():
            if not isinstance(values, dict) or group in ("config_id", "description", "source"):
                continue
            integer_keys = values.get(INTEGER_SUFFIX)
            integer_keys = set(integer_keys) if isinstance(integer_keys, list) else set()
            labels = values.get(LABEL_SUFFIX)
            labels = labels if isinstance(labels, dict) else {}
            listed = set()

            def build(name, value):
                range_value = values.get(name + RANGE_SUFFIX)
                low = high = None
                if isinstance(range_value, list) and len(range_value) == 2:
                    low, high = range_value
                return ParameterMeta(key=f"{group}.{name}", value=value,
                                     low=low, high=high,
                                     unit=_unit_of(group, name),
                                     integral=name in integer_keys,
                                     label=labels.get(name, ""))

            for name in labels:
                value = values.get(name)
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    metas.append(build(name, value))
                    listed.add(name)
            # 兜底：万一有参数漏配中文名，也不至于从界面上消失
            for name, value in values.items():
                if name in listed or name.startswith("_") \
                        or name.endswith(RANGE_SUFFIX) \
                        or name.endswith(("_note", "_notes")):
                    continue
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    metas.append(build(name, value))
        return metas

    def meta(self, key: str) -> ParameterMeta:
        """按 key 取单个参数的元信息（含整数性）。"""
        for item in self.adjustable():
            if item.key == key:
                return item
        raise GameError(f"参数 {key} 不是可调参数。")

    def effective_of(self, key: str, raw: Any) -> Any:
        """把某个取值折算成该参数的有效值（整数参数向下取整）。"""
        try:
            return self.meta(key).effective_of(raw)
        except GameError:
            return raw

    def to_dict(self) -> dict:
        return deepcopy(self._values)

    def changed(self) -> dict[str, Any]:
        """列出相对出厂参数被改过的项，GUI 用它标黄。"""
        flat_original = _flatten(self._original)
        flat_current = _flatten(self._values)
        return {k: flat_current[k] for k in flat_current
                if k in flat_original and flat_original[k] != flat_current[k]}


def _flatten(node: Any, prefix: str = "") -> dict[str, Any]:
    """把嵌套字典压平成 ``a.b.c`` 形式，便于比较差异。"""
    out: dict[str, Any] = {}
    if isinstance(node, dict):
        for key, value in node.items():
            out.update(_flatten(value, f"{prefix}{key}."))
    elif isinstance(node, list):
        out[prefix[:-1]] = repr(node)
    else:
        out[prefix[:-1]] = node
    return out


DEFAULT_PARAMETERS = EconomyParameters()

__all__ = ["DEFAULT_PARAMETERS", "EconomyParameters", "ParameterMeta", "PARAMETER_FILE"]