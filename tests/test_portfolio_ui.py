"""Real Streamlit dashboard flows; no quote service or network is involved."""

from pathlib import Path
from decimal import Decimal as D

from streamlit.testing.v1 import AppTest

from stock_portfolio_analyzer.csv_import import ImportSettings, load_csv, normalize_rows, suggest_mapping
from stock_portfolio_analyzer.portfolio import SecurityKey

ROOT = Path(__file__).resolve().parents[1]


def start_app():
    return AppTest.from_file(str(ROOT / "app.py")).run(timeout=20)


def import_samples(app, names):
    app.multiselect(key="sample_files").set_value(names)
    app.button(key="stage_samples").click().run()
    app.button(key="validate_batch").click().run()
    return app.button(key="import_batch").click().run()


def overview(app):
    return app.tabs[1]


def test_empty_dashboard_guides_user_to_transactions_without_zero_balance():
    app = start_app()
    assert not app.exception
    assert any("Import transactions" in item.value for item in overview(app).info)
    assert not overview(app).metric


def test_all_samples_render_separate_currency_sections_and_consolidated_holdings():
    app = import_samples(start_app(), ["us_brokerage_a.csv", "us_brokerage_b.csv", "india_brokerage.csv"])
    panel = overview(app)
    assert not app.exception
    assert {heading.value for heading in panel.subheader} >= {"USD portfolio", "INR portfolio"}
    metrics = [item for item in panel.metric if item.label == "Market value"]
    assert len(metrics) == 2 and all(item.value == "Unavailable" for item in metrics)
    tables = [table.value for table in panel.dataframe]
    assert [len(table) for table in tables] == [6, 4]
    aapl = tables[0].loc[tables[0]["Ticker"] == "AAPL"].iloc[0]
    assert aapl["Quantity"] == "33"
    assert aapl["Brokerage"] == "US Brokerage A, US Brokerage B"
    assert len(panel.get("plotly_chart")) == 6
    assert any("sample transactions" in caption.value.lower() for caption in panel.caption)


def test_transaction_table_filters_do_not_change_portfolio_scope():
    app = import_samples(start_app(), ["us_brokerage_a.csv", "india_brokerage.csv"])
    app.multiselect(key="filter:Brokerage").set_value(["Indian Brokerage"]).run()
    assert not app.exception
    assert {heading.value for heading in overview(app).subheader} >= {"USD portfolio", "INR portfolio"}


def test_account_view_and_filters_recompute_all_summary_data_without_mutating_imports():
    app = import_samples(start_app(), ["us_brokerage_a.csv", "us_brokerage_b.csv", "india_brokerage.csv"])
    app.radio(key="portfolio_view").set_value("By account").run()
    assert len(overview(app).dataframe[0].value) == 8
    app.selectbox(key="portfolio_brokerage").select("US Brokerage A").run()
    table = overview(app).dataframe[0].value
    assert len(table) == 4 and set(table["Brokerage"]) == {"US Brokerage A"}
    assert table.loc[table["Ticker"] == "AAPL", "Quantity"].iloc[0] == "19"
    assert any("Brokerage: US Brokerage A" in item.value for item in overview(app).caption)
    assert len(app.session_state["transactions"]) == 48
    app.selectbox(key="portfolio_exchange").select("NSE").run()
    assert not app.exception
    assert not overview(app).metric
    assert any("No transactions match" in item.value for item in overview(app).info)


def test_holdings_order_survives_filtering_a_currency_out_and_back_in():
    app = import_samples(start_app(), ["us_brokerage_a.csv", "india_brokerage.csv"])
    app.selectbox(key="price_source").select("Sample reference prices").run()
    app.selectbox(key="holdings_sort:USD").select("Market value").run()
    app.checkbox(key="holdings_descending:USD").check().run()
    original_order = list(overview(app).dataframe[0].value["Ticker"])

    app.selectbox(key="portfolio_exchange").select("NSE").run()
    app.selectbox(key="portfolio_exchange").select(None).run()

    assert not app.exception
    assert app.selectbox(key="holdings_sort:USD").value == "Market value"
    assert app.checkbox(key="holdings_descending:USD").value is True
    assert list(overview(app).dataframe[0].value["Ticker"]) == original_order
    assert len(app.session_state["transactions"]) == 32


def test_market_allocation_without_prices_is_unavailable_not_cost_basis_in_disguise():
    app = import_samples(start_app(), ["us_brokerage_a.csv"])
    app.radio(key="portfolio_allocation").set_value("Market value").run()
    assert not app.exception
    assert len(overview(app).get("plotly_chart")) == 1  # Realized gains only, no allocation pies.
    assert any("Allocation requires prices" in item.value for item in overview(app).info)
    assert next(item for item in overview(app).metric if item.label == "Largest holding · Market value").value == "Unavailable"


def test_dashboard_uses_accepted_transactions_not_staged_previews():
    app = start_app()
    app.button(key="stage_samples").click().run()
    app.button(key="validate_batch").click().run()
    assert not overview(app).metric
    assert any("Import transactions" in item.value for item in overview(app).info)


def test_renderer_accepts_described_test_prices_and_shows_complete_values():
    # Exercise the renderer's price-ready boundary without adding a price loader.
    script = '''
from datetime import date
from decimal import Decimal as D
from stock_portfolio_analyzer.models import Transaction
from stock_portfolio_analyzer.portfolio import SecurityKey
from stock_portfolio_analyzer.portfolio_ui import render_portfolio
rows = [Transaction(date(2025,1,1), "A", "NASDAQ", "AAPL", "BUY", D(10), D(100), "USD", D(5), None, "synthetic", 2, "1", {})]
render_portfolio(rows, prices={SecurityKey("NASDAQ", "AAPL", "USD"): D(120)}, price_description="Synthetic test prices — not live")
'''
    app = AppTest.from_string(script).run(timeout=20)
    assert not app.exception
    assert next(item for item in app.metric if item.label == "Market value").value == "$1,200.00"
    assert next(item for item in app.metric if item.label == "Total gain/loss").value == "$195.00"
    assert next(item for item in app.metric if item.label == "Purchase cost of shares held").value == "$1,005.00"
    assert any("Synthetic test prices" in item.value for item in app.caption)
    app.radio(key="portfolio_allocation").set_value("Market value").run()
    assert len(app.get("plotly_chart")) == 3
    assert any("Portfolio insights" in item.value for item in app.markdown)
    assert any("selected prices" in item.value.lower() for item in app.caption)


def render_history(csv_rows, *, prices=None, description=None):
    frame = load_csv(("Date,Symbol,Action,Shares,Price\n" + csv_rows).encode())
    rows, issues = normalize_rows(frame, ImportSettings(suggest_mapping(frame.columns), "Test", "NASDAQ", "USD"),
                                  source="test.csv", file_id="test")
    assert not issues
    app = AppTest.from_string('''
import streamlit as st
from stock_portfolio_analyzer.portfolio_ui import render_portfolio
render_portfolio(st.session_state["rows"], prices=st.session_state["prices"],
                 price_description=st.session_state["description"])
''')
    app.session_state["rows"] = rows
    app.session_state["prices"] = prices
    app.session_state["description"] = description
    return app.run(timeout=20)


def test_closed_portfolio_preserves_realized_results_without_allocations():
    app = render_history("2025-01-01,AAPL,BUY,10,100\n2025-01-02,AAPL,SELL,10,110\n")
    assert not app.exception
    metrics = {item.label: item.value for item in app.metric}
    assert metrics["Market value"] == "$0.00"
    assert metrics["Realized gain/loss"] == metrics["Total gain/loss"] == "$100.00"
    assert any("No open holdings" in item.value for item in app.info)
    assert not app.dataframe
    assert len(app.get("plotly_chart")) == 1


def test_invalid_history_shows_error_without_partial_metrics():
    app = render_history("2025-01-01,AAPL,SELL,10,100\n")
    assert not app.exception
    assert any("available quantity" in item.value for item in app.error)
    assert not app.metric and not app.get("plotly_chart")


def test_partial_price_subtotal_is_labeled_and_complete_value_stays_unavailable():
    app = render_history("2025-01-01,AAPL,BUY,10,100\n2025-01-01,MSFT,BUY,10,100\n",
                         prices={SecurityKey("NASDAQ", "AAPL", "USD"): D(120)},
                         description="Synthetic test prices — not live")
    assert not app.exception
    assert any("Priced holdings subtotal: \\$1,200.00" in item.value for item in app.caption)
    assert next(item for item in app.metric if item.label == "Market value").value == "Unavailable"
    app.radio(key="portfolio_allocation").set_value("Market value").run()
    assert len(app.get("plotly_chart")) == 1
    app.selectbox(key="holdings_sort:USD").select("Market value").run()
    app.checkbox(key="holdings_descending:USD").check().run()
    assert list(app.dataframe[0].value["Ticker"]) == ["AAPL", "MSFT"]
    assert list(app.dataframe[0].value["Market value"]) == ["$1,200.00", "Unavailable"]


def test_zero_price_portfolio_has_zero_value_but_undefined_market_concentration():
    app = render_history("2025-01-01,AAPL,BUY,10,100\n",
                         prices={SecurityKey("NASDAQ", "AAPL", "USD"): D(0)},
                         description="Synthetic zero-price test — not live")
    app.radio(key="portfolio_allocation").set_value("Market value").run()
    assert not app.exception
    assert next(item for item in app.metric if item.label == "Market value").value == "$0.00"
    assert next(item for item in app.metric if item.label == "Largest holding · Market value").value == "Unavailable"
    assert len(app.get("plotly_chart")) == 1


def test_supplied_prices_without_description_are_not_displayed_as_market_data():
    app = render_history("2025-01-01,AAPL,BUY,10,100\n", prices={SecurityKey("NASDAQ", "AAPL", "USD"): D(120)})
    assert not app.exception
    assert app.error and not app.metric
