"""行业经济实验室：同城连续月进、政策试验与可恢复运行存档。

窗口只在主线程修改；单个工作线程在会话副本上推进，核对修订号后
整体提交。参数调整和政策不重建城市，配置方案与运行存档分开保存。
"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import logging
import math
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, ttk

from tools.lab_session import LabSession, write_json_atomic
from backend.logging_setup import setup_logging
from backend.systems.v21.industry_scenario import (
    control_group, default_scenario, simulate, validate_scenario,
)

logger = logging.getLogger(__name__)

# 设计规范：纯度色 + 金色 accent，曲线/表格保留可读性
COLORS = {
    'bg': '#edf1f5', 'ink': '#172b3a', 'muted': '#607080',
    'accent': '#147d76', 'baseline': '#98a7b5', 'line': '#dbe3eb',
    'error': '#b53d38', 'warn': '#d97c5a', 'good': '#3d8a4e',
}

# 可选历史折线指标；均读取已经完成的月报。
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
    ('七行业工资实付',    ('paid_wages_cents',      'top')),
    ('七行业总现金',      ('industry_cash_cents',   'top')),
    ('外部现金池',        ('external_cash_cents',   'top')),
    ('OCEAN 池',          ('ocean_cash',            'top')),
    ('外部原料单位',      ('external_raw_units',    'top')),
    ('总居民人数',        ('population',            'top')),
    ('劳动人数',          ('labor_force',           'top')),
    ('消费等价人数',      ('household_population',  'top')),
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
    ('pardon_welfare',    '追加失业救济',       None,          {'cash_cr': True}),
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


def export_bundle(path, scenario, baseline, history, commands=None):
    payload = {'format': 'industry-lab-report-1', 'scenario': scenario,
               'baseline': baseline, 'history': history,
               'scope': scenario['scope'], 'commands': deepcopy(commands or [])}
    write_json_atomic(path, payload)


# 行业表 13 列
TABLE_COLUMNS = [
    ('name',           '行业',         78, 'w'),
    ('employed',       '当前就业',         58, 'e'),
    ('capacity',       '容纳',         58, 'e'),
    ('target',         '目标',         58, 'e'),
    ('attractiveness', '吸引力',       62, 'e'),
    ('wage_cents',     '月薪 CR',      78, 'e'),
    ('sales_cents',    '营业额 CR',    100, 'e'),
    ('profit_cents',   '税前利润 CR',  108, 'e'),
    ('cash_cents',     '当前现金 CR',  110, 'e'),
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
        self.session = LabSession()
        self.scenario = self.session.scenario
        self.baseline, self.history = self.session.baseline, self.session.history
        self._chart_start = 0
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='industry-lab')
        self._future, self._pending, self._debounce = None, None, None
        self._revision, self._closed, self._syncing, self._invalid = 0, False, False, False
        self._result_revision = -1
        self.variables, self.entries, self.row_frames = {}, {}, {}
        self.industry = tk.StringVar(value='IND 工业')
        self.months = tk.StringVar(value='1')
        self.curve = tk.StringVar(value=CURVE_LABELS[0])
        self.status = tk.StringVar(value='第0月：调整参数或推进下一月。')
        self.summary = tk.StringVar(value='第0月 · 尚未结算')
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
        self._result_revision = self._revision
        self.after(40, self.render)
        self.after(50, self._poll)

    def _build(self):
        header = tk.Frame(self, bg=COLORS['ink'], padx=22, pady=13)
        header.pack(fill='x')
        tk.Label(header, text='BIPOLE / 行业经济实验室 · 持续月进',
                 bg=COLORS['ink'], fg='white',
                 font=('Microsoft YaHei', 19, 'bold')).pack(anchor='w')
        tk.Label(header, text=f'左侧调参+市政指令 · 右侧当前状态与最近月报 · {len(CURVES)}项历史曲线 · 月度人口变化 / 有限外部资金',
                 bg=COLORS['ink'], fg='#b8d0d7').pack(anchor='w', pady=(5, 0))
        toolbar = ttk.Frame(self, padding=(15, 10))
        toolbar.pack(fill='x')
        for text, command in [('恢复基准', self.reset), ('保存方案', self.save_scenario),
                              ('载入方案', self.load_scenario), ('保存运行', self.save_session),
                              ('载入运行', self.load_session), ('导出报告', self.export_report)]:
            ttk.Button(toolbar, text=text, command=command).pack(side='left', padx=(0, 7))
        ttk.Label(toolbar, text='行业').pack(side='left', padx=(15, 5))
        choose = ttk.Combobox(toolbar, textvariable=self.industry, state='readonly', width=15,
                              values=[f'{i} {d["name"]}' for i, d in self.scenario['sectors'].items()])
        choose.pack(side='left')
        choose.bind('<<ComboboxSelected>>', self._select_industry)
        ttk.Label(toolbar, text='推演月数').pack(side='left', padx=(15, 5))
        month_box = ttk.Spinbox(toolbar, from_=1, to=120, textvariable=self.months, width=5)
        month_box.pack(side='left')
        ttk.Button(toolbar, text='下一月', command=lambda: self.request_compute(1)).pack(side='left', padx=4)
        ttk.Button(toolbar, text='推进N月', command=self.request_compute).pack(side='left')

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
        ttk.Label(plotbar, text=f'历史趋势指标（{len(CURVES)}选1）').pack(side='left')
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
    def _build_mayor_panel(self):
        """左侧下方面板：阶段 15 思想的 6 类市政指令 + purchase_external。"""
        outer = tk.LabelFrame(self.mayor_panel, text='2. 市政指令（对当前城市执行）',
                              bg=COLORS['bg'], fg=COLORS['accent'],
                              font=('Microsoft YaHei', 11, 'bold'), padx=8, pady=6)
        outer.pack(fill='x', expand=True, pady=(6, 0))
        tk.Label(outer, text='指令作用于当前城市；政策从下一结算月生效，不重置历史。',
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
        ttk.Button(exec_row, text='恢复基准并重建城市', command=self.reset).pack(side='left', padx=(0, 6))

    # ------------------------------------------------------------------
    # 市政指令执行：当前会话上原子提交，保留当前月份和历史
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
            if any(not math.isfinite(v) or v < 0 for v in (cash_cr, raw_units)):
                raise ValueError('金额与原料必须是非负有限数字')
            if not 0 <= people <= 10000 or not 1 <= months <= 120:
                raise ValueError('人数须为0—10000整数，期限须为1—120整数')
        except ValueError as exc:
            self.status.set(f'参数错误：{exc}')
            return
        try:
            self._apply_mayor_command(kind, industry, cash_cr, people, months, raw_units)
            self._revision += 1
            self._publish_session()
            self.status.set(f'市政指令 {kind} 已提交（行业 {industry}），下次月进结算。')
        except (ValueError, KeyError) as exc:
            self.status.set(f'指令被拒：{exc}')

    def _apply_mayor_command(self, kind, industry, cash_cr, people, months, raw_units):
        if kind == 'purchase_external':
            self.session.command(kind, cash_cents=int(round(cash_cr * 100)),
                                 raw_units=raw_units, from_account='GOV', note=f'实验室 {kind}')
        else:
            self.session.command(kind, industry=industry, cash_cr=cash_cr,
                                 people=people, months=months, note=f'实验室 {kind}')

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
            self.status.set(f'参数未生效：{exc}。请修正后重算；当前图表仍是上次有效结果。')

    def _busy(self):
        return bool(self._future or self._pending or self._closed)

    def _publish_session(self):
        self.scenario = self.session.scenario
        self.baseline, self.history = self.session.baseline, self.session.history
        self._result_revision = self._revision
        self.render()

    def set_control(self, section, key, value):
        if self._busy():
            self._sync_controls()
            self.status.set('月进正在结算，请完成后调整参数。')
            return
        self.session.set_parameter(section, key, value, self.selected())
        self._invalid = False
        self._revision += 1
        self._publish_session()
        self._sync_controls()
        self.status.set('参数已更新，只影响后续月份；既有历史保留。')

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

    def request_compute(self, months=None):
        if self._closed or self._invalid or self._busy():
            return
        try:
            months = int(self.months.get()) if months is None else months
            if type(months) is not int or not 1 <= months <= 120:
                raise ValueError('推进月数须为1—120整数')
        except ValueError as exc:
            self.status.set(f'参数未生效：{exc}')
            return
        self._revision += 1
        self._pending = (self._revision, deepcopy(self.session), months)
        self.status.set(f'正在从第{self.session.city.month}月继续推进{months}个月…')

    @staticmethod
    def _advance_session(session, months):
        logger.info('实验室月进开始 month=%d months=%d', session.city.month, months)
        session.advance(months)
        logger.info('实验室月进完成 month=%d reports=%d', session.city.month, len(session.history))
        return session

    def _enqueue(self):
        self.request_compute()

    def _poll(self):
        if self._closed:
            return
        if self._future and self._future[1].done():
            revision, future = self._future
            self._future = None
            try:
                candidate = future.result()
                if revision == self._revision:
                    self.session = candidate
                    self._publish_session()
                    self.status.set(f'第{self.session.city.month}月结算完成；现金、人口与库存已核对。')
            except Exception:
                logger.exception('实验室月进失败 revision=%d', revision)
                self.status.set('月进失败，城市与历史保留；详细原因已写入日志。')
        if not self._future and self._pending:
            revision, session, months = self._pending
            self._pending = None
            self._future = (revision, self._executor.submit(self._advance_session, session, months))
        self._poll_id = self.after(50, self._poll)

    def _resolve_curve_value(self, report, curve_spec):
        """把曲线规范解析为 report 中的数值。"""
        _, spec = curve_spec
        key, kind = spec[:2]
        if kind == 'top':
            if key == 'employment_sum':
                return sum(report['employment'].values())
            return report.get(key, 0)
        if kind == 'industry':
            return report['sectors'].get(spec[2], {}).get(key, 0)
        return 0

    def render(self):
        city, baseline_city = self.session.city, self.session.baseline_city
        employment = city.employment()
        employed = sum(employment.values())
        difference = employed - sum(baseline_city.employment().values())
        report = self.history[-1] if self.history else None
        period = f'最近月报：第{report["month"]}月；曲线为已结算历史' if report else '尚未月结，月营业额等指标显示“—”'
        self.summary.set(
            f'当前状态 · 第 {city.month} 月 · 就业 {employed:,} 人（较基准 {difference:+,}）'
            f' · 失业 {sum(city.unemployed_by_group.values()):,} 人\n'
            f'政府 {city.cash["GOV"]/100:,.2f} CR / 居民储蓄 {sum(city.cash[h] for h in city.population)/100:,.2f} CR'
            f' / OCEAN {city.ocean_cash/100:,.2f} CR\n'
            f'{period} · 当前政策 {len(city._active_policies)} 条'
        )
        for i in city.keys:
            sector = report['sectors'][i] if report else None
            values = (
                city.sector(i)['name'], employment[i],
                sector['capacity'] if sector else '—', sector['target'] if sector else '—',
                f"{sector['attractiveness']:.1f}" if sector else '—',
                f"{sector['wage_cents']/100:,.2f}" if sector else '—',
                f"{sector['sales_cents']/100:,.0f}" if sector else '—',
                f"{sector['profit_cents']/100:,.0f}" if sector and sector.get('profit_cents') is not None else '—',
                f"{city.cash[i]/100:,.2f}", f"{city.stock[i]:,.1f}", f"{city.raw[i]:,.1f}",
                f"{city.prices[i]/100:,.2f}", f"{city.loss_streak.get(i, 0)}",
            )
            tag = ('crisis',) if sector and sector.get('crisis') else ()
            if self.table.exists(i):
                self.table.item(i, values=values, tags=tag)
            else:
                self.table.insert('', 'end', iid=i, values=values, tags=tag)
        self.table.tag_configure('crisis', background='#fde7e3', foreground=COLORS['error'])
        if report:
            sector = report['sectors'][self.selected()]
            short = '、'.join(BOTTLENECKS[k] for k in sector['capacity_details']['bottlenecks']) or '无'
            self.explanation.set(
                f"最近第{report['month']}月报 · {sector['name']}：用工目标 {sector['target']} 人；容纳短板：{short}。"
                f"产出 {sector['output_units']:,.1f} 单位；连续亏损 {sector.get('loss_streak_months', 0)} 个月。"
                '表中就业、现金、库存和售价是当前状态，其余为最近一次月结。'
            )
        else:
            self.explanation.set('当前城市已经建立；表中就业、现金、库存和售价立即显示，月度经营指标将在下一月结算后出现。')
        self._render_policy_text()
        self.draw_chart()

    def _render_policy_text(self):
        report = {'active_policies': {f'{target}|{kind}': expiry for (target, kind), expiry in self.session.city._active_policies.items()},
                  'recent_mayor_actions': self.session.commands}
        lines = []
        active = report.get('active_policies') or {}
        if active:
            lines.append('【当前已下达政策】')
            for key, expiry in active.items():
                lines.append(f'  · {key} → 最后生效月 {expiry}')
        else:
            lines.append('【活跃政策】当前无')
        actions = report.get('recent_mayor_actions') or []
        if actions:
            lines.append('【近期市政指令流水】')
            for a in actions[-5:]:
                ind = a.get('industry', '—')
                cash = a.get('cash_cr', a.get('cash_cents', 0) / 100)
                lines.append(f'  · {a.get("kind", "—")} / {ind} / {cash:,.0f} CR')
        else:
            lines.append('【近期指令流水】尚无操作')
        self.policy_text.delete('1.0', 'end')
        self.policy_text.insert('1.0', '\n'.join(lines))

    def clear_chart(self):
        self._chart_start = len(self.history)
        self.status.set('折线已清空；城市和完整导出历史保留。')
        self.draw_chart()

    def draw_chart(self):
        canvas = self.chart
        canvas.delete('all')
        if len(self.history) <= self._chart_start or canvas.winfo_width() < 150:
            return
        width, height = canvas.winfo_width(), canvas.winfo_height()
        left, top, right, bottom = 85, 35, width-25, height-45
        # 找到当前选中的曲线规范
        label = self.curve.get()
        spec = next((spec for name, spec in CURVES if name == label), None)
        if spec is None:
            return
        key = spec[0]
        money = 'cents' in key or key == 'ocean_cash'

        def values(history):
            return [self._resolve_curve_value(r, (label, spec)) / (100 if money else 1) for r in history]
        base, current = values(self.baseline[self._chart_start:]), values(self.history[self._chart_start:])
        low, high = min(0, *base, *current), max(1, *base, *current)
        if high - low < 1:
            high = low + 1
        for n in range(5):
            y = top + (bottom - top) * n / 4
            value = high - (high - low) * n / 4
            canvas.create_line(left, y, right, y, fill=COLORS['line'])
            canvas.create_text(left - 8, y, text=f'{value:,.0f}', anchor='e', fill=COLORS['muted'])
        unit = 'CR' if money else ('人' if key in ('employment_sum', 'unemployed', 'employed', 'population', 'labor_force', 'household_population') else '单位')
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
        canvas.create_text(left, bottom + 22, text=f'第{self._chart_start + 1}月', anchor='w', fill=COLORS['muted'])
        canvas.create_text(right, bottom + 22, text=f'第{len(self.history)}月', anchor='e', fill=COLORS['muted'])

    def reset(self):
        if self._busy():
            self.status.set('请等待月进完成，再恢复基准。')
            return
        self.session = LabSession()
        self._chart_start = 0
        self._revision += 1
        self._invalid = False
        self._publish_session()
        self._sync_controls()
        self.status.set('已恢复第0月基准，城市和历史已重新建立。')

    def save_session(self):
        if self._busy() or self._invalid:
            self.status.set('请等待结算完成并修正参数后保存。')
            return
        path = filedialog.asksaveasfilename(parent=self, title='保存实验运行', defaultextension='.json')
        if path:
            try:
                write_json_atomic(path, self.session.to_dict())
                self.status.set('当前城市、基准、政策与完整历史已保存。')
            except (OSError, ValueError, TypeError) as exc:
                self.status.set(f'保存失败：{exc}')

    def load_session(self):
        if self._busy():
            self.status.set('请等待月进完成后载入。')
            return
        path = filedialog.askopenfilename(parent=self, title='载入实验运行')
        if path:
            try:
                candidate = LabSession.from_dict(json.loads(Path(path).read_text(encoding='utf-8-sig')))
                self.session = candidate
                self._revision += 1
                self._invalid, self._chart_start = False, 0
                self._publish_session()
                self._sync_controls()
                self.status.set('运行已恢复，可从当前月份继续推进。')
            except (OSError, ValueError, KeyError, TypeError) as exc:
                self.status.set(f'载入失败，原城市保留：{exc}')

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
                write_json_atomic(path, payload)
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
            if not isinstance(payload, dict) or payload.get('format') != 'industry-lab-scenario-1':
                raise ValueError('请选择方案配置；运行状态请使用载入运行')
            candidate = payload['scenario']
            months = payload.get('months', int(self.months.get()))
            if type(months) is not int or not 1 <= months <= 120:
                raise ValueError('方案中的推演月数无效')
            validate_scenario(candidate)
            candidate_session = LabSession(candidate)
            if self._busy():
                raise ValueError("月进正在进行")
            self.session, self._invalid = candidate_session, False
            self._revision += 1
            self._chart_start = 0
            self._publish_session()
            self.months.set(str(months))
            self._sync_controls()
            self.status.set("方案已载入并建立第0月城市。")
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
                export_bundle(path, self.scenario, self.baseline, self.history, self.session.commands)
                self.status.set(f'报告已导出：{Path(path).name}（含生效参数、基准、逐月真实交易）')
            except OSError as exc:
                self.status.set(f'导出失败：{exc}')

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._pending = None
        self._revision += 1
        if hasattr(self, "_poll_id"):
            self.after_cancel(self._poll_id)
        self._executor.shutdown(wait=True, cancel_futures=True)
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
