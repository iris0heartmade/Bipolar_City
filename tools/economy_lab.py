"""V2.1行业经济实验室。

阶段衔接（2026-10-16）：按阶段 15 思想升级：
- 左侧玩家操作区：3 个新增可调参数（`living_cost_cr` / `household_consumption_size` /
  `external_local_split`）+ 6 类市政指令按钮（subsidy_industry / hire_civil /
  pardon_welfare / limit_hire / limit_layoff / tax_amnesty）+ purchase_external
- 右侧指标区：13 列行业表（就业/容纳/目标/吸引力/月薪/营业额/税前利润/
  经营现金/成品库存/原料库存/售价/亏损月数/crisis）
- 趋势曲线：21 个可选指标 + 「清空折线」按钮
- 当前生效政策展示（_active_policies 跨月生效）

布局思路保留原 EconomyLab 的 PanedWindow（左侧参数 + 右侧指标），新增：
- 左侧下方「市政指令区」（subsidy/hire/pardon/limit-hire/limit-layoff/tax-amnesty/
  purchase-external 七按钮 + 各自参数输入）
- 右侧下方「政策与流水区」：当前生效政策 + 近期市政指令流水
"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import logging
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, ttk

from backend.logging_setup import setup_logging
from backend.systems.v21.industry_economy import IndustryEconomy
from backend.systems.v21.industry_scenario import (
    change_control, control_group, default_scenario, simulate, validate_scenario,
)

logger = logging.getLogger(__name__)

# 设计规范：纯度色 + 金色 accent，曲线/表格保留可读性
COLORS = {
    'bg': '#edf1f5', 'ink': '#172b3a', 'muted': '#607080',
    'accent': '#147d76', 'baseline': '#98a7b5', 'line': '#dbe3eb',
    'error': '#b53d38', 'warn': '#d97c5a', 'good': '#3d8a4e',
}

# 21 个可选折线指标（覆盖阶段 15 web GUI 同款曲线集）。
# 每条 (中文名, key_in_report, kind)
#   kind ∈ {'top','industry','diff'} → top 是顶层指标，
#   industry 是从 sectors[i] 取，iid 留给调用方注入。
CURVES = [
    ('总就业人数',        ('employment_sum',       'top')),       # 顶层：sum(sectors[i].employed)
    ('总失业人数',        ('unemployed',            'top')),
    ('政府现金',          ('government_cash_cents', 'top')),
    ('居民总储蓄',        ('household_cash_cents',  'top')),
    ('居民实际消费',      ('consumer_spending_cents','top')),
    ('七行业总营业额',    ('sales_cents',           'top')),
    ('七行业总工资',      ('wages_cents',           'top')),
    ('七行业总现金',      ('industry_cash_cents',   'top')),
    ('外部现金池',        ('external_cash_cents',   'top')),
    ('OCEAN 池',          ('ocean_cash',            'top')),
    ('外部原料单位',      ('external_raw_units',    'top')),
    ('可劳动人口',        ('workforce_population',  'top')),
    ('家庭人口',          ('household_population',  'top')),
    ('月生活成本',        ('living_cost_cents',     'top')),
    ('财政月度结余',      ('fiscal_balance_cents',  'top')),
    ('工资欠付累计',      ('wage_arrears_cents',    'top')),
    ('守恒残差',          ('cash_residual_cents',   'top')),
    # 行业级
    ('IND 就业',          ('employed',              'industry', 'IND')),
    ('IND 经营现金',      ('cash_cents',            'industry', 'IND')),
    ('IND 亏损月数',      ('loss_streak_months',    'industry', 'IND')),
    ('IND 销售额',        ('sales_cents',           'industry', 'IND')),
]

CURVE_LABELS = [c[0] for c in CURVES]

# 政策指令种类（阶段 15 新增的 6 类 + 阶段 14 续的 purchase_external）
MAYOR_COMMANDS = [
    ('subsidy_industry',  '行业补贴',       'industries',  {'cash_cr': True}),
    ('hire_civil',        '雇公务员',       None,          {'people': True}),
    ('pardon_welfare',    '豁免救济',       None,          {'cash_cr': True}),
    ('limit_hire',        '限招',           'industries',  {'policy_months': True}),
    ('limit_layoff',      '限裁',           'industries',  {'policy_months': True}),
    ('tax_amnesty',       '退税',           'industries',  {}),
    ('purchase_external', '市政采购外部',   None,          {'cash_cr': True, 'raw_units': True}),
]

GROUPS = {
    'city': '城市环境与财政',
    'sector': '所选行业',
    'flow': '劳动力流动',
    'capacity': '容纳限度',
    'attractiveness': '评价反馈',
    'weights': '吸引力权重（自动联动）',
}

BOTTLENECKS = {'building': '建筑岗位', 'cash': '当月工资资金',
               'recurring_finance': '持续经营资金', 'inputs': '原料'}


def compute_comparison(scenario, months):
    return simulate(default_scenario(), months), simulate(scenario, months)


def export_bundle(path, scenario, baseline, history):
    payload = {'format': 'industry-lab-report-1', 'scenario': scenario,
               'baseline': baseline, 'history': history,
               'scope': scenario['scope']}
    Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                          encoding='utf-8')


# 行业表 13 列
TABLE_COLUMNS = [
    ('name',           '行业',         78, 'w'),
    ('employed',       '就业',         58, 'e'),
    ('capacity',       '容纳',         58, 'e'),
    ('target',         '目标',         58, 'e'),
    ('attractiveness', '吸引力',       62, 'e'),
    ('wage_cents',     '月薪 CR',      78, 'e'),
    ('sales_cents',    '营业额 CR',    100, 'e'),
    ('profit_cents',   '税前利润 CR',  108, 'e'),
    ('cash_cents',     '经营现金 CR',  110, 'e'),
    ('stock_units',    '成品库存',      78, 'e'),
    ('raw_units',      '原料库存',      78, 'e'),
    ('price_cents',    '售价 CR',      78, 'e'),
    ('loss_streak',    '亏损月数',      70, 'e'),
]


class EconomyLab(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('BIPOLE · V2.1 行业经济实验室')
        self.geometry('1500x940')
        self.minsize(1180, 760)
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
        self.curve = tk.StringVar(value=CURVE_LABELS[0])
        self.status = tk.StringVar(value='正在计算基准与当前方案…')
        self.summary = tk.StringVar(value='准备实验数据')
        self.explanation = tk.StringVar()
        # 市政指令输入变量
        self.cmd_kind = tk.StringVar(value=MAYOR_COMMANDS[0][0])
        self.cmd_industry = tk.StringVar(value='IND')
        self.cmd_cash_cr = tk.StringVar(value='0')
        self.cmd_people = tk.StringVar(value='0')
        self.cmd_policy_months = tk.StringVar(value='1')
        self.cmd_raw_units = tk.StringVar(value='0')
        style = ttk.Style(self)
        style.theme_use('clam')
        style.configure('.', font=('Microsoft YaHei', 10))
        style.configure('Treeview', rowheight=28, font=('Microsoft YaHei', 9))
        style.configure('Treeview.Heading', font=('Microsoft YaHei', 9, 'bold'))
        # crisis 行用 tag 标红
        style.configure('Crisis.Treeview.Row', background='#fde7e3')
        self._build()
        self._syncing = True
        self._build_controls()
        self._build_mayor_panel()
        self._syncing = False
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.after(40, self.request_compute)
        self.after(50, self._poll)

    def _build(self):
        header = tk.Frame(self, bg=COLORS['ink'], padx=22, pady=13)
        header.pack(fill='x')
        tk.Label(header, text='BIPOLE / 行业经济实验室 · 阶段 15 升级版',
                 bg=COLORS['ink'], fg='white',
                 font=('Microsoft YaHei', 19, 'bold')).pack(anchor='w')
        tk.Label(header, text='左侧调参+市政指令 · 右侧指标+政策 · 21 指标趋势曲线    ·    标准交付包场景 / 固定人口 / 有限外部资金',
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

        # 主分体（左右两栏，左侧参数+市政指令，右侧指标+曲线）
        body = ttk.Panedwindow(self, orient='horizontal')
        body.pack(fill='both', expand=True, padx=15, pady=(0, 0))

        # === 左侧 ===
        left = ttk.Frame(body, width=380)
        body.add(left, weight=0)
        # 上：参数滑块
        param_label = tk.Label(left, text='1. 调参实验室', bg=COLORS['bg'],
                               font=('Microsoft YaHei', 11, 'bold'), fg=COLORS['accent'])
        param_label.pack(anchor='w', padx=2, pady=(4, 0))
        self.parameter_canvas = tk.Canvas(left, bg='white', highlightthickness=0, width=370)
        scroll = ttk.Scrollbar(left, orient='vertical', command=self.parameter_canvas.yview)
        self.parameter_canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        self.parameter_canvas.pack(side='left', fill='both', expand=True)
        self.parameter_frame = tk.Frame(self.parameter_canvas, bg='white', padx=12, pady=7)
        self.parameter_canvas.create_window((0, 0), window=self.parameter_frame, anchor='nw', width=350)
        self.parameter_frame.bind('<Configure>',
                                  lambda _: self.parameter_canvas.configure(scrollregion=self.parameter_canvas.bbox('all')))
        self.parameter_canvas.bind('<MouseWheel>',
                                  lambda e: self.parameter_canvas.yview_scroll(-int(e.delta/120), 'units'))

        # 下：市政指令面板（构建占位，由 _build_mayor_panel 填充）
        self.mayor_panel = tk.Frame(self, bg=COLORS['bg'])
        self.mayor_panel.pack(fill='x', padx=15, pady=(0, 0))

        # === 右侧 ===
        right = ttk.Frame(body, padding=(14, 0, 0, 0))
        body.add(right, weight=1)
        ttk.Label(right, textvariable=self.summary, font=('Microsoft YaHei', 12, 'bold'),
                  wraplength=1100).pack(anchor='w', pady=(0, 8))

        # 13 列行业表
        columns = tuple(c[0] for c in TABLE_COLUMNS)
        widths = tuple(c[2] for c in TABLE_COLUMNS)
        anchors = tuple(c[3] for c in TABLE_COLUMNS)
        labels = tuple(c[1] for c in TABLE_COLUMNS)
        self.table = ttk.Treeview(right, columns=columns, show='headings', height=7, selectmode='browse')
        for col, label, w, anc in zip(columns, labels, widths, anchors):
            self.table.heading(col, text=label)
            self.table.column(col, width=w, minwidth=40, stretch=True, anchor=anc)
        self.table.pack(fill='x')
        self.table.bind('<<TreeviewSelect>>', self._select_table)

        ttk.Label(right, textvariable=self.explanation, wraplength=1100,
                  foreground=COLORS['muted']).pack(anchor='w', pady=8)

        # 政策 + 流水区（新增）
        policy_frame = tk.Frame(right, bg='white', highlightthickness=1,
                                highlightbackground=COLORS['line'])
        policy_frame.pack(fill='x', pady=(6, 0))
        tk.Label(policy_frame, text='当前生效政策 / 近期市政指令流水',
                 bg='white', fg=COLORS['accent'],
                 font=('Microsoft YaHei', 10, 'bold')).pack(anchor='w', padx=8, pady=(6, 0))
        self.policy_text = tk.Text(policy_frame, height=5, bg='white', fg=COLORS['ink'],
                                   font=('Microsoft YaHei', 9), wrap='word', relief='flat')
        self.policy_text.pack(fill='x', padx=8, pady=(0, 6))

        # 趋势图
        plotbar = ttk.Frame(right)
        plotbar.pack(fill='x', pady=(5, 0))
        ttk.Label(plotbar, text='趋势指标（21 选 1）').pack(side='left')
        selector = ttk.Combobox(plotbar, textvariable=self.curve,
                                 values=CURVE_LABELS, state='readonly', width=24)
        selector.pack(side='left', padx=8)
        selector.bind('<<ComboboxSelected>>', lambda _: self.draw_chart())
        ttk.Button(plotbar, text='清空折线', command=self.clear_chart).pack(side='left', padx=(0, 8))
        ttk.Label(plotbar, text='灰：原始基准    绿：当前方案',
                  foreground=COLORS['muted']).pack(side='right')
        self.chart = tk.Canvas(right, bg='white', highlightthickness=1,
                               highlightbackground=COLORS['line'])
        self.chart.pack(fill='both', expand=True, pady=(6, 0))
        self.chart.bind('<Configure>', lambda _: self.draw_chart())

        tk.Label(self, textvariable=self.status, anchor='w', padx=15, pady=9,
                 fg=COLORS['muted'], bg=COLORS['bg'], wraplength=1400).pack(fill='x')

    def _build_controls(self):
        """调参滑块（按 groups 分组），并新增 3 个阶段 15 参数。"""
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
        # 阶段 15 新增 3 个可调参数（city.*）
        self._add_extra_slider(
            'living_cost_cr', '月生活成本', 'cr', 20, 120, 1, lambda v: self._set_city_param('living_cost_cr', v),
            note='每位就业者每月基础支出 CR；同 living_cost_cr 同段（同口径）。')
        self._add_extra_slider(
            'household_consumption_size', '家庭人口', '人/就业者', 1.0, 4.0, 0.1,
            lambda v: self._set_city_param('household_consumption_size', v),
            note='每位就业者背后的家庭人口（1.0—4.0）；真实消费基数 = 就业 × 此值 × 月生活成本。')
        self._add_extra_slider(
            'external_local_split', '外销本地分成', '比例', 0, 1, 0.05,
            lambda v: self._set_city_param('external_local_split', v),
            note='外部订单在本地交付的比例；剩余走 EXT 出口。')

    def _add_extra_slider(self, key, label, unit, lo, hi, step, on_change, note=''):
        """阶段 15 新增的 city.* 参数滑块（在默认 controls 之外）。"""
        frame = tk.Frame(self.parameter_frame, bg='white')
        frame.pack(fill='x', pady=(0, 7))
        tk.Label(frame, text=label, bg='white', anchor='w').pack(anchor='w')
        value = self.scenario['city'].get(key, lo)
        variable = tk.StringVar(value=f'{value:g}')
        row = tk.Frame(frame, bg='white')
        row.pack(fill='x')
        scale = ttk.Scale(row, from_=lo, to=hi, command=lambda v, k=key, s=step, cb=on_change: cb(round(float(v)/s)*s))
        scale.set(value)
        scale.pack(side='left', fill='x', expand=True, padx=(0, 8))
        entry = ttk.Entry(row, textvariable=variable, width=8)
        entry.pack(side='left')
        entry.bind('<Return>', lambda _, k=key, cb=on_change: cb(float(variable.get())))
        entry.bind('<FocusOut>', lambda _, k=key, cb=on_change: cb(float(variable.get())))
        tk.Label(frame, text=f'{lo:g} ～ {hi:g}  {unit}', bg='white', fg=COLORS['muted'],
                 font=('Microsoft YaHei', 8)).pack(anchor='w')
        if note:
            tk.Label(frame, text=note, bg='white', fg=COLORS['muted'],
                     font=('Microsoft YaHei', 8), wraplength=320, justify='left').pack(anchor='w')
        self.variables[('city_extra', key)] = variable
        self.entries[('city_extra', key)] = (entry, scale)

    def _set_city_param(self, key, value):
        if self._syncing:
            return
        try:
            validate_scenario(self.scenario)
        except Exception:  # noqa: BLE001
            pass
        self.scenario['city'][key] = float(value)
        self.status.set(f'已更新 {key} = {value}；下次重算时生效。')
        self.request_compute()

    def _build_mayor_panel(self):
        """左侧下方面板：阶段 15 思想的 6 类市政指令 + purchase_external。"""
        outer = tk.LabelFrame(self.mayor_panel, text='2. 市政指令（按阶段 15 思想）',
                              bg=COLORS['bg'], fg=COLORS['accent'],
                              font=('Microsoft YaHei', 11, 'bold'), padx=8, pady=6)
        outer.pack(fill='x', expand=True, pady=(6, 0))
        tk.Label(outer, text='指令会修改当前游戏状态，并立即重新推演整段时间轴。',
                 bg=COLORS['bg'], fg=COLORS['muted'], font=('Microsoft YaHei', 8)).pack(anchor='w')

        kind_row = tk.Frame(outer, bg=COLORS['bg'])
        kind_row.pack(fill='x', pady=(4, 0))
        tk.Label(kind_row, text='指令', bg=COLORS['bg']).pack(side='left')
        kind_combo = ttk.Combobox(kind_row, textvariable=self.cmd_kind, state='readonly', width=18,
                                  values=[f'{k}  {n}' for k, n, *_ in MAYOR_COMMANDS])
        kind_combo.pack(side='left', padx=(4, 8))

        # 行业选择（仅 subsidy/limit_hire/limit_layoff/tax_amnesty 需要）
        tk.Label(kind_row, text='行业', bg=COLORS['bg']).pack(side='left')
        ind_combo = ttk.Combobox(kind_row, textvariable=self.cmd_industry, state='readonly',
                                 width=8, values=list(self.scenario['sectors']))
        ind_combo.pack(side='left', padx=(4, 8))

        # 参数输入行
        param_row = tk.Frame(outer, bg=COLORS['bg'])
        param_row.pack(fill='x', pady=(4, 0))
        tk.Label(param_row, text='现金(CR)', bg='#fff').pack(side='left')
        tk.Entry(param_row, textvariable=self.cmd_cash_cr, width=8).pack(side='left', padx=(2, 8))
        tk.Label(param_row, text='人数', bg='#fff').pack(side='left')
        tk.Entry(param_row, textvariable=self.cmd_people, width=6).pack(side='left', padx=(2, 8))
        tk.Label(param_row, text='生效月', bg='#fff').pack(side='left')
        tk.Entry(param_row, textvariable=self.cmd_policy_months, width=4).pack(side='left', padx=(2, 8))
        tk.Label(param_row, text='原料', bg='#fff').pack(side='left')
        tk.Entry(param_row, textvariable=self.cmd_raw_units, width=8).pack(side='left', padx=(2, 8))

        # 执行按钮
        exec_row = tk.Frame(outer, bg=COLORS['bg'])
        exec_row.pack(fill='x', pady=(4, 0))
        ttk.Button(exec_row, text='执行市政指令',
                   command=self.execute_mayor_command).pack(side='left', padx=(0, 6))
        ttk.Button(exec_row, text='清空当前方案历史', command=self.reset).pack(side='left', padx=(0, 6))

    # ------------------------------------------------------------------
    # 市政指令执行（直接 mutate scenario 然后重新 simulate）
    # ------------------------------------------------------------------
    def execute_mayor_command(self):
        if self._future or self._pending or self._debounce:
            self.status.set('上次推演还在进行中，请稍候…')
            return
        kind_full = self.cmd_kind.get()
        kind = kind_full.split()[0]
        try:
            cash_cr = float(self.cmd_cash_cr.get())
            people = int(self.cmd_people.get())
            months = int(self.cmd_policy_months.get())
            raw_units = float(self.cmd_raw_units.get())
            industry = self.cmd_industry.get()
        except ValueError as exc:
            self.status.set(f'参数错误：{exc}')
            return
        try:
            self._apply_mayor_command(kind, industry, cash_cr, people, months, raw_units)
            self.status.set(f'市政指令 {kind} 已生效（行业 {industry}）；正在重新推演…')
            self.request_compute()
        except (ValueError, KeyError) as exc:
            self.status.set(f'指令被拒：{exc}')

    def _apply_mayor_command(self, kind, industry, cash_cr, people, months, raw_units):
        # 用最新 scenario 派生 IndustryEconomy，调 mayor_command 后回写
        candidate = IndustryEconomy(deepcopy(self.scenario))
        if kind == 'purchase_external':
            paid_cash, paid_raw, reason = candidate.purchase_external(
                cash_cents=int(round(cash_cr * 100)),
                raw_units=raw_units, from_account='GOV',
                note=f'实验室 {kind}')
            if reason and reason != 'empty':
                raise ValueError(reason)
        else:
            ok, reason, _ = candidate.mayor_command(
                kind=kind, industry=industry, cash_cr=cash_cr, people=people,
                months=months, note=f'实验室 {kind}')
            if not ok:
                raise ValueError(reason)
        # 回写 scenario（to_dict 内部已深拷贝）
        self.scenario = candidate.to_dict()

    # ------------------------------------------------------------------
    # 原有调参 / 模拟 / 渲染逻辑
    # ------------------------------------------------------------------
    def selected(self):
        return self.industry.get().split()[0]

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
            if section == 'city_extra':
                value = self.scenario['city'].get(key, 0)
            else:
                value = control_group(self.scenario, section, self.selected())[key]
            variable.set(f'{value:.6g}')
            self.entries[(section, key)][1].set(value)
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

    def _resolve_curve_value(self, report, curve_spec):
        """把曲线规范解析为 report 中的数值。"""
        name, spec = curve_spec
        if len(spec) == 2:
            kind = spec[0]
            key = spec[1]
        else:
            kind, key = spec[0], spec[1]
            ind = spec[2]
        if kind == 'top':
            if key == 'employment_sum':
                return sum(report['employment'].values())
            return report.get(key, 0)
        if kind == 'industry':
            return report['sectors'].get(ind, {}).get(key, 0)
        return 0

    def render(self):
        if not self.history:
            return
        report = self.history[-1]
        baseline = self.baseline[-1]
        employed = sum(report['employment'].values())
        difference = employed - sum(baseline['employment'].values())
        # 顶部 summary（按阶段 15 拓展：加 OCEAN / 外部池 / 守恒残差 / 活跃政策数）
        active_n = len(report.get('active_policies', {}))
        actions_n = len(report.get('recent_mayor_actions', []))
        self.summary.set(
            f'第 {report["month"]} 月  ·  就业 {employed:,} 人（较基准 {difference:+,}）  ·  失业 {report["unemployed"]:,} 人\n'
            f'政府 {report["government_cash_cents"]/100:,.0f} CR  /  居民储蓄 {report["household_cash_cents"]/100:,.0f} CR  /  '
            f'OCEAN {report.get("ocean_cash", 0)/100:,.0f} CR  /  守恒残差 {report["cash_residual_cents"]} 分\n'
            f'活跃政策 {active_n} 条  /  本月指令流水 {actions_n} 条'
        )
        # 13 列行业表
        for i, s in report['sectors'].items():
            values = (
                s['name'], s['employed'], s['capacity'], s['target'],
                f"{s['attractiveness']:.1f}",
                f"{s['wage_cents']/100:,.2f}",
                f"{s['sales_cents']/100:,.0f}",
                f"{s['profit_cents']/100:,.0f}" if s.get('profit_cents') is not None else '—',
                f"{s['cash_cents']/100:,.0f}",
                f"{s['stock_units']:,.1f}",
                f"{s['raw_units']:,.1f}",
                f"{s['price_cents']/100:,.2f}",
                f"{s.get('loss_streak_months', 0)}",
            )
            tag = ('crisis',) if s.get('crisis') else ()
            if self.table.exists(i):
                self.table.item(i, values=values, tags=tag)
            else:
                self.table.insert('', 'end', iid=i, values=values, tags=tag)
        self.table.tag_configure('crisis', background='#fde7e3', foreground=COLORS['error'])
        sector = report['sectors'][self.selected()]
        short = '、'.join(BOTTLENECKS[k] for k in sector['capacity_details']['bottlenecks'])
        self.explanation.set(
            f"{sector['name']}：用工目标 {sector['target']} 人；容纳短板：{short}。"
            f"产出 {sector['output_units']:,.1f} 标准单位；原料 {sector['raw_units']:,.1f} 单位。"
            f"亏损月数 {sector.get('loss_streak_months', 0)}；crisis={sector.get('crisis')}。"
        )
        # 政策 + 流水文本框
        self._render_policy_text()
        self.draw_chart()

    def _render_policy_text(self):
        report = self.history[-1] if self.history else {}
        lines = []
        active = report.get('active_policies') or {}
        if active:
            lines.append('【活跃政策 _active_policies】')
            for key, expiry in active.items():
                lines.append(f'  · {key} → 过期月份 {expiry}')
        else:
            lines.append('【活跃政策】当前无')
        actions = report.get('recent_mayor_actions') or []
        if actions:
            lines.append('【近期市政指令流水】')
            for a in actions[-5:]:
                ind = a.get('industry', '—')
                cash = a.get('cash_cents', 0) / 100 if a.get('cash_cents') else 0
                lines.append(f'  · {a.get("kind", "—")} / {ind} / {cash:,.0f} CR')
        else:
            lines.append('【近期指令流水】本月尚无')
        self.policy_text.delete('1.0', 'end')
        self.policy_text.insert('1.0', '\n'.join(lines))

    def clear_chart(self):
        self.baseline, self.history = [], []
        self.summary.set('折线已清空。请调整参数或下达指令后再次推演。')
        self.status.set('趋势线已清空。')
        self.draw_chart()

    def draw_chart(self):
        canvas = self.chart
        canvas.delete('all')
        if not self.history or canvas.winfo_width() < 150:
            return
        width, height = canvas.winfo_width(), canvas.winfo_height()
        left, top, right, bottom = 85, 35, width-25, height-45
        # 找到当前选中的曲线规范
        label = self.curve.get()
        spec = next(((name, kw, sc) for (name, kw, *sc) in CURVES if name == label), None)
        if spec is None:
            return
        money = ('cash_cents' in spec[1] or 'sales_cents' in spec[1] or 'wage_cents' in spec[1]
                 or 'cents' in spec[1] or spec[1] == 'government_cash_cents'
                 or spec[1] == 'household_cash_cents' or spec[1] == 'industry_cash_cents'
                 or spec[1] == 'external_cash_cents' or spec[1] == 'ocean_cash'
                 or spec[1] == 'consumer_spending_cents' or spec[1] == 'fiscal_balance_cents'
                 or spec[1] == 'wage_arrears_cents' or spec[1] == 'living_cost_cents'
                 or spec[1] == 'cash_residual_cents')

        def values(history):
            return [self._resolve_curve_value(r, (label, spec)) / (100 if money else 1) for r in history]
        base, current = values(self.baseline), values(self.history)
        low, high = min(0, *base, *current), max(1, *base, *current)
        if high - low < 1:
            high = low + 1
        for n in range(5):
            y = top + (bottom - top) * n / 4
            value = high - (high - low) * n / 4
            canvas.create_line(left, y, right, y, fill=COLORS['line'])
            canvas.create_text(left - 8, y, text=f'{value:,.0f}', anchor='e', fill=COLORS['muted'])
        unit = 'CR' if money else ('人' if 'employment' in spec[1] or 'population' in spec[1]
                                    or spec[1] in ('unemployed', 'external_raw_units', 'employed', 'loss_streak_months')
                                    else '分')
        canvas.create_text(left, 15, text=f'{label} / {unit}', anchor='w', fill=COLORS['ink'])
        for series, color in [(base, COLORS['baseline']), (current, COLORS['accent'])]:
            points = []
            for n, value in enumerate(series):
                points.extend([left + (right - left) * n / max(1, len(series) - 1),
                               bottom - (value - low) / (high - low) * (bottom - top)])
            if len(points) >= 4:
                canvas.create_line(*points, fill=color, width=2)
            else:
                x, y = points
                canvas.create_oval(x - 3, y - 3, x + 3, y + 3, fill=color, outline=color)
        canvas.create_text(left, bottom + 22, text='第1月', anchor='w', fill=COLORS['muted'])
        canvas.create_text(right, bottom + 22, text=f'第{len(current)}月', anchor='e', fill=COLORS['muted'])

    def reset(self):
        self.scenario = default_scenario()
        self._invalid = False
        self._sync_controls()
        self.request_compute()

    def save_scenario(self):
        if self._invalid:
            self.status.set('请先修正无效参数，再保存。')
            return
        path = filedialog.asksaveasfilename(parent=self, title='保存实验方案',
                                            defaultextension='.json',
                                            filetypes=[('JSON方案', '*.json')])
        if path:
            try:
                months = int(self.months.get())
                if not 1 <= months <= 120:
                    raise ValueError('推演月数须为1—120整数')
                payload = {'format': 'industry-lab-scenario-1', 'months': months, 'scenario': self.scenario}
                Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                                      encoding='utf-8')
                self.status.set(f'方案已保存：{Path(path).name}')
            except (OSError, ValueError) as exc:
                self.status.set(f'保存失败：{exc}')

    def load_scenario(self):
        path = filedialog.askopenfilename(parent=self, title='载入实验方案',
                                          filetypes=[('JSON方案', '*.json')])
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
        path = filedialog.asksaveasfilename(parent=self, title='导出逐月报告与账本',
                                            defaultextension='.json',
                                            filetypes=[('JSON报告', '*.json')])
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
    logger.info('V2.1 行业经济实验室启动（阶段 15 升级版）')
    try:
        EconomyLab().mainloop()
    finally:
        logger.info('V2.1 行业经济实验室已关闭')


if __name__ == '__main__':
    main()