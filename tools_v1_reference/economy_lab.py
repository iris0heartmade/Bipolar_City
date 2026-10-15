"""经济模型调参实验室（GUI）。

这是给策划/数值用的工作台，不是游戏界面。启动方式::

    python -m tools.economy_lab          # 或双击 economy_lab.bat

三块面板
--------
左：参数面板。每一项都能改，带范围的自绘滑块，改过就标黄。
中：指标仪表盘。核心指标、结构诊断、风险预警三组，颜色区分健康度。
下：演化曲线。任意指标随月份的变化，配合参数调整立刻看效果。

设计原则
--------
1. **改参数 → 立刻看到后果**。拖动滑块的过程中就在重算，
   不用等松手，更不用再点一次「重新模拟」。重算做了防抖，
   拖得再快也不会把机器压死。
2. **有变化要看得见**。每次刷新都比对上一轮的指标值：
   升的显示 ↑、降的显示 ↓，箭头单独上色，不和健康度颜色打架。
3. **计算值与有效值分开**。人数、金额这类值业务上不能带小数，
   界面显示的是**向下取整后的有效值**，鼠标悬停可看到原始计算值。
4. **不许静默出错**。参数越界当场弹窗说明设计稿允许的范围，不默默改掉。
5. **零新增依赖**。只用标准库 tkinter 加已装的 matplotlib。
"""
from __future__ import annotations

import sys
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from backend.model.indicators import (CATEGORY_LABELS, BY_KEY, INDICATORS,
                                      Indicator)
from backend.model.parameters import EconomyParameters
from backend.systems.model_sim import EconomySimulation
from backend.config import ROOT, read_json
from backend.core.errors import GameError
from tools.lab_slider import Slider

try:
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    from matplotlib.figure import Figure

    import matplotlib
    # matplotlib 的字体名与系统字体注册名要对得上。
    # 本机可用的是「Microsoft YaHei」，写成「Microsoft YaHei UI」会退回
    # DejaVu Sans，中文全部显示成方框。
    _available = {f.name for f in matplotlib.font_manager.fontManager.ttflist}
    for _candidate in ("Microsoft YaHei", "SimHei", "Microsoft JhengHei",
                       "Noto Sans CJK SC", "WenQuanYi Zen Hei", "SimSun"):
        if _candidate in _available:
            matplotlib.rcParams["font.sans-serif"] = [_candidate, "DejaVu Sans"]
            break
    matplotlib.rcParams["axes.unicode_minus"] = False
    HAS_PLOT = True
except ImportError:                      # 没有 matplotlib 也能用，只是没有曲线
    HAS_PLOT = False

# 参数分组的中文名与展示顺序
#
# 这里只列**经济旋钮**。`simulation` 组（每月天数、每年月数、币值缩放、月结粒度）
# 是模型的结构性常量，不在这里出现——调它们不是「调经济」，而是换一套时间与量纲，
# 换了以后前后两轮结果根本不可比。它们仍然可以在配置里直接改。
GROUP_LABELS = [
    ("population", "人口"),
    ("housing", "住房"),
    ("labour", "劳动力与工资"),
    ("business", "企业经营"),
    ("capacity", "产能公式"),
    ("market", "市场与消费"),
    ("tax", "税收"),
    ("city", "市政与信贷"),
    ("education", "教育"),
    ("security", "魔法安全"),
    ("religion", "宗教"),
    ("trade", "对外贸易"),
    ("resources", "战略物资"),
    ("feedback", "经济反馈与压力条件"),
]
GROUP_ORDER = [g for g, _ in GROUP_LABELS]
GROUP_TITLES = dict(GROUP_LABELS)

# 曲线面板默认展示的指标
DEFAULT_CURVES = [
    ("population", "总人口", "#2b6cb0"),
    ("firm_sales", "企业营业额", "#2f855a"),
    ("fiscal_balance", "财政结余", "#c05621"),
    ("capacity_utilisation", "产能利用率", "#6b46c1"),
    ("municipal_cash", "市政现金", "#b7791f"),
]

# 配色
BG = "#eef2f7"
PANEL = "#ffffff"
TEXT_DARK = "#1a202c"
TEXT_MUTED = "#718096"
ACCENT = "#2b6cb0"
RISING = "#2f855a"        # 箭头：升
FALLING = "#c05621"       # 箭头：降
ARROW_IDLE = "#cbd5e0"    # 箭头：无变化
STATUS_COLORS = {"ok": TEXT_DARK, "warn": "#b7791f", "bad": "#c53030"}

# 拖动滑块后等多久再重算。太小会卡，太大就没「实时」的感觉。
RECOMPUTE_DELAY_MS = 220
LEFT_PANEL_WIDTH = 640


class Tooltip:
    """极简悬停提示。绑定到任意 ttk/ tk 控件上。"""

    def __init__(self, widget, text_fn):
        self.widget = widget
        self.text_fn = text_fn
        self._window = None
        self._after = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _event=None):
        self._cancel()
        self._after = self.widget.after(450, self._show)

    def _cancel(self):
        if self._after is not None:
            try:
                self.widget.after_cancel(self._after)
            except tk.TclError:
                pass
            self._after = None

    def _show(self):
        text = self.text_fn()
        if not text or self._window is not None:
            return
        x = self.widget.winfo_rootx() + 12
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        self._window = tk.Toplevel(self.widget)
        self._window.wm_overrideredirect(True)
        self._window.wm_geometry(f"+{x}+{y}")
        frame = tk.Frame(self._window, bg="#2d3748", bd=0)
        frame.pack()
        tk.Label(frame, text=text, bg="#2d3748", fg="#ffffff",
                 justify="left", font=("Microsoft YaHei", 8),
                 padx=8, pady=5).pack()

    def _hide(self, _event=None):
        self._cancel()
        if self._window is not None:
            self._window.destroy()
            self._window = None


class EconomyLab(tk.Tk):
    """调参实验室主窗口。"""

    def __init__(self):
        super().__init__()
        self.title("BIPOLE 经济模型调参实验室")
        self.geometry(f"{LEFT_PANEL_WIDTH + 1180}x960")
        self.minsize(1280, 720)
        self.configure(bg=BG)

        self.params = EconomyParameters()
        self.scenarios = read_json(ROOT / 'data/economy/scenarios_v1.json')['scenarios']
        self.scenario_var = tk.StringVar(value="自定义参数")
        self.sim_months = 60
        self.history = []
        self._vars: dict[str, tk.StringVar] = {}
        self._widgets: dict[str, dict] = {}
        self._running = False
        self._refresh_pending = None
        self._baseline: dict[str, float] = {}   # 上一次动手之前的指标快照
        self._syncing = False          # 回填控件时抑制回调，避免自激循环

        self._build_style()
        self._build_layout()
        self._build_param_panel()
        self._build_dashboard()
        self._build_statusbar()
        self.refresh()

    # --- 外观 ---------------------------------------------------------

    def _build_style(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TFrame", background=BG)
        style.configure("Panel.TFrame", background=PANEL)
        style.configure("TLabel", background=BG, font=("Microsoft YaHei", 9))
        style.configure("PanelTitle.TLabel", background=PANEL,
                        font=("Microsoft YaHei", 11, "bold"))
        style.configure("GroupTitle.TLabel", background=PANEL,
                        font=("Microsoft YaHei", 10, "bold"), foreground=ACCENT)
        style.configure("Hint.TLabel", background=PANEL, foreground=TEXT_MUTED,
                        font=("Microsoft YaHei", 8))
        style.configure("Param.TLabel", background=PANEL,
                        font=("Microsoft YaHei", 9), foreground="#334e68")
        style.configure("ParamEdited.TLabel", background="#fffaf0",
                        font=("Microsoft YaHei", 9, "bold"), foreground="#975a16")
        style.configure("ParamUnit.TLabel", background=PANEL,
                        font=("Microsoft YaHei", 8), foreground=TEXT_MUTED)
        style.configure("Metric.TLabel", background=PANEL,
                        font=("Microsoft YaHei", 10))
        style.configure("MetricName.TLabel", background=PANEL,
                        font=("Microsoft YaHei", 9), foreground="#4a5568")
        style.configure("Arrow.TLabel", background=PANEL,
                        font=("Consolas", 11, "bold"))
        style.configure("Value.TLabel", background=PANEL,
                        font=("Consolas", 11, "bold"))
        style.configure("Accent.TButton", font=("Microsoft YaHei", 9, "bold"))
        style.configure("Reset.TButton", font=("Microsoft YaHei", 9, "bold"),
                        foreground="#c53030")
        style.configure("Entry.TEntry", fieldbackground="#ffffff")
        style.configure("TLabelframe", background=PANEL, bordercolor="#dfe6ef")
        style.configure("TLabelframe.Label", background=PANEL,
                        font=("Microsoft YaHei", 10, "bold"), foreground="#2d3748")

    def _build_layout(self) -> None:
        header = tk.Frame(self, bg=BG)
        header.pack(fill="x", padx=12, pady=(10, 4))
        tk.Label(header, text="经济模型调参实验室", bg=BG, fg="#1a365d",
                 font=("Microsoft YaHei", 15, "bold")).pack(side="left")
        tk.Label(header, text="拖动滑块即时重算，无需手动点按钮",
                 bg=BG, fg=TEXT_MUTED,
                 font=("Microsoft YaHei", 9)).pack(side="left", padx=(12, 0))

        right = ttk.Frame(header)
        right.pack(side="right")
        ttk.Label(right, text="模拟月数").pack(side="left", padx=(0, 4))
        self.months_var = tk.StringVar(value=str(self.sim_months))
        spin = ttk.Spinbox(right, from_=12, to=240, width=5,
                           textvariable=self.months_var)
        spin.pack(side="left", padx=(0, 10))
        spin.bind("<Return>", lambda e: self.refresh())
        ttk.Button(right, text="立即重算", command=self.refresh).pack(side="left", padx=2)
        ttk.Button(right, text="保存参数", command=self._save).pack(side="left", padx=2)
        ttk.Button(right, text="载入参数", command=self._load).pack(side="left", padx=2)
        ttk.Button(right, text="导出指标", command=self._export).pack(side="left", padx=2)
        ttk.Button(right, text="↺ 重置全部参数", style="Reset.TButton",
                   command=self._reset_all).pack(side="left", padx=(10, 2))

        scenario_row = ttk.Frame(self)
        scenario_row.pack(fill="x", padx=12, pady=3)
        ttk.Label(scenario_row, text="条件场景").pack(side="left")
        chooser = ttk.Combobox(scenario_row, textvariable=self.scenario_var, state="readonly", width=24,
                              values=["自定义参数"] + [s['name'] for s in self.scenarios])
        chooser.pack(side="left", padx=8)
        chooser.bind("<<ComboboxSelected>>", self._select_scenario)
        self.scenario_hint = ttk.Label(scenario_row, text="场景条件叠加到左侧参数，冲击按指定月份生效")
        self.scenario_hint.pack(side="left")

        self.event_hint = ttk.Label(self, text="", foreground="#9c4221", wraplength=1500)
        self.event_hint.pack(fill="x", padx=12, pady=2)

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True, padx=12, pady=4)

        self.left = ttk.Frame(body, style="Panel.TFrame", width=LEFT_PANEL_WIDTH)
        self.left.pack(side="left", fill="y")
        self.left.pack_propagate(False)
        self.right = ttk.Frame(body)
        self.right.pack(side="right", fill="both", expand=True)

        # 搜索框，方便在一百多项里快速定位
        search = ttk.Frame(self.left, style="Panel.TFrame")
        search.pack(fill="x", padx=10, pady=(10, 4))
        ttk.Label(search, text="搜索参数", style="PanelTitle.TLabel").pack(side="left")
        ttk.Label(search, text="（中文名或英文均可，人数/金额自动取整）",
                  style="ParamUnit.TLabel").pack(side="left", padx=(8, 0))
        self.search_var = tk.StringVar()
        entry = ttk.Entry(search, textvariable=self.search_var, width=20)
        entry.pack(side="right")
        self.search_var.trace_add("write", lambda *_: self._apply_filter())

        holder = ttk.Frame(self.left, style="Panel.TFrame")
        holder.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        canvas = tk.Canvas(holder, bg=PANEL, highlightthickness=0,
                           width=LEFT_PANEL_WIDTH - 40)
        scroll = ttk.Scrollbar(holder, orient="vertical", command=canvas.yview)
        self.param_frame = ttk.Frame(canvas, style="Panel.TFrame")
        window = canvas.create_window((0, 0), window=self.param_frame, anchor="nw")
        self.param_frame.bind("<Configure>",
                              lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(window, width=e.width))
        self.param_canvas = canvas
        self.param_rows: list[tuple[tk.Widget, str]] = []

    # --- 参数面板 -----------------------------------------------------

    def _select_scenario(self, _event=None):
        self._mark_baseline()
        scenario = next((s for s in self.scenarios if s['name'] == self.scenario_var.get()), None)
        if scenario:
            self.months_var.set(str(scenario['months']))
        self.refresh()

    def _build_param_panel(self) -> None:
        self._meta_cache = {m.key: m for m in self.params.adjustable()}
        for group in GROUP_ORDER:
            title = ttk.Label(self.param_frame, text=GROUP_TITLES.get(group, group),
                               style="GroupTitle.TLabel")
            title.pack(fill="x", padx=12, pady=(12, 4))
            self.param_rows.append((title, group))
            for meta in self.params.adjustable():
                if not meta.key.startswith(group + "."):
                    continue
                self.param_rows.append((self._make_row(meta), meta.key))

    def _make_row(self, meta) -> tk.Frame:
        name = meta.key.split(".", 1)[1]
        row = ttk.Frame(self.param_frame, style="Panel.TFrame")
        row.pack(fill="x", padx=10, pady=2)

        # 界面显示中文名；key 放进悬停提示，需要查代码时还能对上。
        group, key = meta.key.split('.', 1)
        implementation = self.params.get(group + '._implementation', {}).get(key, '')
        label = ttk.Label(row, text=meta.display_name + (" [待接入]" if implementation else ""), width=20, anchor="w",
                          style="Param.TLabel")
        label.pack(side="left")
        Tooltip(label, lambda m=meta, status=implementation: f"{m.display_name}\n{m.key}\n{status or '已接入；有些效果需要满足触发条件'}")

        var = tk.StringVar(value=_fmt(meta.value))
        self._vars[meta.key] = var
        entry = ttk.Entry(row, textvariable=var, width=10,
                          style="Entry.TEntry", justify="right")
        entry.pack(side="left", padx=(6, 2))
        var.trace_add("write", lambda *_, k=meta.key: self._on_param_input(k))

        unit_text = meta.unit if meta.unit else ""
        if meta.integral:
            unit_text = (unit_text + "·取整") if unit_text else "取整"
        if unit_text:
            ttk.Label(row, text=unit_text, width=7, anchor="w",
                      style="ParamUnit.TLabel").pack(side="left", padx=(0, 6))

        if meta.has_range:
            slider = Slider(row, low=meta.low, high=meta.high, value=meta.value,
                            integral=meta.integral, width=250, bg=PANEL,
                            on_change=lambda v, final, k=meta.key:
                                self._on_slider_move(k, v, final))
            slider.pack(side="left")
            self._widgets[meta.key] = {"slider": slider, "entry": entry,
                                       "label": label, "meta": meta}
            if implementation:
                entry.configure(state="disabled")
                slider.on_change = lambda *_: None
                slider.bind("<Button-1>", lambda _e: "break")
                slider.bind("<B1-Motion>", lambda _e: "break")
        else:
            ttk.Label(row, text="—", width=34, style="ParamUnit.TLabel").pack(side="left")
        return row

    # --- 人口联动 -----------------------------------------------------

    # 人口四项必须满足恒等式（设计稿 TEST-01）：
    # 总人口 ＝ 就业 ＋ 失业 ＋ 非劳动力。
    #
    # 联动比例：**从 params 当前默认值读取**（不再硬编码 1500/150/1350）。
    # 这样改 JSON 后联动自动跟随，不会因为硬编码和默认值漂移而行为不一致。
    # 拖动时只把拖动项保留，另外三项按出厂比例**计算建议值**放进输入框但**不静默覆盖**——
    # 用户可以手动接受，也可以改别的数。绝不违反自身"不许静默出错"原则。
    _POPULATION_PARTS = ("employed", "unemployed", "out_of_labour_force")

    def _cohere_population(self, changed: str) -> None:
        """拖动人口组任一项后，按出厂比例给另外三项提供建议值，但不覆盖。"""
        name = changed.split(".", 1)[1]
        if name not in (*self._POPULATION_PARTS, "start"):
            return
        pop = self.params.section("population")
        total = int(pop["start"])
        if total <= 0:
            return

        # 联动基准比例从 JSON 当前默认值读取（不再硬编码 1500/150/1350）。
        # 这样改 JSON 后联动自动跟随，不会因为硬编码和默认值漂移而行为不一致。
        base_employed = max(0, int(pop.get("employed", 1500)))
        base_unemployed = max(0, int(pop.get("unemployed", 150)))
        base_non_labour = max(0, int(pop.get("out_of_labour_force", 1350)))
        base_total = base_employed + base_unemployed + base_non_labour
        if base_total <= 0:
            base_total = total or 1
            base_employed = int(base_total * 0.5)
            base_unemployed = int(base_total * 0.05)
            base_non_labour = base_total - base_employed - base_unemployed

        # 只有拖 start 时才联动其余三项：总人口约束需要恒等式。
        # 拖 employed/unemployed/non_labour 时不再静默覆盖另外两项——
        # 用户可能想单独调整结构（D-03 修复：禁止静默出错）。
        if name != "start":
            # 仅更新 labour_force 与 start 的恒等关系（如果是 employed/unemployed 变动）
            self.params.set("population.labour_force",
                            int(pop.get("employed", 0)) + int(pop.get("unemployed", 0)),
                            validate=False)
            self._sync_widget("population.labour_force",
                              int(pop.get("employed", 0)) + int(pop.get("unemployed", 0)))
            return

        # start 拖动时：联动重算另三项
        keep = None
        fixed = 0
        remaining = total

        base_shares = {"employed": base_employed, "unemployed": base_unemployed,
                       "out_of_labour_force": base_non_labour}
        shares = {k: v / base_total for k, v in base_shares.items()}
        weight = sum(shares.values()) or 1.0

        allocated, values = 0, {}
        for key in shares:
            # 就业与失业优先按比例取整
            share = int(remaining * shares[key] / weight)
            values[key] = max(0, share)
            allocated += share
        leftover = remaining - allocated
        # 余量补给非劳动力（余量项），保证四项加起来精确等于总人口
        values["out_of_labour_force"] = max(
            0, values["out_of_labour_force"] + leftover)

        for key, value in values.items():
            self.params.set(f"population.{key}", value, validate=False)
            self._sync_widget(f"population.{key}", value)
        self.params.set("population.labour_force",
                        values.get("employed", 0) + values.get("unemployed", 0),
                        validate=False)
        self._sync_widget("population.labour_force",
                          values.get("employed", 0) + values.get("unemployed", 0))
        _ = pop

    def _sync_widget(self, key: str, value) -> None:
        """把联动算出来的值回填到界面（不再触发回调）。"""
        if key in self._vars:
            self._set_var(key, value)
        widget = self._widgets.get(key)
        if widget:
            widget["slider"].set(float(value))

    # --- 参数联动 -----------------------------------------------------

    def _set_var(self, key: str, value) -> None:
        """回填输入框但不触发回调（否则会自激）。"""
        self._syncing = True
        try:
            self._vars[key].set(_fmt(value))
        finally:
            self._syncing = False

    def _on_slider_move(self, key: str, value, final: bool) -> None:
        """滑块拖动中：立刻更新显示，稍后防抖重算。

        回填的是**参数集里实际生效的值**，不是传进来的原始值。
        整数参数可能被向下取整（传 900.87 存进去是 900），
        界面必须显示 900，否则会出现「框里写着 900.87、模型里跑的是 900」
        这种界面对不上账的情况。
        """
        try:
            self.params.set(key, value)
        except Exception as exc:
            messagebox.showerror("参数超出设计稿允许范围", str(exc), parent=self)
            meta = self._widgets.get(key, {}).get("meta")
            self._set_var(key, meta.value if meta else 0)
            return
        self._mark_baseline()
        stored = self.params.get(key)
        if key.startswith("population."):
            # 人口是四项联动的，先把恒等式恢复好再重算，
            # 否则每拖一下就弹一次错误框。
            self._cohere_population(key)
            stored = self.params.get(key)
        widget = self._widgets.get(key)
        if widget:
            widget["slider"].set(stored)
        self._set_var(key, stored)
        self._schedule_refresh(delay=0 if final else RECOMPUTE_DELAY_MS)

    def _on_param_input(self, key: str) -> None:
        """输入框里手改：同样触发实时重算。"""
        if self._syncing:
            return
        raw = self._vars[key].get().strip()
        try:
            value = float(raw)
        except ValueError:
            return
        if self._apply_value(key, value):
            self._schedule_refresh()

    def _apply_value(self, key: str, value) -> bool:
        """把一个取值写进参数集，并同步滑块与输入框。"""
        try:
            self.params.set(key, value)
        except Exception as exc:
            messagebox.showerror("参数超出设计稿允许范围", str(exc), parent=self)
            meta = self._widgets.get(key, {}).get("meta")
            self._set_var(key, meta.value if meta else 0)
            if meta:
                self._widgets[key]["slider"].set(meta.value)
            return False
        self._mark_baseline()
        if key.startswith("population."):
            self._cohere_population(key)
        stored = self.params.get(key)
        widget = self._widgets.get(key)
        if widget:
            widget["slider"].set(stored)
        self._set_var(key, stored)
        return True

    def _mark_baseline(self) -> None:
        """改参数**之前**拍一张快照，箭头要跟它比。

        之前是「跟上一次刷新的结果比」，结果参数一改就连续重算两次，
        第二次拿第一次的结果当基准，等于自己跟自己比，箭头立刻被清掉。
        语义上用户想看的也是「我刚才那一拖让数字往哪边动了」，
        所以基准必须是**动手之前**的那个状态。
        """
        if self.history:
            self._baseline = dict(self.history[-1].metrics)
        else:
            self._baseline = {}

    def _schedule_refresh(self, delay: int = RECOMPUTE_DELAY_MS) -> None:
        """防抖：拖动过程中只保留最后一次请求。"""
        if self._refresh_pending is not None:
            try:
                self.after_cancel(self._refresh_pending)
            except tk.TclError:
                pass
        self._refresh_pending = self.after(delay, self._do_refresh)

    def _do_refresh(self) -> None:
        self._refresh_pending = None
        self.refresh()

    def _mark_changed_rows(self) -> None:
        changed = self.params.changed()
        for key, widget in self._widgets.items():
            widget["label"].configure(
                style="ParamEdited.TLabel" if key in changed else "Param.TLabel")

    def _apply_filter(self) -> None:
        """搜索同时匹配中文名与英文 key，两种习惯都能用。"""
        needle = self.search_var.get().strip().lower()
        for row, key in self.param_rows:
            is_title = isinstance(row, ttk.Label)
            if not needle:
                row.pack(fill="x", padx=(12 if is_title else 10),
                         pady=((12, 4) if is_title else 2))
                continue
            meta = self._meta_cache.get(key)
            group = key.lower().split(".")[0]
            haystack = key.lower()
            if meta is not None:
                haystack += " " + meta.label.lower()
            hit = needle in haystack or \
                GROUP_TITLES.get(group, "").lower().find(needle) >= 0
            if hit:
                row.pack(fill="x", padx=(12 if is_title else 10),
                         pady=((12, 4) if is_title else 2))
            else:
                row.pack_forget()

    def _reset_all(self) -> None:
        changed = self.params.changed()
        if changed:
            if not messagebox.askyesno(
                    "确认重置",
                    f"将丢弃 {len(changed)} 项已修改的参数，恢复到出厂值。\n\n"
                    "要继续吗？", parent=self):
                return
        self.params.reset()
        self.scenario_var.set("自定义参数")
        for key in self._vars:
            widget = self._widgets.get(key)
            value = self.params.get(key)
            self._set_var(key, value)
            if widget:
                widget["slider"].set(float(value))
        self._mark_changed_rows()
        self._mark_baseline()
        self.refresh()

    # --- 指标仪表盘 ---------------------------------------------------

    def _build_dashboard(self) -> None:
        # 曲线先占位、再让仪表盘伸展：pack 按加入顺序分配空间，
        # 先加入的曲线能先拿到 minheight，仪表盘吃掉剩下的。
        # 反过来排的话仪表盘会把曲线挤成一条边，标题都看不见。
        self._build_curves()

        # 仪表盘整体放进可滚动的画布：核心指标有三十多项，
        # 窗口不够高时必须能滚，而不是把后半截指标直接截掉看不见。
        holder = tk.Frame(self.right, bg=BG)
        holder.pack(fill="both", expand=True, padx=3, pady=(3, 0))
        canvas = tk.Canvas(holder, bg=BG, highlightthickness=0)
        scroll = ttk.Scrollbar(holder, orient="vertical", command=canvas.yview)
        top = tk.Frame(canvas, bg=BG)
        window = canvas.create_window((0, 0), window=top, anchor="nw")
        top.bind("<Configure>",
                 lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        canvas.bind("<Configure>",
                    lambda e: canvas.itemconfigure(window, width=e.width))
        self.metric_labels: dict[str, tuple[ttk.Label, ttk.Label, tk.Frame]] = {}

        for category in ("core", "structure", "risk"):
            frame = ttk.LabelFrame(top, text=CATEGORY_LABELS[category])
            frame.pack(side="left", fill="both", expand=True, padx=3, pady=3)
            items = [i for i in INDICATORS if i.category == category]
            # 核心指标三十多项，单列排下来有一屏多，还得滚动才看得到尾。
            # 分两列摆，一屏就能看全，调参时不用来回滚。
            columns = [frame]
            if category == "core" and len(items) > 14:
                columns = [tk.Frame(frame, bg=PANEL), tk.Frame(frame, bg=PANEL)]
                for column in columns:
                    column.pack(side="left", fill="both", expand=True)
            for index, ind in enumerate(items):
                holder = columns[index % len(columns)]
                self._make_metric_box(holder, ind)

    def _make_metric_box(self, parent, ind: Indicator) -> None:
        box = ttk.Frame(parent, style="Panel.TFrame")
        box.pack(fill="x", padx=8, pady=3)
        ttk.Label(box, text=ind.label, style="MetricName.TLabel").pack(side="left")
        value = ttk.Label(box, text="—", style="Value.TLabel", width=12, anchor="e")
        value.pack(side="right")
        arrow = ttk.Label(box, text="", style="Arrow.TLabel", width=2, anchor="e")
        arrow.pack(side="right", padx=(0, 4))
        self.metric_labels[ind.key] = (arrow, value, box)
        Tooltip(box, lambda k=ind.key: self._metric_tip(k))

    def _metric_tip(self, key: str) -> str:
        ind = BY_KEY.get(key)
        if ind is None or not self.history:
            return ""
        value = self.history[-1].metrics.get(key)
        if value is None:
            return ""
        lines = [f"{ind.label}（{ind.key}）", ind.description, ""]
        if ind.integral and isinstance(value, float) and value != int(value):
            lines.append(f"计算值：{value:,.4f}")
            lines.append(f"有效值：{ind.effective_of(value):,}（业务上不能带小数）")
        else:
            lines.append(f"当前值：{ind.format(value)}")
        previous = self._baseline.get(key)
        if previous is not None:
            lines.append(f"上次值：{ind.format(previous)}")
        lines.append(f"健康区间：{ind.healthy if ind.healthy else '不判断'}")
        return "\n".join(lines)

    # --- 曲线 ---------------------------------------------------------

    def _build_curves(self) -> None:
        frame = ttk.Frame(self.right)
        # side="bottom" + expand：窗口再小也保证曲线有 300px，
        # 不会因为仪表盘指标多就被压成一条缝。
        frame.pack(side="bottom", fill="both", expand=True, padx=6, pady=6)
        if not HAS_PLOT:
            ttk.Label(frame, text="未安装 matplotlib，无法显示曲线（其余功能可用）",
                      style="Hint.TLabel").pack()
            return
        figure = Figure(figsize=(11, 3.6), dpi=100, facecolor=BG)
        self.axes = [figure.add_subplot(131), figure.add_subplot(132), figure.add_subplot(133)]
        figure.set_layout_engine("constrained")
        self.canvas = FigureCanvasTkAgg(figure, master=frame)
        widget = self.canvas.get_tk_widget()
        widget.pack(fill="both", expand=True)
        widget.configure(height=300)

    def _build_statusbar(self) -> None:
        bar = ttk.Frame(self, style="Panel.TFrame")
        bar.pack(fill="x", side="bottom")
        self.status = ttk.Label(bar, text="就绪", style="MetricName.TLabel")
        self.status.pack(side="left", padx=8, pady=4)

    # --- 刷新 ---------------------------------------------------------

    def refresh(self) -> None:
        if self._running:
            return
        self._running = True
        self.update_idletasks()
        try:
            months = max(1, min(240, int(self.months_var.get())))
            self.sim_months = months
            params = EconomyParameters(self.params.changed())
            scenario = next((s for s in self.scenarios if s['name'] == self.scenario_var.get()), None)
            if scenario:
                for key, value in scenario.get('overrides', {}).items():
                    params.set(key, value, validate=False)
            sim = EconomySimulation(months=months, parameters=params, shocks=scenario.get('shocks') if scenario else None)
            self.scenario_hint.configure(text=(scenario.get('description', '条件与行为门槛见场景配置') if scenario else '当前全部结果由左侧参数决定'))
            self.history = sim.run()
            recent = [f"第{r.month}月：{event}" for r in self.history[-6:] for event in r.events if any(word in event for word in ("破产", "外迁", "救济不足", "债务", "冲击"))]
            self.event_hint.configure(text="｜".join(recent[-2:]) if recent else "最近6个月未出现企业退出、救济不足或债务违约")
            self._paint_metrics(self.history[-1] if self.history else None)
            self._paint_curves()
            self._mark_changed_rows()
            changed = len(self.params.changed())
            self.status.config(text=f"{self.scenario_var.get()}｜模拟 {months} 个月完成｜当前显示第 "
                                   f"{self.history[-1].month if self.history else 0} 月"
                                   f"｜已改动参数 {changed} 项")
        except Exception as exc:                       # 出错要让人看见，不能静默
            self.status.config(text=f"模拟失败：{exc}")
            messagebox.showerror("模拟失败", str(exc), parent=self)
        finally:
            self._running = False

    def _paint_metrics(self, result) -> None:
        """刷新指标数值，并标出相比上一轮是升还是降。

        箭头和数值**分开上色**：箭头只表达方向（绿升橙降），
        数值只表达健康度（黑/橙/红）。混成一个颜色就分不清
        「这个数变好了」还是「这个数变危险了」。

        箭头按**显示值**判定：只有格式化后的字符串变了才算有变化，
        否则会出现数值在动、箭头乱闪的噪音。
        基准是 ``_baseline``（用户上一次动手之前的状态），
        所以连算两次也不会把箭头自己抹掉。
        """
        if result is None:
            return
        for key, (arrow, label, _box) in self.metric_labels.items():
            ind = BY_KEY.get(key)
            value = result.metrics.get(key)
            if ind is None:
                continue
            state = ind.status(value)
            text = ind.format(value)
            label.config(text=text, foreground=STATUS_COLORS.get(state, TEXT_DARK))

            previous = self._baseline.get(key)
            if previous is None or value is None:
                arrow.config(text="", foreground=ARROW_IDLE)
            elif ind.format(previous) == text:
                arrow.config(text="", foreground=ARROW_IDLE)
            elif value > previous:
                arrow.config(text="↑", foreground=RISING)
            else:
                arrow.config(text="↓", foreground=FALLING)

    def _paint_curves(self) -> None:
        if not HAS_PLOT or not self.history:
            return
        months = [r.month for r in self.history]
        groups = [
            ([('real_output', '实际产出'), ('population', '人口'), ('private_firm_count', '企业数')], '规模（首月=100）'),
            ([('unemployment_rate', '失业率'), ('capacity_utilisation', '利用率')], '比例（%）'),
            ([('municipal_cash', '市政现金'), ('church_cash', '教会现金')], '现金（CR）'),
        ]
        for ax, (items, title) in zip(self.axes, groups):
            ax.clear()
            for key, label in items:
                values = [r.metrics[key] for r in self.history]
                if title.startswith('规模'):
                    values = [v / max(1, values[0]) * 100 for v in values]
                elif title.startswith('比例'):
                    values = [v * 100 for v in values]
                else:
                    values = [v / 100 for v in values]
                ax.plot(months, values, label=label, linewidth=1.5)
            ax.set_title(title, fontsize=9)
            ax.set_xlabel('月', fontsize=8)
            ax.grid(alpha=0.25)
            ax.legend(fontsize=7)
        self.canvas.draw_idle()

    # --- 存取 ---------------------------------------------------------

    def _save(self) -> None:
        path = filedialog.asksaveasfilename(
            defaultextension=".json", filetypes=[("参数文件", "*.json")])
        if not path:
            return
        import json
        with open(path, "w", encoding="utf-8") as stream:
            json.dump({"config_id": "economy_model_parameters_custom",
                       "overrides": self.params.changed(), "scenario": self.scenario_var.get(),
                       "months": self.sim_months},
                      stream, ensure_ascii=False, indent=2)
        self.status.config(text=f"参数已保存到 {path}")

    def _load(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("参数文件", "*.json")])
        if not path:
            return
        import json
        try:
            with open(path, encoding='utf-8') as stream:
                payload = json.load(stream)
            candidate = EconomyParameters(payload.get('overrides') or {})
            candidate.validate_all_deep()
            scenario = payload.get('scenario', '自定义参数')
            if scenario not in ['自定义参数'] + [s['name'] for s in self.scenarios]:
                raise ValueError('参数方案引用了不存在的场景')
            months = max(1, min(240, int(payload.get('months', self.sim_months))))
        except (OSError, ValueError, TypeError, KeyError, GameError) as exc:
            messagebox.showerror('方案载入失败', str(exc), parent=self)
            return
        self._mark_baseline()
        self.params = candidate
        self.scenario_var.set(scenario)
        self.months_var.set(str(months))
        # 完整回填；不能只改方案里的差异项而留下上次界面的旧值。
        for key in self._vars:
            self._sync_widget(key, candidate.get(key))
        self._mark_changed_rows()
        self.refresh()

    def _export(self) -> None:
        if not self.history:
            return
        path = filedialog.asksaveasfilename(defaultextension=".csv",
                                            filetypes=[("CSV", "*.csv")])
        if not path:
            return
        import csv
        keys = sorted({k for r in self.history for k in r.metrics})
        with open(path, "w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(["月份"] + [f"{BY_KEY[k].label}（{BY_KEY[k].unit}）" for k in keys])
            for r in self.history:
                writer.writerow([r.month] + [BY_KEY[k].display_value(r.metrics[k]) for k in keys])
        self.status.config(text=f"指标已导出到 {path}")


def _fmt(value) -> str:
    """数值显示：整数不带小数点，浮点保留四位。"""
    if isinstance(value, float):
        return f"{value:.4f}".rstrip("0").rstrip(".") or "0"
    return str(value)


def main() -> int:
    app = EconomyLab()
    app.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
