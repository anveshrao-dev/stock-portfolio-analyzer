"""Independent cash-flow answers and end-of-day quantities."""

from dataclasses import replace
from datetime import date
from decimal import Decimal as D

import pytest

from stock_portfolio_analyzer import performance as pf
from stock_portfolio_analyzer.market_data import MarketData, Quote
from stock_portfolio_analyzer.portfolio import CalculationError, SecurityKey
from test_portfolio import trade

AAPL = SecurityKey("NASDAQ", "AAPL", "USD")
MSFT = SecurityKey("NASDAQ", "MSFT", "USD")


def day(n):
    return date(2025, 1, n)


def test_history_uses_end_of_day_quantities_and_sale_proceeds_are_not_cash_holdings():
    rows = [trade(fees="2"), trade("SELL", "4", "120", "1", day=2),
            trade("SELL", "6", "130", day=3)]
    data = {AAPL: MarketData(closes={day(1): D("110"), day(2): D("125")})}
    points = pf.historical_values(rows, data)["USD"]
    assert [(p.day, p.value) for p in points] == [(day(1), D("1100")), (day(2), D("750")), (day(3), D(0))]


def test_partial_history_has_gaps_without_backfill_or_lookahead():
    rows = [trade(), trade(ticker="MSFT")]
    data = {AAPL: MarketData(closes={day(1): D("110"), day(2): D("120")}),
            MSFT: MarketData(closes={day(2): D("200"), day(3): D("210")})}
    points = pf.historical_values(rows, data)["USD"]
    assert [p.value for p in points] == [None, D("3200"), None]
    assert points[0].missing == (MSFT,)
    assert points[2].missing == (AAPL,)


def test_missing_provider_session_is_retained_as_gap_even_without_trades_that_day():
    data = {AAPL: MarketData(closes={day(1): D("100"), day(3): D("120")},
                            sessions=(day(1), day(2), day(3)))}
    assert [point.value for point in pf.historical_values([trade()], data)["USD"]] == [D("1000"), None, D("1200")]


def test_history_separates_currencies_accounts_and_identical_exchange_tickers():
    india = SecurityKey("NSE", "AAPL", "INR")
    nyse = SecurityKey("NYSE", "AAPL", "USD")
    rows = [trade(), trade(account="B"), trade(exchange="NYSE"), trade(exchange="NSE", currency="INR")]
    result = pf.historical_values(rows, {
        AAPL: MarketData(closes={day(1): D("110")}),
        nyse: MarketData(closes={day(1): D("200")}),
        india: MarketData(closes={day(1): D("500")})})
    assert result["USD"][0].value == D("4200")
    assert result["INR"][0].value == D("5000")


def test_invalid_history_and_prices_raise_instead_of_returning_partial_trend():
    with pytest.raises(CalculationError):
        pf.historical_values([trade("SELL")], {})
    with pytest.raises(CalculationError):
        pf.historical_values([trade()], {AAPL: MarketData(closes={day(1): D("NaN")})})


@pytest.mark.parametrize("last,expected", [("1100", .1), ("800", -.2), ("1000", 0)])
def test_xirr_known_one_year_gain_loss_and_zero(last, expected):
    result = pf.xirr([(date(2023, 1, 1), D("-1000")), (date(2024, 1, 1), D(last))])
    assert result.rate == pytest.approx(expected, abs=1e-10)
    assert result.reason is None


def test_xirr_actual_day_count_leap_year_and_same_day_aggregation():
    result = pf.xirr([(date(2024, 1, 1), D("-600")), (date(2024, 1, 1), D("-400")),
                     (date(2025, 1, 1), D("1100"))])
    assert result.rate == pytest.approx(1.1 ** (365 / 366) - 1, abs=1e-10)


def test_xirr_accepts_alternating_cash_flows_when_exactly_one_root_exists():
    # NPV at 10% is -1000 + 100/1.1 - 100/1.1² + 1320/1.1³ = 0.
    result = pf.xirr([
        (date(2021, 1, 1), D("-1000")),
        (date(2022, 1, 1), D("100")),
        (date(2023, 1, 1), D("-100")),
        (date(2024, 1, 1), D("1320")),
    ])
    assert result.rate == pytest.approx(.1, abs=1e-10)
    assert result.reason is None


def test_xirr_withholds_three_distinct_roots_as_ambiguous():
    # In q=1/(1+r), NPV = 1000(q-.5)(q-.8)(q-.9): three valid roots.
    result = pf.xirr([
        (date(2021, 1, 1), D("-360")),
        (date(2022, 1, 1), D("1570")),
        (date(2023, 1, 1), D("-2200")),
        (date(2024, 1, 1), D("1000")),
    ])
    assert result.rate is None
    assert "multiple" in result.reason.lower()


@pytest.mark.parametrize("flows", [[], [(day(1), D("-100"))],
    [(day(1), D("-100")), (day(1), D("110"))],
    [(day(1), D("-100")), (day(2), D("230")), (day(3), D("-132"))],
    [(day(1), D("NaN")), (day(2), D("100"))],
])
def test_xirr_unreliable_cases_have_reason_instead_of_a_number(flows):
    result = pf.xirr(flows)
    assert result.rate is None and result.reason


def test_xirr_dated_terminal_value_and_trade_fees():
    buy = replace(trade(price="99.8", fees="2"), date=date(2023, 1, 1))
    data = {AAPL: MarketData(closes={date(2024, 1, 1): D("110")})}
    result = pf.portfolio_xirr([buy], data, "USD", confirmed=True)
    assert result.rate == pytest.approx(.1, abs=1e-10)
    assert result.valuation_date == date(2024, 1, 1)


def test_xirr_requires_confirmation_complete_common_terminal_and_no_blockers():
    buy = trade()
    data = {AAPL: MarketData(closes={day(2): D("110")})}
    assert pf.portfolio_xirr([buy], data, "USD", confirmed=False).rate is None
    assert pf.portfolio_xirr([buy, trade(ticker="MSFT")], data, "USD", confirmed=True).rate is None
    data[AAPL] = MarketData(closes={day(2): D("110")}, performance_blockers=("Dividends",))
    assert "Dividends" in pf.portfolio_xirr([buy], data, "USD", confirmed=True).reason


def test_closed_portfolio_xirr_needs_no_quote_and_includes_net_sale_fees():
    rows = [replace(trade(), date=date(2023, 1, 1)),
            replace(trade("SELL", "10", "110.2", "2"), date=date(2024, 1, 1))]
    result = pf.portfolio_xirr(rows, {}, "USD", confirmed=True)
    assert result.rate == pytest.approx(.1, abs=1e-10)


def test_offline_reference_supports_dated_synthetic_xirr_but_not_a_trend():
    buy = replace(trade(), date=date(2023, 1, 1))
    data = {AAPL: MarketData(quote=Quote(D("110"), date(2024, 1, 1), "SYNTHETIC", synthetic=True))}
    result = pf.portfolio_xirr([buy], data, "USD", confirmed=True)
    assert result.rate == pytest.approx(.1, abs=1e-10)
    assert pf.historical_values([buy], data)["USD"][0].value is None


def test_old_terminal_prices_do_not_omit_later_trades_from_xirr():
    data = {AAPL: MarketData(closes={day(1): D("100")})}
    result = pf.portfolio_xirr([trade(), trade(day=2)], data, "USD", confirmed=True)
    assert result.rate is None and result.reason


def test_quotes_from_different_dates_cannot_be_combined_into_terminal_value():
    data = {AAPL: MarketData(quote=Quote(D("110"), day(2), "SYNTHETIC", synthetic=True)),
            MSFT: MarketData(quote=Quote(D("110"), day(3), "SYNTHETIC", synthetic=True))}
    assert pf.portfolio_xirr([trade(), trade(ticker="MSFT")], data, "USD", confirmed=True).rate is None


def test_irregular_cashflows_known_root_and_extreme_rates_do_not_overflow():
    # r=0 gives zero NPV regardless of dates when deposits and withdrawals sum to 0.
    flows = [(date(2020, 2, 1), D("-1000")), (date(2022, 7, 3), D("-500")),
             (date(2023, 1, 20), D("700")), (date(2025, 4, 2), D("800"))]
    assert pf.xirr(flows).rate == pytest.approx(0, abs=1e-10)
    assert pf.xirr([(day(1), D("-0.00000001")), (day(2), D("999999999999"))]).rate is None
