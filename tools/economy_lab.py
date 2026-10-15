"""V2.1行业经济实验室。标准库Tk界面，共用游戏运行内核。"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import logging
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, ttk

from backend.logging_setup import setup_logging
from backend.systems.v21.industry_scenario import (
    change_control, control_group, default_scenario, simulate, validate_scenario,
)

logger = logging.getLogger(__name__)
COLORS = {'bg': '#edf1f5', 'ink': '#172b3a', 'muted': '#607080',
          'accent': '#147d76', 'baseline': '#98a7b5', 'line': '#dbe3eb', 'error': '#b53d38'}
CURVES = {'就业人数': ('employed', False), '行业容纳限度': ('capacity', False),
          '行业吸引力': ('attractiveness', False), '行业营业额': ('sales_cents', True),
          '行业经营现金': ('cash_cents', True), '岗位月薪': ('wage_cents', True),
          '全城失业人数': ('unemployed', False), '政府现金': ('government_cash_cents', True),
          '居民总储蓄': ('household_cash_cents', True), '居民实际消费': ('consumer_spending_cents', True)}
GROUPS = {'city': '城市环境与财政', 'sector': '所选行业', 'flow': '劳动力流动',
          'capacity': '容纳限度', 'attractiveness': '评价反馈', 'weights': '吸引力权重（自动联动）'}
BOTTLENECKS = {'building': '建筑岗位', 'cash': '当月工资资金', 'recurring_finance': '持续经营资金', 'inputs': '原料'}


def compute_comparison(scenario, months):
    return simulate(default_scenario(), months), simulate(scenario, months)


def export_bundle(path, scenario, baseline, history):
    """报告包含生效配置和逐月账本，不能只导出漂亮曲线。"""
    payload = {'format': 'industry-lab-report-1', 'scenario': scenario,
               'baseline': baseline, 'history': history,
               'scope': scenario['scope']}
    Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')


class EconomyLab(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('BIPOLE · V2.1 行业经济实验室')
        self.geometry('1460x900')
        self.minsize(1120, 720)
        self.configure(bg=COLORS['bg'])
        self.scenario = default_scenario()
        self.baseline, self.history = [], []
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='industry-lab')
        self._future, self._pending, self._debounce = None, None, None
        self._revision, self._closed, self._syncing, self._invalid = 0, False, False, False
        self._result_revision = -1
        self.variables, self.entries, self.row_frames = {}, {}, {}
        self.industry = tk.StringVar(value='IND 工业')
        self.months = tk.StringVar(value='36')
        self.curve = tk.StringVar(value='行业经营现金')
        self.status = tk.StringVar(value='正在计算基准与当前方案…')
        self.summary = tk.StringVar(value='准备实验数据')
        self.explanation = tk.StringVar()
        style = ttk.Style(self)
        style.theme_use('clam')
        style.configure('.', font=('Microsoft YaHei', 10))
        style.configure('Treeview', rowheight=30, font=('Microsoft YaHei', 9))
        style.configure('Treeview.Heading', font=('Microsoft YaHei', 9, 'bold'))
        self._build()
        self._syncing = True
        self._build_controls()
        self._syncing = False
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.after(40, self.request_compute)
        self.after(50, self._poll)

    def _build(self):
        header = tk.Frame(self, bg=COLORS['ink'], padx=22, pady=13)
        header.pack(fill='x')
        tk.Label(header, text='BIPOLE  /  行业经济实验室', bg=COLORS['ink'], fg='white',
                 font=('Microsoft YaHei', 19, 'bold')).pack(anchor='w')
        tk.Label(header, text='调整参数 → 实际推演 → 对照基准    ·    标准交付包场景 / 固定人口 / 有限外部资金',
                 bg=COLORS['ink'], fg='#b8d0d7').pack(anchor='w', pady=(5, 0))
        toolbar = ttk.Frame(self, padding=(15, 10))
        toolbar.pack(fill='x')
        for text, command in [('恢复基准', self.reset), ('保存方案', self.save_scenario),
                              ('载入方案', self.load_scenario), ('导出报告', self.export_report)]:
            ttk.Button(toolbar, text=text, command=command).pack(side='left', padx=(0, 7))
        ttk.Label(toolbar, text='行业').pack(side='left', padx=(15, 5))
        choose = ttk.Combobox(toolbar, textvariable=self.industry, state='readonly', width=15,
                              values=[f'{i} {d["name"]}' for i, d in self.scenario['sectors'].items()])
        choose.pack(side='left')
        choose.bind('<<ComboboxSelected>>', self._select_industry)
        ttk.Label(toolbar, text='推演月数').pack(side='left', padx=(15, 5))
        month_box = ttk.Spinbox(toolbar, from_=1, to=120, textvariable=self.months, width=5)
        month_box.pack(side='left')
        self.months.trace_add('write', lambda *_: self.request_compute())
        body = ttk.Panedwindow(self, orient='horizontal')
        body.pack(fill='both', expand=True, padx=15)
        left = ttk.Frame(body, width=345)
        body.add(left, weight=0)
        self.parameter_canvas = tk.Canvas(left, bg='white', highlightthickness=0, width=340)
        scroll = ttk.Scrollbar(left, orient='vertical', command=self.parameter_canvas.yview)
        self.parameter_canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        self.parameter_canvas.pack(side='left', fill='both', expand=True)
        self.parameter_frame = tk.Frame(self.parameter_canvas, bg='white', padx=12, pady=7)
        self.parameter_canvas.create_window((0, 0), window=self.parameter_frame, anchor='nw', width=320)
        self.parameter_frame.bind('<Configure>', lambda _: self.parameter_canvas.configure(scrollregion=self.parameter_canvas.bbox('all')))
        self.parameter_canvas.bind('<MouseWheel>', lambda e: self.parameter_canvas.yview_scroll(-int(e.delta/120), 'units'))
        right = ttk.Frame(body, padding=(14, 0, 0, 0))
        body.add(right, weight=1)
        ttk.Label(right, textvariable=self.summary, font=('Microsoft YaHei', 12, 'bold'), wraplength=1000).pack(anchor='w', pady=(0, 8))
        columns = ('name', 'employment', 'capacity', 'score', 'wage', 'cash', 'sales', 'profit')
        self.table = ttk.Treeview(right, columns=columns, show='headings', height=7, selectmode='browse')
        for key, label, width in zip(columns, ['行业', '就业 人', '容纳 人', '吸引力 分', '月薪 CR', '经营现金 CR', '营业额 CR', '税前利润 CR'],
                                     [92, 65, 65, 78, 82, 115, 105, 100]):
            self.table.heading(key, text=label)
            self.table.column(key, width=width, minwidth=50, stretch=True, anchor='e' if key != 'name' else 'w')
        self.table.pack(fill='x')
        self.table.bind('<<TreeviewSelect>>', self._select_table)
        ttk.Label(right, textvariable=self.explanation, wraplength=1050, foreground=COLORS['muted']).pack(anchor='w', pady=8)
        plotbar = ttk.Frame(right)
        plotbar.pack(fill='x', pady=(5, 0))
        ttk.Label(plotbar, text='趋势指标').pack(side='left')
        selector = ttk.Combobox(plotbar, textvariable=self.curve, values=list(CURVES), state='readonly', width=22)
        selector.pack(side='left', padx=8)
        selector.bind('<<ComboboxSelected>>', lambda _: self.draw_chart())
        ttk.Label(plotbar, text='灰：原始基准    绿：当前方案', foreground=COLORS['muted']).pack(side='right')
        self.chart = tk.Canvas(right, bg='white', highlightthickness=1, highlightbackground=COLORS['line'])
        self.chart.pack(fill='both', expand=True, pady=(6, 0))
        self.chart.bind('<Configure>', lambda _: self.draw_chart())
        tk.Label(self, textvariable=self.status, anchor='w', padx=15, pady=9,
                 fg=COLORS['muted'], bg=COLORS['bg'], wraplength=1400).pack(fill='x')

    def selected(self):
        return self.industry.get().split()[0]

    def _build_controls(self):
        for section, title in GROUPS.items():
            tk.Label(self.parameter_frame, text=title, bg='white', fg=COLORS['accent'],
                     font=('Microsoft YaHei', 11, 'bold')).pack(anchor='w', pady=(12, 5))
            for meta in (m for m in self.scenario['controls'] if m['section'] == section):
                key = (section, meta['key'])
                frame = tk.Frame(self.parameter_frame, bg='white')
                frame.pack(fill='x', pady=(0, 7))
                tk.Label(frame, text=meta['label'], bg='white', anchor='w').pack(anchor='w')
                value = control_group(self.scenario, section, self.selected())[meta['key']]
                variable = tk.StringVar(value=f'{value:g}')
                row = tk.Frame(frame, bg='white')
                row.pack(fill='x')
                scale = ttk.Scale(row, from_=meta['min'], to=meta['max'],
                                  command=lambda v, m=meta: self._slide(m, v))
                scale.set(value)
                scale.pack(side='left', fill='x', expand=True, padx=(0, 8))
                entry = ttk.Entry(row, textvariable=variable, width=8)
                entry.pack(side='left')
                entry.bind('<Return>', lambda _, m=meta: self._commit_entry(m))
                entry.bind('<FocusOut>', lambda _, m=meta: self._commit_entry(m))
                tk.Label(frame, text=f"{meta['min']:g} ～ {meta['max']:g}  {meta['unit']}",
                         bg='white', fg=COLORS['muted'], font=('Microsoft YaHei', 8)).pack(anchor='w')
                self.variables[key], self.entries[key] = variable, (entry, scale)

    def _slide(self, meta, value):
        if self._syncing:
            return
        value = round(float(value)/meta['step'])*meta['step']
        self.set_control(meta['section'], meta['key'], round(value, 8))

    def _commit_entry(self, meta):
        if self._syncing:
            return
        try:
            value = float(self.variables[meta['section'], meta['key']].get())
            self.set_control(meta['section'], meta['key'], value)
        except (ValueError, KeyError) as exc:
            self._invalid = True
            self._revision += 1
            self.status.set(f'参数未生效：{exc}。请修正后重算；当前图表仍是上次有效结果。')

    def set_control(self, section, key, value):
        self.scenario = change_control(self.scenario, section, key, value, self.selected())
        self._invalid = False
        self._sync_controls()
        self.request_compute()

    def _sync_controls(self):
        self._syncing = True
        for (section, key), variable in self.variables.items():
            value = control_group(self.scenario, section, self.selected())[key]
            variable.set(f'{value:.6g}')
            self.entries[section, key][1].set(value)
        self._syncing = False

    def _select_industry(self, _=None):
        self._sync_controls()
        self.render()

    def _select_table(self, _=None):
        selection = self.table.selection()
        if selection:
            i = selection[0]
            self.industry.set(f'{i} {self.scenario["sectors"][i]["name"]}')
            self._select_industry()

    def request_compute(self):
        if self._closed or self._invalid:
            return
        self._revision += 1
        if self._debounce:
            self.after_cancel(self._debounce)
        self._debounce = self.after(240, self._enqueue)

    def _enqueue(self):
        self._debounce = None
        try:
            months = int(self.months.get())
            if not 1 <= months <= 120:
                raise ValueError('推演月数须为1—120整数')
            validate_scenario(self.scenario)
        except ValueError as exc:
            self.status.set(f'参数未生效：{exc}。图表保留上次有效结果。')
            return
        self._pending = (self._revision, deepcopy(self.scenario), months)
        self.status.set('正在重新推演；试算不会修改游戏城市或游戏存档…')

    def _poll(self):
        if self._closed:
            return
        if self._future and self._future[1].done():
            revision, future = self._future
            self._future = None
            try:
                baseline, history = future.result()
                if revision == self._revision and not self._invalid:
                    self.baseline, self.history = baseline, history
                    self._result_revision = revision
                    self.render()
                    self.status.set(f'第{len(history)}月结果已更新；每月检查现金、劳动力和库存守恒。基准未做完整世界平衡校准，收缩也会如实显示。')
            except Exception:
                logger.exception('行业实验推演失败 revision=%d', revision)
                if revision == self._revision:
                    self.status.set('本次推演失败，未更新图表；详细错误已记录到日志。')
        if not self._future and self._pending:
            revision, scenario, months = self._pending
            self._pending = None
            self._future = (revision, self._executor.submit(compute_comparison, scenario, months))
        self.after(50, self._poll)

    def render(self):
        if not self.history:
            return
        report = self.history[-1]
        baseline = self.baseline[-1]
        employed = sum(report['employment'].values())
        difference = employed-sum(baseline['employment'].values())
        self.summary.set(f'第 {report["month"]} 月  ·  就业 {employed:,} 人（较基准 {difference:+,}）  ·  失业 {report["unemployed"]:,} 人\n'
                         f'政府 {report["government_cash_cents"]/100:,.0f} CR  /  居民储蓄 {report["household_cash_cents"]/100:,.0f} CR  /  现金对账差额 {report["cash_residual_cents"]} 分')
        for i, s in report['sectors'].items():
            values = (s['name'], s['employed'], s['capacity'], f"{s['attractiveness']:.1f}",
                      f"{s['wage_cents']/100:,.2f}", f"{s['cash_cents']/100:,.0f}",
                      f"{s['sales_cents']/100:,.0f}", f"{s['profit_cents']/100:,.0f}" if s['profit_cents'] is not None else '不适用')
            if self.table.exists(i):
                self.table.item(i, values=values)
            else:
                self.table.insert('', 'end', iid=i, values=values)
        sector = report['sectors'][self.selected()]
        short = '、'.join(BOTTLENECKS[k] for k in sector['capacity_details']['bottlenecks'])
        self.explanation.set(f"{sector['name']}：用工目标 {sector['target']} 人；容纳短板：{short}。"
                             f"产出 {sector['output_units']:,.1f} 标准单位；原料 {sector['raw_units']:,.1f} 单位。工作环境初始0.5为中性假设；调参代表实验条件。")
        self.draw_chart()

    def draw_chart(self):
        canvas = self.chart
        canvas.delete('all')
        if not self.history or canvas.winfo_width() < 150:
            return
        width, height = canvas.winfo_width(), canvas.winfo_height()
        left, top, right, bottom = 85, 35, width-25, height-45
        key, money = CURVES[self.curve.get()]
        def values(history):
            return [(r[key] if key in r else r['sectors'][self.selected()][key])/(100 if money else 1) for r in history]
        base, current = values(self.baseline), values(self.history)
        low, high = min(0, *base, *current), max(1, *base, *current)
        if high-low < 1:
            high = low+1
        for n in range(5):
            y = top+(bottom-top)*n/4
            value = high-(high-low)*n/4
            canvas.create_line(left, y, right, y, fill=COLORS['line'])
            canvas.create_text(left-8, y, text=f'{value:,.0f}', anchor='e', fill=COLORS['muted'])
        canvas.create_text(left, 15, text=f'{self.curve.get()}  /  {"CR" if money else "分" if key=="attractiveness" else "人"}', anchor='w', fill=COLORS['ink'])
        for series, color in [(base, COLORS['baseline']), (current, COLORS['accent'])]:
            points = []
            for n, value in enumerate(series):
                points.extend([left+(right-left)*n/max(1,len(series)-1), bottom-(value-low)/(high-low)*(bottom-top)])
            if len(points) >= 4:
                canvas.create_line(*points, fill=color, width=2)
            else:
                x, y = points
                canvas.create_oval(x-3, y-3, x+3, y+3, fill=color, outline=color)
        canvas.create_text(left, bottom+22, text='第1月', anchor='w', fill=COLORS['muted'])
        canvas.create_text(right, bottom+22, text=f'第{len(current)}月', anchor='e', fill=COLORS['muted'])

    def reset(self):
        self.scenario = default_scenario()
        self._invalid = False
        self._sync_controls()
        self.request_compute()

    def save_scenario(self):
        if self._invalid:
            self.status.set('请先修正无效参数，再保存。')
            return
        path = filedialog.asksaveasfilename(parent=self, title='保存实验方案', defaultextension='.json', filetypes=[('JSON方案', '*.json')])
        if path:
            try:
                months = int(self.months.get())
                if not 1 <= months <= 120:
                    raise ValueError('推演月数须为1—120整数')
                payload = {'format': 'industry-lab-scenario-1', 'months': months, 'scenario': self.scenario}
                Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
                self.status.set(f'方案已保存：{Path(path).name}')
            except (OSError, ValueError) as exc:
                self.status.set(f'保存失败：{exc}')

    def load_scenario(self):
        path = filedialog.askopenfilename(parent=self, title='载入实验方案', filetypes=[('JSON方案', '*.json')])
        if not path:
            return
        try:
            payload = json.loads(Path(path).read_text(encoding='utf-8-sig'))
            candidate = payload.get('scenario', payload)
            months = payload.get('months', int(self.months.get()))
            if type(months) is not int or not 1 <= months <= 120:
                raise ValueError('方案中的推演月数无效')
            validate_scenario(candidate)
            self.scenario, self._invalid = candidate, False
            self.months.set(str(months))
            self._sync_controls()
            self.request_compute()
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.status.set(f'载入失败，保留原方案：{exc}')

    def export_report(self):
        if not self.history or self._invalid or self._result_revision != self._revision or self._future or self._pending or self._debounce:
            self.status.set('请等待当前有效方案推演完成，再导出报告。')
            return
        path = filedialog.asksaveasfilename(parent=self, title='导出逐月报告与账本', defaultextension='.json', filetypes=[('JSON报告', '*.json')])
        if path:
            try:
                export_bundle(path, self.scenario, self.baseline, self.history)
                self.status.set(f'报告已导出：{Path(path).name}（含生效参数、基准、逐月真实交易）')
            except OSError as exc:
                self.status.set(f'导出失败：{exc}')

    def close(self):
        self._closed = True
        self._executor.shutdown(wait=False, cancel_futures=True)
        self.destroy()


def main():
    setup_logging()
    logger.info('行业经济实验室启动，共用V2.1行业运行内核')
    try:
        EconomyLab().mainloop()
    finally:
        logger.info('行业经济实验室已关闭')


if __name__ == '__main__':
    main()
