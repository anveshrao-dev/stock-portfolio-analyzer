"""Price-source behavior independent of the rest of the application layout."""

from datetime import date
from decimal import Decimal

import pytest

from streamlit.testing.v1 import AppTest

from stock_portfolio_analyzer import market_ui
from stock_portfolio_analyzer.market_data import MarketData, Quote
from stock_portfolio_analyzer.portfolio import SecurityKey
from test_portfolio import trade


def price_app():
    app = AppTest.from_string('''
import streamlit as st
from stock_portfolio_analyzer.market_ui import render_price_controls
st.session_state['selection'] = render_price_controls(st.session_state['rows'])
''')
    app.session_state['rows'] = [trade()]
    return app.run()


def test_reference_description_uses_loaded_date_not_a_hardcoded_date(monkeypatch):
    monkeypatch.setattr(market_ui, 'load_synthetic', lambda path: {
        SecurityKey('NASDAQ', 'AAPL', 'USD'): MarketData(
            quote=Quote(Decimal('120'), date(2026, 8, 3), 'SAMPLE - fictional', synthetic=True))
    })
    app = price_app()
    app.selectbox(key='price_source').select('Sample reference prices').run()
    selection = app.session_state['selection']
    assert '2026-08-03' in selection.description
    assert 'fictional' in selection.description
    assert selection.data


@pytest.mark.parametrize('blockers', [(), ('Dividends detected; XIRR unavailable',)])
def test_failed_quote_requests_are_retryable_without_waiting_fifteen_minutes(monkeypatch, blockers):
    market_ui.cached_yahoo.clear()
    results = iter([MarketData(errors=('Temporary provider outage',), performance_blockers=blockers),
                    MarketData(quote=Quote(Decimal('120'), date(2026, 9, 30), 'Yahoo'))])
    monkeypatch.setattr(market_ui, 'fetch_yahoo', lambda *args: next(results))
    app = price_app()
    app.selectbox(key='price_source').select('Yahoo Finance').run()
    app.button(key='fetch_market').click().run()
    assert not app.session_state['selection'].data[SecurityKey('NASDAQ', 'AAPL', 'USD')].quote
    app.button(key='fetch_market').click().run()
    assert app.session_state['selection'].data[SecurityKey('NASDAQ', 'AAPL', 'USD')].quote.price == 120
    market_ui.cached_yahoo.clear()
