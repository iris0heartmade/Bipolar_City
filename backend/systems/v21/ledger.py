"""V2.1 阶段 3：完整账本（Ledger）类。

【2026-10-15】Bipole 城市聚合经济系统理论说明书 V2.1 §1.5 + §7.3 完整账本。

V1.0 时期简化的 `model_ledger.py` 已封存到 `backend/systems_v1_reference/`。
V2.1 重构为完整 Ledger：

【V2.1 §7.3 关键约束】
- 行业经营现金与同名行业居民储蓄是**不同账户**
- 负现金**不能**自动由居民储蓄填补
- 折旧只影响会计利润，**不再额外扣一次现金**
- 债务本金不计入当期利润
"""
from dataclasses import dataclass
from typing import Optional

from backend.core.errors import GameError
from backend.systems.v21.accounts import (
    Account, AccountBook, OCEAN_ACCOUNT, PEER_ACCOUNT_PREFIX, PEER_KIND,
    church_account_id, cohort_account_id, firm_account_id,
    government_account_id, peer_account, property_pass_account_id,
)


@dataclass(frozen=True)
class PeerFlow:
    """V2.1 阶段 0 抽出的城市间过账数据类（保留）。"""
    month: int
    direction: str              # 'out' 本城付出 / 'in' 本城收到
    peer_city_id: str
    category: str
    amount_cents: int


class Ledger:
    """V2.1 完整账本。

    持有 AccountBook + 期初快照 + 短期欠付 + 邻城过账记录。
    """

    def __init__(self, city_id: str, book: AccountBook, opening_snapshot: Optional[dict] = None):
        self.book = book
        self.city_id = city_id
        self.opening_snapshot: dict[str, int] = opening_snapshot or book.snapshot()
        self.month = 0
        self.transactions: list[tuple[str, str, int, str]] = []
        self.shortfall: dict[str, int] = {}           # 当月短付累计
        self.peer_flows: list[PeerFlow] = []          # 邻城过账记录

    def cash(self, account: str) -> int:
        return self.book[account].cash_cents

    def available(self, source: str, amount: int) -> int:
        """V2.1 §1.5 免检规则（仅 ocean 免检，邻城受透支检查）。

        与 V1.0 A-02 单点入口设计一致：只有 ocean 账户可无限支付。
        """
        if source not in self.book:
            raise GameError(f'账户不存在：{source}')
        if amount <= 0:
            return 0
        account = self.book[source]
        if account.is_ocean:
            return amount
        return min(amount, max(0, account.available_cents))

    def begin_month(self, month: int = None):
        """月初初始化：开账快照、清理短付与过账记录。"""
        self.month = self.month + 1 if month is None else month
        self.opening_snapshot = self.book.snapshot()
        self.transactions = []
        self.shortfall = {}
        # peer_flows 不清空，跨月累积

    def pay(self, source: str, target: str, amount: int, category: str) -> int:
        """付款。

        返回实际支付金额（受 available_cents 限制）。
        - 实际修改 source/target 的 cash_cents
        - 当 source 或 target 是 city:<id> 时自动登记 PeerFlow
        - 短付部分写入 self.shortfall
        - ocean 可扣成负数（V2.1 §1.5 海平面近似恒定；记账不强制约束）
        """
        if amount <= 0:
            return 0
        if source not in self.book or target not in self.book:
            raise GameError(f'账户不存在：{source} 或 {target}')
        if not isinstance(amount, int):
            raise GameError('交易必须使用整数分')

        available = self.available(source, amount)
        paid = min(amount, available)
        if paid:
            # 实际过账：source 扣、target 加
            self.book[source].cash_cents -= paid
            self.book[target].cash_cents += paid
            self.transactions.append((source, target, paid, category))
            self._record_peer_flow(source, target, paid)
        if paid < amount:
            key = f'{source}->{target}:{category}'
            self.shortfall[key] = self.shortfall.get(key, 0) + amount - paid
        return paid

    def _record_peer_flow(self, source: str, target: str, paid: int):
        """A-03：自动登记邻城过账。"""
        if source not in self.book or target not in self.book:
            return
        src = self.book[source]
        tgt = self.book[target]
        if not src.is_peer and tgt.is_peer:
            self.peer_flows.append(PeerFlow(
                month=self.month, direction='out',
                peer_city_id=tgt.account_id[len(PEER_ACCOUNT_PREFIX):],
                category=self.transactions[-1][3], amount_cents=paid,
            ))
        elif src.is_peer and not tgt.is_peer:
            self.peer_flows.append(PeerFlow(
                month=self.month, direction='in',
                peer_city_id=src.account_id[len(PEER_ACCOUNT_PREFIX):],
                category=self.transactions[-1][3], amount_cents=paid,
            ))

    def settle(self):
        """统一清算（占位）：实际过账由 Settlement.settle() 承担。

        本方法仅做内部一致性检查：所有 transactions 累加后账户余额不为负
        （对非 ocean 账户）。PROP 通道月末清零在 end_month() 中做。
        """
        for source, target, amount, _category in self.transactions:
            src = self.book[source]
            if not src.is_ocean and src.cash_cents < 0:
                raise GameError(f'账户 {source} 余额不足：{src.cash_cents}')

    def end_month(self):
        """月末清理：PROP 物业通道余额清零（V2.1 §1.5 + §7.5）。

        PROP 是同月内收入分配通道，月末清零，不创造房东人口。
        """
        prop_id = property_pass_account_id()
        if prop_id in self.book:
            self.book[prop_id].cash_cents = 0

    def ocean_net(self) -> int:
        """海洋账户现金变化（V2.1 §11 TEST-04 守恒）。"""
        opening = self.opening_snapshot.get(OCEAN_ACCOUNT, 0)
        return self.cash(OCEAN_ACCOUNT) - opening

    def local_total(self) -> int:
        """本地现金合计（不含 ocean/peer）。"""
        return sum(a.cash_cents for a in self.book.local_leaves())


# ===== 工厂：构建 V2.1 完整账户体系（V2.1 §14.12 期初资金）=====

def build_industrial_city_accounts(
    city_id: str = 'industrial_city',
    industry_opening_cents: Optional[dict[str, int]] = None,
    cohort_opening_cents: Optional[dict[int, int]] = None,
    government_opening_cents: int = 10_000_000,      # 100,000 CR
    church_opening_cents: int = 2_000_000,          # 20,000 CR
    ocean_opening_cents: int = 0,
) -> AccountBook:
    """构建工业新城完整账户体系（V2.1 §14.12）。

    默认期初：
    - GOV 100,000 CR
    - 教会现金 20,000 CR（与 CHU 行业经营现金共用钱包）
    - 八群体居民储蓄按 cohort_opening_cents（默认 0；外部用 split_initial_savings 分配）
    - 八行业经营现金按 industry_opening_cents（默认 0；外部按 V2.1 §14.12 配置）
    - 海洋 0
    - PROP 物业通道 0
    """
    from backend.systems.v21.industries import INDUSTRY_DEFINITIONS, IndustryId
    book = AccountBook(city_id=city_id)

    # 政府
    book.open(government_account_id(), 'municipal', 'municipal', cash_cents=government_opening_cents)

    # 海洋
    book.open(OCEAN_ACCOUNT, 'external', 'ocean', city_id=None, cash_cents=ocean_opening_cents)

    # 六非公务员行业独立经营现金（GOV 没有独立经营钱包，公务员工资由 GOV 真实支付）
    industry_defaults = {
        IndustryId.AGR.value: 7_000_000,    # 70,000 CR
        IndustryId.IND.value: 25_000_000,   # 250,000 CR
        IndustryId.SRV.value: 17_000_000,   # 170,000 CR
        IndustryId.BNK.value: 6_000_000,    # 60,000 CR
        IndustryId.MAG.value: 3_500_000,    # 35,000 CR
    }
    if industry_opening_cents:
        industry_defaults.update(industry_opening_cents)
    for iid, cash in industry_defaults.items():
        book.open(firm_account_id(iid), 'firm', iid, cash_cents=cash)

    # 教会非营利储备（与 CHU 行业经营现金共用一个物理钱包：church）
    # CHU 行业经营现金也用 church 账户
    book.open(church_account_id(), 'church', 'church', cash_cents=church_opening_cents)

    # 八群体居民储蓄
    if not cohort_opening_cents:
        cohort_opening_cents = {h: 0 for h in range(8)}
    for h, cash in cohort_opening_cents.items():
        book.open(cohort_account_id(h), 'household', f'H{h}', cash_cents=cash)

    # PROP 物业通道（月末清零）
    book.open(property_pass_account_id(), 'property_pass', 'landlords_aggregate', cash_cents=0)

    return book
