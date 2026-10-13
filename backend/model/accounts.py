"""账户与钱包。金额一律整数分；预留是受限余额，不是另一笔钱。

账户边界遵循 M02：房东是家庭的财产身份，用 parent_account_id 挂在家庭主钱包下，
不额外增加人口，也不与家庭镜像并存。合并现金只统计没有子账户的叶子钱包。
"""
from dataclasses import asdict, dataclass
from typing import Iterator, Optional

HOUSEHOLD = "household"
FIRM = "firm"
MUNICIPAL = "municipal"
CHURCH = "church"
LANDLORD = "landlord"
LENDER = "lender"
EXTERNAL = "external"

LOCAL_KINDS = (HOUSEHOLD, FIRM, MUNICIPAL, CHURCH, LANDLORD, LENDER)


@dataclass
class Account:
    """单个钱包。city_id 为 None 表示非本地外部账户，不计入本城守恒项。"""

    account_id: str
    owner_kind: str
    owner_id: str
    city_id: Optional[str] = None
    parent_account_id: Optional[str] = None
    cash_cents: int = 0
    reserved_cents: int = 0
    currency: str = "CR"

    @property
    def is_external(self) -> bool:
        return self.city_id is None

    @property
    def available_cents(self) -> int:
        """可用钱＝现金－预留；预留不得加回全城现金。"""
        return self.cash_cents - self.reserved_cents

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        return cls(**data)


class AccountBook:
    """账户集合。父子账户只计叶子，外部账户与本地账户分开。"""

    def __init__(self, accounts: Iterator[Account] = ()):
        self._accounts: dict[str, Account] = {}
        for account in accounts:
            self.add(account)

    def add(self, account: Account) -> Account:
        if account.account_id in self._accounts:
            raise KeyError(f"账户重复：{account.account_id}")
        if account.parent_account_id and account.parent_account_id not in self._accounts:
            raise KeyError(f"父账户不存在：{account.parent_account_id}")
        if account.owner_kind not in LOCAL_KINDS and not account.is_external:
            raise ValueError(f"未知主体类型：{account.owner_kind}")
        self._accounts[account.account_id] = account
        return account

    def open(self, account_id: str, owner_kind: str, owner_id: str,
             city_id: Optional[str], cash_cents: int = 0, parent_account_id: str = None,
             currency: str = "CR") -> Account:
        if cash_cents < 0:
            raise ValueError("期初现金不能为负")
        return self.add(Account(account_id=account_id, owner_kind=owner_kind, owner_id=owner_id,
                                city_id=city_id, parent_account_id=parent_account_id,
                                cash_cents=cash_cents, currency=currency))

    def __getitem__(self, account_id: str) -> Account:
        return self._accounts[account_id]

    def __contains__(self, account_id: str) -> bool:
        return account_id in self._accounts

    def __iter__(self):
        return iter(self._accounts.values())

    def leaves(self) -> Iterator[Account]:
        """全部账户都是独立现金钱包。

        parent_account_id 只声明财产身份归属（房东属于家庭，不是独立人口），
        并不表示父账户替子账户持有资金，因此现金合计统计所有账户；
        若把父账户排除，家庭共有的现金就会凭空漏出合计。
        """
        return iter(self._accounts.values())

    def mirrored_pairs(self) -> list[tuple[str, str]]:
        """同一主体在父子账户上被重复记钱的诊断项，正常应为空。"""
        pairs = []
        for account in self._accounts.values():
            if account.parent_account_id and account.owner_id == self._accounts[account.parent_account_id].owner_id:
                pairs.append((account.parent_account_id, account.account_id))
        return pairs

    def local_leaves(self) -> Iterator[Account]:
        return (a for a in self.leaves() if not a.is_external)

    def external_leaves(self) -> Iterator[Account]:
        return (a for a in self.leaves() if a.is_external)

    def total_cash(self, currency: str = "CR") -> int:
        return sum(a.cash_cents for a in self.leaves() if a.currency == currency)

    def total_reserved(self, currency: str = "CR") -> int:
        return sum(a.reserved_cents for a in self.leaves() if a.currency == currency)

    def snapshot(self) -> dict[str, int]:
        """开账余额快照。月界必须在第一笔付款前保存。"""
        return {a.account_id: a.cash_cents for a in self._accounts.values()}

    def to_dict(self):
        return [a.to_dict() for a in self._accounts.values()]

    @classmethod
    def from_dict(cls, rows) -> "AccountBook":
        return cls(Account.from_dict(row) for row in rows)