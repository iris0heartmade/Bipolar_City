"""M24 经济触发器与 IndustryGame + REST 集成测试。

策略：直接 mutate city._trigger._game_over = True 模拟 advance 后的 game_over 状态，
避免 mutate cash 触发 attractiveness.cash_runway 校验崩溃。
真实 advance 触发路径在 test_governance_trigger.py 单元测试中覆盖（13 项）。
"""
import pytest

from backend.industry_api import IndustryGame


@pytest.fixture
def tmp_save_dir(tmp_path):
    return tmp_path


def _force_game_over(city, key='government_bankruptcy'):
    """模拟 city.advance() 触发器检测后 game_over=True 的状态。"""
    cfg = city._trigger.config[key]
    city._trigger._game_over = True
    city._trigger._reasons = [cfg.label]
    city._trigger._streak[key] = cfg.grace_months


def test_industrygame_advance_loop_breaks_on_game_over(tmp_save_dir):
    """advance(months=10) 在 game_over 已置位时立即 break（不再推进）。"""
    game = IndustryGame(save_dir=tmp_save_dir)
    _force_game_over(game.city, 'government_bankruptcy')
    before_month = game.city.month
    game.command({'type': 'advance', 'months': 10})
    # 已经 game_over，循环开头就 break（advance 已置位 game_over 时）
    assert game.city.month <= before_month + 1


def test_snapshot_exposes_game_over_fields(tmp_save_dir):
    game = IndustryGame(save_dir=tmp_save_dir)
    snap = game.snapshot()
    assert 'is_game_over' in snap
    assert 'game_over_reasons' in snap
    assert 'trigger_streak' in snap
    assert snap['is_game_over'] is False
    assert snap['game_over_reasons'] == []


def test_snapshot_reflects_game_over_state(tmp_save_dir):
    game = IndustryGame(save_dir=tmp_save_dir)
    _force_game_over(game.city, 'government_bankruptcy')
    snap = game.snapshot()
    assert snap['is_game_over'] is True
    assert any('政府现金破产' in r for r in snap['game_over_reasons'])
    assert snap['trigger_streak']['government_bankruptcy'] >= 3


def test_save_and_load_preserves_game_over(tmp_save_dir):
    """存档序列化 _trigger 状态；load 后保留 game_over。"""
    game = IndustryGame(save_dir=tmp_save_dir)
    _force_game_over(game.city, 'government_bankruptcy')
    game.command({'type': 'save'})
    game2 = IndustryGame(save_dir=tmp_save_dir)
    game2.command({'type': 'load'})
    assert game2.city._trigger.is_game_over
    assert game2.city._trigger.reasons == game.city._trigger.reasons


def test_new_game_after_game_over_resets_trigger(tmp_save_dir):
    """game_over 后 new_game 开新城市，触发器归零。"""
    game = IndustryGame(save_dir=tmp_save_dir)
    _force_game_over(game.city, 'government_bankruptcy')
    game.command({'type': 'new_game', 'seed': 42})
    assert game.city._trigger.is_game_over is False
    assert game.city._trigger.reasons == []
    assert all(v == 0 for v in game.city._trigger.streak.values())


def test_command_rejects_advance_after_game_over(tmp_save_dir):
    """game_over 后 advance 命令返回 409。"""
    import asyncio
    game = IndustryGame(save_dir=tmp_save_dir)
    _force_game_over(game.city, 'government_bankruptcy')
    from fastapi import FastAPI
    from backend.industry_api import router as industry_router
    app = FastAPI()
    app.include_router(industry_router)
    app.state.lock = asyncio.Lock()
    app.state.industry_game = game
    from starlette.testclient import TestClient
    client = TestClient(app)

    # advance 应该返回 409
    r = client.post('/api/industry/commands', json={'type': 'advance', 'months': 1})
    assert r.status_code == 409
    assert r.json()['detail']['error'] == 'game_over'

    # save 仍然允许
    r = client.post('/api/industry/commands', json={'type': 'save'})
    assert r.status_code == 200

    # new_game 仍然允许
    r = client.post('/api/industry/commands', json={'type': 'new_game', 'seed': 42})
    assert r.status_code == 200
    assert game.city._trigger.is_game_over is False

    # load 也允许
    r = client.post('/api/industry/commands', json={'type': 'save'})
    assert r.status_code == 200
    r = client.post('/api/industry/commands', json={'type': 'load'})
    assert r.status_code == 200


def test_unemployment_crisis_in_game_over(tmp_save_dir):
    game = IndustryGame(save_dir=tmp_save_dir)
    _force_game_over(game.city, 'unemployment_crisis')
    snap = game.snapshot()
    assert snap['is_game_over']
    assert any('失业率危机' in r for r in snap['game_over_reasons'])


def test_wage_arrears_runaway_in_game_over(tmp_save_dir):
    game = IndustryGame(save_dir=tmp_save_dir)
    _force_game_over(game.city, 'wage_arrears_runaway')
    snap = game.snapshot()
    assert snap['is_game_over']
    assert any('累计欠薪' in r for r in snap['game_over_reasons'])


def test_population_collapse_in_game_over(tmp_save_dir):
    game = IndustryGame(save_dir=tmp_save_dir)
    _force_game_over(game.city, 'population_collapse')
    snap = game.snapshot()
    assert snap['is_game_over']
    assert any('人口锐减' in r for r in snap['game_over_reasons'])


def test_streak_survives_across_snapshots(tmp_save_dir):
    """streak 在 snapshot 中持续累积，不被读取清零。"""
    game = IndustryGame(save_dir=tmp_save_dir)
    game.city._trigger._streak['unemployment_crisis'] = 2
    snap1 = game.snapshot()
    snap2 = game.snapshot()
    assert snap1['trigger_streak']['unemployment_crisis'] == 2
    assert snap2['trigger_streak']['unemployment_crisis'] == 2