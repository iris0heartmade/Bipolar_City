"""运行存档校验；只检查数据，不补齐历史或修改余额。"""
import math


def validate_state(data, template):
    def finite(value):
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError('存档含非有限数值')
        if isinstance(value, dict):
            for child in value.values():
                finite(child)
        elif isinstance(value, (tuple, list)):
            for child in value:
                finite(child)
    finite(data)
    keys, groups = set(template.keys), set(template.population)
    for name in ('population', 'unemployed_by_group', 'workers'):
        if not isinstance(data[name], dict) or set(data[name]) != groups:
            raise ValueError(f'存档 {name} 群体不完整')
    for row in data['workers'].values():
        if not isinstance(row, dict) or set(row) != keys:
            raise ValueError('存档就业行业不完整')
    for name in ('definitions', 'base_wages', 'nominal_wages', 'prices', 'qbar', 'full_raw',
                 'raw', 'stock', 'initial_stock', 'external_goods', 'consumed', 'total_produced',
                 'receipts_history', 'wage_history', 'pay_history', 'demand_history',
                 'previous_demand_factor', 'loss_streak', 'arrears'):
        if not isinstance(data[name], dict) or set(data[name]) != keys:
            raise ValueError(f'存档 {name} 行业不完整')
    for name in ('receipts_history', 'wage_history', 'pay_history', 'demand_history'):
        for history in data[name].values():
            if not isinstance(history, list) or not 1 <= len(history) <= 3:
                raise ValueError(f'存档 {name} 历史缺失')
            if any(type(v) not in (int, float) or v < 0 for v in history):
                raise ValueError(f'存档 {name} 数值无效')
            if name == 'pay_history' and any(v > 1 for v in history):
                raise ValueError('存档工资实付比例无效')
    for name in ('qbar', 'full_raw', 'raw', 'stock', 'initial_stock', 'external_goods', 'consumed', 'total_produced'):
        if any(type(v) not in (int, float) or v < 0 for v in data[name].values()):
            raise ValueError(f'存档 {name} 数量无效')
    for name in ('raw_used', 'external_raw', 'initial_raw', 'initial_external_raw',
                 'cumulative_raw_purchased_units', 'cumulative_raw_input_qty', 'government_raw_units',
                 'cumulative_government_raw_resale_units', 'cumulative_government_raw_resale_cents'):
        if type(data[name]) not in (int, float) or data[name] < 0:
            raise ValueError(f'存档 {name} 数量无效')
    if type(data['cumulative_government_raw_resale_cents']) is not int:
        raise ValueError('市政原料转售回款必须为整数分')
    for name in ('base_wages', 'nominal_wages', 'prices'):
        if any(type(v) is not int or v <= 0 for v in data[name].values()):
            raise ValueError(f'存档 {name} 必须为正整数分')
    for i, row in data['arrears'].items():
        if not isinstance(row, dict) or set(row) != groups:
            raise ValueError(f'存档 {i} 欠薪债权不完整')
    if not isinstance(data['cash'], dict) or not set(template.cash) <= set(data['cash']):
        raise ValueError('存档现金账户不完整')
    for i, definition in data['definitions'].items():
        jobs = definition['job_capacity_max']
        if type(jobs) is not int or jobs < template.definitions[i]['initial_employees_industrial_city']:
            raise ValueError('存档岗位容量无效')
    if type(data['funded_civil_staff_plan']) is not int or data['funded_civil_staff_plan'] < 0:
        raise ValueError('持续公务员岗位计划无效')
    if type(data['labor_force']) is not int or data['labor_force'] < 0:
        raise ValueError('存档劳动力无效')
    if not -1 < data['population_carry'] < 1 or data['cpi'] <= 0 or data['living_cost_cents'] <= 0:
        raise ValueError('存档人口余量或价格指数无效')
    for name in ('pending_transactions', '_last_policy_actions'):
        if not isinstance(data[name], list):
            raise ValueError('存档待结算记录无效')
    if any(not isinstance(action, dict) or action.get('kind') not in (
            'subsidy_industry', 'public_works', 'hire_civil', 'limit_hire', 'limit_layoff',
            'tax_amnesty', 'pardon_welfare') for action in data['_last_policy_actions']):
        raise ValueError('存档待结算政策无效')
    for tx in data['pending_transactions']:
        if (not isinstance(tx, dict) or not {'from', 'to', 'kind', 'amount_cents'} <= set(tx)
                or tx['from'] not in data['cash'] or tx['to'] not in data['cash']
                or type(tx['amount_cents']) is not int or tx['amount_cents'] < 0):
            raise ValueError('存档待结算交易无效')
    if data['_settling'] is not False:
        raise ValueError('不能读取未完成的月结')
    if data['month']:
        for name in ('transactions', 'sales', 'shortfalls', 'requested', 'income', 'current_wages',
                     'actual_activity', 'market_available', 'actual_domestic', 'actual_rent'):
            if name not in data:
                raise ValueError(f'存档缺少月结字段：{name}')
        if not isinstance(data['last_report'], dict) or data['last_report'].get('month') != data['month']:
            raise ValueError('存档最近月报缺失')

    if (type(data['household_size']) not in (int, float) or data['household_size'] <= 0
            or data['household_size'] != data['scenario']['city']['household_consumption_size']):
        raise ValueError('消费等价系数与配置不一致')
    if not isinstance(data['tax_refunded'], dict):
        raise ValueError('退税记录无效')
    for key, amount in data['tax_refunded'].items():
        if not isinstance(key, str) or key.count('|') != 1:
            raise ValueError('退税记录键无效')
        month, industry = key.split('|')
        if not month.isdigit() or not 0 <= int(month) <= data['month'] or industry not in keys:
            raise ValueError('退税记录期间或行业无效')
        if type(amount) is not int or amount < 0:
            raise ValueError('累计已退税额无效')
    if data['month']:
        validate_report(data['last_report'], keys)
        report = data['last_report']
        def pending_net(accounts):
            return sum((tx['amount_cents'] if tx['to'] in accounts else 0)
                       - (tx['amount_cents'] if tx['from'] in accounts else 0)
                       for tx in data['pending_transactions'])
        for field, accounts in (('government_cash_cents', {'GOV'}),
                                ('household_cash_cents', groups),
                                ('industry_cash_cents', keys - {'GOV'}),
                                ('external_cash_cents', {'EXT'})):
            if report[field] + pending_net(accounts) != sum(data['cash'][account] for account in accounts):
                raise ValueError('最近月报现金与运行账户不一致')
        current_employment = {i: sum(row[i] for row in data['workers'].values()) for i in keys}
        pending_hired = sum(action.get('people', 0) for action in data['_last_policy_actions']
                            if action.get('kind') == 'hire_civil')
        current_employment['GOV'] -= pending_hired
        if report['employment'] != current_employment:
            raise ValueError('最近月报就业与运行状态不一致')
        if (report['population'] != sum(data['population'].values())
                or report['labor_force'] != data['labor_force']
                or report['wage_arrears_cents'] != data['wage_arrears_cents']):
            raise ValueError('最近月报人口或欠薪与运行状态不一致')
    if 'report_history' in data:
        history = data['report_history']
        if not isinstance(history, list) or len(history) > 120:
            raise ValueError('月报历史须为不超过120期的列表')
        if bool(history) != bool(data['month']):
            raise ValueError('月报历史与月份不符')
        expected_first = data['month'] - len(history) + 1
        if expected_first < 1:
            raise ValueError('月报历史超出运行月份')
        for index, report in enumerate(history, expected_first):
            validate_report(report, keys)
            if report['month'] != index:
                raise ValueError('月报历史月份不连续')
        if history and history[-1] != data['last_report']:
            raise ValueError('月报历史末期与最近月报不一致')


def validate_report(report, keys):
    """缓存仅作展示；核验结构与自身恒等式，最新一期另与运行账户核对。"""
    if not isinstance(report, dict) or type(report.get('month')) is not int or report['month'] < 1:
        raise ValueError('月报月份无效')
    for name in ('employment', 'payroll_employment', 'sectors'):
        if not isinstance(report.get(name), dict) or set(report[name]) != keys:
            raise ValueError('月报行业不完整')
    for name in ('employment', 'payroll_employment'):
        if any(type(v) is not int or v < 0 for v in report[name].values()):
            raise ValueError('月报就业人数无效')
    for name in ('population', 'labor_force', 'unemployed', 'wages_cents', 'paid_wages_cents',
                 'wage_arrears_cents', 'government_cash_cents', 'household_cash_cents',
                 'industry_cash_cents', 'external_cash_cents', 'consumer_spending_cents', 'sales_cents'):
        if type(report.get(name)) is not int or report[name] < 0:
            raise ValueError('月报汇总无效')
    if report['labor_force'] != sum(report['employment'].values()) + report['unemployed']:
        raise ValueError('月报劳动力不守恒')
    for i, sector in report['sectors'].items():
        if not isinstance(sector, dict) or sector.get('employed') != report['employment'][i]:
            raise ValueError('月报行业就业与汇总不一致')
        for name in ('cash_cents', 'sales_cents', 'tax_cents', 'wage_cents'):
            if type(sector.get(name)) is not int or sector[name] < 0:
                raise ValueError('月报行业金额无效')
    if not isinstance(report.get('transactions'), list):
        raise ValueError('月报交易流水缺失')
    fiscal = 0
    for tx in report['transactions']:
        if not isinstance(tx, dict) or not {'from', 'to', 'kind', 'amount_cents'} <= set(tx):
            raise ValueError('月报交易流水无效')
        amount = tx['amount_cents']
        if type(amount) is not int or amount < 0:
            raise ValueError('月报交易金额无效')
        fiscal += (amount if tx['to'] == 'GOV' else 0) - (amount if tx['from'] == 'GOV' else 0)
    if report.get('fiscal_balance_cents') != fiscal:
        raise ValueError('月报财政与交易流水不一致')
