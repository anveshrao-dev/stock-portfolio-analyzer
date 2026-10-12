"""Hand-calculated expectations for the account-first portfolio engine."""

from dataclasses import replace
from datetime import date
from decimal import Decimal, Inexact, ROUND_DOWN, localcontext
from pathlib import Path

import pytest

from stock_portfolio_analyzer.models import Transaction
from stock_portfolio_analyzer.portfolio import (
    CalculationError, SecurityKey, calculate_portfolio,
)

D = Decimal
AAPL = SecurityKey("NASDAQ", "AAPL", "USD")


def trade(action="BUY", quantity="10", price="100", fees="0", *, day=1,
          account="Account A", ticker="AAPL", exchange="NASDAQ", currency="USD"):
    return Transaction(
        date=date(2025, 1, day), brokerage=account, exchange=exchange,
        ticker=ticker, action=action, quantity=D(quantity), price=D(price),
        currency=currency, fees=D(fees), transaction_id=None, source="test.csv",
        source_row=day + 1, row_key=f"{account}:{day}", raw={},
    )


def test_empty_portfolio_has_no_invented_currency_or_holdings():
    result = calculate_portfolio([])
    assert result.accounts == ()
    assert result.holdings == ()
    assert result.securities == ()
    assert result.sales == ()
    assert result.totals == {}


def test_two_buys_partial_sale_fee_inclusive_basis_and_valuation():
    # Purchases 1002 + 602 = 1604, 15 shares. Sell 6/15 of basis = 641.60.
    transactions = [trade(fees="2"), trade(quantity="5", price="120", fees="2", day=2),
                    trade("SELL", "6", "130", "3", day=3)]
    result = calculate_portfolio(transactions, prices={AAPL: D("140")})
    holding = result.holdings[0]
    assert holding.quantity == D("9")
    assert holding.cost_basis == D("962.40")
    assert holding.average_cost.quantize(D("0.0001")) == D("106.9333")
    assert holding.realized_gain == D("135.40")
    assert holding.purchase_amount == D("1604")
    assert holding.sales_proceeds == D("777")
    assert holding.market_value == D("1260")
    assert holding.unrealized_gain == D("297.60")
    assert holding.total_gain == D("433")
    assert holding.return_pct.quantize(D("0.01")) == D("30.92")
    sale = result.sales[0]
    assert (sale.quantity, sale.net_proceeds, sale.cost_basis_removed, sale.realized_gain) == (
        D("6"), D("777"), D("641.6"), D("135.4")
    )
    assert sale.source == "test.csv" and sale.source_row == 4
    totals = result.totals["USD"]
    assert (totals.cost_basis, totals.realized_gain, totals.market_value, totals.unrealized_gain, totals.total_gain) == (
        D("962.4"), D("135.4"), D("1260"), D("297.6"), D("433")
    )


def test_full_sale_removes_basis_and_keeps_realized_loss_without_a_price():
    result = calculate_portfolio([trade(fees="2"), trade("SELL", "10", "90", "3", day=2)])
    assert result.holdings == ()
    closed = result.accounts[0]
    assert closed.quantity == closed.cost_basis == 0
    assert closed.average_cost is None and closed.return_pct is None
    assert closed.realized_gain == D("-105")
    assert closed.market_value == closed.unrealized_gain == 0
    totals = result.totals["USD"]
    assert totals.market_value == 0 and totals.total_gain == D("-105")
    assert totals.missing_prices == ()


def test_reopening_does_not_reuse_sold_cost_basis_or_erase_realized_gain():
    result = calculate_portfolio([
        trade(), trade("SELL", "10", "110", "2", day=2),
        trade(quantity="2", price="200", fees="4", day=3),
    ], prices={AAPL: D("190")})
    holding = result.holdings[0]
    assert holding.quantity == 2
    assert holding.cost_basis == 404 and holding.average_cost == 202
    assert holding.realized_gain == 98 and holding.unrealized_gain == -24
    assert holding.total_gain == 74
    assert holding.purchase_amount == 1404 and holding.sales_proceeds == 1098


def test_later_purchase_uses_only_remaining_basis_after_a_sale():
    rows = [trade(), trade("SELL", "5", "120", day=2),
            trade(quantity="5", price="200", day=3), trade("SELL", "2", "160", day=4)]
    holding = calculate_portfolio(rows).holdings[0]
    assert holding.quantity == 8 and holding.cost_basis == 1200
    assert holding.average_cost == 150 and holding.realized_gain == 120


def test_repeating_average_full_close_has_no_residual_basis():
    result = calculate_portfolio([
        trade(quantity="3", price="1", fees="1"),
        trade("SELL", "1", "2", day=2),
        trade("SELL", "1", "2", day=3),
        trade("SELL", "1", "2", day=4),
    ])
    account = result.accounts[0]
    assert account.quantity == 0 and account.cost_basis == 0
    assert abs(account.realized_gain - D("2")) < D("1e-45")
    assert abs(sum(s.cost_basis_removed for s in result.sales) - D("4")) < D("1e-25")


def test_fractional_quantities_and_tiny_remaining_lots_are_not_rounded_away():
    result = calculate_portfolio([
        trade(quantity="0.1", price="10"), trade(quantity="0.2", price="10", day=2),
        trade("SELL", "0.29999999", "12", day=3),
    ], prices={AAPL: D("15")})
    holding = result.holdings[0]
    assert holding.quantity == D("0.00000001")
    assert holding.cost_basis == D("0.00000010")
    assert holding.market_value == D("0.00000015")
    assert holding.unrealized_gain == D("0.00000005")


def test_consolidation_keeps_account_cost_bases_and_sales_separate():
    rows = [trade(), trade(price="200", account="Account B"),
            trade("SELL", "5", "150", day=2)]
    result = calculate_portfolio(rows, prices={AAPL: D("160")})
    assert len(result.accounts) == 2
    account_a, account_b = result.accounts
    assert (account_a.cost_basis, account_a.realized_gain) == (500, 250)
    assert (account_b.cost_basis, account_b.realized_gain) == (2000, 0)
    stock = result.securities[0]
    assert stock.accounts == result.accounts
    assert stock.quantity == 15 and stock.cost_basis == 2500
    assert stock.average_cost.quantize(D("0.01")) == D("166.67")
    assert stock.realized_gain == 250 and stock.market_value == 2400
    assert stock.unrealized_gain == -100 and stock.total_gain == 150
    assert result.totals["USD"].total_gain == 150


def test_closed_account_still_contributes_realized_gains_to_consolidation():
    result = calculate_portfolio([
        trade(), trade("SELL", "10", "120", day=2),
        trade(quantity="5", price="150", account="Account B"),
    ], prices={AAPL: D("160")})
    assert len(result.holdings) == 1
    assert len(result.securities[0].accounts) == 2
    assert result.securities[0].quantity == 5
    assert result.securities[0].realized_gain == 200
    assert result.totals["USD"].total_gain == 250


def test_identical_tickers_on_different_exchanges_or_currencies_do_not_merge():
    nse = SecurityKey("NSE", "AAPL", "INR")
    usd_nse = SecurityKey("NSE", "AAPL", "USD")
    result = calculate_portfolio([
        trade(), trade(quantity="2", price="300", exchange="NSE", currency="INR"),
        trade(quantity="1", price="50", exchange="NSE"),
    ], prices={AAPL: D("110"), nse: D("320"), usd_nse: D("55")})
    assert len(result.securities) == 3
    assert set(result.totals) == {"USD", "INR"}
    assert result.totals["USD"].market_value == 1155
    assert result.totals["INR"].market_value == 640
    assert result.totals["USD"].unrealized_gain == 105
    assert result.totals["INR"].unrealized_gain == 40


def test_missing_price_propagates_without_hiding_the_known_subtotal():
    msft = SecurityKey("NASDAQ", "MSFT", "USD")
    result = calculate_portfolio([trade(), trade(quantity="2", price="200", ticker="MSFT")],
                                 prices={AAPL: D("120")})
    unknown = next(account for account in result.accounts if account.security == msft)
    assert unknown.price is None and unknown.market_value is None
    assert unknown.unrealized_gain is None and unknown.total_gain is None and unknown.return_pct is None
    totals = result.totals["USD"]
    assert totals.cost_basis == 1400
    assert totals.market_value is None and totals.unrealized_gain is None and totals.total_gain is None
    assert totals.known_market_value == 1200
    assert totals.missing_prices == (msft,)


def test_missing_prices_are_unique_and_only_affect_their_currency():
    result = calculate_portfolio([
        trade(), trade(account="Account B"),
        trade(ticker="INFY", exchange="NSE", currency="INR"),
    ], prices={SecurityKey("NSE", "INFY", "INR"): D("110")})
    assert result.totals["USD"].missing_prices == (AAPL,)
    assert result.totals["USD"].market_value is None
    assert result.totals["INR"].market_value == 1100


def test_zero_supplied_price_is_a_total_loss_not_a_missing_price():
    result = calculate_portfolio([trade()], prices={AAPL: D("0")})
    assert result.holdings[0].market_value == 0
    assert result.holdings[0].unrealized_gain == -1000
    assert result.holdings[0].return_pct == -100
    assert result.totals["USD"].missing_prices == ()


@pytest.mark.parametrize("changes", [
    {"action": "DIVIDEND"}, {"quantity": D("-1")}, {"quantity": D("0")},
    {"price": D("0")}, {"price": D("NaN")}, {"fees": D("-1")},
    {"quantity": D("Infinity")}, {"price": 100.0}, {"fees": D("sNaN")},
    {"brokerage": ""}, {"ticker": ""}, {"currency": "EUR"}, {"exchange": "LSE"},
    {"date": "2025-01-01"}, {"action": "buy"},
])
def test_invalid_direct_inputs_raise_instead_of_producing_partial_results(changes):
    with pytest.raises(CalculationError):
        calculate_portfolio([trade(), replace(trade(day=2), **changes)])


@pytest.mark.parametrize("price", [D("-1"), D("NaN"), D("Infinity"), 100.0, None])
def test_invalid_supplied_prices_raise(price):
    with pytest.raises(CalculationError):
        calculate_portfolio([trade()], prices={AAPL: price})


@pytest.mark.parametrize("changes", [{}, {"account": "Other"}, {"exchange": "NYSE"}, {"currency": "INR"}])
def test_oversells_and_cross_account_borrowing_raise(changes):
    quantity = "11" if not changes else "1"
    with pytest.raises(CalculationError, match="available quantity"):
        calculate_portfolio([trade(), trade("SELL", quantity, "110", day=2, **changes)])


def test_date_order_is_stable_and_input_is_not_mutated():
    buy, sell = trade(), trade("SELL", "5", "120", day=2)
    rows = [sell, buy]
    prices = {AAPL: D("110")}
    result = calculate_portfolio(rows, prices=prices)
    assert result.holdings[0].quantity == 5
    assert rows == [sell, buy] and prices == {AAPL: D("110")}
    with pytest.raises(CalculationError, match="available quantity"):
        calculate_portfolio([replace(sell, date=buy.date), buy])


def test_engine_does_not_silently_deduplicate_previously_accepted_trades():
    tx = trade()
    assert calculate_portfolio([tx, tx]).holdings[0].quantity == 20


def test_calculations_do_not_depend_on_or_change_callers_decimal_context():
    rows = [trade(quantity="3", price="1", fees="1"), trade("SELL", "1", "2", day=2)]
    expected = calculate_portfolio(rows, prices={AAPL: D("2")})
    with localcontext() as context:
        context.prec = 6
        context.rounding = ROUND_DOWN
        context.traps[Inexact] = True
        actual = calculate_portfolio(rows, prices={AAPL: D("2")})
        assert actual == expected
        assert context.prec == 6
        assert context.rounding == ROUND_DOWN and context.traps[Inexact]
        average = actual.holdings[0].average_cost
        returns = actual.holdings[0].return_pct
        total_gain = actual.totals["USD"].total_gain
    assert average == expected.holdings[0].average_cost
    assert returns == expected.holdings[0].return_pct
    assert total_gain == expected.totals["USD"].total_gain


def test_same_day_order_controls_the_average_at_each_sale():
    first_buy = trade(quantity="10", price="100")
    later_buy = trade(quantity="10", price="200")
    sell = trade("SELL", "10", "150")
    before = calculate_portfolio([first_buy, sell, later_buy]).holdings[0]
    after = calculate_portfolio([first_buy, later_buy, sell]).holdings[0]
    assert before.cost_basis == 2000 and before.realized_gain == 500
    assert after.cost_basis == 1500 and after.realized_gain == 0


def test_sale_fees_greater_than_proceeds_are_not_silently_clamped():
    result = calculate_portfolio([trade(quantity="1", price="1"), trade("SELL", "1", "1", "2", day=2)])
    assert result.sales[0].net_proceeds == -1
    assert result.sales[0].realized_gain == -2
    assert result.totals["USD"].total_gain == -2


def test_price_from_wrong_exchange_or_currency_does_not_value_a_holding():
    result = calculate_portfolio([trade()], prices={
        SecurityKey("NYSE", "AAPL", "USD"): D("120"),
        SecurityKey("NASDAQ", "AAPL", "INR"): D("9999"),
    })
    assert result.holdings[0].market_value is None
    assert result.totals["USD"].missing_prices == (AAPL,)


@pytest.mark.parametrize("changes", [
    {"quantity": D("1e12")}, {"quantity": D("0.000000001")},
    {"price": D("1e1000000")}, {"fees": D("1e12")},
])
def test_direct_transaction_magnitudes_follow_the_import_contract(changes):
    with pytest.raises(CalculationError):
        calculate_portfolio([replace(trade(), **changes)])


def test_maximum_supported_numbers_keep_their_fractional_cost():
    holding = calculate_portfolio([trade(quantity="999999999999.99999999", price="999999999999.99999999", fees="0.01")]).holdings[0]
    assert holding.cost_basis == D("999999999999999999980000.0100000000000001")


def test_invalid_price_key_is_reported_clearly():
    with pytest.raises(CalculationError, match="SecurityKey"):
        calculate_portfolio([trade()], prices={"AAPL": D("100")})


def test_all_sample_imports_reconstruct_expected_account_and_security_quantities():
    from stock_portfolio_analyzer.csv_import import ImportSettings, load_csv, normalize_rows, suggest_mapping
    from stock_portfolio_analyzer.validation import validate_transactions

    data = Path(__file__).resolve().parents[1] / "data"
    rows = []
    for filename, account, exchange, currency, fmt in [
        ("us_brokerage_a.csv", "US Brokerage A", "NASDAQ", "USD", "%Y-%m-%d"),
        ("us_brokerage_b.csv", "US Brokerage B", "NASDAQ", "USD", "%m/%d/%Y"),
        ("india_brokerage.csv", "Indian Brokerage", "NSE", "INR", "%d/%m/%Y"),
    ]:
        frame = load_csv((data / filename).read_bytes())
        normalized, issues = normalize_rows(frame, ImportSettings(suggest_mapping(frame.columns), account, exchange, currency, fmt),
                                            source=filename, file_id=filename)
        assert not issues
        rows.extend(normalized)
    validated = validate_transactions(rows)
    assert len(validated.accepted) == 48 and not validated.rejected and not validated.duplicates
    result = calculate_portfolio(validated.accepted)
    assert len(result.holdings) == 12
    assert len(result.securities) == 10
    assert {stock.security.ticker: stock.quantity for stock in result.securities} == {
        "AAPL": D("33"), "MSFT": D("17"), "NVDA": D("28"), "AMZN": D("14"),
        "GOOGL": D("18"), "TSLA": D("5"), "RELIANCE": D("28"), "TCS": D("13"),
        "INFY": D("31"), "HDFCBANK": D("25"),
    }
    assert set(result.totals) == {"USD", "INR"}
    assert all(total.market_value is None for total in result.totals.values())
    for total in result.totals.values():
        # Independently reconcile net cash outlay with remaining basis and gain.
        assert abs(total.purchase_amount - total.sales_proceeds - total.cost_basis + total.realized_gain) < D("1e-20")
