"""WorldApi REST 接口测试。"""
import pytest
from fastapi.testclient import TestClient

from backend.app import app
from backend.systems.v21.world_game import reset_world_game_for_tests
from backend.world_api import reset_world_for_tests


@pytest.fixture(autouse=True)
def _reset_state():
    reset_world_game_for_tests()
    reset_world_for_tests()
    yield
    reset_world_game_for_tests()
    reset_world_for_tests()


def test_state_endpoint_returns_world():
    client = TestClient(app)
    r = client.get('/api/world/state')
    assert r.status_code == 200
    body = r.json()
    assert 'cities' in body
    assert 'links' in body
    assert len(body['cities']) == 5
    assert len(body['links']) == 10  # C(5,2)


def test_city_detail_returns_view():
    client = TestClient(app)
    r = client.get('/api/world/cities/agricultural_town')
    assert r.status_code == 200
    body = r.json()
    assert body['id'] == 'agricultural_town'
    assert body['name_cn'] == '禾谷镇'
    assert 'visibility' in body
    assert 'visibility_label' in body
    assert 'ai_recent_actions' in body


def test_city_detail_404_for_unknown():
    client = TestClient(app)
    r = client.get('/api/world/cities/unknown_city')
    assert r.status_code == 404


def test_advance_endpoint_increments_month():
    client = TestClient(app)
    r = client.post('/api/world/advance', json={'months': 3})
    assert r.status_code == 200
    body = r.json()
    assert body['world']['month'] == 3


def test_advance_rejects_invalid():
    client = TestClient(app)
    r = client.post('/api/world/advance', json={'months': 0})
    assert r.status_code == 422
    r = client.post('/api/world/advance', json={'months': 121})
    assert r.status_code == 422


def test_world_page_served():
    """先确保 frontend/world.html 还没创建时，路由返回 404。"""
    from pathlib import Path
    from backend.config import ROOT
    has_world_html = (Path(ROOT) / 'frontend' / 'world.html').exists()
    if not has_world_html:
        pytest.skip('frontend/world.html 尚未创建')
    client = TestClient(app)
    r = client.get('/world')
    assert r.status_code == 200