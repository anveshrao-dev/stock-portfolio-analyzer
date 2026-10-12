"""Dashboard transformations and charts must reflect account-first accounting."""

from datetime import date
from decimal import Decimal as D

import pytest

from stock_portfolio_analyzer.models import Transaction
from stock_portfolio_analyzer.portfolio import SecurityKey, calculate_portfolio
from stock_portfolio_analyzer.dashboard import (
    allocation_data, allocation_figure, concentration, format_money,
    gains_figure, holdings_frame, portfolio_insights, select_transactions,
    transaction_summary,
)


def trade(account="A", ticker="AAPL", exchange="NASDAQ", currency="USD",
          quantity="10", price="100", action="BUY", day=1):
    return Transaction(date(2025, 1, day), account, exchange, ticker, action,
                       D(quantity), D(price), currency, D(0), None,
                       "test.csv", day + 1, f"{account}:{day}", {})


def test_filters_select_whole_account_histories_and_allow_empty_intersections():
    rows = [trade(), trade(action="SELL", quantity="5", day=2),
            trade(account="B"), trade(account="India", exchange="NSE", currency="INR")]
    selected = select_transactions(rows, brokerage="A", exchange="NASDAQ")
    assert selected == rows[:2]
    assert calculate_portfolio(selected).holdings[0].quantity == 5
    assert select_transactions(rows, brokerage="A", exchange="NSE") == []
    assert select_transactions(rows) == rows
    assert len(rows) == 4


def test_transaction_summary_describes_imported_history_without_market_data():
    rows = [trade(day=3), trade(ticker="MSFT", action="SELL", day=9),
            trade(account="B", day=5)]

    summary = transaction_summary(rows)

    assert summary.transaction_count == 3
    assert summary.security_count == 2
    assert summary.first_date == date(2025, 1, 3)
    assert summary.last_date == date(2025, 1, 9)
    assert summary.buy_count == 2
    assert summary.sell_count == 1


def test_portfolio_insights_compare_recorded_cost_with_selected_prices():
    portfolio = calculate_portfolio(
        [trade(ticker="AAPL"), trade(ticker="MSFT")],
        prices={SecurityKey("NASDAQ", "AAPL", "USD"): D("120"),
                SecurityKey("NASDAQ", "MSFT", "USD"): D("90")},
    )

    insights = portfolio_insights(portfolio, "USD")

    assert insights.open_position_return_pct == D("5")
    assert insights.best.security.ticker == "AAPL"
    assert insights.best.return_pct == D("20")
    assert insights.worst.security.ticker == "MSFT"
    assert insights.worst.return_pct == D("-10")
    assert insights.largest_move.security.ticker == "AAPL"
    assert insights.largest_move.unrealized_gain == D("200")


def test_portfolio_insights_are_unavailable_when_any_open_price_is_missing():
    portfolio = calculate_portfolio(
        [trade(ticker="AAPL"), trade(ticker="MSFT")],
        prices={SecurityKey("NASDAQ", "AAPL", "USD"): D("120")},
    )

    assert portfolio_insights(portfolio, "USD") is None


def test_numeric_holdings_sort_uses_exact_values_with_missing_values_last():
    rows = [trade(ticker="LOW", quantity="2"), trade(ticker="HIGH", quantity="10"), trade(ticker="MISSING")]
    result = calculate_portfolio(rows, prices={SecurityKey("NASDAQ", "LOW", "USD"): D("9"),
                                             SecurityKey("NASDAQ", "HIGH", "USD"): D("100")})
    ascending = holdings_frame(result, "USD", consolidated=True, sort_by="Market value")
    descending = holdings_frame(result, "USD", consolidated=True, sort_by="Market value", descending=True)
    assert list(ascending["Ticker"]) == ["LOW", "HIGH", "MISSING"]
    assert list(descending["Ticker"]) == ["HIGH", "LOW", "MISSING"]
    assert list(holdings_frame(result, "USD", consolidated=True, sort_by="Quantity")["Quantity"]) == ["2", "10", "10"]


def test_account_and_consolidated_tables_preserve_overlapping_accounts():
    result = calculate_portfolio([trade(), trade(account="B", quantity="5", price="200")])
    accounts = holdings_frame(result, "USD", consolidated=False)
    combined = holdings_frame(result, "USD", consolidated=True)
    assert len(accounts) == 2 and len(combined) == 1
    assert set(accounts["Brokerage"]) == {"A", "B"}
    assert combined.iloc[0]["Brokerage"] == "A, B"
    assert combined.iloc[0]["Quantity"] == "15"
    assert combined.iloc[0]["Purchase cost of shares held"] == "$2,000.00"
    assert combined.iloc[0]["Average purchase cost"] == "$133.33"
    assert combined.iloc[0]["Selected price"] == "Unavailable"
    assert combined.iloc[0]["Unrealized return %"] == "Unavailable"


def test_holdings_table_excludes_closed_positions_but_realized_chart_keeps_them():
    result = calculate_portfolio([trade(), trade(action="SELL", price="110", day=2)])
    assert holdings_frame(result, "USD", consolidated=True).empty
    figure = gains_figure(result, "USD")
    assert list(figure.data[0].x) == ["AAPL · NASDAQ"]
    assert list(figure.data[0].y) == [100.0]
    assert list(figure.data[1].y) == [0.0]


def test_cost_allocation_consolidates_securities_and_keeps_brokerages_separate():
    result = calculate_portfolio([trade(), trade(account="B", quantity="5"), trade(ticker="MSFT", quantity="5")])
    stocks = allocation_data(result, "USD", basis="Cost basis", group="security")
    accounts = allocation_data(result, "USD", basis="Cost basis", group="brokerage")
    assert stocks == {"AAPL · NASDAQ": D("1500"), "MSFT · NASDAQ": D("500")}
    assert accounts == {"A": D("1500"), "B": D("500")}
    assert concentration(stocks) == ("AAPL · NASDAQ", D("75"))


def test_market_allocation_uses_complete_prices_and_currency_denominators():
    result = calculate_portfolio([trade(), trade(ticker="MSFT"), trade(exchange="NSE", currency="INR")], prices={
        SecurityKey("NASDAQ", "AAPL", "USD"): D("120"),
        SecurityKey("NASDAQ", "MSFT", "USD"): D("80"),
        SecurityKey("NSE", "AAPL", "INR"): D("3000"),
    })
    usd = allocation_data(result, "USD", basis="Market value", group="security")
    inr = allocation_data(result, "INR", basis="Market value", group="security")
    assert sum(usd.values()) == 2000 and sum(inr.values()) == 30000
    assert concentration(usd) == ("AAPL · NASDAQ", D("60"))
    figure = allocation_figure(usd, "USD", "Allocation by stock · Market value")
    assert list(figure.data[0].values) == [1200.0, 800.0]
    assert "USD" in figure.layout.title.text


def test_partial_prices_do_not_make_complete_allocation_or_zero_unrealized_bars():
    result = calculate_portfolio([trade(), trade(ticker="MSFT")], prices={SecurityKey("NASDAQ", "AAPL", "USD"): D("120")})
    assert allocation_data(result, "USD", basis="Market value", group="security") is None
    assert allocation_data(result, "USD", basis="Market value", group="brokerage") is None
    assert concentration(None) is None
    figure = gains_figure(result, "USD")
    assert list(figure.data[1].y) == [200.0, None]
    assert "USD" in figure.layout.yaxis.title.text


def test_zero_market_value_has_no_concentration_or_pie():
    result = calculate_portfolio([trade()], prices={SecurityKey("NASDAQ", "AAPL", "USD"): D(0)})
    allocations = allocation_data(result, "USD", basis="Market value", group="security")
    assert allocations == {"AAPL · NASDAQ": D(0)}
    assert concentration(allocations) is None


@pytest.mark.parametrize("value,currency,expected", [
    (D("1234567.895"), "USD", "$1,234,567.90"),
    (D("1234567.895"), "INR", "₹12,34,567.90"),
    (D("-1234.50"), "INR", "-₹1,234.50"),
    (D("-0.001"), "USD", "$0.00"),
    (None, "USD", "Unavailable"),
])
def test_currency_formatting(value, currency, expected):
    assert format_money(value, currency) == expected


def test_exchange_labels_prevent_identical_tickers_from_merging_in_charts():
    result = calculate_portfolio([trade(), trade(exchange="NYSE")])
    assert allocation_data(result, "USD", basis="Cost basis", group="security") == {
        "AAPL · NASDAQ": D(1000), "AAPL · NYSE": D(1000),
    }


def test_priced_holdings_show_losses_and_percentages_correctly():
    result = calculate_portfolio([trade()], prices={SecurityKey("NASDAQ", "AAPL", "USD"): D("90")})
    frame = holdings_frame(result, "USD", consolidated=False)
    assert frame.iloc[0]["Market value"] == "$900.00"
    assert frame.iloc[0]["Unrealized gain/loss"] == "-$100.00"
    assert frame.iloc[0]["Unrealized return %"] == "-10.00%"
