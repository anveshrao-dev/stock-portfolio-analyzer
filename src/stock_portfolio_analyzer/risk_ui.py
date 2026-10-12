"""Small, explicitly hypothetical simulator within each currency portfolio."""

from decimal import Decimal

import streamlit as st

from .dashboard import format_money, format_percent, security_label
from .portfolio import CalculationError, Portfolio
from .risk import simulate_price_change


def render_risk_simulator(portfolio: Portfolio, currency: str) -> None:
    with st.expander("Explore a price-change scenario"):
        st.caption("Hypothetical scenarios, not predictions. Only the selected stock price changes; shares and other prices stay fixed.")
        holdings = [stock for stock in portfolio.securities if stock.security.currency == currency and stock.quantity]
        priced = [stock for stock in holdings if stock.price is not None]
        if not holdings:
            st.info("No open holdings to simulate in this selection.")
            return
        if not priced:
            st.info("A baseline price is required. Choose Sample reference prices or load Yahoo Finance data to simulate a holding.")
            st.session_state.pop(f"risk_signature:{currency}", None)
            return
        st.caption(f"Uses the selected {currency} portfolio and filters. A stock changes across all its included accounts.")
        if len(priced) < len(holdings):
            st.caption("Only holdings with a baseline price appear in the selector.")
        security = st.selectbox("Holding to simulate", [stock.security for stock in priced],
                                format_func=security_label, key=f"risk_security:{currency}")
        signature = (security, tuple((stock.security, stock.quantity, stock.price,
                                     tuple(account.brokerage for account in stock.accounts if account.quantity)) for stock in holdings))
        if st.session_state.get(f"risk_signature:{currency}") != signature:
            st.session_state[f"risk_change:{currency}"] = 0
            st.session_state[f"risk_signature:{currency}"] = signature
        change = st.slider("Hypothetical price change (%)", min_value=-100, max_value=100,
                           step=1, format="%d%%", key=f"risk_change:{currency}")
        try:
            scenario = simulate_price_change(portfolio, security, Decimal(change))
        except CalculationError as exc:
            st.error(f"Unable to simulate: {exc}")
            return
        st.caption((f"{security_label(security)} · {scenario.quantity} shares · "
                    f"{format_money(scenario.baseline_price, currency)} → {format_money(scenario.scenario_price, currency)} "
                    f"({format_percent(scenario.change_pct)} change).").replace("$", "\\$"))
        for column, label, amount in zip(st.columns(3),
                                          ("Baseline holding value", "Hypothetical holding value", "Holding value change"),
                                          (scenario.holding_before, scenario.holding_after, scenario.change_amount)):
            column.metric(label, format_money(amount, currency))
        if scenario.missing_prices:
            st.info("The complete portfolio impact is unavailable because other holdings lack prices: " +
                    ", ".join(security_label(key) for key in scenario.missing_prices) + ". The holding scenario above remains available.")
        else:
            before, after, impact = st.columns(3)
            before.metric(f"Baseline {currency} portfolio value", format_money(scenario.portfolio_before, currency))
            after.metric(f"Hypothetical {currency} portfolio value", format_money(scenario.portfolio_after, currency))
            impact.metric(f"Impact on {currency} portfolio", format_percent(scenario.impact_pct))
            if scenario.portfolio_before == 0:
                st.info("Percentage impact is undefined because the baseline portfolio value is zero. A percentage change to a zero price remains zero.")
            st.caption("Impact = holding value change ÷ baseline portfolio value.")
        st.caption("Returning to 0% restores the baseline; this scenario never changes your transactions or saved prices.")
