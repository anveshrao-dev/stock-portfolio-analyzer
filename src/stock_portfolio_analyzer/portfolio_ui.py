"""Currency-separated Portfolio Overview backed by the tested accounting engine."""

from decimal import Decimal
from typing import Mapping, Sequence

import streamlit as st

from .dashboard import (
    allocation_data, allocation_figure, concentration, format_money, format_percent,
    gains_figure, holdings_frame, portfolio_insights, security_label,
    select_transactions, HOLDING_SORT_FIELDS,
)
from .models import Transaction
from .portfolio import CalculationError, Portfolio, SecurityKey, calculate_portfolio
from .risk_ui import render_risk_simulator


def _holdings_order(currency: str) -> tuple[str, bool]:
    """Keep table preferences when filters temporarily hide their widgets."""
    preferences = st.session_state.setdefault("holdings_order_preferences", {})
    sort_key = f"holdings_sort:{currency}"
    descending_key = f"holdings_descending:{currency}"
    saved_sort, saved_descending = preferences.get(currency, ("Ticker", False))
    st.session_state.setdefault(sort_key, saved_sort)
    st.session_state.setdefault(descending_key, saved_descending)
    old_labels = {"Average cost basis": "Average purchase cost",
                  "Remaining cost basis": "Purchase cost of shares held",
                  "Return %": "Unrealized return %"}
    previous = st.session_state.get(sort_key)
    if previous in old_labels:
        st.session_state[sort_key] = old_labels[previous]
    with st.expander("Sort holdings"):
        left, right = st.columns(2)
        order = (
            left.selectbox(f"Sort {currency} holdings by", list(HOLDING_SORT_FIELDS), key=sort_key),
            right.checkbox(f"Descending order · {currency}", key=descending_key),
        )
    preferences[currency] = order
    return order


def _currency_section(portfolio: Portfolio, currency: str, *, view: str, basis: str) -> None:
    totals = portfolio.totals[currency]
    stocks = allocation_data(portfolio, currency, basis=basis, group="security")
    largest = concentration(stocks)
    st.subheader(f"{currency} portfolio")
    metrics = (
        ("Market value", totals.market_value,
         "Shares held multiplied by the selected prices. Unavailable if any holding lacks a price."),
        ("Purchase cost of shares held", totals.cost_basis,
         "Recorded purchase cost, including fees, for shares you still hold. Uses average-cost accounting; sales reduce this amount."),
        ("Realized gain/loss", totals.realized_gain,
         "Gain or loss from completed sales, after fees, including closed positions."),
        ("Unrealized gain/loss", totals.unrealized_gain,
         "Market value minus the purchase cost of shares still held."),
        ("Total gain/loss", totals.total_gain,
         "Realized plus unrealized gain or loss for the selected transactions."),
    )
    for offset in (0, 3):
        columns = st.columns(3)
        for column, (label, value, help_text) in zip(columns, metrics[offset:offset + 3]):
            column.metric(label, format_money(value, currency), help=help_text)
        if offset == 3:
            columns[2].metric(f"Largest holding · {basis}",
                              "Unavailable" if largest is None else largest[0],
                              help=f"Largest consolidated security by {basis.lower()} within this {currency} selection.")
            if largest:
                columns[2].caption(f"{format_percent(largest[1])} of selected {currency} {basis.lower()}")
    if totals.missing_prices:
        missing = ", ".join(security_label(key) for key in totals.missing_prices)
        st.warning(f"Prices unavailable for: {missing}. Market value and price-based gains are unavailable for the complete portfolio.")
        if any(account.price is not None for account in portfolio.holdings if account.security.currency == currency):
            st.caption((f"Priced holdings subtotal: {format_money(totals.known_market_value, currency)}. "
                        f"This is not the complete {currency} portfolio value.").replace("$", "\\$"))

    insights = portfolio_insights(portfolio, currency)
    if insights:
        st.markdown("#### Portfolio insights")
        direction = "above" if insights.open_position_return_pct >= 0 else "below"
        st.caption((
            f"At selected prices, open holdings are {format_percent(abs(insights.open_position_return_pct))} "
            f"{direction} their purchase cost. "
            f"Strongest: {security_label(insights.best.security)} ({format_percent(insights.best.return_pct)}). "
            f"Weakest: {security_label(insights.worst.security)} ({format_percent(insights.worst.return_pct)}). "
            f"Largest unrealized move: {security_label(insights.largest_move.security)} "
            f"({format_money(insights.largest_move.unrealized_gain, currency)})."
        ).replace("$", "\\$"))

    st.markdown("#### Current holdings")
    sort_by, descending = _holdings_order(currency)
    frame = holdings_frame(portfolio, currency, consolidated=view == "Consolidated by security",
                           sort_by=sort_by, descending=descending)
    if frame.empty:
        st.info("No open holdings. Realized results remain in the summary and More charts.")
    else:
        st.caption(f"{len(frame)} holdings · {view} · {currency}")
        st.dataframe(frame, hide_index=True, width="stretch", key=f"holdings:{currency}",
                     column_config={
                         "Average purchase cost": st.column_config.TextColumn(
                             help="Average recorded purchase cost per share, including fees, for shares still held."),
                         "Purchase cost of shares held": st.column_config.TextColumn(
                             help="Purchase cost including fees for shares still held, using average-cost accounting."),
                         "Unrealized return %": st.column_config.TextColumn(
                             help="Unrealized gain divided by the purchase cost of shares held. This is not an annualized return."),
                     })

    st.markdown(f"#### Stock allocation · {basis}")
    if stocks is None:
        st.info("Allocation requires prices for every open holding. Choose Cost basis in Filters and view options to see purchase-cost allocation.")
    elif largest is None:
        st.info(f"No positive {basis.lower()} to allocate in this selection.")
    else:
        st.plotly_chart(allocation_figure(stocks, currency, f"By stock · {basis}"),
                        width="stretch", key=f"allocation:stock:{currency}",
                        config={"displaylogo": False})

    with st.expander("More charts"):
        if largest:
            brokerage = allocation_data(portfolio, currency, basis=basis, group="brokerage")
            st.plotly_chart(allocation_figure(brokerage, currency, f"By brokerage · {basis}"),
                            width="stretch", key=f"allocation:brokerage:{currency}",
                            config={"displaylogo": False})
        st.plotly_chart(gains_figure(portfolio, currency), width="stretch", key=f"gains:{currency}",
                        config={"displaylogo": False})
        if totals.missing_prices:
            st.caption("Missing prices appear as gaps in unrealized gains, not zero gains.")
    render_risk_simulator(portfolio, currency)


def render_portfolio(transactions: Sequence[Transaction], *,
                     prices: Mapping[SecurityKey, Decimal] | None = None,
                     price_description: str | None = None) -> None:
    """Render accepted history. Any supplied price mapping must be described.

    The price provider supplies provenance without coupling accounting to HTTP.
    """
    st.header("Portfolio Overview")
    if not transactions:
        st.info("Import transactions in the Transactions tab to view your portfolio.")
        return
    if prices and (not price_description or not price_description.strip()):
        st.error("Supplied prices require a source/as-of description before they can be displayed.")
        return
    with st.expander("Filters and view options"):
        left, right = st.columns(2)
        brokerage = left.selectbox("Portfolio brokerage", [None, *sorted({tx.brokerage for tx in transactions})],
                                   format_func=lambda value: "All brokerages" if value is None else value,
                                   key="portfolio_brokerage")
        exchange = right.selectbox("Portfolio exchange", [None, *sorted({tx.exchange for tx in transactions})],
                                    format_func=lambda value: "All exchanges" if value is None else value,
                                    key="portfolio_exchange")
        view = st.radio("Holdings view", ["Consolidated by security", "By account"],
                         horizontal=True, key="portfolio_view")
        basis = st.radio("Allocation basis", ["Cost basis", "Market value"],
                          horizontal=True, key="portfolio_allocation",
                          help="Cost basis uses the purchase cost of shares held, including fees. Market value uses the selected prices.")
        selected = select_transactions(transactions, brokerage=brokerage, exchange=exchange)
        currencies = sorted({tx.currency for tx in selected}, key=lambda value: (value != "USD", value))
        st.caption("Filters apply to every metric and chart below. Currencies stay separate; no currency conversion is applied.")
    description = price_description or "Cost basis only · load prices for current valuation"
    if any(tx.source.startswith("Sample / ") for tx in selected) and not description.startswith("Sample reference prices"):
        description = f"Sample transactions · {description}"
    active_filters = [f"{label}: {value}" for label, value in
                      (("Brokerage", brokerage), ("Exchange", exchange)) if value is not None]
    st.caption(" · ".join([description, *active_filters]))
    if not selected:
        st.info("No transactions match these portfolio filters. Choose another brokerage or exchange.")
        return
    try:
        with st.spinner("Calculating portfolio…"):
            portfolio = calculate_portfolio(selected, prices=prices)
    except CalculationError as exc:
        st.error(f"Unable to calculate this portfolio: {exc}")
        st.info("Review the accepted transaction history before continuing. No partial results are displayed.")
        return
    for index, currency in enumerate(currencies):
        if index:
            st.divider()
        _currency_section(portfolio, currency, view=view, basis=basis)
