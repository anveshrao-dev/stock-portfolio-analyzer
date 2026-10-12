"""Scenario controls exercise the actual portfolio and imported-data workflows."""

from streamlit.testing.v1 import AppTest

from test_portfolio_ui import import_samples, start_app


def scenario_app(prices="{aapl: D('100'), msft: D('200')}", closed=False, initial_sort=None):
    script = f"""
import streamlit as st
from decimal import Decimal as D
from stock_portfolio_analyzer.portfolio import SecurityKey
from stock_portfolio_analyzer.portfolio_ui import render_portfolio
from test_portfolio import trade
aapl = SecurityKey('NASDAQ', 'AAPL', 'USD')
msft = SecurityKey('NASDAQ', 'MSFT', 'USD')
rows = [trade(), trade('SELL', day=2)] if {closed} else [trade(), trade(ticker='MSFT')]
render_portfolio(rows, prices={prices}, price_description='SYNTHETIC test prices · 2025-01-03')
"""
    app = AppTest.from_string(script)
    if initial_sort is not None:
        app.session_state["holdings_sort:USD"] = initial_sort
    return app.run(timeout=20)


def metric(app, label):
    return next(item.value for item in app.metric if item.label == label)


def test_slider_changes_scenario_only_and_labels_results_hypothetical():
    app = scenario_app()
    assert not app.exception
    app.slider(key="risk_change:USD").set_value(-25).run()
    assert not app.exception
    assert metric(app, "Hypothetical holding value") == "$750.00"
    assert metric(app, "Hypothetical USD portfolio value") == "$2,750.00"
    assert metric(app, "Impact on USD portfolio") == "-8.33%"
    assert metric(app, "Market value") == "$3,000.00"
    assert any("not predictions" in item.value for item in app.caption)
    app.slider(key="risk_change:USD").set_value(-100).run()
    assert metric(app, "Hypothetical holding value") == "$0.00"
    assert metric(app, "Hypothetical USD portfolio value") == "$2,000.00"


def test_missing_prices_guide_user_and_partial_prices_withhold_total_effect():
    app = scenario_app(prices="{}")
    assert not app.exception and not app.slider
    assert any("baseline price" in item.value for item in app.info)
    app = scenario_app(prices="{aapl: D('100')}")
    app.slider(key="risk_change:USD").set_value(10).run()
    assert metric(app, "Hypothetical holding value") == "$1,100.00"
    assert not any(item.label in {"Baseline USD portfolio value",
                                  "Hypothetical USD portfolio value",
                                  "Impact on USD portfolio"} for item in app.metric)
    assert any("complete portfolio impact" in item.value for item in app.info)


def test_closed_portfolio_has_no_simulator_controls_and_zero_baseline_is_explained():
    closed = scenario_app(closed=True)
    assert not closed.exception and not closed.slider
    zero = scenario_app(prices="{aapl: D('0'), msft: D('0')}")
    zero.slider(key="risk_change:USD").set_value(50).run()
    assert metric(zero, "Hypothetical USD portfolio value") == "$0.00"
    assert metric(zero, "Impact on USD portfolio") == "Unavailable"
    assert any("zero" in item.value for item in zero.info)


def test_sample_scenarios_separate_currencies_preserve_transactions_and_reset_on_scope_change():
    app = import_samples(start_app(), ["us_brokerage_a.csv", "us_brokerage_b.csv", "india_brokerage.csv"])
    original = tuple(tx.fingerprint for tx in app.session_state["transactions"])
    app.selectbox(key="price_source").select("Sample reference prices").run()
    app.slider(key="risk_change:USD").set_value(20).run()
    assert metric(app, "Hypothetical holding value") == "$9,836.64"
    assert app.slider(key="risk_change:INR").value == 0
    app.selectbox(key="portfolio_brokerage").select("US Brokerage A").run()
    assert app.slider(key="risk_change:USD").value == 0
    assert tuple(tx.fingerprint for tx in app.session_state["transactions"]) == original
    app.selectbox(key="price_source").select("Cost basis only").run()
    assert not app.exception and not app.slider
    app.selectbox(key="price_source").select("Sample reference prices").run()
    assert not app.exception
    assert app.slider(key="risk_change:USD").value == 0
    assert tuple(tx.fingerprint for tx in app.session_state["transactions"]) == original


def test_changing_selected_security_resets_the_price_shock():
    app = scenario_app()
    app.slider(key="risk_change:USD").set_value(20).run()
    app.selectbox(key="risk_security:USD").select_index(1).run()
    assert not app.exception
    assert app.slider(key="risk_change:USD").value == 0
    assert metric(app, "Hypothetical holding value") == "$2,000.00"


def test_holdings_sort_controls_use_numeric_market_values():
    app = scenario_app()
    app.selectbox(key="holdings_sort:USD").select("Market value").run()
    app.checkbox(key="holdings_descending:USD").check().run()
    assert not app.exception
    assert list(app.dataframe[0].value["Ticker"]) == ["MSFT", "AAPL"]


def test_saved_holdings_sort_survives_clearer_purchase_cost_labels():
    app = scenario_app(initial_sort="Remaining cost basis")
    assert not app.exception
    assert app.selectbox(key="holdings_sort:USD").value == "Purchase cost of shares held"
    assert len(app.dataframe[0].value) == 2
