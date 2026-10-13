"""交易队列与统一清算入口（M02）。

清算规则：
1. 内部转账同时减付款方、加收款方；外部账户是"世界其余部分"的镜像，允许为负。
2. 同一 month + source_ref + payment_part 只清算一次，重复提交不产生第二次付款。
3. 付款不足导致本地账户负现金、或侵占已预留资金时整批拒绝，不允许无限透支。
4. 先算全量 delta，全部通过才落账，因此清算要么整体成功要么完全不改变世界。
"""
from typing import Iterable, Optional

from backend.core.errors import GameError
from backend.model.accounts import AccountBook
from backend.model.transactions import PENDING, Transaction


class InsufficientFunds(GameError):
    """付款方余额或可用余额不足；不改变任何账户。"""


class Settlement:
    """一个月内的交易收集与清算。外部边界账户与本地账户使用同一本账。"""

    def __init__(self, book: AccountBook, month: int, opening: Optional[dict[str, int]] = None,
                 settled: Iterable[Transaction] = ()):
        self.book = book
        self.month = month
        # 月初开账余额必须在本月第一笔付款前保存。
        self.opening = dict(opening) if opening is not None else book.snapshot()
        self._queue: list[Transaction] = []
        self._seen: dict[str, Transaction] = {}
        self._reservations: dict[str, dict] = {}
        self.journal: list[Transaction] = []
        # 读档时载入已结算流水，同一义务不会因为重开服务再次付款。
        for tx in settled:
            self.journal.append(tx)
            self._seen[tx.tx_id] = tx

    # --- 收集 ---------------------------------------------------------

    def enqueue(self, tx: Transaction) -> Transaction:
        if tx.month != self.month:
            raise GameError(f"交易月份 {tx.month} 不属于本月 {self.month}")
        if tx.amount_cents <= 0:
            raise GameError(f"交易金额必须为正：{tx.amount_cents}")
        for account_id in (tx.from_account, tx.to_account):
            if account_id not in self.book:
                raise GameError(f"交易引用了不存在的账户：{account_id}")
        if self.book[tx.from_account].currency != tx.currency:
            raise GameError("收付双方币种不一致")

        existing = self._seen.get(tx.tx_id)
        if existing is not None:
            if existing.amount_cents != tx.amount_cents:
                raise GameError(f"同一义务重复提交且金额不一致：{tx.source_ref}")
            return existing          # 幂等：不重复入队，也不重复付款
        self._seen[tx.tx_id] = tx
        self._queue.append(tx)
        return tx

    @property
    def pending(self) -> list[Transaction]:
        return list(self._queue)

    def discard(self) -> None:
        """放弃本批待清算交易，账户余额保持不变。"""
        self._queue.clear()

    # --- 预留 ---------------------------------------------------------

    def reserve(self, account_id: str, amount_cents: int, source_ref: str = "") -> int:
        """预留只是受限余额，不额外记一份现金；可用钱因此减少。

        预留按 source_ref 归集，实际支付同一 source_ref 的交易时自动核销，
        部分履约只需释放剩余部分。
        """
        if amount_cents <= 0:
            raise GameError("预留金额必须为正")
        account = self.book[account_id]
        if account.available_cents < amount_cents:
            raise InsufficientFunds(f"账户 {account_id} 可用余额不足以预留 {amount_cents}")
        account.reserved_cents += amount_cents
        slot = self._reservations.setdefault(source_ref, {"account_id": account_id, "remaining": 0})
        if slot["account_id"] != account_id:
            raise GameError(f"预留 {source_ref} 已归属于其他账户")
        slot["remaining"] += amount_cents
        return account.reserved_cents

    def release(self, account_id: str, amount_cents: int, source_ref: str = "") -> int:
        """释放预留；已经花掉的部分不能凭空退成可用余额。"""
        account = self.book[account_id]
        if amount_cents <= 0 or account.reserved_cents < amount_cents:
            raise GameError("释放金额超过已预留额度")
        slot = self._reservations.get(source_ref)
        if slot is not None and slot["account_id"] == account_id:
            slot["remaining"] = max(0, slot["remaining"] - amount_cents)
        account.reserved_cents -= amount_cents
        return account.reserved_cents

    # --- 清算 ---------------------------------------------------------

    def _plan(self) -> tuple[dict[str, int], dict[str, int]]:
        """算出全量现金变化与预留核销，不修改任何状态。"""
        cash_delta: dict[str, int] = {}
        reserved_delta: dict[str, int] = {}
        remaining = {ref: slot["remaining"] for ref, slot in self._reservations.items()}
        for tx in self._queue:
            cash_delta[tx.from_account] = cash_delta.get(tx.from_account, 0) - tx.amount_cents
            cash_delta[tx.to_account] = cash_delta.get(tx.to_account, 0) + tx.amount_cents
            left = remaining.get(tx.source_ref, 0)
            slot = self._reservations.get(tx.source_ref)
            if left > 0 and slot and slot["account_id"] == tx.from_account:
                used = min(left, tx.amount_cents)
                reserved_delta[tx.from_account] = reserved_delta.get(tx.from_account, 0) + used
                remaining[tx.source_ref] = left - used
        return cash_delta, reserved_delta

    def settle(self) -> list[Transaction]:
        """一次清算全部待处理交易；任一付款方不足则整批不落账。"""
        cash_delta, reserved_delta = self._plan()
        for account_id, delta in cash_delta.items():
            account = self.book[account_id]
            if account.is_external:
                continue      # 外部边界账户是镜像，不做本地不透支检查
            after = account.cash_cents + delta
            reserved_after = account.reserved_cents - reserved_delta.get(account_id, 0)
            if after < 0:
                raise InsufficientFunds(
                    f"账户 {account_id} 余额不足：现有 {account.cash_cents} 分，需支付 {abs(min(delta, 0))} 分")
            if after - reserved_after < 0:
                raise InsufficientFunds(
                    f"账户 {account_id} 的付款会侵占 {account.reserved_cents} 分预留")

        for account_id, delta in cash_delta.items():
            self.book[account_id].cash_cents += delta
        for account_id, used in reserved_delta.items():
            self.book[account_id].reserved_cents -= used
        for tx in self._queue:
            slot = self._reservations.get(tx.source_ref)
            if slot and slot["account_id"] == tx.from_account:
                slot["remaining"] = max(0, slot["remaining"] - tx.amount_cents)

        settled = [tx.settled() for tx in self._queue]
        self.journal.extend(settled)
        self._queue.clear()
        return settled

    # --- 诊断 ---------------------------------------------------------

    def journal_by_category(self) -> dict[str, int]:
        """按类别汇总本月已清交易的付款方金额，支出记负。

        这是"付款方视角"的支出额，不是类别净流量：企业间互购会记 -86000，
        但因为收付双方是同一个账户，现金净影响为 0。
        现金守恒请用 systems.accounting 的 check_conservation。
        """
        totals: dict[str, int] = {}
        for tx in self.journal:
            totals[tx.category] = totals.get(tx.category, 0) - tx.amount_cents
        return totals

    def trace(self, source_ref: str) -> list[Transaction]:
        return [tx for tx in self.journal if tx.source_ref == source_ref]

    def pending_count(self) -> int:
        return len(self._queue)

    def has_pending(self) -> bool:
        return bool(self._queue)


__all__ = ["Settlement", "InsufficientFunds", "PENDING", "Transaction"]