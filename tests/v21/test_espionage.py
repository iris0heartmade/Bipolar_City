"""M28 行业情报单元测试。"""
import random

import pytest

from backend.systems.v21.espionage import (
    EspionageAttempt,
    EspionageConfig,
    attempt_espionage,
    load_espionage_config,
    validate_espionage,
)


def test_load_default_config():
    cfg = load_espionage_config()
    assert cfg.cost_cr > 0
    assert cfg.min_visibility == 3
    assert cfg.discovery_probability_max > cfg.discovery_probability_low


def test_validate_visibility_blocks_below_threshold():
    cfg = EspionageConfig()
    ok, reason = validate_espionage(cfg, 'agricultural_town', current_month=1,
                                    visibility=2, history=[])
    assert not ok
    assert '可见等级不足' in reason


def test_validate_allows_alliance_visibility():
    cfg = EspionageConfig()
    ok, reason = validate_espionage(cfg, 'agricultural_town', current_month=1,
                                    visibility=3, history=[])
    assert ok, reason


def test_validate_max_per_month():
    cfg = EspionageConfig(max_per_month=1)
    history = [EspionageAttempt(target_city_id='agricultural_town', month=1, cost_cr=2000, success=True)]
    ok, reason = validate_espionage(cfg, 'agricultural_town', current_month=1,
                                    visibility=4, history=history)
    assert not ok
    assert '本月已用尽' in reason


def test_validate_cooldown():
    cfg = EspionageConfig(cooldown_months=1)
    history = [EspionageAttempt(target_city_id='agricultural_town', month=1, cost_cr=2000, success=True)]
    ok, reason = validate_espionage(cfg, 'agricultural_town', current_month=2,
                                    visibility=4, history=history)
    assert not ok
    assert '冷却中' in reason


def test_validate_cooldown_passes_after_period():
    cfg = EspionageConfig(cooldown_months=1)
    history = [EspionageAttempt(target_city_id='agricultural_town', month=1, cost_cr=2000, success=True)]
    ok, reason = validate_espionage(cfg, 'agricultural_town', current_month=3,
                                    visibility=4, history=history)
    assert ok, reason


def test_attempt_high_friendliness_low_discovery():
    cfg = EspionageConfig(discovery_probability_low=0.0,
                        discovery_probability_max=0.0,
                        discovery_friendliness_threshold=70)
    rng = random.Random(42)
    result = attempt_espionage(cfg, 'x', current_month=1, friendliness=80, rng=rng)
    assert result.success
    assert not result.discovered
    assert result.friendliness_delta == 0


def test_attempt_low_friendliness_discovery_always():
    """把概率设为 100%，friendly < threshold 必被发现。"""
    cfg = EspionageConfig(discovery_probability_low=0.0,
                        discovery_probability_max=1.0,
                        discovery_friendliness_threshold=70)
    rng = random.Random(42)
    result = attempt_espionage(cfg, 'x', current_month=1, friendliness=60, rng=rng)
    assert not result.success
    assert result.discovered
    assert result.friendliness_delta == -20


def test_attempt_deterministic_with_seed():
    cfg = EspionageConfig(discovery_probability_max=0.5,
                        discovery_probability_low=0.5,
                        discovery_friendliness_threshold=70)
    # 同样 seed + friendly → 同样结果
    rng1 = random.Random(42)
    r1 = attempt_espionage(cfg, 'x', current_month=1, friendliness=60, rng=rng1)
    rng2 = random.Random(42)
    r2 = attempt_espionage(cfg, 'x', current_month=1, friendliness=60, rng=rng2)
    assert r1.discovered == r2.discovered
    assert r1.success == r2.success


def test_espionage_attempt_roundtrip():
    a = EspionageAttempt(target_city_id='x', month=5, cost_cr=2000.5,
                        success=True, discovered=False, friendliness_delta=0,
                        note='test')
    d = a.to_dict()
    a2 = EspionageAttempt.from_dict(d)
    assert a2.target_city_id == 'x'
    assert a2.cost_cr == 2000.5
    assert a2.note == 'test'


def test_espionage_cost_is_constant():
    cfg = load_espionage_config()
    rng = random.Random(42)
    r1 = attempt_espionage(cfg, 'x', current_month=1, friendliness=80, rng=rng)
    assert r1.cost_cr == cfg.cost_cr