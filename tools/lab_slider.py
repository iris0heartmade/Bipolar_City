"""经济模型调参实验室的滑块控件。

为什么自己画而不用 ``ttk.Scale``
--------------------------------
``ttk.Scale`` 在 clam 主题下只有一套固定外观：灰底槽 + 竖条纹，
既不好看，槽宽还跟着容器伸缩——左栏一窄就把它压成几十像素，
看起来像一根细线，完全看不出当前值在哪儿。

自己用 Canvas 画可以拿到三样东西：

1. **尺寸固定**，不会被容器压扁，轨道永远有足够的抓握面积；
2. **外观可控**：圆角轨道、已填充段、居中把手、左右端点的最小/最大值标注；
3. **拖动过程能连续回调**，这才做得到「边拖边出结果」。

整数参数（人数、金额等）在这里就折算成**有效值**：
滑块按像素取整时可能拖出 3000.37，传给模型的必须是 3000。
"""
from __future__ import annotations

import math
import tkinter as tk


# 配色
TRACK = "#e2e8f0"        # 未填充的槽
FILL = "#3182ce"         # 已填充段
HANDLE = "#2b6cb0"       # 把手
HANDLE_EDGE = "#1a4f8a"
HANDLE_HOVER = "#3f83c5"
TEXT_MUTED = "#a0aec0"
TRACK_HEIGHT = 8
HANDLE_WIDTH = 14
HANDLE_HEIGHT = 20
PADDING_X = 10           # 轨道左右留白，保证端点也能被点到


class Slider(tk.Canvas):
    """一条可拖动的水平滑块。"""

    def __init__(self, master, low: float, high: float, value: float,
                 integral: bool = False, width: int = 230, height: int = 34,
                 on_change=None, on_commit=None, **kwargs):
        self.low = float(low)
        self.high = float(high)
        if self.high <= self.low:
            self.high = self.low + 1.0
        self.integral = integral
        self.on_change = on_change
        self.on_commit = on_commit
        self._value = self.snap(value)
        self._hover = False
        self._dragging = False

        super().__init__(master, width=width, height=height,
                         highlightthickness=0, bd=0, bg=kwargs.pop("bg", "#ffffff"),
                         cursor="hand2", **kwargs)

        self._track_y = height // 2
        self._usable = max(1, width - 2 * PADDING_X - HANDLE_WIDTH)
        self.bind("<Configure>", lambda e: self._redraw())
        self.bind("<Button-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self._redraw()

    # --- 取值 ---------------------------------------------------------

    @property
    def span(self) -> float:
        return self.high - self.low

    def snap(self, raw: float) -> float:
        """把像素位置折算成取值：夹进范围，整数参数再向下取整。"""
        value = max(self.low, min(self.high, float(raw)))
        if self.integral:
            value = int(math.floor(value))
        return value

    @property
    def value(self) -> float:
        return self._value

    def set(self, raw) -> None:
        """外部改值（例如重置、载入参数），不触发回调。"""
        self._value = self.snap(float(raw))
        self._redraw()

    def _to_x(self, value: float) -> float:
        ratio = (value - self.low) / self.span if self.span else 0.0
        return PADDING_X + ratio * self._usable

    def _to_value(self, x: float) -> float:
        ratio = (x - PADDING_X) / self._usable if self._usable else 0.0
        return self.snap(self.low + max(0.0, min(1.0, ratio)) * self.span)

    # --- 绘制 ---------------------------------------------------------

    def _redraw(self) -> None:
        self.delete("all")
        width = self.winfo_width() or int(self.cget("width"))
        cy = self._track_y
        left, right = PADDING_X, width - PADDING_X
        x = self._to_x(self._value)

        # 轨道
        self.create_line(left, cy, right, cy, fill=TRACK,
                         width=TRACK_HEIGHT, capstyle="round")
        # 已填充段
        if x > left:
            self.create_line(left, cy, x, cy, fill=FILL,
                             width=TRACK_HEIGHT, capstyle="round")
        # 把手
        color = HANDLE_HOVER if self._hover or self._dragging else HANDLE
        self.create_rectangle(
            x - HANDLE_WIDTH // 2, cy - HANDLE_HEIGHT // 2,
            x + HANDLE_WIDTH // 2, cy + HANDLE_HEIGHT // 2,
            fill=color, outline=HANDLE_EDGE, width=1)
        self.create_line(x, cy - HANDLE_HEIGHT // 2 + 5,
                         x, cy + HANDLE_HEIGHT // 2 - 5,
                         fill="#ffffff", width=1)

    # --- 交互 ---------------------------------------------------------

    def _emit(self, final: bool) -> None:
        if self.on_change:
            self.on_change(self._value, final)
        if final and self.on_commit:
            self.on_commit(self._value)

    def _on_press(self, event) -> None:
        self._dragging = True
        self._value = self._to_value(event.x)
        self._redraw()
        self._emit(False)

    def _on_drag(self, event) -> None:
        if not self._dragging:
            return
        new = self._to_value(event.x)
        if new != self._value:
            self._value = new
            self._redraw()
            self._emit(False)

    def _on_release(self, _event) -> None:
        if not self._dragging:
            return
        self._dragging = False
        self._emit(True)

    def _on_enter(self, _event) -> None:
        self._hover = True
        self._redraw()

    def _on_leave(self, _event) -> None:
        self._hover = False
        self._redraw()
