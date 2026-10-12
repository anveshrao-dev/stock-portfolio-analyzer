"""Dated prices with provenance. No UI, cache, fallback, or FX conversion."""

import csv
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path
from typing import Mapping, Sequence
from zoneinfo import ZoneInfo

from .models import Transaction
from .portfolio import SecurityKey, calculate_portfolio


@dataclass(frozen=True)
class Quote:
    price: Decimal
    valuation_date: date
    source: str
    as_of: datetime | None = None
    synthetic: bool = False


@dataclass(frozen=True)
class MarketData:
    quote: Quote | None = None
    closes: dict[date, Decimal] = field(default_factory=dict)
    errors: tuple[str, ...] = ()
    performance_blockers: tuple[str, ...] = ()
    sessions: tuple[date, ...] = ()


def yahoo_symbol(key: SecurityKey) -> str:
    """Support exchange-local equity symbols only; metadata verifies the mapping."""
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9.&_-]{0,29}", key.ticker):
        raise ValueError("Unsupported exchange-local ticker.")
    if key.exchange == "NSE" and key.currency == "INR":
        return f"{key.ticker}.NS"
    if key.exchange in ("NASDAQ", "NYSE") and key.currency == "USD":
        return key.ticker.replace(".", "-")
    raise ValueError("Yahoo support is limited to NASDAQ/NYSE in USD and NSE in INR.")


def _price(value) -> Decimal:
    try:
        price = Decimal(str(value))
        if not price.is_finite() or not 0 <= price < Decimal("1e12"):
            raise ValueError("Price is outside the supported range.")
        # Yahoo floats may contain binary noise; accounting accepts eight places.
        with localcontext() as context:
            context.prec = 40
            return price.quantize(Decimal("0.00000001"))
    except (InvalidOperation, TypeError) as exc:
        raise ValueError("Price is not a finite number.") from exc


def fetch_yahoo(key: SecurityKey, start: date, *, now: datetime | None = None,
                ticker_factory=None) -> MarketData:
    """Fetch one security. Failure stays local to that security.

    Close is not dividend-adjusted. Yahoo still split-adjusts raw Close, so any
    split since the first transaction blocks this unsupported history entirely.
    Today's daily bar is excluded even after close (no calendar-based inference).
    """
    now = now or datetime.now(timezone.utc)
    try:
        symbol = yahoo_symbol(key)
        if now.tzinfo is None or start > now.date():
            raise ValueError("A timezone-aware request time and nonfuture start date are required.")
        if ticker_factory is None:
            import yfinance as yf
            ticker_factory = yf.Ticker
        ticker = ticker_factory(symbol)
        frame = ticker.history(start=start, end=now.date() + timedelta(days=1),
                               interval="1d", auto_adjust=False, back_adjust=False,
                               actions=True, repair=False, keepna=True, timeout=10)
        meta = ticker.get_history_metadata()
        exchanges = {"NASDAQ": {"NMS", "NGM", "NCM"}, "NYSE": {"NYQ"}, "NSE": {"NSI"}}
        if (meta.get("symbol") != symbol or meta.get("currency") != key.currency
                or meta.get("exchangeName") not in exchanges[key.exchange]
                or meta.get("instrumentType") != "EQUITY"):
            raise ValueError("Provider symbol, exchange, instrument type, or currency does not match the transaction.")
        zone = ZoneInfo(meta.get("exchangeTimezoneName", ""))
        today = now.astimezone(zone).date()
        if frame.empty or not {"Close", "Stock Splits", "Dividends"}.issubset(frame.columns):
            raise ValueError("No usable history/corporate-action data returned; cannot verify prices.")
        if frame.index.has_duplicates:
            raise ValueError("Provider returned duplicate history dates.")
        relevant = frame.loc[[start <= stamp.date() <= today for stamp in frame.index]]
        if relevant[["Stock Splits", "Dividends"]].isna().any().any():
            raise ValueError("Incomplete corporate-action data; cannot verify prices.")
        if (relevant["Stock Splits"] != 0).any():
            message = "Stock split detected since the first transaction; split-adjusted quantities are unsupported. Valuation withheld."
            return MarketData(errors=(message,), performance_blockers=(message,))
        blockers = ("Dividends detected; dividend cash flows are unsupported, so XIRR is unavailable.",) if (relevant["Dividends"] != 0).any() else ()
        errors = []
        closes = {}
        for stamp, row in relevant.iterrows():
            day = stamp.date()
            if day >= today:
                continue
            try:
                closes[day] = _price(row["Close"])
            except ValueError:
                errors.append(f"Invalid/missing close on {day}; historical valuation has a gap.")
        quote = None
        try:
            raw_time = meta["regularMarketTime"]
            if isinstance(raw_time, datetime):
                if raw_time.tzinfo is None:
                    raise ValueError("Quote time has no timezone.")
                timestamp = raw_time.astimezone(timezone.utc)
            else:
                timestamp = datetime.fromtimestamp(raw_time, timezone.utc)
            if timestamp > now or timestamp.date() < start:
                raise ValueError("Quote timestamp is in the future or predates the history.")
            quote = Quote(_price(meta["regularMarketPrice"]), timestamp.astimezone(zone).date(),
                          "Yahoo Finance via yfinance · latest available regular-session quote", timestamp)
        except (ValueError, KeyError, TypeError, OverflowError, OSError):
            errors.append("Latest quote has no valid price/timestamp; historical closes remain available.")
        sessions = tuple(stamp.date() for stamp in relevant.index if stamp.date() < today)
        return MarketData(quote, closes, tuple(errors), blockers, sessions)
    except Exception as exc:
        # Third-party failures must not crash the UI or erase other securities.
        return MarketData(errors=(f"Yahoo data unavailable ({type(exc).__name__}): {exc}",))


def load_synthetic(path: Path) -> dict[SecurityKey, MarketData]:
    """Load the repository fixture only. A single reference point is not history."""
    result = {}
    with path.open(newline="", encoding="utf-8") as stream:
        for row in csv.DictReader(stream):
            key = SecurityKey(row["exchange"], row["ticker"], row["currency"])
            yahoo_symbol(key)
            if key in result:
                raise ValueError(f"Duplicate reference price: {key}.")
            if not row["source"].startswith("SAMPLE"):
                raise ValueError("Sample reference prices must identify their fictional source.")
            quote = Quote(_price(row["price"]), date.fromisoformat(row["as_of"]), row["source"], synthetic=True)
            result[key] = MarketData(quote=quote)
    return result


def usable_prices(transactions: Sequence[Transaction], data: Mapping[SecurityKey, MarketData]) -> dict[SecurityKey, Decimal]:
    """Reject quotes older than any selected transaction for that security."""
    calculate_portfolio(transactions)  # fail visibly on invalid history
    last_dates = {}
    for tx in transactions:
        key = SecurityKey(tx.exchange, tx.ticker, tx.currency)
        last_dates[key] = max(tx.date, last_dates.get(key, tx.date))
    return {key: item.quote.price for key, item in data.items()
            if key in last_dates and item.quote is not None
            and item.quote.valuation_date >= last_dates[key]}
