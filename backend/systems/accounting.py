"""会计断言与守恒检查（M02）。

核心恒等式：各本地叶子钱包的现金变化合计，等于与非本地账户的实际净收付款。
外部边界账户（世界其余部分）是本地变化的镜像，因此两者互为相反数；
两者相加为零，说明全世界的现金既没有被凭空创造也没有被销毁。
"""
from dataclasses import dataclass, field
from typing import Optional

from backend.core.errors import GameError
from backend.model.accounts import AccountBook


@dataclass
class ConservationReport:
    city_cash_delta_cents: int = 0
    external_cash_delta_cents: int = 0
    world_cash_delta_cents: int = 0
    opening_world_cash_cents: int = 0
    closing_world_cash_cents: int = 0
    category_totals: dict[str, int] = field(default_factory=dict)
    violations: list[str] = field(default_factory=list)

    @property
    def is_balanced(self) -> bool:
        return not self.violations

    def raise_if_unbalanced(self) -> "ConservationReport":
        if self.violations:
            raise GameError("月度会计不闭合：" + "；".join(self.violations))
        return self


def city_cash_delta(book: AccountBook, opening: dict[str, int]) -> int:
    return sum(a.cash_cents - opening.get(a.account_id, 0) for a in book.local_leaves())


def external_cash_delta(book: AccountBook, opening: dict[str, int]) -> int:
    return sum(a.cash_cents - opening.get(a.account_id, 0) for a in book.external_leaves())


def world_cash(book: AccountBook, opening: Optional[dict[str, int]] = None) -> int:
    return sum(a.cash_cents for a in book.leaves())


def category_totals(journal) -> dict[str, int]:
    """按类别汇总已清交易。支出记负、收入记正。"""
    totals: dict[str, int] = {}
    for tx in journal:
        totals[tx.category] = totals.get(tx.category, 0) - tx.amount_cents
    return totals


def check_conservation(book: AccountBook, opening: dict[str, int], journal=()) -> ConservationReport:
    report = ConservationReport(
        city_cash_delta_cents=city_cash_delta(book, opening),
        external_cash_delta_cents=external_cash_delta(book, opening),
        opening_world_cash_cents=sum(opening.values()),
        closing_world_cash_cents=sum(a.cash_cents for a in book.leaves()),
        category_totals=category_totals(journal),
    )
    report.world_cash_delta_cents = report.city_cash_delta_cents + report.external_cash_delta_cents

    if report.city_cash_delta_cents + report.external_cash_delta_cents != 0:
        report.violations.append(
            f"本地变化 {report.city_cash_delta_cents} 与外部变化 {report.external_cash_delta_cents} 不互为相反数")
    if report.world_cash_delta_cents != 0:
        report.violations.append(f"全城现金凭空变化 {report.world_cash_delta_cents} 分")

    for account in book:
        if account.is_external:
            continue
        if account.cash_cents < 0:
            report.violations.append(f"本地账户 {account.account_id} 出现未授权负余额 {account.cash_cents}")
        if account.available_cents < 0:
            report.violations.append(f"本地账户 {account.account_id} 侵占预留，可用余额 {account.available_cents}")
        if account.reserved_cents < 0:
            report.violations.append(f"账户 {account.account_id} 预留为负")
    for parent, child in book.mirrored_pairs():
        report.violations.append(f"账户 {child} 与父账户 {parent} 属于同一主体，现金可能被重复记账")
    return report


def assert_conserved(book: AccountBook, opening: dict[str, int], journal=()) -> ConservationReport:
    return check_conservation(book, opening, journal).raise_if_unbalanced()


def double_counted_sources(journal) -> dict[str, list[str]]:
    """同一 source_ref 被不同 payment_part 之外的重复全额支付即视为重复计账。

    允许同一义务分多次支付（里程碑款），但同一 payment_part 只能出现一次。
    """
    seen: dict[str, list[str]] = {}
    for tx in journal:
        seen.setdefault(tx.tx_id, []).append(tx.tx_id)
    return {key: rows for key, rows in seen.items() if len(rows) > 1}