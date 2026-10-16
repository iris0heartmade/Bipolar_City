"""MayorPreferences 测试。"""
import pytest

from backend.systems.v21.mayor_preferences import (
    MayorPreferences, default_preferences, get_preferences, reset_cache_for_tests,
    WELFARE_STANCES,
)


@pytest.fixture(autouse=True)
def _reset_cache():
    reset_cache_for_tests()
    yield
    reset_cache_for_tests()


def test_default_preferences_loads_all_four_npcs():
    profiles = default_preferences()
    assert 'agricultural_town' in profiles
    assert 'commercial_port' in profiles
    assert 'magic_academy' in profiles
    assert 'mining_border' in profiles
    # 玩家城市不在 profiles 里
    assert 'industrial_city' not in profiles


def test_get_preferences_returns_correct_npc():
    p = get_preferences('agricultural_town')
    assert p.archetype == 'agricultural_town'
    assert p.tax_rate_preference == 0.10
    assert p.welfare_stance == 'austere'


def test_get_preferences_unknown_returns_default():
    p = get_preferences('unknown_city')
    assert p.city_id == 'unknown_city'
    assert p.archetype == ''


def test_industry_focus_has_all_seven_industries():
    for cid in ['agricultural_town', 'commercial_port', 'magic_academy', 'mining_border']:
        p = get_preferences(cid)
        assert len(p.industry_focus) == 7
        for i in ('AGR', 'IND', 'SRV', 'CHU', 'BNK', 'MAG', 'GOV'):
            assert i in p.industry_focus


def test_tax_rate_preference_in_valid_range():
    for cid in ['agricultural_town', 'commercial_port', 'magic_academy', 'mining_border']:
        p = get_preferences(cid)
        assert 0 <= p.tax_rate_preference <= 0.5


def test_welfare_stance_valid():
    for cid in ['agricultural_town', 'commercial_port', 'magic_academy', 'mining_border']:
        p = get_preferences(cid)
        assert p.welfare_stance in WELFARE_STANCES


def test_different_npcs_have_different_focus():
    agricultural = get_preferences('agricultural_town')
    magic = get_preferences('magic_academy')
    mining = get_preferences('mining_border')
    # 各 NPC 主导行业不同
    assert agricultural.industry_focus['AGR'] > agricultural.industry_focus['MAG']
    assert magic.industry_focus['MAG'] > magic.industry_focus['AGR']
    assert mining.industry_focus['IND'] > mining.industry_focus['MAG']


def test_expansion_drive_and_risk_in_unit_range():
    for cid in ['agricultural_town', 'commercial_port', 'magic_academy', 'mining_border']:
        p = get_preferences(cid)
        assert 0 <= p.expansion_drive <= 1
        assert 0 <= p.risk_tolerance <= 1
        assert 0 <= p.purchase_urgency <= 1