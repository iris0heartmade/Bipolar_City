"""M26 端到端浏览器验收：玩家外交行动接口。

启动临时后端；加载 /world；点击 NPC 城市；测试 5 类外交按钮；
校验友好度变化 + UI 反馈 + 历史记录。
"""
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'artifacts/diplomacy-action-2026-10-16'


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    with socket.socket() as channel:
        channel.bind(('127.0.0.1', 0))
        port = channel.getsockname()[1]
    base = f'http://127.0.0.1:{port}'
    errors, events = [], []

    with tempfile.TemporaryDirectory(prefix='bipole-dip-') as folder:
        env = {**os.environ, 'BIPOLE_PORT': str(port), 'BIPOLE_SAVE_DIR': folder,
               'PYTHONIOENCODING': 'utf-8'}
        with (OUTPUT / 'server.log').open('w', encoding='utf-8') as log:
            server = subprocess.Popen(
                [sys.executable, '-m', 'backend.app'],
                cwd=ROOT, env=env,
                stdout=log, stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
            )
            try:
                for _ in range(100):
                    try:
                        with urllib.request.urlopen(base + '/api/industry/state', timeout=1):
                            break
                    except OSError:
                        if server.poll() is not None:
                            raise RuntimeError('服务启动失败')
                        time.sleep(.1)

                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(headless=True)
                    page = browser.new_page(viewport={'width': 1560, 'height': 1060})
                    page.on('console', lambda msg: print(f'[console.{msg.type}] {msg.text}'))
                    page.on('pageerror', lambda err: print(f'[pageerror] {err}'))
                    page.goto(base + '/world', wait_until='networkidle')
                    page.wait_for_selector('.city-node', timeout=10000)

                    # 1. 点击 NPC 城市（禾谷镇）
                    page.locator('.city-node[data-city-id="agricultural_town"]').first.click()
                    page.wait_for_timeout(500)
                    # 外交按钮出现
                    trade_btn = page.locator('.dip-btn[data-action="trade_agreement"]')
                    if trade_btn.count() >= 1:
                        events.append('✓ NPC 城市详情显示外交按钮')
                    else:
                        errors.append('✗ 外交按钮缺失')

                    # 截图（初始）
                    page.screenshot(path=str(OUTPUT / 'world-detail-initial.png'), full_page=True)

                    # 2. 点击 trade_agreement（友好 +1: 50→52 包括初始 50 + 1）
                    initial_state = page.evaluate("""async (base) => {
                        const r = await fetch(base + '/api/world/state');
                        return await r.json();
                    }""", base)
                    initial_friendliness = next(
                        l['friendliness'] for l in initial_state['links']
                        if (l['from'] == 'agricultural_town' or l['to'] == 'agricultural_town')
                    )
                    trade_btn.first.click()
                    page.wait_for_timeout(800)
                    # 验证 result 区显示成功
                    result_text = page.locator('#dip-result').inner_text()
                    if '友好度' in result_text and '/' in result_text:
                        events.append(f'✓ trade_agreement 友好度变化：{result_text.strip()}')
                    else:
                        errors.append(f'✗ trade_agreement result 异常: {result_text!r}')

                    # 3. 调 aid（输入 600）→ 友好度 +6（ceil(600/100)）
                    aid_btn = page.locator('.dip-btn[data-action="aid"]')
                    aid_btn.first.click()
                    page.wait_for_timeout(300)
                    page.locator('#aid-amount').fill('600')
                    page.locator('#aid-confirm').click()
                    page.wait_for_timeout(800)
                    result_text = page.locator('#dip-result').inner_text()
                    if '友好度' in result_text:
                        events.append(f'✓ aid 友好度变化：{result_text.strip()}')
                    else:
                        errors.append(f'✗ aid result 异常: {result_text!r}')

                    # 4. 调 statement（友好 +2）
                    stmt_btn = page.locator('.dip-btn[data-action="statement"]')
                    stmt_btn.first.click()
                    page.wait_for_timeout(800)
                    result_text = page.locator('#dip-result').inner_text()
                    if '友好度' in result_text:
                        events.append(f'✓ statement 友好度变化：{result_text.strip()}')
                    else:
                        errors.append(f'✗ statement result 异常: {result_text!r}')

                    # 5. 调 tariff（友好 -10）
                    tariff_btn = page.locator('.dip-btn[data-action="tariff"]')
                    tariff_btn.first.click()
                    page.wait_for_timeout(800)
                    result_text = page.locator('#dip-result').inner_text()
                    if '友好度' in result_text:
                        events.append(f'✓ tariff 友好度变化：{result_text.strip()}')
                    else:
                        errors.append(f'✗ tariff result 异常: {result_text!r}')

                    # 6. 截图（多次外交后）
                    page.screenshot(path=str(OUTPUT / 'world-detail-after-actions.png'), full_page=True)

                    # 7. 外交历史可见
                    history = page.locator('#dip-history').inner_text()
                    if 'trade_agreement' in history and 'aid' in history:
                        events.append('✓ 外交历史区显示行动列表')
                    else:
                        errors.append(f'✗ 历史区内容异常: {history[:200]!r}')

                    # 8. 同月第 4 次 trade_agreement 应被拒（max_per_month=3）
                    # 当前已用过 trade_agreement 1 次，再连点 2 次（共 3 次），第 4 次应拒
                    for _ in range(2):
                        trade_btn.first.click()
                        page.wait_for_timeout(400)
                    trade_btn.first.click()
                    page.wait_for_timeout(800)
                    result_text = page.locator('#dip-result').inner_text()
                    if '拒绝' in result_text or '本月' in result_text:
                        events.append(f'✓ 第 4 次 trade_agreement 被拒：{result_text.strip()[:50]}')
                    else:
                        errors.append(f'✗ 第 4 次 trade_agreement 应被拒，实际: {result_text!r}')

                    # 9. /api/world/diplomacy_action 加未校验无效 action 返回 422
                    invalid_r = page.evaluate("""async (base) => {
                        const r = await fetch(base + '/api/world/diplomacy_action', {
                            method: 'POST',
                            headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify({action_key: 'nonsense', target_city_id: 'agricultural_town', amount_cr: 0, note: ''})
                        });
                        return r.status;
                    }""", base)
                    if invalid_r == 422:
                        events.append('✓ 未知 action_key 返回 422')
                    else:
                        errors.append(f'✗ 未知 action 应返回 422，实际 {invalid_r}')

                    # 10. /api/world/diplomacy_actions/agricultural_town 返回历史
                    hist_r = page.evaluate("""async (base) => {
                        const r = await fetch(base + '/api/world/diplomacy_actions/agricultural_town');
                        return await r.json();
                    }""", base)
                    if len(hist_r) >= 3:
                        events.append(f'✓ 历史端点返回 {len(hist_r)} 条记录')
                    else:
                        errors.append(f'✗ 历史端点记录数: {len(hist_r)}')

                    if errors:
                        errors.insert(0, f'✗ 失败 {len(errors)} 项')

                    browser.close()
            finally:
                server.terminate()
                try:
                    server.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    server.kill()

    print('=== M26 端到端验收 ===')
    for ev in events:
        print(ev)
    if errors:
        for err in errors:
            print(err)
        print(f'截图: {OUTPUT}')
        return 1
    print(f'全部通过，截图: {OUTPUT}')
    return 0


if __name__ == '__main__':
    sys.exit(main())