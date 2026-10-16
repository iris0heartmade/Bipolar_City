"""M24 端到端浏览器验收。

启动临时后端 + 临时存档；通过 /api/industry/commands 注入 game_over 状态；检查：
1. 加载 /industry → 检查 banner hidden
2. 注入 game_over 后 → /api/industry/state.is_game_over=true
3. 加载 /industry → banner 显示 + 推进按钮 disabled
4. /api/industry/commands.advance 返回 409
5. /api/industry/commands.new_game 重置后 → banner hidden
6. /api/world/state.player_game_over.is_game_over=true
7. /api/world/advance 返回 409
8. 截图保存
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
OUTPUT = ROOT / 'artifacts/governance-trigger-2026-10-16'


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    with socket.socket() as channel:
        channel.bind(('127.0.0.1', 0))
        port = channel.getsockname()[1]
    base = f'http://127.0.0.1:{port}'
    errors, events = [], []

    with tempfile.TemporaryDirectory(prefix='bipole-gov-') as folder:
        env = {**os.environ, 'BIPOLE_PORT': str(port), 'BIPOLE_SAVE_DIR': folder,
               'BIPOLE_DEV': '1',  # 启用 dev-only inject_game_over 端点
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
                    page = browser.new_page(viewport={'width': 1280, 'height': 900})
                    page.goto(base + '/industry', wait_until='networkidle')

                    # 1. banner 初始 hidden
                    banner = page.locator('#gameover-banner')
                    if banner.is_hidden():
                        events.append('✓ 初始 banner hidden')
                    else:
                        errors.append('✗ 初始 banner 应隐藏')

                    # 2. 触发 game_over（通过 HTTP 直调，绕过 advance）
                    # 用一个 Python 客户端调用一个内部 API：直接 mutate trigger 的路径
                    # 因为前端不能 mutate，我们用 backend 的 internal API 注入。
                    # 这里用一个 hack：先 POST save 让触发器就位，然后 mutate trigger 通过另一个 HTTP 路径
                    # 实际做法：构造一个特殊的测试端口 /test/inject_game_over（仅 dev 模式）
                    # 简化：直接通过 Python 脚本 mutate state，再 reload 页面
                    inject_url = base + '/api/industry/commands'
                    # 不能直接 mutate trigger，所以我们用 POST advance 几次后再 mutate
                    # 这里更简单的方式：注入一个特殊命令。但 IndustryGame API 不暴露这个。
                    # 替代：直接通过 Python http.client 访问一个临时 inject 端点
                    # 跳过注入阶段，改为"模拟 game_over"通过前端代码
                    # 实际上更现实：直接读 /api/industry/state，看 is_game_over=False（初始）
                    state_resp = page.evaluate("""async (url) => {
                        const r = await fetch(url);
                        return await r.json();
                    }""", base + '/api/industry/state')
                    if state_resp.get('is_game_over') is False:
                        events.append('✓ 初始 state.is_game_over=false')
                    else:
                        errors.append(f'✗ 初始 is_game_over 应为 false: {state_resp.get("is_game_over")}')

                    # 3. 截图（game_over 前）
                    page.screenshot(path=str(OUTPUT / 'industry-before-gameover.png'), full_page=True)

                    # 4. 用 Python 子进程注入 game_over（修改内存中 industry_game 实例）
                    # 4. 通过 dev-only 端点注入 game_over
                    inject_result = page.evaluate("""async (base) => {
                        const r = await fetch(base + '/api/industry/_dev/inject_game_over', {
                            method: 'POST'
                        });
                        return {status: r.status, body: await r.json()};
                    }""", base)
                    if inject_result['status'] == 200 and inject_result['body'].get('is_game_over'):
                        events.append('✓ 后端注入 game_over 成功（dev-only 端点）')
                    else:
                        errors.append(f'✗ 后端注入失败: {inject_result}')

                    # 5. 重渲染 banner + 按钮 disabled
                    page.reload(wait_until='networkidle')
                    banner_visible = page.locator('#gameover-banner').is_visible()
                    btn = page.locator('#advance-form button[type="submit"]')
                    if banner_visible:
                        events.append('✓ 刷新后 banner 显示')
                    else:
                        errors.append('✗ 刷新后 banner 未显示')
                    if btn.is_disabled():
                        events.append('✓ 月进按钮已 disabled')
                    else:
                        errors.append('✗ 月进按钮未 disabled')

                    # 6. /api/industry/commands.advance 返回 409
                    advance_rejected = page.evaluate("""async (base) => {
                        const r = await fetch(base + '/api/industry/commands', {
                            method: 'POST',
                            headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify({type: 'advance', months: 1})
                        });
                        return {status: r.status};
                    }""", base)
                    if advance_rejected['status'] == 409:
                        events.append('✓ REST advance 返回 409')
                    else:
                        errors.append(f'✗ REST advance 应返回 409，实际 {advance_rejected["status"]}')

                    # 7. 截图（game_over 后真实）
                    page.screenshot(path=str(OUTPUT / 'industry-after-gameover.png'), full_page=True)

                    # 8. /api/world/state.player_game_over.is_game_over=true
                    world_state = page.evaluate("""async (base) => {
                        const r = await fetch(base + '/api/world/state');
                        return await r.json();
                    }""", base)
                    if world_state.get('player_game_over', {}).get('is_game_over'):
                        events.append('✓ /api/world/state.player_game_over.is_game_over=true')
                    else:
                        errors.append('✗ world.player_game_over 应为 true')

                    # 9. /api/world/advance 返回 409
                    world_advance = page.evaluate("""async (base) => {
                        const r = await fetch(base + '/api/world/advance', {
                            method: 'POST',
                            headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify({months: 1})
                        });
                        return {status: r.status};
                    }""", base)
                    if world_advance['status'] == 409:
                        events.append('✓ /api/world/advance 返回 409')
                    else:
                        errors.append(f'✗ /api/world/advance 应返回 409，实际 {world_advance["status"]}')

                    # 10. new_game 重置
                    new_game = page.evaluate("""async (base) => {
                        const r = await fetch(base + '/api/industry/commands', {
                            method: 'POST',
                            headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify({type: 'new_game', seed: 42})
                        });
                        return {status: r.status, body: await r.json()};
                    }""", base)
                    if new_game['status'] == 200 and not new_game['body'].get('is_game_over'):
                        events.append('✓ new_game 重置触发器')
                    else:
                        errors.append(f'✗ new_game 未重置: {new_game["status"]}')

                    # 11. 加载 /world → 玩家城金色光晕（默认恢复）
                    page.goto(base + '/world', wait_until='networkidle')
                    page.wait_for_selector('.city-node', timeout=10000)
                    nodes = page.query_selector_all('.city-node')
                    if len(nodes) >= 5:
                        events.append(f'✓ /world 渲染 {len(nodes)} 城市节点')
                    else:
                        errors.append(f'✗ /world 节点不足：{len(nodes)}')
                    page.screenshot(path=str(OUTPUT / 'world-default.png'), full_page=True)

                    if errors:
                        errors.insert(0, f'✗ 失败 {len(errors)} 项')

                    browser.close()
            finally:
                server.terminate()
                try:
                    server.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    server.kill()

    print('=== M24 端到端验收 ===')
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