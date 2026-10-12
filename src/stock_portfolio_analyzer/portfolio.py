"""Account-first weighted-average cost basis and optional price valuation.

Only pass an accepted, complete transaction history. Duplicate decisions belong
to import validation: this engine processes every supplied transaction exactly
once and raises on invalid input rather than returning a partial portfolio.
Prices are caller-supplied numeric inputs, never an assertion of live quotes.
"""

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import (
    Context, Decimal, DecimalException, DivisionByZero, InvalidOperation,
    Overflow, ROUND_HALF_EVEN, Underflow, localcontext,
)
from types import MappingProxyType
from typing import Mapping, Sequence

from .models import Transaction

ZERO = Decimal(0)
# Explicit settings isolate calculations from both caller context and its traps.
_CONTEXT = Context(prec=50, rounding=ROUND_HALF_EVEN, Emin=-999999,
                   Emax=999999, capitals=1, clamp=0, flags=[],
                   traps=[InvalidOperation, DivisionByZero, Overflow, Underflow])


class CalculationError(ValueError):
    """A history or supplied price cannot be used for a reliable calculation."""


@dataclass(frozen=True, order=True)
class SecurityKey:
    exchange: str
    ticker: str
    currency: str


@dataclass(frozen=True)
class Position:
    """Amounts in one currency; purchases include fees, sales are net of fees."""

    security: SecurityKey
    quantity: Decimal
    cost_basis: Decimal
    realized_gain: Decimal
    purchase_amount: Decimal
    sales_proceeds: Decimal
    price: Decimal | None

    @property
    def average_cost(self) -> Decimal | None:
        with localcontext(_CONTEXT):
            return self.cost_basis / self.quantity if self.quantity else None

    @property
    def market_value(self) -> Decimal | None:
        if not self.quantity:
            return ZERO
        with localcontext(_CONTEXT):
            return None if self.price is None else self.quantity * self.price

    @property
    def unrealized_gain(self) -> Decimal | None:
        value = self.market_value
        with localcontext(_CONTEXT):
            return None if value is None else value - self.cost_basis

    @property
    def total_gain(self) -> Decimal | None:
        unrealized = self.unrealized_gain
        with localcontext(_CONTEXT):
            return None if unrealized is None else self.realized_gain + unrealized

    @property
    def return_pct(self) -> Decimal | None:
        """Unrealized gain / remaining basis × 100, not annualized performance."""
        unrealized = self.unrealized_gain
        if not self.cost_basis or unrealized is None:
            return None
        with localcontext(_CONTEXT):
            return unrealized / self.cost_basis * 100


@dataclass(frozen=True)
class AccountPosition(Position):
    brokerage: str


@dataclass(frozen=True)
class ConsolidatedPosition(Position):
    accounts: tuple[AccountPosition, ...]


@dataclass(frozen=True)
class SaleResult:
    """Per-sale audit record in chronological (stable same-date) order."""

    date: date
    brokerage: str
    security: SecurityKey
    quantity: Decimal
    net_proceeds: Decimal
    cost_basis_removed: Decimal
    realized_gain: Decimal
    source: str
    source_row: int
    row_key: str
    transaction_id: str | None


@dataclass(frozen=True)
class CurrencyTotals:
    currency: str
    cost_basis: Decimal
    realized_gain: Decimal
    purchase_amount: Decimal
    sales_proceeds: Decimal
    known_market_value: Decimal
    missing_prices: tuple[SecurityKey, ...]

    @property
    def market_value(self) -> Decimal | None:
        return None if self.missing_prices else self.known_market_value

    @property
    def unrealized_gain(self) -> Decimal | None:
        value = self.market_value
        with localcontext(_CONTEXT):
            return None if value is None else value - self.cost_basis

    @property
    def total_gain(self) -> Decimal | None:
        unrealized = self.unrealized_gain
        with localcontext(_CONTEXT):
            return None if unrealized is None else self.realized_gain + unrealized


@dataclass(frozen=True)
class Portfolio:
    accounts: tuple[AccountPosition, ...]
    securities: tuple[ConsolidatedPosition, ...]
    sales: tuple[SaleResult, ...]
    totals: Mapping[str, CurrencyTotals]

    @property
    def holdings(self) -> tuple[AccountPosition, ...]:
        return tuple(account for account in self.accounts if account.quantity > 0)


@dataclass
class _Ledger:
    quantity: Decimal = ZERO
    cost_basis: Decimal = ZERO
    realized_gain: Decimal = ZERO
    purchase_amount: Decimal = ZERO
    sales_proceeds: Decimal = ZERO


def _check_decimal(value: Decimal, label: str, *, zero_allowed: bool = False) -> None:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise CalculationError(f"{label} must be a finite Decimal; construct it from a string.")
    if value < 0 or (not zero_allowed and value == 0):
        raise CalculationError(f"{label} must be {'nonnegative' if zero_allowed else 'positive'}.")
    # Match the import contract and keep arithmetic within a predictable range.
    if value >= Decimal("1e12") or value.as_tuple().exponent < -8:
        raise CalculationError(f"{label} supports up to 12 integer and 8 decimal digits.")


def _check_security(security: SecurityKey) -> None:
    if not isinstance(security, SecurityKey):
        raise CalculationError("Price keys must be SecurityKey(exchange, ticker, currency).")
    if security.exchange not in ("NASDAQ", "NYSE", "NSE"):
        raise CalculationError("Unsupported exchange; expected NASDAQ, NYSE, or NSE.")
    if security.currency not in ("USD", "INR"):
        raise CalculationError("Unsupported currency; expected USD or INR.")
    if not isinstance(security.ticker, str) or not re.fullmatch(r"[A-Z0-9][A-Z0-9.&_-]{0,29}", security.ticker):
        raise CalculationError("Ticker must be a normalized nonempty exchange-local identifier.")


def _check_transaction(tx: Transaction) -> None:
    if not isinstance(tx, Transaction):
        raise CalculationError("Expected normalized Transaction objects.")
    location = f"{tx.source}, record {tx.source_row}"
    if type(tx.date) is not date:
        raise CalculationError(f"{location}: transaction date must be a date without a time.")
    if not isinstance(tx.brokerage, str) or not tx.brokerage or tx.brokerage != tx.brokerage.strip():
        raise CalculationError(f"{location}: brokerage/account must be nonempty and trimmed.")
    try:
        _check_security(SecurityKey(tx.exchange, tx.ticker, tx.currency))
        if tx.action not in ("BUY", "SELL"):
            raise CalculationError("Unsupported action; expected BUY or SELL.")
        _check_decimal(tx.quantity, "quantity")
        _check_decimal(tx.price, "price")
        _check_decimal(tx.fees, "fees", zero_allowed=True)
    except CalculationError as exc:
        raise CalculationError(f"{location}: {exc}") from exc


def calculate_portfolio(transactions: Sequence[Transaction], *,
                        prices: Mapping[SecurityKey, Decimal] | None = None) -> Portfolio:
    """Reconstruct complete accepted histories and optionally value open holdings.

    Prices must be finite, nonnegative Decimal values in the key's currency,
    below 1e12 and with at most eight decimal places, matching import limits.
    Omit absent quotes from the mapping. No quote retrieval, FX conversion,
    date filtering, duplicate removal, or fabricated missing prices occurs.
    """
    rows = tuple(transactions)
    supplied_prices = dict(prices) if prices is not None else {}
    for tx in rows:
        _check_transaction(tx)
    for security, price in supplied_prices.items():
        _check_security(security)
        _check_decimal(price, f"Price for {security}", zero_allowed=True)

    try:
        with localcontext(_CONTEXT):
            return _calculate(rows, supplied_prices)
    except DecimalException as exc:
        raise CalculationError(f"Decimal arithmetic failed: {exc}") from exc


def _calculate(rows: tuple[Transaction, ...], prices: dict[SecurityKey, Decimal]) -> Portfolio:
    ledgers: dict[tuple[str, str, str, str], _Ledger] = {}
    sales = []
    # sorted is stable: do not infer a BUY-before-SELL order on equal dates.
    for tx in sorted(rows, key=lambda item: item.date):
        ledger = ledgers.setdefault(tx.inventory_key, _Ledger())
        if tx.action == "BUY":
            paid = tx.quantity * tx.price + tx.fees
            ledger.quantity += tx.quantity
            ledger.cost_basis += paid
            ledger.purchase_amount += paid
            continue
        if tx.quantity > ledger.quantity:
            raise CalculationError(
                f"{tx.source}, record {tx.source_row}: sale of {tx.quantity} exceeds "
                f"available quantity {ledger.quantity} for {tx.inventory_key}. "
                "Check for incomplete history."
            )
        # Multiplying before dividing avoids premature average-cost rounding.
        removed = (ledger.cost_basis if tx.quantity == ledger.quantity else
                   ledger.cost_basis * tx.quantity / ledger.quantity)
        proceeds = tx.quantity * tx.price - tx.fees
        realized = proceeds - removed
        ledger.quantity -= tx.quantity
        ledger.cost_basis -= removed
        ledger.sales_proceeds += proceeds
        ledger.realized_gain += realized
        sales.append(SaleResult(
            tx.date, tx.brokerage, SecurityKey(tx.exchange, tx.ticker, tx.currency),
            tx.quantity, proceeds, removed, realized, tx.source, tx.source_row,
            tx.row_key, tx.transaction_id,
        ))

    accounts = []
    for (brokerage, exchange, ticker, currency), ledger in sorted(ledgers.items()):
        security = SecurityKey(exchange, ticker, currency)
        accounts.append(AccountPosition(
            security, ledger.quantity, ledger.cost_basis, ledger.realized_gain,
            ledger.purchase_amount, ledger.sales_proceeds, prices.get(security), brokerage,
        ))

    by_security: dict[SecurityKey, list[AccountPosition]] = defaultdict(list)
    by_currency: dict[str, list[AccountPosition]] = defaultdict(list)
    for account in accounts:
        by_security[account.security].append(account)
        by_currency[account.security.currency].append(account)
    securities = tuple(ConsolidatedPosition(
        security=security,
        quantity=sum((account.quantity for account in members), ZERO),
        cost_basis=sum((account.cost_basis for account in members), ZERO),
        realized_gain=sum((account.realized_gain for account in members), ZERO),
        purchase_amount=sum((account.purchase_amount for account in members), ZERO),
        sales_proceeds=sum((account.sales_proceeds for account in members), ZERO),
        price=prices.get(security), accounts=tuple(members),
    ) for security, members in sorted(by_security.items()))
    totals = {}
    for currency, members in sorted(by_currency.items()):
        totals[currency] = CurrencyTotals(
            currency=currency,
            cost_basis=sum((account.cost_basis for account in members), ZERO),
            realized_gain=sum((account.realized_gain for account in members), ZERO),
            purchase_amount=sum((account.purchase_amount for account in members), ZERO),
            sales_proceeds=sum((account.sales_proceeds for account in members), ZERO),
            known_market_value=sum((account.market_value for account in members if account.market_value is not None), ZERO),
            missing_prices=tuple(sorted({account.security for account in members if account.market_value is None})),
        )
    return Portfolio(tuple(accounts), securities, tuple(sales), MappingProxyType(totals))
