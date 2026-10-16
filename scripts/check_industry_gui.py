"""实际运行Tk事件循环，操作控件回调与存档；不冒充鼠标端到端验收。"""
from copy import deepcopy
import json
from pathlib import Path
import threading
import time
from unittest.mock import patch

from tools.economy_lab import EconomyLab
from tools.lab_session import LabSession


def main():
    directory = Path(__file__).resolve().parents[1] / 'artifacts/industry-gui-check'
    directory.mkdir(parents=True, exist_ok=True)
    app = EconomyLab()
    events, errors = [], []
    app.report_callback_exception = lambda kind, value, trace: errors.append(str(value))

    def idle():
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            app.update()
            if not app._future and not app._pending:
                assert not errors, errors
                return
            time.sleep(.01)
        raise AssertionError(app.status.get())

    def button(text):
        def walk(widget):
            for child in widget.winfo_children():
                if child.winfo_class() == 'TButton' and child.cget('text') == text:
                    return child
                found = walk(child)
                if found:
                    return found
        found = walk(app)
        assert found is not None, text
        found.invoke()
        app.update()

    try:
        idle()
        assert app.session.city.month == 0 and app.history == [] and app.months.get() == '1'
        assert f'{app.session.city.cash["GOV"]/100:,.2f}' in app.summary.get()
        assert len(app.table.get_children()) == 7
        events.append('初始第0月且未自动推进；默认每次1月')
        button('下一月')
        idle()
        first = deepcopy(app.history)
        button('下一月')
        idle()
        assert app.session.city.month == 2 and app.history[:1] == first
        expected = LabSession()
        expected.advance(2)
        assert app.session.to_dict() == expected.to_dict()
        assert len(app.table.get_children()) == 7 and len(app.chart.find_all()) > 10
        for label in ('政府现金', 'IND 就业', '守恒残差'):
            app.curve.set(label)
            app.draw_chart()
        assert app._resolve_curve_value(app.history[-1], ('政府现金', ('government_cash_cents', 'top'))) == app.history[-1]['government_cash_cents']
        events.append('真实事件循环推进1+1等于2月；七行业表和多指标曲线有真实数值')
        old_history = deepcopy(app.history)
        app.set_control('city', 'household_consumption_size', 3)
        app.set_control('city', 'living_cost_cr', 80)
        assert app.history == old_history and app.session.city.household_size == 3
        assert app.session.city.living_cost_cents == round(8000 * app.session.city.cpi)
        app.cmd_kind.set('limit_layoff')
        app.cmd_industry.set('IND')
        app.cmd_policy_months.set('1')
        button('执行市政指令')
        assert app.session.city.month == 2 and app.session.city._active_policies
        assert 'limit_layoff' in app.policy_text.get('1.0', 'end')
        events.append('参数仅影响未来，派生消费值同步；限裁施加当前第2月城市并立即显示')
        runtime_path = directory / 'session.json'
        with patch('tools.economy_lab.filedialog.asksaveasfilename', return_value=str(runtime_path)):
            button('保存运行')
        assert runtime_path.exists(), app.status.get()
        expected = LabSession.from_dict(json.loads(runtime_path.read_text(encoding='utf-8')))
        expected.advance()
        button('下一月')
        idle()
        assert app.session.to_dict() == expected.to_dict()
        with patch('tools.economy_lab.filedialog.askopenfilename', return_value=str(runtime_path)):
            button('载入运行')
        assert app.session.city.month == 2
        button('下一月')
        idle()
        assert app.session.to_dict() == expected.to_dict()
        events.append('含活跃政策存档成功；载入再推进与不中断运行逐项相同')
        old_history = deepcopy(app.history)
        old_government = app.session.city.cash['GOV']
        app.cmd_kind.set('subsidy_industry')
        app.cmd_cash_cr.set('100')
        button('执行市政指令')
        assert app.session.city.cash['GOV'] == old_government - 10000
        assert f'{(old_government - 10000)/100:,.2f}' in app.summary.get()
        assert app.table.item('IND', 'values')[8] == f'{app.session.city.cash["IND"]/100:,.2f}'
        assert app.history == old_history
        before_invalid = app.session.to_dict()
        app.cmd_cash_cr.set('inf')
        button('执行市政指令')
        assert app.session.to_dict() == before_invalid and not errors
        app.cmd_kind.set('limit_layoff')
        app.cmd_cash_cr.set('0')
        app.cmd_policy_months.set('0')
        button('执行市政指令')
        assert app.session.to_dict() == before_invalid and not errors
        app.cmd_policy_months.set('1')
        events.append('补贴100CR后当前国库与行业现金立即更新，历史月报不改写；无穷金额与0月政策期限被拒')
        history = deepcopy(app.history)
        button('清空折线')
        assert app.history == history and not app.chart.find_all()
        report_path = directory / 'report.json'
        with patch('tools.economy_lab.filedialog.asksaveasfilename', return_value=str(report_path)):
            button('导出报告')
        exported = json.loads(report_path.read_text(encoding='utf-8'))
        assert len(exported['history']) == 3 and exported['history'][0]['transactions']
        assert any(command['kind'] == 'limit_layoff' for command in exported['commands'])
        events.append('清空折线保留全部城市历史；导出3月交易及真实指令')
        corrupt_path = directory / 'corrupt-session.json'
        corrupt_payload = app.session.to_dict()
        del corrupt_payload['history'][0]['employment']
        corrupt_path.write_text(json.dumps(corrupt_payload, ensure_ascii=False), encoding='utf-8')
        before_load = app.session.to_dict()
        with patch('tools.economy_lab.filedialog.askopenfilename', return_value=str(corrupt_path)):
            button('载入运行')
        assert app.session.to_dict() == before_load and '载入失败' in app.status.get()
        assert not errors, errors
        events.append('早期历史损坏的存档在替换前被拒绝，城市、基准和历史完整保留，无渲染异常')
        app.variables['sector', 'wage_multiplier'].set('999')
        meta = next(m for m in app.scenario['controls'] if m['key'] == 'wage_multiplier')
        app._commit_entry(meta)
        assert app._invalid and app.history == history
        app.set_control('sector', 'wage_multiplier', 1.25)
        app.months.set('4')
        button('推进N月')
        idle()
        assert app.session.city.month == 7
        assert len(app.history) == 7
        events.append('无效参数不改变城市；修正后推进N月从第3月继续至第7月')
        scenario_path = directory / 'scenario.json'
        with patch('tools.economy_lab.filedialog.asksaveasfilename', return_value=str(scenario_path)):
            button('保存方案')
        saved = deepcopy(app.scenario)
        with patch('tools.economy_lab.filedialog.askopenfilename', return_value=str(scenario_path)):
            button('载入方案')
        assert app.scenario == saved and app.session.city.month == 0 and not app.history
        events.append('方案和运行状态分格式；载入方案明确重建第0月城市')
        app.cmd_kind.set('hire_civil')
        app.cmd_people.set('10')
        button('执行市政指令')
        assert app.session.city.employment()['GOV'] == 60
        with patch('tools.economy_lab.filedialog.asksaveasfilename', return_value=str(runtime_path)):
            button('保存运行')
        expected = LabSession.from_dict(json.loads(runtime_path.read_text(encoding='utf-8')))
        expected.advance()
        with patch('tools.economy_lab.filedialog.askopenfilename', return_value=str(runtime_path)):
            button('载入运行')
        button('下一月')
        idle()
        assert app.session.to_dict() == expected.to_dict()
        assert app.session.city.employment()['GOV'] == 60
        events.append('雇佣公务员后运行存档可恢复，次月岗位与就业计划保留，继续结果完全相同')
        button('下一月')
        app.close()
        assert not any(t.name.startswith('industry-lab') and t.is_alive() for t in threading.enumerate())
        events.append('执行期间关闭窗口后工作线程结束，无迟到提交或Tk回调异常')
        assert not errors, errors
        evidence = {'passed': True, 'checks': events, 'scope': '真实Tk事件循环、控件invoke回调、运行存档及连续经济数值；非OS鼠标验收'}
        evidence_path = directory / 'evidence.json'
        evidence_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({'passed_checks': len(events), 'evidence': str(evidence_path)}, ensure_ascii=False))
    finally:
        if not app._closed:
            app.close()


if __name__ == '__main__':
    main()
