from copy import deepcopy
from types import SimpleNamespace
import asyncio
import pytest
from backend.industry_api import IndustryGame
from backend.game_api import build_state, placement, _handle_build, _handle_remove


def game(tmp_path):
    return IndustryGame(tmp_path)


def test_preview_uses_catalog_and_full_footprint(tmp_path):
    g = game(tmp_path)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(industry_game=g)))
    house = next(b for b in build_state(g)['world']['buildings'] if b['definition_id'] == 'row_house')
    assert asyncio.run(placement(request, 'workshop', house['x'], house['y'], 0))['valid'] is False
    assert asyncio.run(placement(request, 'workshop', 2, 18, 2))['valid'] is True
    assert asyncio.run(placement(request, 'workshop', 27, 16, 1))['valid'] is False


def test_unique_initial_ids(tmp_path):
    buildings = build_state(game(tmp_path))['world']['buildings']
    assert len({b['id'] for b in buildings}) == len(buildings)


def test_real_cost_next_month_capacity_and_replay(tmp_path):
    g = game(tmp_path)
    cash = dict(g.city.cash)
    jobs, output = g.city.jobs('IND'), g.city.qbar['IND']
    payload = dict(definition_id='workshop', x=2, y=18, rotation=2, request_id='one')
    _handle_build(payload, g)
    _handle_build(payload, g)
    assert cash['GOV']-g.city.cash['GOV'] == 640000
    assert g.city.cash['IND']-cash['IND'] == 640000
    assert g.city.jobs('IND') == jobs
    assert len([b for b in build_state(g)['world']['buildings'] if b['status']=='constructing']) == 1
    g.command({'type':'advance', 'months':1})
    assert g.city.jobs('IND') == jobs+40
    assert g.city.qbar['IND'] > output


def test_invalid_build_is_atomic(tmp_path):
    g = game(tmp_path)
    before = deepcopy(g.city.__dict__)
    with pytest.raises(ValueError):
        _handle_build(dict(definition_id='workshop',x=8,y=8), g)
    assert g.city.__dict__ == before


def test_cannot_remove_townhall(tmp_path):
    g=game(tmp_path)
    hall=next(b for b in build_state(g)['world']['buildings'] if b['definition_id']=='town_hall')
    with pytest.raises(ValueError):
        _handle_remove({'building_id':hall['id']},g)


def test_overlap_and_insufficient_cash_leave_city_unchanged(tmp_path):
    g=game(tmp_path)
    _handle_build(dict(definition_id='workshop',x=2,y=18,rotation=2),g)
    before=deepcopy(g.city.__dict__)
    with pytest.raises(ValueError, match='重叠'):
        _handle_build(dict(definition_id='market',x=3,y=18,rotation=2),g)
    assert g.city.__dict__ == before
    g.city.cash['IND'] += g.city.cash['GOV']
    g.city.cash['GOV']=0
    before=deepcopy(g.city.__dict__)
    with pytest.raises(ValueError, match='现金不足'):
        _handle_build(dict(definition_id='workshop',x=9,y=18,rotation=2),g)
    assert g.city.__dict__ == before


def test_housing_is_not_agriculture_jobs_and_survives_save(tmp_path):
    g=game(tmp_path)
    jobs=g.city.jobs('AGR')
    _handle_build(dict(definition_id='row_house',x=0,y=8,rotation=0),g)
    assert build_state(g)['housing']['capacity']==3600
    g.command({'type':'save'})
    g.command({'type':'load'})
    g.command({'type':'advance','months':1})
    assert g.city.jobs('AGR')==jobs
    assert build_state(g)['housing']['capacity']==3628
    assert any(t['kind']=='building_maintenance' for t in g.city.last_report['transactions'])
    g.command({'type':'save'})
    g.command({'type':'load'})
    assert build_state(g)['housing']['capacity']==3628


def test_request_id_cannot_be_reused_for_another_map_command(tmp_path):
    g=game(tmp_path)
    _handle_build(dict(definition_id='workshop',x=2,y=18,rotation=2,request_id='id'),g)
    before=deepcopy(g.city.__dict__)
    with pytest.raises(ValueError,match='编号'):
        _handle_build(dict(definition_id='workshop',x=9,y=18,rotation=2,request_id='id'),g)
    assert g.city.__dict__==before


def test_new_seed_and_save_restores_construction(tmp_path):
    g=game(tmp_path)
    g.command({'type':'new_game','seed':123})
    assert build_state(g)['world']['seed']==123
    _handle_build(dict(definition_id='workshop',x=2,y=18,rotation=2),g)
    g.command({'type':'save'})
    g.command({'type':'new_game','seed':456})
    g.command({'type':'load'})
    assert build_state(g)['world']['seed']==123
    assert build_state(g)['summary']['construction']==1


@pytest.mark.parametrize('x,y,rotation',[(0,0,0),(7,11,1),(22,18,2),(26,17,0)])
def test_road_or_terrain_required(tmp_path,x,y,rotation):
    from backend.game_api import validate_placement
    g=game(tmp_path)
    with pytest.raises(ValueError):
        validate_placement(g.city,'workshop',x,y,rotation)


def test_rotate_footprint_and_entry_match_renderer(tmp_path):
    from backend.game_api import validate_placement
    g=game(tmp_path)
    # 旋转90度后2×3占地，入口在左侧道路x=15。
    validate_placement(g.city,'workshop',16,11,1)
    with pytest.raises(ValueError):
        validate_placement(g.city,'workshop',16,11,0)



def test_relocation_keeps_capacity_and_checks_complete_footprint(tmp_path):
    from backend.game_api import _handle_relocate
    g=game(tmp_path)
    _handle_build(dict(definition_id='workshop',x=2,y=18,rotation=2),g)
    b=next(b for b in build_state(g)['world']['buildings'] if b.get('player_built'))
    g.command({'type':'advance','months':1})
    jobs=g.city.jobs('IND')
    before=deepcopy(g.city.__dict__)
    with pytest.raises(ValueError):
        _handle_relocate(dict(building_id=b['id'],new_x=14,new_y=18,rotation=2),g)
    assert g.city.__dict__==before
    _handle_relocate(dict(building_id=b['id'],new_x=9,new_y=18,rotation=2),g)
    assert g.city.jobs('IND')==jobs
    assert next(b for b in build_state(g)['world']['buildings'] if b.get('player_built'))['x']==9


def test_remove_returns_capacity_and_ids_are_never_reused(tmp_path):
    g=game(tmp_path)
    baseline=g.city.jobs('IND')
    output=g.city.qbar['IND']
    _handle_build(dict(definition_id='workshop',x=2,y=18,rotation=2),g)
    first=next(b for b in build_state(g)['world']['buildings'] if b.get('player_built'))['id']
    g.command({'type':'advance','months':1})
    _handle_remove(dict(building_id=first),g)
    assert g.city.jobs('IND')==baseline
    assert g.city.qbar['IND']==pytest.approx(output)
    _handle_build(dict(definition_id='workshop',x=2,y=18,rotation=2),g)
    second=next(b for b in build_state(g)['world']['buildings'] if b.get('player_built'))['id']
    assert first!=second


def test_maintenance_due_is_real_and_prevents_demolition(tmp_path):
    from backend.systems.v21.map_economy import advance_buildings
    g=game(tmp_path)
    _handle_build(dict(definition_id='park',x=0,y=8,rotation=0),g)
    b=next(b for b in build_state(g)['world']['buildings'] if b.get('player_built'))
    g.city.cash['IND'] += g.city.cash['GOV']
    g.city.cash['GOV']=0
    advance_buildings(g.city)
    assert b['maintenance_due_cents']==900
    with pytest.raises(ValueError,match='欠款'):
        _handle_remove(dict(building_id=b['id']),g)
    g.city.assert_invariants()


def test_seed_reproduces_initial_visual_layout(tmp_path):
    g=game(tmp_path)
    g.command({'type':'new_game','seed':123})
    first=build_state(g)['world']['buildings']
    g.command({'type':'new_game','seed':456})
    second=build_state(g)['world']['buildings']
    assert first != second
    g.command({'type':'new_game','seed':123})
    assert build_state(g)['world']['buildings'] == first


@pytest.mark.parametrize('damage', ['overlap','duplicate','outside','capacity','output','maintenance','housing'])
def test_damaged_map_state_is_rejected(tmp_path,damage):
    from backend.systems.v21.map_economy import assert_map_invariants
    g=game(tmp_path)
    _handle_build(dict(definition_id='workshop',x=2,y=18,rotation=2),g)
    b=next(b for b in g.city.scenario['_map_buildings'] if b.get('player_built'))
    if damage=='overlap': b.update(x=5,y=8,rotation=0)
    elif damage=='duplicate': b['id']='h0'
    elif damage=='outside': b['x']=27
    elif damage=='capacity': g.city.definitions['IND']['job_capacity_max']+=1
    elif damage=='output': g.city.qbar['IND']+=1
    elif damage=='maintenance': b['maintenance_due_cents']=-1
    elif damage=='housing': g.city.housing_capacity+=1
    before=deepcopy(g.city.__dict__)
    with pytest.raises(ValueError): assert_map_invariants(g.city)
    assert g.city.__dict__ == before


def test_map_read_is_pure_and_initial_layout_passes_geometry(tmp_path):
    from backend.systems.v21.map_economy import assert_map_invariants
    g=game(tmp_path)
    before=deepcopy(g.city.__dict__)
    state=build_state(g)
    assert g.city.__dict__==before
    g.city.scenario['_map_buildings']=state['world']['buildings']
    assert_map_invariants(g.city)
