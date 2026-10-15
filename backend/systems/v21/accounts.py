"""V2.1 阶段 3：完整账户体系。

【2026-10-15】Bipole 城市聚合经济系统理论说明书 V2.1 §1.5 完整账户体系。

V1.0 时期简化的 6 钱包（firms/households/government/church/landlords/external）
已封存到 `backend/systems_v1_reference/`。V2.1 重构为完整多钱包体系：

【完整账户清单】（V2.1 §1.5）
- 政府：GOV 单一钱包
- 六非公务员行业独立经营现金：firms:AGR/IND/SRV/CHU/BNK/MAG
- 七行业居民储蓄（按行业归属）：cohorts:H1..H7（AGR/IND/SRV/CHU/BNK/MAG/GOV）
- h=0 群体未明确归属居民储蓄：cohorts:H0
- PROP 物业通道（临时，月末清零，不创造房东人口）
- CHURCH 教会非营利储备（与 CHU 行业经营现金共用一个物理钱包）
- BANK 银行经营现金（与 BNK 行业经营现金共用一个物理钱包）
- WORLD 海洋边界：ocean
- 邻城对手方：city:<id>

【关键约束】
- 行业经营现金与同名行业居民储蓄是**不同账户**（V2.1 §7.3）
- 负现金不能自动由居民储蓄填补（V2.1 §7.3）
- 折旧只影响会计利润，不再额外扣一次现金（V2.1 §7.3）
- 债务本金不计入当期利润（V2.1 §7.3）
- PROP 月末清零，不创造房东人口（V2.1 §1.5 + §7.5）
- CHU 行业经营现金与 CHURCH 教会钱包共用同一物理钱包（V2.1 §1.5）
- BANK 行业经营现金与 BNK 行业经营现金本身是同一物理钱包（V2.1 §1.5 + §8.1）
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

# 阶段 0 兼容常量（V2.1 阶段 3 完整版与阶段 0 抽出版共存）
OCEAN = 'ocean'                       # 海洋（账户 ID 同 OCEAN_ACCOUNT）
OCEAN_ACCOUNT = 'ocean'
PEER = 'peer'                         # 邻城（owner_kind 类别）
PEER_ACCOUNT_PREFIX = 'city:'
EXTERNAL = 'external'                 # V1.0 兼容：海洋 owner_kind 仍归 EXTERNAL 类别

# owner_kind 类别（V2.1 §1.5）
MUNICIPAL = 'municipal'         # 市政府
FIRM = 'firm'                   # 行业经营
HOUSEHOLD = 'household'         # 居民群体储蓄
CHURCH_KIND = 'church'          # 教会非营利（与 CHU 行业共用）
PROP_KIND = 'property_pass'     # 物业通道（月末清零）
OCEAN_KIND = EXTERNAL            # 海洋（与 V1.0 兼容）
PEER_KIND = PEER                # 邻城


def peer_account(city_id: str) -> str:
    """返回邻城账户的标准 ID。"""
    if not city_id:
        raise ValueError("peer_account: city_id 不能为空")
    return f"{PEER_ACCOUNT_PREFIX}{city_id}"


# ===== 阶段 0 抽出的纯函数（向后兼容 V2.1 阶段 0 测试）=====

def is_ocean(account_id: str, owner_kind: str, city_id: Optional[str]) -> bool:
    """精确判定：是否海洋边界账户。

    判定规则：
    1. account_id == OCEAN_ACCOUNT 且 city_id is None
    2. owner_kind == EXTERNAL 且 city_id is None（V1.0 兼容）
    邻城账户（is_peer=True）的 owner_kind=PEER 不会通过此判定。
    """
    if account_id == OCEAN_ACCOUNT and city_id is None:
        return True
    if owner_kind == EXTERNAL and city_id is None:
        return True
    return False


def is_peer(account_id: str, city_id: Optional[str]) -> bool:
    """精确判定：是否邻城对手方账户。"""
    return account_id.startswith(PEER_ACCOUNT_PREFIX) and city_id is not None


def peer_city_id_of(account_id: str) -> Optional[str]:
    """若 account_id 是 city:<id> 形式，返回 <id>；否则返回 None。"""
    if not account_id.startswith(PEER_ACCOUNT_PREFIX):
        return None
    return account_id[len(PEER_ACCOUNT_PREFIX):]


@dataclass
class Account:
    """单个账户（V2.1 §1.5）。"""
    account_id: str
    owner_kind: str
    owner_id: str
    city_id: Optional[str] = None           # 本地账户 = 本城 id；外部账户 = None
    cash_cents: int = 0                     # 现金（整数分）
    reserved_cents: int = 0                 # 预留（受限余额）
    parent_account_id: Optional[str] = None  # 父账户（如家庭-房东关系）
    currency: str = 'CR'
    notes: str = ''

    @property
    def is_external(self) -> bool:
        """非本地外部账户（海洋或邻城）；不计入本城守恒项。

        严格按 account_id 命名空间判定（不依赖 city_id，因为 city_id 可能未设置）。
        """
        if self.account_id == OCEAN_ACCOUNT:
            return True
        if self.account_id.startswith(PEER_ACCOUNT_PREFIX):
            return True
        return False

    @property
    def is_ocean(self) -> bool:
        """海洋边界账户：海平面免透支检查，可无限支付。

        判定规则（V2.1 §1.5）：
        1. account_id == OCEAN_ACCOUNT（无论 city_id）
        2. owner_kind == OCEAN_KIND 且 city_id is None（V1.0 兼容）
        """
        if self.account_id == OCEAN_ACCOUNT:
            return True
        if self.owner_kind == OCEAN_KIND and self.city_id is None:
            return True
        return False

    @property
    def is_peer(self) -> bool:
        """邻城对手方账户：受透支检查（仅能花本城已挣的钱）。

        按 account_id 命名空间判定（不依赖 city_id，因为 city_id 可能未设置）。
        """
        return self.account_id.startswith(PEER_ACCOUNT_PREFIX)

    @property
    def is_property_pass(self) -> bool:
        """PROP 物业通道：临时账户，月末清零。"""
        return self.owner_kind == PROP_KIND

    @property
    def available_cents(self) -> int:
        """可用钱 = cash - reserved；预留不得加回全城现金。"""
        return self.cash_cents - self.reserved_cents


class AccountBook:
    """账户集合（V2.1 §1.5）。"""
    LOCAL_KINDS = (MUNICIPAL, FIRM, HOUSEHOLD, CHURCH_KIND, PROP_KIND, PEER_KIND)

    def __init__(self, city_id: str, accounts: list[Account] = None):
        self.city_id = city_id
        self._accounts: dict[str, Account] = {}
        for a in (accounts or []):
            self.add(a)

    def add(self, account: Account) -> Account:
        """添加账户。"""
        if account.account_id in self._accounts:
            raise KeyError(f"账户重复：{account.account_id}")
        # 1. 海洋账户校验：account_id=OCEAN_ACCOUNT 或 owner_kind=OCEAN_KIND
        is_ocean_account = (account.account_id == OCEAN_ACCOUNT
                            or account.owner_kind == OCEAN_KIND)
        if is_ocean_account and account.city_id is not None:
            raise ValueError(f"海洋账户不能有 city_id：{account.account_id}")
        # 2. 邻城账户校验：account_id 以 city: 开头
        is_peer_account = account.account_id.startswith(PEER_ACCOUNT_PREFIX)
        if is_peer_account and account.city_id is None:
            raise ValueError(f"邻城账户必须指定 city_id：{account.account_id}")
        # 3. 主体类型校验：本地账户必须在 LOCAL_KINDS；外部账户（海洋/邻城）免检
        if not is_ocean_account and not is_peer_account and account.owner_kind not in self.LOCAL_KINDS:
            raise ValueError(f"未知主体类型：{account.owner_kind}")
        # 4. 本地账户默认 city_id
        if not is_ocean_account and not is_peer_account and account.city_id is None:
            account.city_id = self.city_id
        self._accounts[account.account_id] = account
        return account

    def open(self, account_id: str, owner_kind: str, owner_id: str,
             cash_cents: int = 0, reserved_cents: int = 0,
             parent_account_id: Optional[str] = None,
             city_id: Optional[str] = None,
             currency: str = 'CR', notes: str = '') -> Account:
        """开账户便捷方法。"""
        if cash_cents < 0:
            raise ValueError("期初现金不能为负")
        return self.add(Account(
            account_id=account_id, owner_kind=owner_kind, owner_id=owner_id,
            city_id=city_id, cash_cents=cash_cents, reserved_cents=reserved_cents,
            parent_account_id=parent_account_id, currency=currency, notes=notes,
        ))

    def __getitem__(self, account_id: str) -> Account:
        return self._accounts[account_id]

    def __contains__(self, account_id: str) -> bool:
        return account_id in self._accounts

    def __iter__(self):
        return iter(self._accounts.values())

    def snapshot(self) -> dict[str, int]:
        """期初余额快照（用于 begin_month）。"""
        return {a.account_id: a.cash_cents for a in self._accounts.values()}

    def local_leaves(self):
        """本地账户（account_id 不属于 OCEAN/PEER 命名空间；按命名空间判定）。"""
        return (a for a in self._accounts.values()
                if not a.is_external)

    def external_leaves(self):
        """外部账户（OCEAN_ACCOUNT + city: 命名空间）。"""
        return (a for a in self._accounts.values() if a.is_external)


# ===== 账户 ID 命名规范（V2.1 §1.5）=====

def firm_account_id(industry_id: str) -> str:
    """行业经营现金账户 ID：firms:<INDUSTRY_ID>。"""
    return f"firms:{industry_id}"


def cohort_account_id(cohort_id: int) -> str:
    """居民群体储蓄账户 ID：cohorts:H<cohort_id>。"""
    return f"cohorts:H{cohort_id}"


def church_account_id() -> str:
    """教会非营利储备账户 ID：church（与 CHU 行业经营现金共用）。"""
    return "church"


def government_account_id() -> str:
    """市政府账户 ID：government。"""
    return "government"


def property_pass_account_id() -> str:
    """PROP 物业通道账户 ID：property_pass（月末清零）。"""
    return "property_pass"


def landlords_account_id() -> str:
    """历史 landlords 账户 ID（V1.0 兼容映射，V2.1 改为 property_pass）。"""
    return "landlords"
