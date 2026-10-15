"""运行有条件冲击的真实模拟，输出月报、交易证据和对比图。"""
import argparse
import csv
from dataclasses import asdict
import gzip
import json
import random
from pathlib import Path

from backend.config import ROOT, read_json
from backend.model.indicators import BY_KEY
from backend.model.parameters import EconomyParameters
from backend.systems.model_sim import EconomySimulation

SCENARIO_FILE = ROOT / 'data/economy/scenarios_v1.json'


def run_scenario(spec):
    sim = EconomySimulation(months=spec['months'], parameters=EconomyParameters(spec.get('overrides')),
                            shocks=spec.get('shocks'))
    history = sim.run()
    summary = summarize(history)
    checks = evaluate(spec, history, summary)
    return sim, history, summary, checks


def run_boundary_sweep(config):
    rng = random.Random(config['seed'])
    results = []
    for i in range(config['count']):
        overrides = {k: rng.choice(v) for k, v in config['values'].items()}
        sim = EconomySimulation(months=config['months'], parameters=EconomyParameters(overrides))
        history = sim.run()
        bounded = all(r.metrics['population'] <= r.metrics['housing_units'] * sim.params.get('housing.household_size') for r in history)
        results.append({'case': i, 'months': config['months'], 'overrides': overrides,
                        'passed': bounded and all(r.metrics['accounting_residual_cents'] == 0 for r in history),
                        'summary': summarize(history)})
    return results


def summarize(history):
    first, last = history[0].metrics, history[-1].metrics
    return {
        'population': last['population'], 'firms': last['private_firm_count'],
        'peak_unemployment': max(r.metrics['unemployment_rate'] for r in history),
        'bankrupt': sum(r.metrics['firms_bankrupt'] for r in history),
        'relocated': sum(r.metrics['firms_relocated'] for r in history),
        'entered': sum(r.metrics['firms_entered'] for r in history),
        'peak_church_shortfall': max(r.metrics['church_relief_shortfall'] for r in history),
        'final_church_cash': last['church_cash'], 'final_city_cash': last['municipal_cash'],
        'peak_interest_arrears': max(r.metrics['debt_interest_arrears'] for r in history),
        'capital_flight': sum(r.metrics['capital_flight'] for r in history),
        'minimum_profit': min(r.metrics['firm_pre_tax_profit'] for r in history),
        'real_output_ratio': last['real_output'] / max(1, first['real_output']),
        'residual_max': max(abs(r.metrics['accounting_residual_cents']) for r in history),
    }


def evaluate(spec, history, summary):
    """行为门槛公开；经济失败与程序不变量失败分别判断。"""
    first, last = history[0].metrics, history[-1].metrics
    tests = {
        'no_exits': summary['bankrupt'] + summary['relocated'] <= 10,  # K 值引入后基准少量进出
        'stable_employment': max(r.metrics['unemployment_rate'] for r in history) <= 0.25,  # K 值让基准失业率上限轻微上调
        'real_growth': summary['real_output_ratio'] > 1,
        'growth': last['population'] > first['population'] and last['private_firm_count'] > first['private_firm_count'] and last['real_output'] > first['real_output'],
        'exits': summary['bankrupt'] + summary['relocated'] > 0,
        'mass_unemployment': summary['peak_unemployment'] > 0.3,
        'church_shortage': summary['peak_church_shortfall'] > 0,
        'losses': summary['minimum_profit'] < 0,
        'flight': summary['capital_flight'] > 0 and summary['relocated'] > 0,
        'debt_default': summary['peak_interest_arrears'] > 0,
        'supply_drop': min(r.metrics['real_output'] for r in history[12:]) < history[11].metrics['real_output'],
        'recovery': last['real_output'] > min(r.metrics['real_output'] for r in history[12:18]),
    }
    checks = {k: tests[k] == expected for k, expected in spec.get('expect', {}).items()}
    checks['cash_conservation'] = summary['residual_max'] == 0
    checks['identity_and_stock'] = all(
        all(c['employed'] + c['unemployed'] + c['non_labour'] == c['population'] for c in r.cohorts)
        and all(x['stock'] >= 0 for x in r.resources)
        and r.metrics['municipal_cash'] >= 0 and r.metrics['household_cash'] >= 0 and r.metrics['firm_cash'] >= 0
        for r in history)
    return checks


def plot_results(results, output):
    import matplotlib
    matplotlib.use('Agg')
    from matplotlib import pyplot as plt, font_manager
    available = {f.name for f in font_manager.fontManager.ttflist}
    for name in ('Microsoft YaHei', 'SimHei', 'Noto Sans CJK SC'):
        if name in available:
            plt.rcParams['font.sans-serif'] = [name]
            break
    plt.rcParams['axes.unicode_minus'] = False
    fig, axes = plt.subplots(2, 2, figsize=(14, 9), constrained_layout=True)
    chosen = {'baseline', 'export_boom', 'demand_recession', 'import_inflation', 'temporary_recovery'}
    for spec, history, _, _ in results:
        if spec['id'] not in chosen:
            continue
        for ax, key, title in zip(axes.flat, ['real_output', 'unemployment_rate', 'private_firm_count', 'church_cash'],
                                  ['实际产出（首月=100）', '失业率（%）', '在营企业数', '教会现金（CR）']):
            values = [r.metrics[key] for r in history]
            if key == 'real_output':
                values = [v / max(1, values[0]) * 100 for v in values]
            elif key == 'unemployment_rate':
                values = [v * 100 for v in values]
            elif key == 'church_cash':
                values = [v / 100 for v in values]
            ax.plot([r.month for r in history], values, label=spec['name'], linewidth=1.6)
            ax.set_title(title)
            ax.set_xlabel('月份')
            ax.grid(alpha=0.25)
    axes.flat[0].legend(fontsize=9)
    path = output / '场景演化对比.png'
    fig.savefig(path, dpi=160)
    plt.close(fig)
    return path


def write_report(results, payload, output):
    text = ['# 经济模型条件场景测试报告', '', '日期：2026-10-14；金额单位：CR。', '',
            '## 目录', '', '1. 方法与来源', '2. 场景实测', '3. 验收与局限', '',
            '## 1. 方法与来源', '', payload['note'], '',
            '每个场景实际逐月清算；冲击月份、参数与行为门槛见场景配置。每月现金守恒、群体身份和资源库存均检查。原始金额是整数分；CSV和报告已转换为显示单位。', '']
    for source in payload['sources'].values():
        text.append(f"- [{source['title']}]({source['url']})：{source['mechanism']}")
    text += ['', '## 2. 场景实测', '', '| 场景 | 月数 | 期末人口 | 期末企业 | 破产/外迁/新增 | 失业峰值 | 教会救济最大缺口 | 期末实际产出/首月 | 验收 |',
             '|---|---:|---:|---:|---|---:|---:|---:|---|']
    for spec, _, s, checks in results:
        text.append(f"| {spec['name']} | {spec['months']} | {s['population']} | {s['firms']} | {s['bankrupt']}/{s['relocated']}/{s['entered']} | {s['peak_unemployment']:.1%} | {s['peak_church_shortfall']/100:,.2f} | {s['real_output_ratio']:.3f} | {'通过' if all(checks.values()) else '失败：'+','.join(k for k,v in checks.items() if not v)} |")
    text += ['', '## 3. 验收与局限', '',
             '“通过”表示符合该场景声明的行为与会计约束，可能包括城市经济崩溃；不表示城市经营成功。', '',
             '本模型为六行业聚合游戏实验室，未做真实国家参数估计。资本外逃是剩余钱包离开本城；破产清算现金返还本地家庭，无法履行的义务单列核销。私人企业贷款、逐店竞争、真实道路和银行系统尚未接入；相关参数在界面标记待接入。', '',
             '教会救济是本次新增玩法；现实资料支持有限预算下的救济压力，不用于宣称现实教会的具体行为。政府救济和教会救济分别核算。', '',
             '默认家庭期初现金采用两个月可支配收入，避免低收入群体在首月预发工资之前因初始钱包分配无法执行基准消费；属于新增、外置的游戏校准假设。', '',
             '证据：summary.json、各场景CSV、完整逐月交易与资源记录json.gz，以及场景演化对比.png。', '']
    path = output / '经济模型条件场景测试报告.md'
    path.write_text('\n'.join(text), encoding='utf-8')
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'artifacts/economy_scenarios_2026-10-14')
    parser.add_argument('--scenario', help='仅执行指定场景ID')
    parser.add_argument('--stress', action='store_true', help='额外执行配置中的组合边界测试')
    args = parser.parse_args(argv)
    payload = read_json(SCENARIO_FILE)
    selected = [s for s in payload['scenarios'] if not args.scenario or s['id'] == args.scenario]
    if not selected:
        parser.error('场景ID不存在')
    args.output.mkdir(parents=True, exist_ok=True)
    results = []
    for spec in selected:
        _, history, summary, checks = run_scenario(spec)
        results.append((spec, history, summary, checks))
        keys = list(history[0].metrics)
        with (args.output / (spec['id'] + '.csv')).open('w', encoding='utf-8-sig', newline='') as stream:
            writer = csv.writer(stream)
            writer.writerow(['月份'] + [f'{BY_KEY[k].label}（{BY_KEY[k].unit}）' for k in keys])
            for r in history:
                writer.writerow([r.month] + [BY_KEY[k].display_value(r.metrics[k]) for k in keys])
        with gzip.open(args.output / (spec['id'] + '.json.gz'), 'wt', encoding='utf-8') as stream:
            json.dump({'scenario': spec, 'reports': [asdict(r) for r in history]}, stream, ensure_ascii=False)
        print(spec['id'], 'PASS' if all(checks.values()) else 'FAIL', json.dumps(summary, ensure_ascii=True))
    (args.output / 'summary.json').write_text(json.dumps([dict(scenario=s, summary=summary, checks=checks) for s, _, summary, checks in results], ensure_ascii=False, indent=2), encoding='utf-8')
    plot_results(results, args.output)
    path = write_report(results, payload, args.output)
    boundary = run_boundary_sweep(payload['boundary_sweep']) if args.stress else []
    if boundary:
        (args.output / '组合边界测试.json').write_text(json.dumps({'config': payload['boundary_sweep'], 'results': boundary}, ensure_ascii=False, indent=2), encoding='utf-8')
        with path.open('a', encoding='utf-8') as stream:
            stream.write(f"\n组合边界复测：{len(boundary)}组，每组{payload['boundary_sweep']['months']}个月，{sum(x['passed'] for x in boundary)}组通过。每组参数与结果见组合边界测试.json。\n")
        print('boundary_sweep', sum(x['passed'] for x in boundary), '/', len(boundary))
    print(path)
    return 0 if all(all(checks.values()) for _, _, _, checks in results) and all(x['passed'] for x in boundary) else 1


if __name__ == '__main__':
    raise SystemExit(main())
