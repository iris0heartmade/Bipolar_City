"""Tk实际事件循环与控件回调验收；不依赖操作系统坐标。"""
from copy import deepcopy
import json
from pathlib import Path
import time
from unittest.mock import patch

from backend.systems.v21.industry_scenario import default_scenario
from tools.economy_lab import EconomyLab, compute_comparison


def main():
    root = Path(__file__).resolve().parents[1]
    directory = root/'artifacts/industry-gui-check'
    directory.mkdir(parents=True, exist_ok=True)
    app = EconomyLab()
    app.geometry('1260x820+80+80')
    events = []

    def wait_result():
        deadline = time.monotonic()+15
        while time.monotonic() < deadline:
            app.update()
            if app.history and app._result_revision == app._revision and not app._future and not app._pending and not app._debounce:
                return
            time.sleep(.02)
        raise AssertionError(f'GUI未完成重算：{app.status.get()}')

    try:
        assert app.scenario == default_scenario(), '界面初始化擅自改了配置'
        wait_result()
        assert app.history == app.baseline
        assert len(app.table.get_children()) == 7
        assert len(app.chart.find_all()) > 10
        events.append('真实Tk窗口、七行业表格及Canvas曲线已生成；初值未被滑块量化改写')
        app.months.set('12')
        app.set_control('sector', 'wage_multiplier', 1.25)
        wait_result()
        assert app.history[0]['wages_cents'] != app.baseline[0]['wages_cents']
        events.append('工业工资1.25倍后，实际工资支出与基准不同')
        scenario_path, export_path = directory/'scenario.json', directory/'report.json'
        with patch('tools.economy_lab.filedialog.asksaveasfilename', return_value=str(scenario_path)):
            app.save_scenario()
        saved = deepcopy(app.scenario)
        app.set_control('sector', 'wage_multiplier', 1.5)
        with patch('tools.economy_lab.filedialog.askopenfilename', return_value=str(scenario_path)):
            app.load_scenario()
        wait_result()
        assert app.scenario == saved and app.months.get() == '12'
        with patch('tools.economy_lab.filedialog.asksaveasfilename', return_value=str(export_path)):
            app.export_report()
        exported = json.loads(export_path.read_text(encoding='utf-8'))
        assert exported['scenario'] == saved and len(exported['history']) == 12
        assert exported['history'][0]['transactions']
        events.append('保存、载入和完整逐月报告导出成功，配置和12个月账本可核对')
        app.variables['sector', 'wage_multiplier'].set('999')
        meta = next(m for m in app.scenario['controls'] if m['key'] == 'wage_multiplier')
        app._commit_entry(meta)
        assert app._invalid and app.scenario == saved
        events.append('非法工资倍率999未生效，保留上次有效方案')
        app.set_control('sector', 'wage_multiplier', 1.25)
        for value in (.9, 1.1, 1.4, 1.2):
            app.set_control('sector', 'wage_multiplier', value)
            app.update()
        wait_result()
        _, expected = compute_comparison(app.scenario, 12)
        assert app.history == expected
        events.append('快速连续调参最终只显示最后1.2倍方案，不被旧任务覆盖')
        previous = deepcopy(app.scenario)
        app.industry.set('SRV 服务商业')
        app._select_industry()
        assert app.scenario == previous and '服务商业' in app.explanation.get()
        events.append('切换行业仅切换显示，不改写其他行业参数')
        app.reset()
        wait_result()
        assert app.history == app.baseline
        events.append('恢复基准后，当前曲线和基准结果完全一致')
        evidence = {'date': '2026-10-15', 'passed': True, 'checks': events,
                    'native_window_note': '另行使用Windows Computer Use实际点击工资滑块，观察到工资倍率2和就业/现金曲线变化',
                    'scope': 'GUI控件及回调、文件读写与真实数值；不是完整游戏平衡验收'}
        path = root/'docs/04_验收记录/行业实验室GUI验证-2026-10-15.json'
        path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({'passed_checks': len(events), 'evidence': str(path)}, ensure_ascii=False))
    finally:
        app.close()


if __name__ == '__main__':
    main()
