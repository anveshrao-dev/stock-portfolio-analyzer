"""Provider boundary: deterministic responses, never internet in tests."""

from datetime import date, datetime, timezone
from decimal import Decimal as D
from pathlib import Path

import pandas as pd
import pytest

from stock_portfolio_analyzer import market_data as md
from stock_portfolio_analyzer.portfolio import SecurityKey

AAPL = SecurityKey("NASDAQ", "AAPL", "USD")
NOW = datetime(2025, 1, 6, 22, tzinfo=timezone.utc)


class Provider:
    def __init__(self, *, meta=None, frame=None, error=False):
        self.meta = {"symbol": "AAPL", "exchangeName": "NMS", "currency": "USD",
                     "instrumentType": "EQUITY", "regularMarketPrice": 120.5,
                     "regularMarketTime": int(NOW.timestamp()), "exchangeTimezoneName": "America/New_York"}
        self.meta.update(meta or {})
        self.frame = frame if frame is not None else pd.DataFrame(
            {"Close": [100.0, 110.0, 120.5], "Dividends": [0.0, 0.0, 0.0], "Stock Splits": [0, 0, 0]},
            index=pd.to_datetime(["2025-01-02", "2025-01-03", "2025-01-06"]).tz_localize("America/New_York"))
        self.error = error

    def history(self, **kwargs):
        assert kwargs["auto_adjust"] is False and kwargs["actions"] is True
        assert kwargs["repair"] is False and kwargs["timeout"] <= 10
        assert kwargs["end"] == date(2025, 1, 7)
        if self.error:
            raise TimeoutError("service unavailable")
        return self.frame

    def get_history_metadata(self):
        return self.meta


def fetch(provider=None, key=AAPL):
    def factory(symbol):
        assert symbol == ("AAPL" if key == AAPL else "RELIANCE.NS")
        return provider or Provider()
    return md.fetch_yahoo(key, date(2025, 1, 2), now=NOW, ticker_factory=factory)


def test_verified_quote_has_timestamp_currency_and_only_completed_daily_closes():
    result = fetch()
    assert result.quote.price == D("120.5")
    assert result.quote.as_of == NOW
    assert result.quote.valuation_date == date(2025, 1, 6)
    assert result.closes == {date(2025, 1, 2): D("100"), date(2025, 1, 3): D("110")}
    assert "Yahoo" in result.quote.source and not result.quote.synthetic
    assert not result.errors


def test_provider_normalized_timestamp_is_supported_without_losing_timezone():
    # yfinance 1.7 metadata returns pandas.Timestamp, not only UNIX seconds.
    result = fetch(Provider(meta={"regularMarketTime": pd.Timestamp("2025-01-06 17:00:00", tz="America/New_York")}))
    assert result.quote is not None
    assert result.quote.as_of == NOW


def test_naive_provider_timestamp_is_withheld():
    result = fetch(Provider(meta={"regularMarketTime": datetime(2025, 1, 6, 17)}))
    assert result.quote is None and result.errors


@pytest.mark.parametrize("key,want", [
    (AAPL, "AAPL"), (SecurityKey("NSE", "RELIANCE", "INR"), "RELIANCE.NS"),
    (SecurityKey("NYSE", "BRK.B", "USD"), "BRK-B"),
])
def test_symbol_mapping_is_exchange_specific(key, want):
    assert md.yahoo_symbol(key) == want


@pytest.mark.parametrize("meta", [
    {"exchangeName": "NYQ"}, {"currency": "INR"}, {"symbol": "MSFT"},
    {"instrumentType": "ETF"}, {"exchangeTimezoneName": ""},
])
def test_provider_identity_mismatch_is_rejected(meta):
    result = fetch(Provider(meta=meta))
    assert result.quote is None and not result.closes and result.errors


@pytest.mark.parametrize("meta", [
    {"regularMarketPrice": float("nan")}, {"regularMarketPrice": -1},
    {"regularMarketTime": 0}, {"regularMarketTime": int(NOW.timestamp()) + 86400},
])
def test_invalid_quote_does_not_destroy_valid_history(meta):
    result = fetch(Provider(meta=meta))
    assert result.quote is None and result.errors
    assert result.closes[date(2025, 1, 3)] == D("110")


def test_network_error_is_explicit_not_synthetic_fallback():
    result = fetch(Provider(error=True))
    assert result.quote is None and not result.closes and result.errors


def test_splits_block_valuation_and_dividends_are_flagged():
    provider = Provider()
    provider.frame.loc[provider.frame.index[1], "Stock Splits"] = 2
    result = fetch(provider)
    assert result.quote is None and not result.closes and result.errors
    assert result.performance_blockers
    provider = Provider()
    provider.frame.loc[provider.frame.index[1], "Dividends"] = 0.25
    result = fetch(provider)
    assert result.quote is not None and result.closes
    assert result.performance_blockers


def test_missing_close_is_a_gap_never_replaced_with_previous_value():
    provider = Provider()
    provider.frame.loc[provider.frame.index[1], "Close"] = float("nan")
    result = fetch(provider)
    assert date(2025, 1, 3) not in result.closes
    assert date(2025, 1, 3) in result.sessions
    assert result.errors


def test_offline_reference_is_synthetic_dated_and_has_no_history():
    results = md.load_synthetic(Path(__file__).resolve().parents[1] / "data/synthetic_reference_prices.csv")
    assert len(results) == 10
    quote = results[AAPL].quote
    assert quote.price == D("248.40") and quote.synthetic
    assert quote.as_of is None and quote.valuation_date == date(2026, 9, 30)
    assert all(not result.closes for result in results.values())


def test_quote_older_than_trade_is_not_used_for_current_holdings():
    from test_portfolio import trade
    result = fetch()
    assert md.usable_prices([trade(day=7)], {AAPL: result}) == {}
    assert md.usable_prices([trade(day=2)], {AAPL: result}) == {AAPL: D("120.5")}


def test_nse_metadata_and_local_quote_date_are_preserved():
    result = fetch(Provider(meta={"symbol": "RELIANCE.NS", "exchangeName": "NSI", "currency": "INR",
                                  "exchangeTimezoneName": "Asia/Kolkata"}), SecurityKey("NSE", "RELIANCE", "INR"))
    assert result.quote.valuation_date == date(2025, 1, 7)


def test_duplicate_reference_prices_fail_instead_of_overwriting(tmp_path):
    path = tmp_path / "prices.csv"
    row = "AAPL,NASDAQ,USD,120,2025-01-03,SAMPLE - fictional reference\n"
    path.write_text("ticker,exchange,currency,price,as_of,source\n" + row + row)
    with pytest.raises(ValueError, match="Duplicate"):
        md.load_synthetic(path)
