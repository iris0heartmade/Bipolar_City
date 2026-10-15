from fastapi.testclient import TestClient

from backend.app import app
from backend.core.service import GameService
from test_simulation import legal_position


def test_real_http_build_and_recover(tmp_path):
    with TestClient(app) as client:
        app.state.game=GameService(tmp_path)
        assert client.get('/').status_code==200
        assert client.get('/static/js/main.js').status_code==200
        before=client.get('/api/state').json();x,y=legal_position(app.state.game.world)
        assert client.get('/api/placement',params={"definition_id":"row_house","x":x,"y":y}).json()["valid"]
        placed=client.post('/api/commands',json={"type":"place_blueprint","definition_id":"row_house","x":x,"y":y}).json()
        assert placed["summary"]["blueprints"]==1 and placed["world"]["cash"]==before["world"]["cash"]
        assert client.post('/api/commands',json={"type":"commit"}).status_code==200
        after=client.post('/api/commands',json={"type":"advance_month"}).json()
        assert after["summary"]["capacity"]==before["summary"]["capacity"]+28
        assert after["world"]["population"]>before["world"]["population"]
        assert client.post('/api/commands',json={"type":"save"}).status_code==200
        client.post('/api/commands',json={"type":"advance_month"})
        loaded=client.post('/api/commands',json={"type":"load"}).json()
        assert loaded["world"]==after["world"]


def test_http_rejects_float_and_unknown_commands(tmp_path):
    with TestClient(app) as client:
        app.state.game=GameService(tmp_path)
        before=client.get('/api/state').json()["world"]
        assert client.post('/api/commands',json={"type":"place_blueprint","definition_id":"row_house","x":1.2,"y":8}).status_code==422
        assert client.post('/api/commands',json={"type":"unlimited_money"}).status_code==422
        assert client.post('/api/commands',json={"type":"commit"}).status_code==400
        assert client.get('/api/state').json()["world"]==before


def test_http_for_sale_and_property_mode(tmp_path):
    with TestClient(app) as client:
        app.state.game=GameService(tmp_path);x,y=legal_position(app.state.game.world)
        placed=client.post('/api/commands',json={"type":"place_blueprint","definition_id":"row_house","x":x,"y":y,"housing_mode":"for_sale"}).json()
        building_id=placed["world"]["buildings"][-1]["id"]
        client.post('/api/commands',json={"type":"commit"})
        sold=client.post('/api/commands',json={"type":"advance_month"}).json()
        assert sold["housing"]["unsold_units"]==5 and sold["world"]["history"][-1]["housing_sales"]==2
        assert sold["building_info"][str(building_id)]["capacity"]==8
        before=sold["world"]
        assert client.post('/api/commands',json={"type":"set_housing_mode","building_id":building_id,"housing_mode":"public"}).status_code==400
        assert client.get('/api/state').json()["world"]==before
