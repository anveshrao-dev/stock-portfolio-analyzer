"""Streamlit entry point for Stock Portfolio Analyzer."""

import streamlit as st

from stock_portfolio_analyzer.transactions_ui import render_transactions
from stock_portfolio_analyzer.portfolio_ui import render_portfolio
from stock_portfolio_analyzer.market_data import usable_prices
from stock_portfolio_analyzer.market_ui import render_price_controls
from stock_portfolio_analyzer.performance_ui import render_performance
from stock_portfolio_analyzer.portfolio import CalculationError


def main() -> None:
    """Render imports, portfolio overview, and evidence-based performance."""
    st.set_page_config(page_title="Stock Portfolio Analyzer", layout="wide")
    st.title("Stock Portfolio Analyzer")
    st.write("Bring brokerage CSVs together to see what you own, what you paid, and your gains or losses.")

    transactions, overview, history = st.tabs(
        ["Transactions", "Portfolio Overview", "Historical Performance"]
    )

    with transactions:
        render_transactions()

    selection = render_price_controls(st.session_state["transactions"])
    with overview:
        try:
            prices = usable_prices(st.session_state["transactions"], selection.data)
        except CalculationError as exc:
            st.error(f"Unable to value the accepted history: {exc}. Review the transactions; no partial portfolio is displayed.")
        else:
            render_portfolio(st.session_state["transactions"], prices=prices,
                             price_description=selection.description)

    with history:
        render_performance(st.session_state["transactions"], selection)


if __name__ == "__main__":
    main()
