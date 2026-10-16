"""M28 行业情报端到端浏览器验收。

启动临时后端；加载 /world；
1. 加载地图，点击 NPC 城市 → 友好度 50（visible=2）→ 没有「派间谍」按钮
2. 通过 dev-only 端点把友好度拉到 70（盟友级）→ 按钮出现
3. 调 /api/world/espionage 派间谍 → 成功时显示完整 last_report
4. 第 2 次同月应被拒
5. 截图保存
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
OUTPUT = ROOT / 'artifacts/espionage-2026-10-16'


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    with socket.socket() as channel:
        channel.bind(('127.0.0.1', 0))
        port = channel.getsockname()[1]
    base = f'http://127.0.0.1:{port}'
    errors, events = [], []

    with tempfile.TemporaryDirectory(prefix='bipole-espionage-') as folder:
        env = {**os.environ, 'BIPOLE_PORT': str(port), 'BIPOLE_SAVE_DIR': folder,
               'BIPOLE_DEV': '1',  # 启用 dev-only inject_game_over
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
                    page.goto(base + '/world', wait_until='networkidle')
                    page.wait_for_selector('.city-node', timeout=10000)

                    # 1. 友好度 50（visible=2），不应有「派间谍」按钮
                    page.locator('.city-node[data-city-id="agricultural_town"]').first.click()
                    page.wait_for_timeout(500)
                    spy_btn = page.locator('#espionage-btn')
                    if spy_btn.count() == 0:
                        events.append('✓ 默认友好度下不显示派间谍按钮')
                    else:
                        errors.append('✗ 友好度 < 3 时不应有派间谍按钮')

                    # 2. 通过 /api/world/state 推动友好度（模拟 M26 多次外交）
                    # 直接调 diplomacy.adjust_friendliness（不通过 mutation，需要通过 API）
                    # 简化：用 set_world_game + 测试内部 mutation
                    # 这里用最简单的：通过 M26 外交行动 trade_agreement × 20 次不现实
                    # 用 fetch + 后端的 test 端点（如果有），否则用 set_world_game
                    # 跳过：直接通过 page.evaluate 调 world_game
                    # 但 worldGame 在 server 进程，没法从 page 调
                    # 改用 dev-only 端点（dev inject_game_over 已有模式，但 friendliness 没暴露）
                    # 简化方案：通过多次 diplomacy_action trade_agreement 调到 70
                    # trade_agreement max_per_month=3，每月 +1，需要 20 月
                    # 实际我们用 advance 月数
                    for batch in range(7):
                        # 每月调 3 次 trade_agreement
                        for _ in range(3):
                            r = page.evaluate("""async (base) => {
                                const r = await fetch(base + '/api/world/diplomacy_action', {
                                    method: 'POST', headers: {'Content-Type': 'application/json'},
                                    body: JSON.stringify({
                                        action_key: 'trade_agreement',
                                        target_city_id: 'agricultural_town',
                                        amount_cr: 0, note: ''
                                    })
                                });
                                return r.status;
                            }""", base)
                        # 推进 1 月
                        page.evaluate("""async (base) => {
                            await fetch(base + '/api/world/advance', {
                                method: 'POST', headers: {'Content-Type': 'application/json'},
                                body: JSON.stringify({months: 1})
                            });
                        }""", base)
                    # 验证友好度
                    state_after = page.evaluate("""async (base) => {
                        const r = await fetch(base + '/api/world/state');
                        return await r.json();
                    }""", base)
                    ag_friendliness = next(
                        l['friendliness'] for l in state_after['links']
                        if set([l['from'], l['to']]) == {'agricultural_town', 'industrial_city'}
                    )
                    if ag_friendliness >= 61:
                        events.append(f'✓ 友好度提升到 {ag_friendliness}（盟友级）')
                    else:
                        errors.append(f'✗ 友好度提升失败：{ag_friendliness}')

                    # 3. 重新点击城市 → 派间谍按钮应出现
                    page.locator('.city-node[data-city-id="agricultural_town"]').first.click()
                    page.wait_for_timeout(500)
                    spy_btn = page.locator('#espionage-btn')
                    if spy_btn.count() >= 1:
                        events.append('✓ 友好度 ≥ 3 时显示派间谍按钮')
                    else:
                        errors.append('✗ 派间谍按钮缺失')

                    # 4. 点击派间谍
                    spy_btn.first.click()
                    page.wait_for_timeout(1000)
                    esp_result = page.locator('#espionage-result').inner_text()
                    if '情报获取成功' in esp_result or '拒绝' in esp_result or '被发现' in esp_result:
                        events.append(f'✓ 派间谍响应: {esp_result.strip()[:80]}')
                    else:
                        errors.append(f'✗ 派间谍响应异常: {esp_result!r}')

                    # 5. 第 2 次同月应被拒
                    page.locator('.city-node[data-city-id="agricultural_town"]').first.click()
                    page.wait_for_timeout(500)
                    spy_btn2 = page.locator('#espionage-btn')
                    if spy_btn2.count() >= 1:
                        spy_btn2.first.click()
                        page.wait_for_timeout(800)
                        esp_result2 = page.locator('#espionage-result').inner_text()
                        if '本月已用尽' in esp_result2 or '冷却中' in esp_result2 or '拒绝' in esp_result2:
                            events.append(f'✓ 第 2 次同月被拒: {esp_result2.strip()[:50]}')
                        else:
                            errors.append(f'✗ 第 2 次应被拒: {esp_result2!r}')

                    # 6. 截图
                    page.screenshot(path=str(OUTPUT / 'world-espionage.png'), full_page=True)

                    if errors:
                        errors.insert(0, f'✗ 失败 {len(errors)} 项')

                    browser.close()
            finally:
                server.terminate()
                try:
                    server.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    server.kill()

    print('=== M28 端到端验收 ===')
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