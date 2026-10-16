"""M27 NPC-NPC 贸易网端到端浏览器验收。

启动临时后端；加载 /world；
1. 校验 /api/world/state.npc_trade_routes 存在并有内容
2. 推进 1 月 → 贸易流仍存在（advance 后 NpcCityEngine advance + 友好度增量）
3. NPC-NPC 友好度从 50 → 51（+1/月）
4. 玩家-NPC 友好度不变（玩家需要 M26 外交行动才能变化）
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
OUTPUT = ROOT / 'artifacts/npc-trade-routes-2026-10-16'


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    with socket.socket() as channel:
        channel.bind(('127.0.0.1', 0))
        port = channel.getsockname()[1]
    base = f'http://127.0.0.1:{port}'
    errors, events = [], []

    with tempfile.TemporaryDirectory(prefix='bipole-npc-trade-') as folder:
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
                    page.goto(base + '/world', wait_until='networkidle')
                    page.wait_for_selector('.city-node', timeout=10000)

                    # 1. /api/world/state.npc_trade_routes 存在
                    state_data = page.evaluate("""async (base) => {
                        const r = await fetch(base + '/api/world/state');
                        return await r.json();
                    }""", base)
                    routes = state_data.get('npc_trade_routes', [])
                    if len(routes) >= 1:
                        events.append(f'✓ state.npc_trade_routes 有 {len(routes)} 条')
                    else:
                        errors.append(f'✗ state.npc_trade_routes 为空')
                    # 字段验证
                    if routes and 'volume_cr' in routes[0] and 'city_a' in routes[0]:
                        events.append(f'✓ 贸易流字段完整：{list(routes[0].keys())}')
                    else:
                        errors.append(f'✗ 贸易流字段缺失: {routes[0] if routes else None}')

                    # 2. 推进 3 月后贸易流仍存在 + 友好度变化
                    initial_npc_friendliness = {
                        'a-c': 50,  # agricultural_town <-> mining_border（样本）
                    }
                    initial_npc_friendliness_val = next(
                        l['friendliness'] for l in state_data['links']
                        if set([l['from'], l['to']]) == {'agricultural_town', 'mining_border'}
                    )
                    # 推进 3 月
                    for _ in range(3):
                        advance_r = page.evaluate("""async (base) => {
                            const r = await fetch(base + '/api/world/advance', {
                                method: 'POST', headers: {'Content-Type': 'application/json'},
                                body: JSON.stringify({months: 1})
                            });
                            return r.status;
                        }""", base)
                        if advance_r != 200:
                            errors.append(f'✗ advance 失败 {advance_r}')
                            break
                    page.wait_for_timeout(800)

                    state_after = page.evaluate("""async (base) => {
                        const r = await fetch(base + '/api/world/state');
                        return await r.json();
                    }""", base)
                    after_npc_friendliness_val = next(
                        l['friendliness'] for l in state_after['links']
                        if set([l['from'], l['to']]) == {'agricultural_town', 'mining_border'}
                    )
                    # NPC-NPC 友好度应增加（贸易流触发 +1/月）
                    if after_npc_friendliness_val > initial_npc_friendliness_val:
                        events.append(f'✓ NPC-NPC 友好度自然增长：{initial_npc_friendliness_val} → {after_npc_friendliness_val}')
                    else:
                        errors.append(f'✗ NPC-NPC 友好度未增长: {initial_npc_friendliness_val} → {after_npc_friendliness_val}')

                    # 3. 玩家-NPC 友好度未变化（仍 50）
                    player_relations = [l for l in state_after['links']
                                        if 'industrial_city' in (l['from'], l['to'])]
                    initial_player_relations = [l for l in state_data['links']
                                                if 'industrial_city' in (l['from'], l['to'])]
                    for after_r, before_r in zip(player_relations, initial_player_relations):
                        other_city = (set([after_r['from'], after_r['to']]) - {'industrial_city'}).pop()
                        if other_city == 'industrial_city':
                            continue
                        if before_r['friendliness'] == after_r['friendliness'] == 50:
                            events.append(f'✓ 玩家-NPC {other_city} 友好度不变（仍 50）：{after_r["friendliness"]}')
                        elif after_r['friendliness'] != before_r['friendliness']:
                            errors.append(f'✗ 玩家-NPC 友好度意外变化 {before_r} → {after_r}')
                            break

                    # 4. 贸易路线 SVG 元素存在（页面用 stroke-dasharray 渲染）
                    trade_lines = page.query_selector_all('line[data-trade-flow="1"]')
                    if len(trade_lines) >= 1:
                        events.append(f'✓ 渲染 {len(trade_lines)} 条贸易路线（虚线）')
                    else:
                        errors.append('✗ 贸易路线 SVG 元素缺失')

                    # 5. 截图
                    page.screenshot(path=str(OUTPUT / 'world-with-trade-routes.png'), full_page=True)

                    if errors:
                        errors.insert(0, f'✗ 失败 {len(errors)} 项')

                    browser.close()
            finally:
                server.terminate()
                try:
                    server.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    server.kill()

    print('=== M27 端到端验收 ===')
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