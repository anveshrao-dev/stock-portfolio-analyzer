"""Historical Performance: dated evidence, clear omissions, no invented returns."""

from decimal import Decimal
from typing import Sequence

import plotly.graph_objects as go
import streamlit as st

from .dashboard import format_money, format_percent, security_label, select_transactions
from .market_data import usable_prices
from .market_ui import PriceSelection
from .models import Transaction
from .performance import historical_values, portfolio_xirr
from .portfolio import CalculationError, SecurityKey, calculate_portfolio


def render_performance(transactions: Sequence[Transaction], selection: PriceSelection) -> None:
    st.header("Historical Performance")
    if not transactions:
        st.info("Import transactions to view purchases, sales, and performance.")
        return
    with st.expander("Filters and view options"):
        left, right = st.columns(2)
        brokerage = left.selectbox("History brokerage", [None, *sorted({tx.brokerage for tx in transactions})],
                                   format_func=lambda value: "All brokerages" if value is None else value,
                                   key="history_brokerage")
        exchange = right.selectbox("History exchange", [None, *sorted({tx.exchange for tx in transactions})],
                                    format_func=lambda value: "All exchanges" if value is None else value,
                                    key="history_exchange")
        st.caption("Filters include each selected account's complete trade history. Currencies stay separate.")
    rows = select_transactions(transactions, brokerage=brokerage, exchange=exchange)
    signature = tuple(tx.fingerprint for tx in rows)
    if st.session_state.get("confirmed_history_signature") != signature:
        st.session_state["confirm_history"] = False
        st.session_state["confirmed_history_signature"] = signature
    description = selection.description
    if any(tx.source.startswith("Sample / ") for tx in rows) and not description.startswith("Sample reference prices"):
        description = f"Sample transactions · {description}"
    active_filters = [f"{label}: {value}" for label, value in
                      (("Brokerage", brokerage), ("Exchange", exchange)) if value is not None]
    st.caption(" · ".join([description, *active_filters]))
    if not rows:
        st.info("No transactions match these history filters.")
        return
    try:
        portfolio = calculate_portfolio(rows, prices=usable_prices(rows, selection.data))
        trends = historical_values(rows, selection.data)
    except CalculationError as exc:
        st.error(f"Unable to calculate performance: {exc}. No partial results are displayed.")
        return
    for currency, totals in portfolio.totals.items():
        st.subheader(f"{currency} performance")
        metrics = [("Lifetime purchases (including fees)", totals.purchase_amount,
                    "All purchases in the selected trade history, including purchase fees."),
                   ("Lifetime sales (net of fees)", totals.sales_proceeds,
                    "All sale proceeds in the selected trade history, after sale fees."),
                   ("Current stock value (selected prices)", totals.market_value,
                    "Value of shares still held at the selected prices. Excludes account cash."),
                   ("Realized gain/loss", totals.realized_gain,
                    "Gain or loss from completed sales, including closed positions, using average purchase cost."),
                   ("Unrealized gain/loss", totals.unrealized_gain,
                    "Selected stock value minus the purchase cost, including fees, of shares still held."),
                   ("Total investment gain/loss", totals.total_gain,
                    "Realized plus unrealized gains on these trades. Excludes dividends, taxes, and account cash.")]
        for offset in (0, 3):
            for column, (label, value, help_text) in zip(st.columns(3), metrics[offset:offset + 3]):
                column.metric(label, format_money(value, currency), help=help_text)
        points = trends[currency]
        valid = [point for point in points if point.value is not None]
        securities = {SecurityKey(tx.exchange, tx.ticker, tx.currency)
                      for tx in rows if tx.currency == currency}
        missing_history = sorted(key for key in securities
                                 if key not in selection.data or not selection.data[key].closes)
        if missing_history:
            if not selection.data:
                st.info("Historical prices have not been loaded. Choose Yahoo Finance and select Load market data.")
            elif all(item.quote and item.quote.synthetic for item in selection.data.values()):
                st.info("Sample reference prices contain one valuation date, not the historical prices needed for a trend.")
            else:
                st.info("The full portfolio trend needs historical prices for: "
                        + ", ".join(security_label(key) for key in missing_history)
                        + ". Check the price-source messages for the cause; the chart is withheld.")
        elif len(valid) < 2:
            st.info("Not enough historical prices to draw a trend.")
        else:
            figure = go.Figure(go.Scatter(
                x=[point.day for point in points],
                y=[None if point.value is None else float(point.value) for point in points],
                customdata=[format_money(point.value, currency) for point in points],
                mode="lines+markers", connectgaps=False,
                hovertemplate="%{x|%Y-%m-%d}<br>%{customdata}<extra></extra>", name="Stock value"))
            figure.update_layout(title=f"Historical stock value · {currency}", xaxis_title="Date",
                                 yaxis_title=currency, height=360)
            st.plotly_chart(figure, width="stretch", key=f"history:{currency}", config={"displaylogo": False})
            st.caption("Stock value uses same-day completed closes and excludes cash. Purchases and sales affect the trend; this is not a return index.",
                       help="End-of-day share quantities × same-date closing prices. Today's daily bar is excluded. Entirely omitted provider sessions cannot be detected without an exchange calendar.")
            missing = len(points) - len(valid)
            if missing:
                detail = "1 date lacks complete prices and appears as a gap" if missing == 1 else f"{missing} dates lack complete prices and appear as gaps"
                st.warning(f"{detail}; prices are not carried forward or interpolated.")

    with st.expander("Calculate annualized return"):
        st.caption("Annualized return (XIRR) accounts for the timing of your purchases and sales. It treats purchases as contributions and sales as withdrawals, with the final stock value included.")
        st.caption("Your selected history must be complete, with no dividends, transfers, or stock splits. This calculation excludes account cash and taxes.")
        confirmed = st.checkbox("I confirm this history is complete and accept the assumptions above.", key="confirm_history")
        if not confirmed:
            st.info("Confirm the complete-history assumptions above to calculate XIRR. Missing prices or unsupported corporate actions can still prevent a result.")
            return
        for currency in portfolio.totals:
            try:
                result = portfolio_xirr(rows, selection.data, currency, confirmed=confirmed)
            except CalculationError as exc:
                st.error(f"Unable to calculate annualized return: {exc}")
                continue
            st.markdown(f"**{currency}**")
            st.metric("Annualized return (XIRR)",
                      "Unavailable" if result.rate is None else format_percent(Decimal(str(result.rate)) * 100),
                      help="Annualized, money-weighted return for dated purchases, sales, and terminal stock value. Shown only when the inputs support one verified answer.")
            if result.reason:
                st.info(result.reason)
            if result.valuation_date:
                st.caption(f"Final valuation: {result.valuation_date.isoformat()} · actual days / 365.",
                           help="The final dated valuation can differ from the latest quote used for current stock value.")
