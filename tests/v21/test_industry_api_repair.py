"""真实行业命令的原子性、保存恢复及输入边界。"""
from copy import deepcopy
import json

from fastapi.testclient import TestClient
import pytest

from backend.app import app
from backend.industry_api import IndustryGame


def test_parameter_updates_reported_consumption_factor(tmp_path):
    game = IndustryGame(tmp_path)
    game.command({'type': 'set_parameter', 'section': 'city',
                  'key': 'household_consumption_size', 'value': 4})
    assert game.city.household_size == 4
    report = game.command({'type': 'advance'})['latest']
    assert report['household_size'] == 4


def test_failure_after_mutation_keeps_original_city(tmp_path, monkeypatch):
    game = IndustryGame(tmp_path)
    before = deepcopy(game.city.to_dict())

    def fail(self, **kwargs):
        self.cash['GOV'] = -1
        raise ValueError('受控失败')

    monkeypatch.setattr(type(game.city), 'mayor_command', fail)
    with pytest.raises(ValueError):
        game.command({'type': 'mayor_command', 'mayor_kind': 'subsidy_industry',
                      'industry': 'IND', 'cash_cr': 10})
    assert game.city.to_dict() == before


def test_save_backup_and_failed_load_preserve_current_city(tmp_path):
    game = IndustryGame(tmp_path)
    game.command({'type': 'advance'})
    game.command({'type': 'save'})
    original = (tmp_path / 'industry-slot.json').read_bytes()
    game.command({'type': 'advance'})
    game.command({'type': 'save'})
    assert (tmp_path / 'industry-slot.bak').read_bytes() == original
    before = deepcopy(game.city.to_dict())
    (tmp_path / 'industry-slot.json').write_text('{}', encoding='utf-8')
    with pytest.raises(ValueError):
        game.command({'type': 'load'})
    assert game.city.to_dict() == before


def test_http_bounds_seed_and_120_month_contract(tmp_path):
    with TestClient(app) as client:
        app.state.industry_game = IndustryGame(tmp_path)
        result = client.post('/api/industry/commands', json={'type': 'new_game', 'seed': 123})
        assert result.status_code == 200
        for fields in ({'type': 'purchase_external', 'cash_cr': -1},
                       {'type': 'purchase_external', 'raw_units': -1},
                       {'type': 'purchase_external', 'cash_cr': True},
                       {'type': 'purchase_external', 'raw_units': True},
                       {'type': 'advance', 'months': 1.5},
                       {'type': 'advance', 'months': True}):
            assert client.post('/api/industry/commands', json=fields).status_code == 422
        assert client.post('/api/industry/commands', json={'type': 'advance', 'months': 120}).status_code == 200


def test_policy_save_round_trip_and_replay(tmp_path):
    game = IndustryGame(tmp_path)
    game.command({'type': 'advance'})
    command = {'type': 'mayor_command', 'mayor_kind': 'limit_layoff',
               'industry': 'IND', 'policy_months': 3, 'request_id': 'policy-once'}
    game.command(command)
    game.command(command)
    game.command({'type': 'save'})
    json.loads((tmp_path / 'industry-slot.json').read_text(encoding='utf-8'))
    expected = game.command({'type': 'advance'})['latest']
    game.command({'type': 'load'})
    assert game.command({'type': 'advance'})['latest'] == expected


def test_direct_and_map_endpoint_replay_same_command(tmp_path):
    from backend.game_api import Command as MapCommand, commands
    from types import SimpleNamespace
    import asyncio
    game = IndustryGame(tmp_path)
    game.command({'type': 'advance', 'request_id': 'cross-entry'})
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        industry_game=game, lock=asyncio.Lock())))
    result = asyncio.run(commands(MapCommand(type='advance', request_id='cross-entry'), request))
    assert result['month'] == 1
