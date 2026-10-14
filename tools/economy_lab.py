"""经济模型调参实验室（GUI）。

这是给策划/数值用的工作台，不是游戏界面。启动方式::

    python -m tools.economy_lab

三块面板
--------
左：参数面板。每一项都能改，带范围的可拖滑块，改动即时标黄。
中：指标仪表盘。核心指标、结构诊断、风险预警三组，颜色区分健康度。
右：演化曲线。任意指标随月份的变化，配合参数调整立刻看效果。

设计原则
--------
1. **改参数 → 立刻看到后果**。每次松手就重跑一次模拟并刷新曲线。
2. **不许静默出错**。参数越界当场弹窗说明设计稿允许的范围，不默默改掉。
3. **零新增依赖**。只用标准库 tkinter 加已装的 matplotlib。
"""
from __future__ import annotations

import sys
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from backend.model.indicators import (CATEGORY_LABELS, BY_KEY, INDICATORS,
                                     Indicator)
from backend.model.parameters import EconomyParameters
from backend.systems.model_sim import EconomySimulation

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

STATUS_COLORS = {"ok": "#1a202c", "warn": "#b7791f", "bad": "#c53030"}
BG = "#f7fafc"
PANEL = "#ffffff"


class EconomyLab(tk.Tk):
    """调参实验室主窗口。"""

    def __init__(self):
        super().__init__()
        self.title("BIPOLE 经济模型调参实验室")
        self.geometry("1560x940")
        self.configure(bg=BG)

        self.params = EconomyParameters()
        self.sim_months = 60
        self.history = []
        self._vars: dict[str, tk.StringVar] = {}
        self._widgets: dict[str, dict] = {}
        self._running = False
        self._curve_metric = tk.StringVar(value="population")

        self._build_style()
        self._build_layout()
        self._build_param_panel()
        self._build_dashboard()
        self._build_curves()
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
                        font=("Microsoft YaHei", 10, "bold"), foreground="#2d3748")
        style.configure("Hint.TLabel", background=BG, foreground="#718096",
                        font=("Microsoft YaHei", 8))
        style.configure("Metric.TLabel", background=PANEL,
                        font=("Microsoft YaHei", 10))
        style.configure("MetricName.TLabel", background=PANEL,
                        font=("Microsoft YaHei", 9), foreground="#4a5568")
        style.configure("Value.TLabel", background=PANEL,
                        font=("Consolas", 11, "bold"))
        style.configure("TScale", background=PANEL)
        style.configure("Accent.TButton", font=("Microsoft YaHei", 9, "bold"))

    def _build_layout(self) -> None:
        header = ttk.Frame(self)
        header.pack(fill="x", padx=10, pady=(10, 4))
        ttk.Label(header, text="经济模型调参实验室",
                  font=("Microsoft YaHei", 15, "bold")).pack(side="left")

        right = ttk.Frame(header)
        right.pack(side="right")
        ttk.Label(right, text="模拟月数").pack(side="left", padx=(0, 4))
        self.months_var = tk.StringVar(value=str(self.sim_months))
        spin = ttk.Spinbox(right, from_=12, to=240, width=5, textvariable=self.months_var)
        spin.pack(side="left", padx=(0, 10))
        ttk.Button(right, text="重新模拟", style="Accent.TButton",
                   command=self.refresh).pack(side="left", padx=2)
        ttk.Button(right, text="重置参数", command=self._reset_all).pack(side="left", padx=2)
        ttk.Button(right, text="保存参数", command=self._save).pack(side="left", padx=2)
        ttk.Button(right, text="载入参数", command=self._load).pack(side="left", padx=2)
        ttk.Button(right, text="导出指标", command=self._export).pack(side="left", padx=2)

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True, padx=10, pady=4)

        self.left = ttk.Frame(body, style="Panel.TFrame")
        self.left.pack(side="left", fill="y")
        self.right = ttk.Frame(body)
        self.right.pack(side="right", fill="both", expand=True)

        # 搜索框，方便在 94 项里快速定位
        search = ttk.Frame(self.left, style="Panel.TFrame")
        search.pack(fill="x", padx=8, pady=(8, 2))
        ttk.Label(search, text="搜索参数", style="PanelTitle.TLabel").pack(side="left")
        self.search_var = tk.StringVar()
        entry = ttk.Entry(search, textvariable=self.search_var, width=22)
        entry.pack(side="right")
        self.search_var.trace_add("write", lambda *_: self._apply_filter())

        canvas = tk.Canvas(self.left, bg=PANEL, highlightthickness=0, width=430)
        scroll = ttk.Scrollbar(self.left, orient="vertical", command=canvas.yview)
        self.param_frame = ttk.Frame(canvas, style="Panel.TFrame")
        window = canvas.create_window((0, 0), window=self.param_frame, anchor="nw")
        self.param_frame.bind("<Configure>",
                              lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side="left", fill="both", expand=True, padx=(6, 0), pady=6)
        scroll.pack(side="right", fill="y", pady=6)
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(window, width=e.width))
        self.param_canvas = canvas
        self.param_rows: list[tuple[tk.Frame, str]] = []

    # --- 参数面板 -----------------------------------------------------

    def _build_param_panel(self) -> None:
        for group in GROUP_ORDER:
            title = ttk.Label(self.param_frame, text=GROUP_TITLES.get(group, group),
                               style="GroupTitle.TLabel")
            title.pack(fill="x", padx=10, pady=(10, 2))
            self.param_rows.append((title, group))
            for meta in self.params.adjustable():
                if not meta.key.startswith(group + "."):
                    continue
                self.param_rows.append((self._make_row(meta), meta.key))

    def _make_row(self, meta) -> tk.Frame:
        name = meta.key.split(".", 1)[1]
        row = ttk.Frame(self.param_frame, style="Panel.TFrame")
        row.pack(fill="x", padx=8, pady=1)

        var = tk.StringVar(value=_fmt(meta.value))
        self._vars[meta.key] = var

        ttk.Label(row, text=name, width=22, style="MetricName.TLabel").pack(side="left")
        entry = ttk.Entry(row, textvariable=var, width=9)
        entry.pack(side="left", padx=(0, 4))
        var.trace_add("write", lambda *_, k=meta.key: self._on_param_change(k))

        if meta.has_range:
            scale = ttk.Scale(row, from_=meta.low, to=meta.high,
                              orient="horizontal", length=150)
            scale.set(meta.value)
            scale.bind("<ButtonRelease-1>", lambda e, k=meta.key: self._sync_from_scale(k))
            scale.pack(side="left", fill="x", expand=True)
            self._widgets[meta.key] = {"scale": scale, "entry": entry, "meta": meta}
        else:
            ttk.Label(row, text="—", width=18, style="MetricName.TLabel").pack(side="left")
        return row

    def _sync_from_scale(self, key: str) -> None:
        scale = self._widgets[key]["scale"]
        self._vars[key].set(_fmt(float(scale.get())))

    def _on_param_change(self, key: str) -> None:
        widget = self._widgets.get(key)
        meta = widget["meta"] if widget else None
        try:
            value = float(self._vars[key].get())
        except ValueError:
            return
        try:
            self.params.set(key, value)
        except Exception as exc:
            messagebox.showerror("参数超出设计稿允许范围", str(exc), parent=self)
            self._vars[key].set(_fmt(meta.value if meta else 0))
            return
        if widget:
            widget["scale"].set(value)

    def _apply_filter(self) -> None:
        needle = self.search_var.get().strip().lower()
        for row, key in self.param_rows:
            if not needle:
                row.pack(fill="x", padx=8, pady=1) if not isinstance(row, ttk.Label) \
                    else row.pack(fill="x", padx=10, pady=(10, 2))
                continue
            label = key.lower()
            group = label.split(".")[0]
            if needle in label or GROUP_TITLES.get(group, "").lower().find(needle) >= 0:
                if not isinstance(row, ttk.Label):
                    row.pack(fill="x", padx=8, pady=1)
                else:
                    row.pack(fill="x", padx=10, pady=(10, 2))
            else:
                row.pack_forget()

    def _reset_all(self) -> None:
        self.params.reset()
        for key, var in self._vars.items():
            var.set(_fmt(self.params.get(key)))
            widget = self._widgets.get(key)
            if widget:
                widget["scale"].set(float(self.params.get(key)))
        self.refresh()

    # --- 指标仪表盘 ---------------------------------------------------

    def _build_dashboard(self) -> None:
        top = ttk.Frame(self.right)
        top.pack(fill="both", expand=True)
        self.metric_labels: dict[str, tuple[ttk.Label, ttk.Label]] = {}

        for category in ("core", "structure", "risk"):
            frame = ttk.LabelFrame(top, text=CATEGORY_LABELS[category], style="Panel.TFrame")
            frame.pack(side="left", fill="both", expand=True, padx=3, pady=3)
            for ind in [i for i in INDICATORS if i.category == category]:
                box = ttk.Frame(frame, style="Panel.TFrame")
                box.pack(fill="x", padx=6, pady=2)
                ttk.Label(box, text=ind.label, style="MetricName.TLabel").pack(side="left")
                value = ttk.Label(box, text="—", style="Value.TLabel", width=13,
                                  anchor="e")
                value.pack(side="right")
                self.metric_labels[ind.key] = (value, box)

        bottom = ttk.Frame(self.right, style="Panel.TFrame")
        bottom.pack(fill="both", expand=True, padx=3, pady=3)

    def _build_curves(self) -> None:
        frame = ttk.Frame(self.right)
        frame.pack(fill="both", expand=True, padx=6, pady=6)
        if not HAS_PLOT:
            ttk.Label(frame, text="未安装 matplotlib，无法显示曲线（其余功能可用）",
                      style="Hint.TLabel").pack()
            return
        figure = Figure(figsize=(9, 4.2), dpi=100, facecolor=BG)
        self.axes = figure.add_subplot(111)
        self.canvas = FigureCanvasTkAgg(figure, master=frame)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)

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
            sim = EconomySimulation(months=months, parameters=self.params)
            self.history = sim.run()
            self._paint_metrics(self.history[-1] if self.history else None)
            self._paint_curves()
            changed = len(self.params.changed())
            self.status.config(text=f"模拟 {months} 个月完成｜当前显示第 "
                                   f"{self.history[-1].month if self.history else 0} 月"
                                   f"｜已改动参数 {changed} 项")
        except Exception as exc:                       # 出错要让人看见，不能静默
            self.status.config(text=f"模拟失败：{exc}")
            messagebox.showerror("模拟失败", str(exc), parent=self)
        finally:
            self._running = False

    def _paint_metrics(self, result) -> None:
        """刷新指标数值。

        状态只用**前景色**表达：ttk 主题控件不接受 background 参数，
        逐个改底色会直接抛 TclError。颜色足够区分，不必再动背景。
        """
        if result is None:
            return
        for key, (label, box) in self.metric_labels.items():
            ind = BY_KEY.get(key)
            value = result.metrics.get(key)
            if ind is None:
                continue
            state = ind.status(value)
            text = ind.format(value)
            if state == "bad":
                text = f"⚠ {text}"
            elif state == "warn":
                text = f"△ {text}"
            label.config(text=text, foreground=STATUS_COLORS.get(state, "#1a202c"))

    def _paint_curves(self) -> None:
        if not HAS_PLOT or not self.history:
            return
        self.axes.clear()
        months = [r.month for r in self.history]
        for key, title, color in DEFAULT_CURVES:
            values = []
            for r in self.history:
                value = r.metrics.get(key)
                values.append(float(value) if value is not None else 0)
            self.axes.plot(months, values, label=title, color=color, linewidth=1.6)
        self.axes.set_title("演化曲线（改左栏参数后自动重算）",
                            fontname="Microsoft YaHei", fontsize=10)
        self.axes.set_xlabel("月", fontname="Microsoft YaHei", fontsize=9)
        self.axes.grid(alpha=0.25)
        self.axes.legend(fontsize=8, ncol=5, loc="upper left")
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
                       "overrides": self.params.changed()},
                      stream, ensure_ascii=False, indent=2)
        self.status.config(text=f"参数已保存到 {path}")

    def _load(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("参数文件", "*.json")])
        if not path:
            return
        import json
        with open(path, encoding="utf-8") as stream:
            payload = json.load(stream)
        self._reset_all()
        for key, value in (payload.get("overrides") or {}).items():
            try:
                self.params.set(key, value)
                self._vars[key].set(_fmt(value))
                if key in self._widgets:
                    self._widgets[key]["scale"].set(float(value))
            except Exception:
                continue
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
            writer.writerow(["月份"] + keys)
            for r in self.history:
                writer.writerow([r.month] + [r.metrics.get(k, "") for k in keys])
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
