"""Canonical transactions and review results, independent of Streamlit."""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal


@dataclass(frozen=True)
class Transaction:
    date: date
    brokerage: str
    exchange: str
    ticker: str
    action: str
    quantity: Decimal
    price: Decimal
    currency: str
    fees: Decimal
    transaction_id: str | None
    source: str
    source_row: int
    row_key: str
    raw: dict[str, str] = field(compare=False, repr=False)

    @property
    def inventory_key(self) -> tuple[str, str, str, str]:
        return self.brokerage, self.exchange, self.ticker, self.currency

    @property
    def fingerprint(self) -> tuple:
        return (self.date, *self.inventory_key, self.action, self.quantity, self.price, self.fees)


@dataclass(frozen=True)
class Issue:
    source: str
    source_row: int
    row_key: str
    reason: str
    raw: dict[str, str]

    @classmethod
    def for_transaction(cls, tx: Transaction, reason: str) -> "Issue":
        return cls(tx.source, tx.source_row, tx.row_key, reason, tx.raw)


@dataclass
class ValidationResult:
    accepted: list[Transaction] = field(default_factory=list)
    rejected: list[Issue] = field(default_factory=list)
    duplicates: list[Issue] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
