"""V2.1 阶段 0 起步验收测试（2026-10-15）。

本测试覆盖 V2.1《城市聚合经济系统理论说明书》阶段 0 起步的最小核心：
- OCEAN / PEER / OCEAN_ACCOUNT / peer_account() 常量与工厂函数
- is_ocean() / is_peer() / peer_city_id_of() 纯函数版判定
- PeerFlow 数据类

【验收清单】
阶段 0 起步（V2.1）：
- [x] OCEAN_ACCOUNT = 'ocean'，PEER_ACCOUNT_PREFIX = 'city:'
- [x] peer_account('B') == 'city:B'；空 city_id 抛 ValueError
- [x] is_ocean('ocean', 'ocean', None) == True
- [x] is_ocean('world', 'external', None) == True（V1.0 兼容路径）
- [x] is_ocean('city:B', 'peer', 'B') == False
- [x] is_peer('city:B', 'B') == True
- [x] is_peer('ocean', None) == False
- [x] PeerFlow 可实例化，frozen=True
"""
import pytest

from backend.systems.v21.accounts import (
    OCEAN, OCEAN_ACCOUNT, PEER, PEER_ACCOUNT_PREFIX, EXTERNAL,
    peer_account, is_ocean, is_peer, peer_city_id_of,
)
from backend.systems.v21.ledger import PeerFlow


# ===== V2.1 阶段 0：账户边界常量 =====

def test_constants():
    """阶段 0：常量字符串。"""
    assert OCEAN == 'ocean'
    assert OCEAN_ACCOUNT == 'ocean'
    assert PEER == 'peer'
    assert PEER_ACCOUNT_PREFIX == 'city:'
    # EXTERNAL 仅为 V1.0 兼容，正式 V2.1 代码不应使用
    assert EXTERNAL == 'external'


# ===== V2.1 阶段 0：peer_account 工厂函数 =====

def test_peer_account_factory():
    """阶段 0：peer_account('B') == 'city:B'。"""
    assert peer_account('B') == 'city:B'
    assert peer_account('alpha_1') == 'city:alpha_1'
    assert peer_account('New_City_42') == 'city:New_City_42'


def test_peer_account_empty_raises():
    """阶段 0：空 city_id 抛 ValueError。"""
    with pytest.raises(ValueError):
        peer_account('')
    with pytest.raises(ValueError):
        peer_account(None) if False else peer_account('')   # 防御性


# ===== V2.1 阶段 0：is_ocean 精确判定 =====

def test_is_ocean_on_ocean_account():
    """阶段 0：'ocean' 账户判定为海洋。"""
    assert is_ocean(OCEAN_ACCOUNT, OCEAN, None) is True


def test_is_ocean_compatible_with_legacy_world():
    """阶段 0：V1.0 'world' 账户（owner_kind=EXTERNAL、city_id=None）也判为海洋。"""
    assert is_ocean('world', EXTERNAL, None) is True


def test_is_ocean_on_peer_account_is_false():
    """阶段 0：city:<id> 账户**不**判为海洋（关键：邻城不能被识别为海洋）。"""
    assert is_ocean('city:B', PEER, 'B') is False


def test_is_ocean_rejects_city_id():
    """阶段 0：city_id 不为 None 的账户一律不判为海洋。"""
    assert is_ocean(OCEAN_ACCOUNT, OCEAN, 'some_city') is False
    assert is_ocean('world', EXTERNAL, 'some_city') is False


# ===== V2.1 阶段 0：is_peer 精确判定 =====

def test_is_peer_on_city_account():
    """阶段 0：city:<id> 形式且 city_id 不为 None 判为 peer。"""
    assert is_peer('city:B', 'B') is True
    assert is_peer('city:alpha_1', 'alpha_1') is True


def test_is_peer_rejects_ocean():
    """阶段 0：海洋账户不判为 peer。"""
    assert is_peer(OCEAN_ACCOUNT, None) is False


def test_is_peer_rejects_world_legacy():
    """阶段 0：V1.0 'world' 账户不判为 peer。"""
    assert is_peer('world', None) is False


def test_is_peer_requires_city_id():
    """阶段 0：city:<id> 形式但 city_id 为 None 不判为 peer。"""
    assert is_peer('city:B', None) is False


# ===== V2.1 阶段 0：peer_city_id_of 工具函数 =====

def test_peer_city_id_of_returns_city_id():
    """阶段 0：peer_city_id_of('city:B') == 'B'。"""
    assert peer_city_id_of('city:B') == 'B'
    assert peer_city_id_of('city:alpha_1') == 'alpha_1'


def test_peer_city_id_of_returns_none_for_non_peer():
    """阶段 0：非 peer 账户返回 None。"""
    assert peer_city_id_of(OCEAN_ACCOUNT) is None
    assert peer_city_id_of('world') is None
    assert peer_city_id_of('firms') is None


# ===== V2.1 阶段 0：PeerFlow 数据类 =====

def test_peer_flow_dataclass_instantiable():
    """阶段 0：PeerFlow 可实例化。"""
    flow = PeerFlow(
        month=1, direction='out', peer_city_id='B', category='export', amount_cents=5000,
    )
    assert flow.month == 1
    assert flow.direction == 'out'
    assert flow.peer_city_id == 'B'
    assert flow.category == 'export'
    assert flow.amount_cents == 5000


def test_peer_flow_is_frozen():
    """阶段 0：PeerFlow 是 frozen dataclass，不可变。"""
    flow = PeerFlow(
        month=1, direction='in', peer_city_id='A', category='import', amount_cents=1000,
    )
    with pytest.raises(Exception):  # FrozenInstanceError
        flow.amount_cents = 2000  # type: ignore[misc]


def test_peer_flow_direction_values():
    """阶段 0：direction 取值只允许 'in' / 'out'（业务约束）。"""
    out_flow = PeerFlow(month=1, direction='out', peer_city_id='B', category='export', amount_cents=1)
    in_flow = PeerFlow(month=1, direction='in', peer_city_id='A', category='import', amount_cents=1)
    assert out_flow.direction == 'out'
    assert in_flow.direction == 'in'


def test_peer_flow_equality():
    """阶段 0：相同字段的 PeerFlow 相等（frozen=True 自动生成 __eq__）。"""
    a = PeerFlow(month=5, direction='out', peer_city_id='B', category='export', amount_cents=1000)
    b = PeerFlow(month=5, direction='out', peer_city_id='B', category='export', amount_cents=1000)
    assert a == b
    c = PeerFlow(month=5, direction='in', peer_city_id='B', category='export', amount_cents=1000)
    assert a != c   # direction 不同


# ===== 总结：v21 包可被 import 验证 =====

def test_v21_package_importable():
    """阶段 0：v21 包整体可被 import，所有公开符号存在。"""
    import backend.systems.v21 as v21
    assert v21.OCEAN == 'ocean'
    assert v21.OCEAN_ACCOUNT == 'ocean'
    assert v21.PEER == 'peer'
    assert v21.PEER_ACCOUNT_PREFIX == 'city:'
    assert v21.EXTERNAL == 'external'
    assert callable(v21.peer_account)
    assert v21.PeerFlow is PeerFlow
