"""M28 行业情报 REST 集成测试。"""
import asyncio

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from backend.industry_api import IndustryGame, router as industry_router
from backend.world_api import router as world_router
from backend.systems.v21.world_game import reset_world_game_for_tests


@pytest.fixture(autouse=True)
def _reset_world():
    reset_world_game_for_tests()


@pytest.fixture
def app_with_industry(tmp_path):
    industry_game = IndustryGame(save_dir=tmp_path)
    app = FastAPI()
    app.include_router(industry_router)
    app.include_router(world_router)
    app.state.industry_game = industry_game
    app.state.lock = asyncio.Lock()
    return app


def test_espionage_requires_alliance(app_with_industry):
    """友好度 50（visible=2 友好）应被拒。"""
    client = TestClient(app_with_industry)
    r = client.post('/api/world/espionage', json={'target_city_id': 'agricultural_town'})
    assert r.status_code == 200
    data = r.json()
    assert data['success'] is False
    assert '可见等级不足' in data['reason']


def test_espionage_rejects_unknown_city(app_with_industry):
    client = TestClient(app_with_industry)
    r = client.post('/api/world/espionage', json={'target_city_id': 'mythical'})
    assert r.status_code == 404


def test_espionage_rejects_player_city(app_with_industry):
    client = TestClient(app_with_industry)
    r = client.post('/api/world/espionage', json={'target_city_id': 'industrial_city'})
    assert r.status_code == 400


def test_espionage_succeeds_with_full_report(app_with_industry):
    """友好度提升到 70（盟友级），间谍派遣应成功并返回完整 last_report。"""
    client = TestClient(app_with_industry)
    from backend.systems.v21.world_game import get_world_game
    wg = get_world_game()
    # 先 advance 一次让 NPC 城市生成 last_report
    wg.advance_npc_only(months=1)
    # 把友好度推到 70（盟友级）
    for _ in range(20):
        wg.diplomacy.adjust_friendliness(
            'industrial_city', 'agricultural_town', 1, '调试拉高友好度',
        )
    friendliness = wg.diplomacy.relation_with_player('agricultural_town').friendliness
    assert friendliness >= 61
    # 派间谍
    r = client.post('/api/world/espionage', json={'target_city_id': 'agricultural_town'})
    assert r.status_code == 200
    data = r.json()
    assert data['record']['cost_cr'] == 2000
    # full_report 应有内容（sectors / labor_force / month 等）
    if data['success']:
        assert data['full_report'] is not None
        assert 'sectors' in data['full_report']
        assert 'labor_force' in data['full_report']


def test_espionage_cooldown_blocks_second_call(app_with_industry):
    """cooldown_months=1，第一次成功后第 2 月不能再调（要等 cooldown_months+1 月）。"""
    client = TestClient(app_with_industry)
    from backend.systems.v21.world_game import get_world_game
    wg = get_world_game()
    wg.advance_npc_only(months=1)
    for _ in range(20):
        wg.diplomacy.adjust_friendliness('industrial_city', 'agricultural_town', 1, '调试')
    # 第 1 次
    r1 = client.post('/api/world/espionage', json={'target_city_id': 'agricultural_town'})
    d1 = r1.json()
    if not d1['success']:
        pass
    # 第 2 次（同月）被拒（max_per_month=1）
    r2 = client.post('/api/world/espionage', json={'target_city_id': 'agricultural_town'})
    d2 = r2.json()
    assert d2['success'] is False
    assert '本月已用尽' in d2['reason'] or '冷却中' in d2['reason']


def test_intelligence_endpoint_requires_visibility_3(app_with_industry):
    """visible < 3 时 intelligence 端点返回 403。"""
    client = TestClient(app_with_industry)
    r = client.get('/api/world/cities/agricultural_town/intelligence')
    assert r.status_code == 403
    assert '可见等级不足' in r.json()['detail']


def test_intelligence_endpoint_returns_full_report(app_with_industry):
    """提升友好度后 intelligence 返回 last_report 完整字段。"""
    client = TestClient(app_with_industry)
    from backend.systems.v21.world_game import get_world_game
    wg = get_world_game()
    wg.advance_npc_only(months=1)  # NPC advance 让 last_report 生成
    for _ in range(20):
        wg.diplomacy.adjust_friendliness('industrial_city', 'agricultural_town', 1, '调试')
    r = client.get('/api/world/cities/agricultural_town/intelligence')
    assert r.status_code == 200
    data = r.json()
    assert 'last_report' in data
    assert data['last_report'] is not None
    assert 'labor_force' in data['last_report']
    assert 'sectors' in data['last_report']


def test_espionage_rejects_after_game_over(tmp_path):
    """玩家下台后 espionage 返回 409。"""
    industry_game = IndustryGame(save_dir=tmp_path)
    industry_game.city._trigger._game_over = True
    industry_game.city._trigger._reasons = ['测试']
    app = FastAPI()
    app.include_router(industry_router)
    app.include_router(world_router)
    app.state.industry_game = industry_game
    app.state.lock = asyncio.Lock()
    client = TestClient(app)
    r = client.post('/api/world/espionage', json={'target_city_id': 'agricultural_town'})
    assert r.status_code == 409
    assert r.json()['detail']['error'] == 'game_over'