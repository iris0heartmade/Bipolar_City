"""交易凭证。每笔钱都必须能找到付款方、收款方、类别和来源。

交易 ID 由 month + source_ref + payment_part 确定性生成，
同一义务被重复提交时不会产生第二次付款（M02 幂等要求）。
"""
from dataclasses import asdict, dataclass
from typing import Optional

# 交易类别。与 M02/M09 的报表科目一一对应，不允许自由字符串。
SALE = "sale"                      # 营业额（购买方支出，销售方收入）
WAGE = "wage"
PROCUREMENT = "procurement"
COMMERCIAL_RENT = "commercial_rent"
RESIDENTIAL_RENT = "residential_rent"
MAINTENANCE = "maintenance"
FEES = "fees"
INCOME_TAX = "income_tax"
PROPERTY_TAX = "property_tax"
PROFIT_TAX = "profit_tax"
DIVIDEND = "dividend"
CAPITAL = "capital"
RELIEF = "relief"
DONATION = "donation"
GRANT = "grant"
INTEREST = "interest"
PRINCIPAL = "principal"            # 融资现金流，不是收入也不是费用
EXPORT = "export"
IMPORT = "import"
TRANSFER = "transfer"              # 账户间划转，不改变全城现金

PENDING = "pending"
SETTLED = "settled"
FAILED = "failed"


def transaction_id(month: int, source_ref: str, payment_part: str = "main") -> str:
    return f"{month}:{source_ref}:{payment_part}"


@dataclass(frozen=True)
class Transaction:
    from_account: str
    to_account: str
    amount_cents: int
    category: str
    source_ref: str
    month: int
    payment_part: str = "main"
    tx_id: str = ""
    currency: str = "CR"
    commodity_id: Optional[str] = None
    qty: Optional[int] = None
    status: str = PENDING

    def __post_init__(self):
        if not self.tx_id:
            # frozen 数据类只能通过 object.__setattr__ 写入。
            object.__setattr__(self, "tx_id", transaction_id(self.month, self.source_ref, self.payment_part))

    def settled(self) -> "Transaction":
        from dataclasses import replace
        return replace(self, status=SETTLED)

    def failed(self) -> "Transaction":
        from dataclasses import replace
        return replace(self, status=FAILED)

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        return cls(**data)