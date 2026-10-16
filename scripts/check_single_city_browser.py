"""隔离服务、临时存档和真实Chromium交互验收，不接触用户运行中的城市。"""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'artifacts/single-city-browser-2026-10-16'


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    with socket.socket() as channel:
        channel.bind(('127.0.0.1', 0))
        port = channel.getsockname()[1]
    base = f'http://127.0.0.1:{port}'
    events, errors = [], []
    with tempfile.TemporaryDirectory(prefix='bipole-browser-') as folder:
        env = {**os.environ, 'BIPOLE_PORT': str(port), 'BIPOLE_SAVE_DIR': folder,
               'PYTHONIOENCODING': 'utf-8'}
        with (OUTPUT / 'server.log').open('w', encoding='utf-8') as log:
            server = subprocess.Popen([sys.executable, '-m', 'backend.app'], cwd=ROOT,
                env=env, stdout=log, stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            try:
                for _ in range(100):
                    try:
                        with urllib.request.urlopen(base + '/api/game/state', timeout=1):
                            break
                    except OSError:
                        if server.poll() is not None:
                            raise RuntimeError('隔离服务启动失败')
                        time.sleep(.1)
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(headless=True)
                    context = browser.new_context(viewport={'width': 1560, 'height': 1060})
                    page = context.new_page()
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    page.on('dialog', lambda dialog: dialog.accept())

                    def state():
                        response = context.request.get(base + '/api/game/state')
                        assert response.ok
                        return response.json()

                    def click_command(selector):
                        target = page.locator(selector)
                        assert target.count() == 1
                        with page.expect_response(lambda r: '/commands' in r.url and r.request.method == 'POST') as pending:
                            target.click()
                        result = pending.value
                        assert result.status == 200, result.text()
                        return result.json()

                    page.goto(base, wait_until='networkidle')
                    page.wait_for_function('Boolean(window.bipoleView)')
                    assert state()['month'] == 0
                    page.locator('#new-btn').click()
                    page.locator('#seed-input').fill('20251008')
                    fresh = click_command('#confirm-new')
                    assert fresh['world']['seed'] == 20251008
                    events.append('通过真实新建对话框设置种子并创建0月城市')

                    before = state()
                    page.locator('[data-building="workshop"]').click()
                    page.keyboard.press('r')
                    page.keyboard.press('r')
                    position = page.evaluate('window.bipoleView.screenAt(2,18)')
                    page.mouse.move(position['x'], position['y'])
                    page.wait_for_function("document.getElementById('placement-hint').textContent.includes('可建造')")
                    with page.expect_response(lambda r: '/api/game/commands' in r.url and r.request.method == 'POST') as pending:
                        page.mouse.click(position['x'], position['y'])
                    response = pending.value
                    assert response.status == 200, response.text()
                    after = response.json()
                    player_buildings = [b for b in after['world']['buildings'] if b.get('player_built')]
                    assert len(player_buildings) == 1 and player_buildings[0]['status'] == 'constructing'
                    building_id = player_buildings[0]['id']
                    cost = next(d['cost'] for d in after['catalog'] if d['id'] == 'workshop')
                    assert before['government_cash_cents'] - after['government_cash_cents'] == cost
                    initial_capacity = next(i['capacity'] for i in before['industries'] if i['id'] == 'IND')
                    assert next(i['capacity'] for i in after['industries'] if i['id'] == 'IND') == initial_capacity
                    events.append('真实地图鼠标预览、旋转、建工坊：扣款、施工中、完工前不加岗位')
                    first = click_command('#advance-btn')
                    assert first['month'] == 1
                    assert next(b for b in first['world']['buildings'] if b['id'] == building_id)['status'] == 'completed'
                    assert next(i['capacity'] for i in first['industries'] if i['id'] == 'IND') == initial_capacity + 40
                    events.append('真实月进使施工完工，行业岗位与生产设施共同增加')
                    page.screenshot(path=str(OUTPUT / 'map-month-1.png'), full_page=True)

                    page.goto(base + '/industry', wait_until='networkidle')
                    page.wait_for_function("document.getElementById('month').textContent==='1'")
                    page.locator('#subsidy-industry').select_option('SRV')
                    page.locator('#subsidy-amount').fill('100')
                    gov = state()['government_cash_cents']
                    click_command('#subsidy-form button')
                    assert state()['government_cash_cents'] == gov - 10000
                    page.locator('#limit-layoff-industry').select_option('IND')
                    page.locator('#limit-layoff-months').fill('3')
                    click_command('#limit-layoff-form button')
                    assert state()['active_policies']['IND|limit_layoff'] == 4
                    click_command('#save')
                    saved = state()
                    page.locator('#advance-months').fill('1')
                    expected = click_command('#advance-form button')
                    assert expected['month'] == 2
                    expected_report = expected['latest']
                    click_command('#load')
                    assert state()['month'] == saved['month']
                    restored = click_command('#advance-form button')
                    assert restored['latest'] == expected_report
                    events.append('真实表单补贴/限裁/保存/读取/再月进，完整月报逐项相同')
                    page.reload(wait_until='networkidle')
                    page.wait_for_function("document.getElementById('month').textContent==='2'")
                    assert page.locator('#history li').count() == 2
                    assert '2' in page.locator('#chart-summary').inner_text()
                    assert 'IND|limit_layoff' in page.locator('#active-policies').inner_text()
                    events.append('刷新后城市、政策与逐月趋势仍保留')
                    page.screenshot(path=str(OUTPUT / 'industry-month-2.png'), full_page=True)

                    page.goto(base, wait_until='networkidle')
                    page.wait_for_function('Boolean(window.bipoleView)')
                    saved_again = click_command('#save-btn')
                    assert saved_again['month'] == 2
                    click_command('#advance-btn')
                    loaded = click_command('#load-btn')
                    assert loaded['month'] == 2
                    assert any(b['id'] == building_id for b in loaded['world']['buildings'])
                    events.append('地图与行业页面共享月份；地图存读档保留建筑和政策')
                    assert not errors, errors
                    evidence = {'passed': True, 'events': events, 'browser_errors': errors,
                                'isolation': '临时端口、临时存档、独立Chromium实例',
                                'browser_version': browser.version}
                    (OUTPUT / 'evidence.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
                    browser.close()
                    print(json.dumps(evidence, ensure_ascii=False))
            finally:
                server.terminate()
                try:
                    server.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait(timeout=8)


if __name__ == '__main__':
    main()
