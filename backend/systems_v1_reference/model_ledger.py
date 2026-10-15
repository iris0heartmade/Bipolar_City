"""细分钱包复用统一清算器；实际到账与义务缺口分开。

【A-01 改造，2026-10-15】账户结构：
- 'external' 拆为 'ocean'（海平面边界）与 'city:<id>'（邻城对手方）
- 海洋免透支检查，邻城必须受透支检查（否则跨城交易会凭空造钱）
- EXTERNAL 兼容常量保留（"world" 账户仍归 EXTERNAL 类别）
- `Ledger.available(source, amount)` 是 A-02 收敛后的单点入口
- `peer_flows` 是 A-03 城市间交易的过账记录，A-03 完整功能下一轮交付

【A-02 防漏，2026-10-15】免检余额规则只对 ocean 生效：
- model_ledger.py:80（pay 内联）已删除，改为调用 self.ledger.available()
- model_sim.py:152（distribute 内联）已删除，改为调用 self.ledger.available()
- 这两处是 A-02 散落风险点；A-01 完成后必须同步收敛，否则会留下"邻城也免检"的漏洞
"""
from dataclasses import dataclass
from typing import Optional

from backend.core.errors import GameError
from backend.core.settlement import Settlement
from backend.model.accounts import (
    Account, AccountBook, EXTERNAL, OCEAN_ACCOUNT, peer_account,
)


def split(total, weights):
    """最大余数法，整数分/人数合计精确。"""
    weights = [max(0, float(w)) for w in weights]
    if not weights:
        return []
    if not sum(weights):
        weights = [1] * len(weights)
    raw = [int(total) * w / sum(weights) for w in weights]
    result = [int(x) for x in raw]
    for i in sorted(range(len(weights)), key=lambda i: raw[i] - result[i], reverse=True)[:int(total) - sum(result)]:
        result[i] += 1
    return result


def split_bounded(total, weights, capacities):
    result = [0] * len(weights)
    remaining = int(total)
    while remaining:
        active = [w if result[i] < capacities[i] else 0 for i, w in enumerate(weights)]
        if not any(result[i] < capacities[i] for i in range(len(weights))):
            raise GameError('人口分配超出群体容量')
        allocations = split(remaining, active)
        progress = 0
        for i, count in enumerate(allocations):
            accepted = min(count, capacities[i] - result[i])
            result[i] += accepted
            progress += accepted
        if not progress:
            raise GameError('无法分配人口身份')
        remaining -= progress
    return result


@dataclass(frozen=True)
class PeerFlow:
    """A-03 城市间交易的过账记录。本城付出记 'out'，本城收到记 'in'。

    双向记录必须严格对称：本城向 city:B 付 1,000 CR，B 城账本应有镜像的
    PeerFlow(direction='in', peer_city_id='A', amount_cents=1000) 才能通过
    `Ledger.reconcile_peers()` 校验。"""
    month: int
    direction: str              # 'out' 表示本城付出，'in' 表示本城收到
    peer_city_id: str
    category: str               # export / import / capital_flight / migration_out ...
    amount_cents: int


class Ledger:
    """多账户月结账本。A-01 改造后账户组成：
    - firms / households / government / church：本地
    - ocean：海洋边界，免透支检查，海平面近似恒定
    - city:<id>：邻城对手方，受透支检查（'city:<id>'.is_peer=True）
    """

    # A-01: 'external' → 'ocean'，与之前 0.7.0 的 'external' 完全等价（同一物理账户）。
    # 老测试用 EXTERNAL 字符串、'world' 账户 ID 等仍可工作：kinds 字典把 'ocean' 映射到 EXTERNAL 类别。
    ACCOUNTS = ('firms', 'households', 'government', 'church', OCEAN_ACCOUNT)

    def __init__(self, opening=None):
        values = dict.fromkeys(self.ACCOUNTS, 0) if opening is None else dict(opening)
        values.setdefault(OCEAN_ACCOUNT, 0)
        # kinds 把本地账户映射到 LOCAL_KINDS；ocean 账户的 owner_kind 仍是 EXTERNAL 类别，
        # 保证 is_external=True 的语义连续性。
        kinds = {
            'firms': 'firm',
            'households': 'household',
            'government': 'municipal',
            'church': 'church',
            'landlords': 'landlord',
            OCEAN_ACCOUNT: EXTERNAL,   # 海洋账户的 owner_kind 仍是 'external'
        }
        self.book = AccountBook(
            Account(k, kinds[k.split(':')[0]], k,
                    None if k == OCEAN_ACCOUNT or k == EXTERNAL else 'lab', cash_cents=v)
            for k, v in values.items()
        )
        self.month = 0
        self._peer_opening: dict[str, int] = {}   # A-01 邻城期初余额；本轮预留给 A-03
        self._peer_balances: dict[str, int] = {}  # A-01 邻城当前余额（按 city_id 索引）
        self.peer_flows: list[PeerFlow] = []     # A-03 城市间交易过账记录
        self.begin_month()

    @property
    def balances(self):
        values = self.book.snapshot()
        for group in ('firms', 'households'):
            children = [v for k, v in values.items() if k.startswith(group + ':')]
            if children:
                values[group] = sum(children) + (values.get('landlords', 0) if group == 'households' else 0)
        return values

    def cash(self, account):
        return self.book[account].cash_cents

    def available(self, source: str, amount: int) -> int:
        """A-02 收敛后的单点入口：仅 ocean 免透支检查，其余受 available_cents 上限。

        返回 source 账户可支付的金额。这是 A-01 改造后所有付款方能力的唯一判定点。
        之前散落在 model_ledger.py:80（pay 内联）和 model_sim.py:152（distribute 内联）
        的两处规则现统一收敛到此处。
        """
        if source not in self.book:
            raise GameError(f'账户不存在：{source}')
        if amount <= 0:
            return 0
        account = self.book[source]
        # A-01 关键判定：只有海洋（is_ocean=True）才免检。邻城必须受 available_cents 上限。
        if account.is_ocean:
            return amount
        return min(amount, max(0, account.available_cents))

    def begin_month(self, month=None):
        self.month = self.month + 1 if month is None else month
        # A-01: 海洋期初余额（替代原 opening_external）
        self.opening_ocean = self.book[OCEAN_ACCOUNT].cash_cents
        # A-01: 邻城期初余额快照
        self._peer_opening = {city_id: bal for city_id, bal in self._peer_balances.items()}
        self.transactions = []
        self.shortfall = {}
        self.settlement = Settlement(self.book, self.month)

    def pay(self, source, target, amount, category, commodity_id=None, qty=None):
        """付款。规则：
        1. amount 必须正整数分
        2. 通过 self.available(source, amount) 判定可支付能力（A-02 单点）
        3. 短付部分记入 self.shortfall，不透支、不凭空造钱
        4. 当 target 是 city:<id> 时，自动登记 PeerFlow（direction='out'）；A-03 完整功能下一轮
        """
        if amount <= 0:
            return 0
        if source not in self.book or target not in self.book:
            raise GameError(f'账户不存在：{source} 或 {target}')
        if not isinstance(amount, int):
            raise GameError('交易必须使用整数分')
        # A-02：单点判定。ocean 免检，其余受 available_cents 上限。
        available = self.available(source, amount)
        paid = min(amount, available)
        if paid:
            ref = f'lab:{len(self.transactions)}:{category}:{source}:{target}'
            self.settlement.enqueue(Transaction(source, target, paid, category, ref, self.month,
                                                commodity_id=commodity_id, qty=qty * paid / amount if qty is not None else None))
            self.settlement.settle()
            self.transactions.append((source, target, paid, category))
            # A-03 邻城对手方过账记录
            self._record_peer_flow(source, target, paid, category)
        if paid < amount:
            key = f'{source}->{target}:{category}'
            self.shortfall[key] = self.shortfall.get(key, 0) + amount - paid
        return paid

    def _record_peer_flow(self, source: str, target: str, paid: int, category: str) -> None:
        """A-03 框架：登记邻城过账记录。

        判定规则（按 is_peer 精确判定）：
        - source 不是 peer、target 是 peer：direction='out'（本城付给邻城）
        - source 是 peer、target 不是 peer：direction='in'（邻城付给本城）
        - 双方都是 local、双方都是 peer、或涉及 ocean：均不记录
        """
        if source not in self.book or target not in self.book:
            return
        src_acct = self.book[source]
        tgt_acct = self.book[target]
        if not src_acct.is_peer and tgt_acct.is_peer:
            self.peer_flows.append(PeerFlow(
                month=self.month, direction='out',
                peer_city_id=tgt_acct.peer_city_id, category=category, amount_cents=paid,
            ))
        elif src_acct.is_peer and not tgt_acct.is_peer:
            self.peer_flows.append(PeerFlow(
                month=self.month, direction='in',
                peer_city_id=src_acct.peer_city_id, category=category, amount_cents=paid,
            ))

    def by_category(self):
        result = {}
        for _, _, value, category in self.transactions:
            result[category] = result.get(category, 0) + value
        return result

    @property
    def local_total(self):
        return sum(a.cash_cents for a in self.book.local_leaves())

    @property
    def ocean_net(self):
        """A-01 重命名：原 external_net 的语义。海洋账户现金变化。"""
        return self.cash(OCEAN_ACCOUNT) - self.opening_ocean

    @property
    def external_net(self):
        """向后兼容：保留 external_net 别名，等价于 ocean_net。"""
        return self.ocean_net

    # ===== A-03 邻城账户管理（框架已就绪；完整多城下一轮交付）=====

    def open_peer_account(self, city_id: str, opening_cents: int = 0) -> None:
        """A-03 框架：在本城账本里为邻城开一个 city:<id> 账户，受透支检查。

        单城阶段不需要调用；调用后该账户的余额变化会通过 peer_balance 跟踪。
        开账时立即把 opening_cents 同步到 _peer_opening，使 peer_balance 立即返回 0。
        """
        if not city_id:
            raise ValueError('open_peer_account: city_id 不能为空')
        account_id = peer_account(city_id)
        if account_id in self.book:
            return  # 已存在则幂等
        self.book.open(account_id, 'peer', city_id, city_id, cash_cents=opening_cents)
        self._peer_balances[city_id] = opening_cents
        self._peer_opening[city_id] = opening_cents  # 立即建立基线，使 peer_balance=0

    def peer_balance(self, city_id: str) -> int:
        """A-03：邻城在本城账本中的净额（正=本城欠对方）。"""
        if city_id not in self._peer_balances:
            return 0
        return self._peer_balances[city_id] - self._peer_opening.get(city_id, 0)

    def reconcile_peers(self, other_ledgers: 'dict[str, Ledger]') -> None:
        """A-03：对账本城 peer_flows 与对方账本的镜像记录。

        单城阶段：other_ledgers 为空，方法无操作。
        多城阶段（本轮不实现）：遍历 other_ledgers，逐 city_id 校验本城的 outflow
        等于对方的 inflow、inflow 等于对方的 outflow；若不匹配则 raise GameError。

        关键约束：M_cd + M_dc = 0（符号反对称）。
        """
        # 单城阶段实现占位
        if not other_ledgers:
            return
        # 多城阶段校验（占位）：每个 city_id 的双向 peer_flows 必须镜像
        for city_id, other in other_ledgers.items():
            my_out = sum(p.amount_cents for p in self.peer_flows
                         if p.peer_city_id == city_id and p.direction == 'out')
            my_in = sum(p.amount_cents for p in self.peer_flows
                        if p.peer_city_id == city_id and p.direction == 'in')
            their_out = sum(p.amount_cents for p in other.peer_flows
                            if p.peer_city_id == getattr(self, 'city_id', 'self') and p.direction == 'out')
            their_in = sum(p.amount_cents for p in other.peer_flows
                            if p.peer_city_id == getattr(self, 'city_id', 'self') and p.direction == 'in')
            # 严格镜像：我的 out 必须是对方的 in
            if my_out != their_in:
                raise GameError(
                    f'城际对账失败：{getattr(self, "city_id", "?")}→{city_id} '
                    f'本城 outflow={my_out} ≠ 对方 inflow={their_in}')
            if my_in != their_out:
                raise GameError(
                    f'城际对账失败：{city_id}→{getattr(self, "city_id", "?")} '
                    f'本城 inflow={my_in} ≠ 对方 outflow={their_out}')


# 在 model_ledger 末尾补上 Transaction 的导入（被 pay 调用）
from backend.model.transactions import Transaction  # noqa: E402
