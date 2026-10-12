"""Explicit price selection and bounded provider caching for the Streamlit app."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Sequence

import pandas as pd
import streamlit as st

from .dashboard import format_money, security_label
from .market_data import MarketData, fetch_yahoo, load_synthetic
from .models import Transaction
from .portfolio import SecurityKey

REFERENCE_PATH = Path(__file__).resolve().parents[2] / "data/synthetic_reference_prices.csv"


@dataclass(frozen=True)
class PriceSelection:
    data: dict[SecurityKey, MarketData]
    description: str


class QuoteUnavailable(Exception):
    """Keep failed requests out of the shared cache so a user can retry."""

    def __init__(self, result: MarketData):
        self.result = result
        super().__init__("No quote returned")


@st.cache_data(ttl=900, max_entries=128, show_spinner=False)
def cached_yahoo(key: SecurityKey, start: date, request_day: date) -> MarketData:
    """Cache prices only, never transactions. Date key prevents overnight reuse."""
    result = fetch_yahoo(key, start)
    if result.quote is None:
        raise QuoteUnavailable(result)
    return result


def render_price_controls(transactions: Sequence[Transaction]) -> PriceSelection:
    with st.sidebar:
        st.header("Price data")
        # Preserve the source across a hot reload from an older UI label.
        aliases = {"No prices": "Cost basis only", "Offline demo prices": "Sample reference prices",
                   "Offline synthetic reference": "Sample reference prices"}
        previous = st.session_state.get("price_source")
        if previous in aliases:
            st.session_state["price_source"] = aliases[previous]
        mode = st.selectbox("Price source", ["Cost basis only", "Yahoo Finance", "Sample reference prices"], key="price_source")
        starts = {}
        lasts = {}
        for tx in transactions:
            key = SecurityKey(tx.exchange, tx.ticker, tx.currency)
            starts[key] = min(starts.get(key, tx.date), tx.date)
            lasts[key] = max(lasts.get(key, tx.date), tx.date)
        data = {}
        description = "Cost basis only · current valuation needs prices."
        now = datetime.now(timezone.utc)
        if mode == "Cost basis only":
            st.info("Purchase costs and profit from sales are available. Choose Yahoo Finance to compare with market prices.")
        elif mode == "Sample reference prices":
            description = "Sample reference prices · fictional"
            try:
                references = load_synthetic(REFERENCE_PATH)
                dates = sorted({item.quote.valuation_date.isoformat() for item in references.values() if item.quote})
                description += " · " + ", ".join(dates)
                data = {key: item for key, item in references.items() if key in starts}
            except (OSError, ValueError, KeyError) as exc:
                st.error(f"Unable to load offline reference prices: {exc}")
            st.caption("Built-in fictional prices for sample securities. Works offline.")
        elif mode == "Yahoo Finance":
            description = "Yahoo Finance · latest available quotes (may be delayed)"
            st.caption("NASDAQ / NYSE / NSE · cached for 15 minutes.")
            with st.expander("About market prices"):
                st.caption("Yahoo receives symbols and date ranges only. Quotes may be delayed. Price changes exclude dividends; see quote dates below.")
            signature = tuple(sorted((key, start, lasts[key]) for key, start in starts.items()))
            if transactions:
                st.caption(f"{len(transactions)} imported transactions · {len(starts)} securities ready for price lookup.")
            if st.button("Load market data", key="fetch_market", disabled=not transactions):
                fetched = {}
                progress = st.progress(0, text="Loading quotes and daily history…")
                for index, (key, start, _) in enumerate(signature):
                    try:
                        fetched[key] = cached_yahoo(key, start, now.date())
                    except QuoteUnavailable as exc:
                        fetched[key] = exc.result
                    progress.progress((index + 1) / len(signature), text=f"Loaded {index + 1}/{len(signature)} securities")
                progress.empty()
                st.session_state["yahoo_result"] = (signature, datetime.now(timezone.utc), fetched)
            if not transactions:
                st.info("Import transactions first: choose a file, review it, then select Import.")
                return PriceSelection({}, description)
            saved = st.session_state.get("yahoo_result")
            if saved and saved[0] == signature and now - saved[1] < timedelta(minutes=15) and saved[1].date() == now.date():
                data = saved[2]
                st.caption(f"Loaded {saved[1].strftime('%H:%M UTC')} · quote times below.")
            else:
                st.info("Select Load market data for this portfolio. Reload after changing imports or after 15 minutes.")
        if mode != "Cost basis only" and transactions:
            for key in sorted(starts):
                item = data.get(key)
                if item is None:
                    if mode == "Sample reference prices":
                        st.warning(f"{security_label(key)}: no sample reference price. Use Yahoo Finance.")
                    continue
                errors = tuple(dict.fromkeys(item.errors))
                for message in errors:
                    st.warning(f"{security_label(key)}: {message}")
                if item.quote and item.quote.valuation_date < lasts[key]:
                    st.warning(f"{security_label(key)}: quote predates the latest transaction; it is excluded from current holdings valuation.")
                elif item.quote and not item.quote.synthetic and (now.date() - item.quote.valuation_date).days > 7:
                    st.warning(f"{security_label(key)}: latest available quote is more than seven days old. Review its timestamp.")
            audit = [{"Security": security_label(key), "Currency": key.currency,
                      "Price": format_money(item.quote.price, key.currency),
                      "Valuation date": item.quote.valuation_date.isoformat(),
                      "Quote timestamp (UTC)": item.quote.as_of.isoformat() if item.quote.as_of else "Reference date only",
                      "Source": item.quote.source}
                     for key, item in sorted(data.items()) if item.quote]
            if audit:
                with st.expander("Price sources and timestamps"):
                    st.dataframe(pd.DataFrame(audit), hide_index=True, width="stretch")
            limitations = sorted({f"{security_label(key)}: {message}" for key, item in data.items()
                                  for message in item.performance_blockers if message not in item.errors})
            if limitations:
                with st.expander("Annualized return availability"):
                    for message in limitations:
                        st.caption(message)
        return PriceSelection(data, description)
