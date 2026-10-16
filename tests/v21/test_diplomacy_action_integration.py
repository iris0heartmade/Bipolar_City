"""M26 WorldApi POST /diplomacy_action 集成测试。"""
import asyncio

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from backend.industry_api import IndustryGame, router as industry_router
from backend.world_api import router as world_router
from backend.systems.v21.world_game import WorldGame, get_world_game, reset_world_game_for_tests


@pytest.fixture(autouse=True)
def _reset_world():
    reset_world_game_for_tests()


@pytest.fixture
def app_with_industry(tmp_path):
    """挂载 industry_router + world_router 的测试 FastAPI app。"""
    industry_game = IndustryGame(save_dir=tmp_path)
    # 让 world_game 知道 industry_game
    app = FastAPI()
    app.include_router(industry_router)
    app.include_router(world_router)
    app.state.industry_game = industry_game
    app.state.lock = asyncio.Lock()
    return app


def test_diplomacy_action_trade_agreement(app_with_industry):
    client = TestClient(app_with_industry)
    r = client.post('/api/world/diplomacy_action', json={
        'action_key': 'trade_agreement',
        'target_city_id': 'agricultural_town',
        'amount_cr': 0, 'note': '',
    })
    assert r.status_code == 200
    data = r.json()
    assert data['success'] is True
    assert data['record']['action_key'] == 'trade_agreement'
    assert data['record']['friendliness_delta'] == 1
    assert data['relation']['friendliness'] == 51


def test_diplomacy_action_tariff(app_with_industry):
    client = TestClient(app_with_industry)
    r = client.post('/api/world/diplomacy_action', json={
        'action_key': 'tariff',
        'target_city_id': 'commercial_port',
        'amount_cr': 0, 'note': '',
    })
    assert r.status_code == 200
    data = r.json()
    assert data['success'] is True
    assert data['record']['friendliness_delta'] == -10
    assert data['relation']['friendliness'] == 40


def test_diplomacy_action_aid_scales_by_amount(app_with_industry):
    client = TestClient(app_with_industry)
    r = client.post('/api/world/diplomacy_action', json={
        'action_key': 'aid',
        'target_city_id': 'magic_academy',
        'amount_cr': 750, 'note': '紧急援助',
    })
    assert r.status_code == 200
    data = r.json()
    assert data['success'] is True
    assert data['record']['friendliness_delta'] == 8  # ceil(750/100) = 8
    assert data['relation']['friendliness'] == 58


def test_diplomacy_action_unknown_rejected(app_with_industry):
    client = TestClient(app_with_industry)
    r = client.post('/api/world/diplomacy_action', json={
        'action_key': 'nonsense',
        'target_city_id': 'agricultural_town',
        'amount_cr': 0, 'note': '',
    })
    assert r.status_code == 422  # pydantic 校验失败


def test_diplomacy_action_max_per_month_enforced(app_with_industry):
    """trade_agreement 每月上限 3 次，第 4 次 success=false。"""
    client = TestClient(app_with_industry)
    for _ in range(3):
        r = client.post('/api/world/diplomacy_action', json={
            'action_key': 'trade_agreement',
            'target_city_id': 'agricultural_town',
            'amount_cr': 0, 'note': '',
        })
        assert r.json()['success'] is True
    # 第 4 次
    r = client.post('/api/world/diplomacy_action', json={
        'action_key': 'trade_agreement',
        'target_city_id': 'agricultural_town',
        'amount_cr': 0, 'note': '',
    })
    data = r.json()
    assert data['success'] is False
    assert '本月已用尽' in data['reason']


def test_diplomacy_action_history_visible_in_state(app_with_industry):
    client = TestClient(app_with_industry)
    # 发 3 个不同行动
    for action in ('trade_agreement', 'statement', 'aid'):
        amount = 500 if action == 'aid' else 0
        client.post('/api/world/diplomacy_action', json={
            'action_key': action, 'target_city_id': 'agricultural_town',
            'amount_cr': amount, 'note': '',
        })
    # 查该城市历史
    r = client.get('/api/world/diplomacy_actions/agricultural_town')
    history = r.json()
    assert len(history) == 3
    actions = [h['action_key'] for h in history]
    assert 'trade_agreement' in actions
    assert 'statement' in actions
    assert 'aid' in actions


def test_diplomacy_action_rejects_after_game_over(tmp_path):
    """玩家下台后，外交行动返回 409。"""
    industry_game = IndustryGame(save_dir=tmp_path)
    industry_game.city._trigger._game_over = True
    industry_game.city._trigger._reasons = ['测试']
    app = FastAPI()
    app.include_router(industry_router)
    app.include_router(world_router)
    app.state.industry_game = industry_game
    app.state.lock = asyncio.Lock()
    client = TestClient(app)
    r = client.post('/api/world/diplomacy_action', json={
        'action_key': 'trade_agreement',
        'target_city_id': 'agricultural_town',
        'amount_cr': 0, 'note': '',
    })
    assert r.status_code == 409
    assert r.json()['detail']['error'] == 'game_over'


def test_diplomacy_action_invalid_amount_rejected(app_with_industry):
    """amount_cr 为负数被 pydantic 拒绝。"""
    client = TestClient(app_with_industry)
    r = client.post('/api/world/diplomacy_action', json={
        'action_key': 'aid',
        'target_city_id': 'agricultural_town',
        'amount_cr': -100, 'note': '',
    })
    assert r.status_code == 422


def test_diplomacy_action_to_unknown_city_rejected(app_with_industry):
    client = TestClient(app_with_industry)
    r = client.post('/api/world/diplomacy_action', json={
        'action_key': 'trade_agreement',
        'target_city_id': 'mythical_city',
        'amount_cr': 0, 'note': '',
    })
    assert r.status_code == 404
    assert '未知城市' in r.json()['detail']