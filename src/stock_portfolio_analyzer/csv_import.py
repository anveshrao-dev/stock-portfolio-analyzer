"""Strict CSV reading and user-controlled normalization; no inferred dates."""

import csv
import io
import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Iterable

import pandas as pd

from .models import Issue, Transaction

MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_ROWS = 10_000
REQUIRED_FIELDS = ("date", "ticker", "action", "quantity", "price")
METADATA_FIELDS = ("brokerage", "exchange", "currency")
FIELDS = REQUIRED_FIELDS + METADATA_FIELDS + ("fees", "transaction_id")
DATE_FORMATS = {"YYYY-MM-DD": "%Y-%m-%d", "MM/DD/YYYY": "%m/%d/%Y", "DD/MM/YYYY": "%d/%m/%Y"}
EXCHANGES = ("NASDAQ", "NYSE", "NSE")
CURRENCIES = ("USD", "INR")
ALIASES = {
    "date": ("date", "trade date", "transaction date", "trade_date"),
    "ticker": ("symbol", "ticker", "scrip", "security"),
    "action": ("action", "transaction type", "side", "buy/sell"),
    "quantity": ("shares", "quantity", "qty", "units"),
    "price": ("price", "execution price", "price per share", "rate"),
    "fees": ("fees", "commission", "charges"),
    "brokerage": ("brokerage", "source", "account", "broker"),
    "exchange": ("exchange", "market"),
    "currency": ("currency", "ccy"),
    "transaction_id": ("transaction id", "transaction_id", "trade id", "trade_id"),
}


@dataclass(frozen=True)
class ImportSettings:
    mapping: dict[str, str | None]
    brokerage: str
    exchange: str
    currency: str
    date_format: str = "%Y-%m-%d"


def load_csv(content: bytes) -> pd.DataFrame:
    """Read UTF-8 comma-separated CSV without dropping rows or inferring types."""
    if len(content) > MAX_FILE_BYTES:
        raise ValueError("CSV exceeds the 2 MiB per-file limit.")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("CSV must use UTF-8 encoding. Re-export the file as UTF-8 CSV.") from exc
    try:
        records = list(csv.reader(io.StringIO(text, newline=""), strict=True))
    except csv.Error as exc:
        raise ValueError(f"Malformed CSV: {exc}") from exc
    if not records or len(records) < 2:
        raise ValueError("CSV must contain a header and at least one transaction row.")
    header, *rows = records
    normalized_headers = [name.strip().casefold() for name in header]
    if not all(normalized_headers) or len(set(normalized_headers)) != len(header):
        raise ValueError("CSV column names must be nonempty and unique (ignoring case and spaces).")
    if len(rows) > MAX_ROWS:
        raise ValueError("CSV exceeds the 10,000 transaction rows per-file limit.")
    for row_number, row in enumerate(rows, start=2):
        if len(row) != len(header):
            raise ValueError(f"CSV record {row_number} has {len(row)} fields; expected {len(header)}. No rows were skipped.")
    return pd.DataFrame(rows, columns=header, dtype=str)


def suggest_mapping(columns: Iterable[str]) -> dict[str, str | None]:
    """Suggest only unambiguous known headers; the user can change every choice."""
    columns = list(columns)
    result = {}
    for field, aliases in ALIASES.items():
        matches = [column for column in columns if column.strip().casefold() in aliases]
        result[field] = matches[0] if len(matches) == 1 else None
    return result


def mapping_candidates(columns: Iterable[str], field: str, *,
                       include_all: bool = False) -> list[str]:
    """Return source columns appropriate for one canonical transaction field.

    The regular UI stays semantic by matching known header aliases. Advanced
    mode exposes every source column so an unfamiliar brokerage header can still
    be mapped manually.
    """
    if field not in ALIASES:
        raise ValueError(f"Unknown transaction field: {field}")
    columns = list(columns)
    if include_all:
        return columns
    aliases = ALIASES[field]
    return [column for column in columns if column.strip().casefold() in aliases]


def _decimal(value: str, field: str, *, zero_allowed: bool = False) -> Decimal:
    # Bounded plain decimals avoid inferred locale/exponent meanings and overflow.
    if not re.fullmatch(r"[+-]?\d{1,12}(?:\.\d{1,8})?", value):
        raise ValueError(f"{field} must be a plain decimal (up to 12 integer and 8 decimal digits).")
    number = Decimal(value)
    if number < 0 or (number == 0 and not zero_allowed):
        raise ValueError(f"{field} must be {'nonnegative' if zero_allowed else 'greater than zero'}.")
    return number


def normalize_rows(frame: pd.DataFrame, settings: ImportSettings, *, source: str,
                   file_id: str) -> tuple[list[Transaction], list[Issue]]:
    """Return normalized transactions and rejected raw rows with explanations."""
    if any(not settings.mapping.get(field) for field in REQUIRED_FIELDS):
        raise ValueError("Complete the required field mapping: date, ticker, action, quantity, price.")
    mapped = [column for field, column in settings.mapping.items() if field in FIELDS and column is not None]
    if len(set(mapped)) != len(mapped):
        raise ValueError("Each source column can map to only one standard field.")
    if any(column not in frame.columns for column in mapped):
        raise ValueError("A mapped source column is missing from the CSV.")
    if settings.date_format not in DATE_FORMATS.values():
        raise ValueError("Select a supported explicit date format.")
    accepted, rejected = [], []
    for number, raw in enumerate(frame.to_dict("records"), start=2):
        row_key = f"{file_id}:{number}"

        def value(field: str, default: str = "") -> str:
            column = settings.mapping.get(field)
            return (raw[column] if column is not None else default).strip()

        try:
            try:
                tx_date = datetime.strptime(value("date"), settings.date_format).date()
            except ValueError as exc:
                raise ValueError("Invalid transaction date for the selected date format.") from exc
            brokerage = value("brokerage", settings.brokerage)
            exchange = value("exchange", settings.exchange).upper()
            currency = value("currency", settings.currency).upper()
            ticker = value("ticker").upper()
            action = value("action").upper()
            if not brokerage:
                raise ValueError("brokerage/account is required.")
            if exchange not in EXCHANGES:
                raise ValueError("exchange must be NASDAQ, NYSE, or NSE for this version.")
            if currency not in CURRENCIES:
                raise ValueError("currency must be USD or INR for this version.")
            if not ticker:
                raise ValueError("ticker is required.")
            if not re.fullmatch(r"[A-Z0-9][A-Z0-9.&_-]{0,29}", ticker):
                raise ValueError("ticker contains unsupported characters; use the exchange-local symbol.")
            if action not in ("BUY", "SELL"):
                raise ValueError("transaction type must be BUY or SELL; other actions are unsupported.")
            accepted.append(Transaction(
                date=tx_date, brokerage=brokerage, exchange=exchange, ticker=ticker,
                action=action, quantity=_decimal(value("quantity"), "quantity"),
                price=_decimal(value("price"), "price"), currency=currency,
                fees=_decimal(value("fees", "0"), "fees", zero_allowed=True),
                transaction_id=value("transaction_id") or None,
                source=source, source_row=number, row_key=row_key, raw=raw,
            ))
        except ValueError as exc:
            rejected.append(Issue(source, number, row_key, str(exc), raw))
    return accepted, rejected
