"""Offline and provider-error paths through the real Streamlit application."""

from dataclasses import replace
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal as D

from streamlit.testing.v1 import AppTest

from stock_portfolio_analyzer.market_data import MarketData, Quote
from stock_portfolio_analyzer.portfolio import SecurityKey
from test_portfolio import trade
from test_portfolio_ui import start_app, import_samples

AAPL = SecurityKey("NASDAQ", "AAPL", "USD")


def test_yahoo_empty_and_preview_states_explain_import_required_before_fetch(monkeypatch):
    from stock_portfolio_analyzer import market_ui
    calls = []

    def provider(key, start, request_day):
        calls.append(key)
        return MarketData()

    monkeypatch.setattr(market_ui, "cached_yahoo", provider)
    app = start_app()
    app.selectbox(key="price_source").select("Yahoo Finance").run()
    assert app.button(key="fetch_market").disabled
    assert any("Import transactions" in item.value for item in app.sidebar.info)
    assert not any("Expired data" in item.value for item in app.sidebar.info)
    app.button(key="stage_samples").click().run()
    app.button(key="validate_batch").click().run()
    assert app.button(key="fetch_market").disabled
    assert any("Import" in item.value for item in app.sidebar.info)
    assert not calls
    app.button(key="import_batch").click().run()
    assert not app.button(key="fetch_market").disabled
    assert any("16 imported transactions" in item.value and "4 securities" in item.value for item in app.sidebar.caption)
    app.button(key="fetch_market").click().run()
    assert len(calls) == 4 and not app.exception


def test_no_prices_default_displays_lifetime_metrics_but_no_fabricated_history():
    app = import_samples(start_app(), ["us_brokerage_a.csv"])
    history = app.tabs[2]
    assert not app.exception
    assert app.selectbox(key="price_source").value == "Cost basis only"
    assert any(metric.label == "Lifetime purchases (including fees)" for metric in history.metric)
    assert not history.get("plotly_chart")
    assert any("historical prices" in note.value.lower() for note in history.info)


def test_offline_mode_values_both_currencies_and_cannot_fabricate_daily_history():
    app = import_samples(start_app(), ["us_brokerage_a.csv", "us_brokerage_b.csv", "india_brokerage.csv"])
    app.selectbox(key="price_source").select("Sample reference prices").run()
    assert not app.exception
    values = [metric.value for metric in app.tabs[1].metric if metric.label == "Market value"]
    assert len(values) == 2 and all(value != "Unavailable" for value in values)
    assert any("2026-09-30" in note.value for note in app.tabs[1].caption)
    assert any("Sample reference prices" in note.value for note in app.tabs[2].caption)
    assert not app.tabs[2].get("plotly_chart")
    assert any("historical prices" in note.value.lower() for note in app.tabs[2].info)
    app.selectbox(key="price_source").select("Cost basis only").run()
    assert all(metric.value == "Unavailable" for metric in app.tabs[1].metric if metric.label == "Market value")


def test_yahoo_fetch_is_explicit_and_errors_preserve_transactions(monkeypatch):
    from stock_portfolio_analyzer import market_ui
    calls = []
    def unavailable(key, start, request_day):
        calls.append(key)
        return MarketData(errors=("Provider unavailable",))
    monkeypatch.setattr(market_ui, "cached_yahoo", unavailable)
    app = import_samples(start_app(), ["us_brokerage_a.csv"])
    app.selectbox(key="price_source").select("Yahoo Finance").run()
    assert not calls
    app.button(key="fetch_market").click().run()
    assert not app.exception and len(calls) == 4
    assert any("Provider unavailable" in item.value for item in app.warning)
    assert len(app.session_state["transactions"]) == 16


def test_market_messages_deduplicate_errors_and_scope_xirr_only_blockers(monkeypatch):
    from stock_portfolio_analyzer import market_ui

    split = "Stock split detected; valuation withheld."
    dividend = "Dividends detected; XIRR is unavailable."

    def provider(key, start, request_day):
        if key.ticker == "AAPL":
            return MarketData(errors=(split,), performance_blockers=(split,))
        return MarketData(performance_blockers=(dividend,))

    monkeypatch.setattr(market_ui, "cached_yahoo", provider)
    app = import_samples(start_app(), ["us_brokerage_a.csv"])
    app.selectbox(key="price_source").select("Yahoo Finance").run()
    app.button(key="fetch_market").click().run()

    assert sum(split in item.value for item in app.sidebar.warning) == 1
    assert not any(dividend in item.value for item in app.sidebar.warning)
    assert any(dividend in item.value for item in app.sidebar.caption)
    assert not any(split in item.value for item in app.sidebar.caption)


def test_no_prices_mode_explains_that_market_values_need_a_price_source():
    app = start_app()

    assert app.selectbox(key="price_source").value == "Cost basis only"
    assert any(
        "Purchase costs" in item.value
        and "market prices" in item.value
        for item in app.sidebar.info
    )


def test_quote_audit_and_complete_history_are_rendered_with_confirmed_xirr(monkeypatch):
    from stock_portfolio_analyzer import market_ui
    # One year: paid 1000, terminal 1100 -> 10%, independently known.
    rows = [replace(trade(), date=date(2023, 1, 1))]
    def provider(key, start, request_day):
        return MarketData(quote=Quote(D("120"), date(2024, 1, 2), "Yahoo Finance", datetime(2024, 1, 2, 21, tzinfo=timezone.utc)),
                          closes={date(2023, 1, 1): D("100"), date(2024, 1, 1): D("110")})
    monkeypatch.setattr(market_ui, "cached_yahoo", provider)
    app = start_app()
    app.session_state["transactions"] = rows
    app.run()
    app.selectbox(key="price_source").select("Yahoo Finance").run()
    app.button(key="fetch_market").click().run()
    assert not app.exception and len(app.tabs[2].get("plotly_chart")) == 1
    assert any("2024-01-02" in str(table.value) for table in app.sidebar.dataframe)
    assert not any(item.label == "Annualized return (XIRR)" for item in app.tabs[2].metric)
    assert any("Confirm" in item.value and "XIRR" in item.value for item in app.tabs[2].info)
    app.checkbox(key="confirm_history").check().run()
    assert next(item.value for item in app.tabs[2].metric if item.label == "Annualized return (XIRR)") == "10.00%"
    assert any("2024-01-01" in item.value for item in app.tabs[2].caption)


def test_missing_security_history_does_not_show_only_the_early_portfolio_as_a_trend(monkeypatch):
    from stock_portfolio_analyzer import market_ui

    def provider(key, start, request_day):
        if key.ticker == "MSFT":
            return MarketData(errors=("Stock split detected; valuation withheld.",))
        return MarketData(
            quote=Quote(D("120"), date(2025, 1, 10), "Yahoo Finance"),
            closes={date(2025, 1, day): D("100") for day in range(1, 11)},
        )

    monkeypatch.setattr(market_ui, "cached_yahoo", provider)
    app = start_app()
    app.session_state["transactions"] = [trade(), trade(ticker="MSFT", day=4)]
    app.run()
    app.selectbox(key="price_source").select("Yahoo Finance").run()
    app.button(key="fetch_market").click().run()

    assert not app.exception
    assert not app.tabs[2].get("plotly_chart")
    assert any("MSFT" in item.value and "historical prices" in item.value
               for item in app.tabs[2].info)
    assert len(app.session_state["transactions"]) == 2
    # Removing the unpriced security from the selected scope restores its peer's chart.
    app.session_state["transactions"] = [trade()]
    app.run()
    app.button(key="fetch_market").click().run()
    assert len(app.tabs[2].get("plotly_chart")) == 1


def test_new_import_invalidates_download_without_automatic_network(monkeypatch):
    from stock_portfolio_analyzer import market_ui
    calls = []
    def provider(key, start, request_day):
        calls.append(key)
        return MarketData()
    monkeypatch.setattr(market_ui, "cached_yahoo", provider)
    app = import_samples(start_app(), ["us_brokerage_a.csv"])
    app.selectbox(key="price_source").select("Yahoo Finance").run()
    app.button(key="fetch_market").click().run()
    app = import_samples(app, ["india_brokerage.csv"])
    assert len(calls) == 4
    assert any("Load market data" in item.value for item in app.sidebar.info)


def test_expired_session_quotes_are_withheld_until_explicit_reload(monkeypatch):
    from stock_portfolio_analyzer import market_ui
    monkeypatch.setattr(market_ui, "cached_yahoo", lambda *args: MarketData())
    app = import_samples(start_app(), ["us_brokerage_a.csv"])
    app.selectbox(key="price_source").select("Yahoo Finance").run()
    app.button(key="fetch_market").click().run()
    signature, loaded, data = app.session_state["yahoo_result"]
    app.session_state["yahoo_result"] = (signature, loaded - timedelta(minutes=16), data)
    app.run()
    assert any("Load market data" in item.value for item in app.sidebar.info)


def test_historical_scope_does_not_mix_currency_or_omit_purchase_rows():
    app = import_samples(start_app(), ["us_brokerage_a.csv", "india_brokerage.csv"])
    app.selectbox(key="history_exchange").select("NSE").run()
    assert not app.exception
    assert [item.value for item in app.tabs[2].subheader] == ["INR performance"]
    assert any("Exchange: NSE" in item.value for item in app.tabs[2].caption)
    assert len(app.session_state["transactions"]) == 32


def test_xirr_history_confirmation_resets_when_selected_history_changes():
    app = import_samples(start_app(), ["us_brokerage_a.csv", "india_brokerage.csv"])
    app.checkbox(key="confirm_history").check().run()
    app.selectbox(key="history_exchange").select("NSE").run()
    assert not app.checkbox(key="confirm_history").value
    app.checkbox(key="confirm_history").check().run()
    app.selectbox(key="history_brokerage").select("US Brokerage A").run()
    assert not app.tabs[2].metric
    app.selectbox(key="history_exchange").select(None).run()
    assert not app.exception
    assert not app.checkbox(key="confirm_history").value


def test_offline_quote_before_transaction_is_withheld_and_explained():
    app = start_app()
    app.session_state["transactions"] = [replace(trade(), date=date(2026, 10, 1))]
    app.run()
    app.selectbox(key="price_source").select("Sample reference prices").run()
    assert not app.exception
    assert next(metric.value for metric in app.tabs[1].metric if metric.label == "Market value") == "Unavailable"
    assert any("predates" in item.value for item in app.warning)


def test_all_offline_samples_show_unique_xirr_despite_interleaved_buys_and_sells():
    app = import_samples(start_app(), ["us_brokerage_a.csv", "us_brokerage_b.csv", "india_brokerage.csv"])
    app.selectbox(key="price_source").select("Sample reference prices").run()
    app.checkbox(key="confirm_history").check().run()
    assert not app.exception
    values = [item.value for item in app.tabs[2].metric if item.label == "Annualized return (XIRR)"]
    assert values == ["8.21%", "15.72%"]


def test_yahoo_prices_with_sample_trades_retain_transaction_provenance():
    app = import_samples(start_app(), ["us_brokerage_a.csv"])
    app.selectbox(key="price_source").select("Yahoo Finance").run()
    for panel in app.tabs[1:]:
        assert any("Sample transactions" in note.value and "Yahoo Finance" in note.value
                   for note in panel.caption)
