"""Hand-calculated hypothetical shocks, with unchanged accounting inputs."""

from decimal import Decimal as D, Inexact, localcontext

import pytest

from stock_portfolio_analyzer import risk
from stock_portfolio_analyzer.portfolio import CalculationError, SecurityKey, calculate_portfolio
from test_portfolio import trade

AAPL = SecurityKey("NASDAQ", "AAPL", "USD")
MSFT = SecurityKey("NASDAQ", "MSFT", "USD")


@pytest.mark.parametrize("change,price,holding,total,impact", [
    ("20", "120", "1200", "3200", "6.6666666667"),
    ("-25", "75", "750", "2750", "-8.3333333333"),
    ("-100", "0", "0", "2000", "-33.3333333333"),
    ("100", "200", "2000", "4000", "33.3333333333"),
    ("0", "100", "1000", "3000", "0"),
])
def test_price_shock_has_correct_holding_and_portfolio_effect(change, price, holding, total, impact):
    portfolio = calculate_portfolio([trade(), trade(ticker="MSFT")], prices={AAPL: D("100"), MSFT: D("200")})
    result = risk.simulate_price_change(portfolio, AAPL, D(change))
    assert result.baseline_price == D("100") and result.holding_before == D("1000")
    assert result.scenario_price == D(price) and result.holding_after == D(holding)
    assert result.portfolio_before == D("3000") and result.portfolio_after == D(total)
    assert result.impact_pct.quantize(D(".0000000001")) == D(impact)
    assert result.change_amount == D(holding) - D("1000")
    assert portfolio.holdings[0].price == D("100") and portfolio.totals["USD"].market_value == D("3000")


def test_shock_consolidates_accounts_without_merging_exchanges_or_currencies():
    nyse = SecurityKey("NYSE", "AAPL", "USD")
    india = SecurityKey("NSE", "AAPL", "INR")
    rows = [trade(), trade(account="B", quantity="5"), trade(exchange="NYSE"),
            trade(exchange="NSE", currency="INR")]
    portfolio = calculate_portfolio(rows, prices={AAPL: D("100"), nyse: D("50"), india: D("900")})
    result = risk.simulate_price_change(portfolio, AAPL, D("10"))
    assert result.quantity == D("15") and result.holding_after == D("1650")
    assert result.portfolio_before == D("2000") and result.portfolio_after == D("2150")
    assert result.impact_pct == D("7.5")
    assert portfolio.totals["INR"].market_value == D("9000")


def test_missing_other_price_allows_holding_scenario_but_never_partial_total():
    portfolio = calculate_portfolio([trade(), trade(ticker="MSFT")], prices={AAPL: D("100")})
    result = risk.simulate_price_change(portfolio, AAPL, D("10"))
    assert result.holding_after == D("1100") and result.change_amount == D("100")
    assert result.portfolio_before is result.portfolio_after is result.impact_pct is None
    assert result.missing_prices == (MSFT,)


def test_zero_price_and_zero_portfolio_do_not_divide_by_zero():
    result = risk.simulate_price_change(calculate_portfolio([trade()], prices={AAPL: D(0)}), AAPL, D("50"))
    assert result.holding_after == result.portfolio_after == D(0)
    assert result.impact_pct is None


def test_fractional_shares_and_context_do_not_round_scenario_early():
    portfolio = calculate_portfolio([trade(quantity="0.125")], prices={AAPL: D("0.12345678")})
    with localcontext() as context:
        context.prec = 3
        context.traps[Inexact] = True
        result = risk.simulate_price_change(portfolio, AAPL, D("12.5"))
    assert result.scenario_price == D("0.1388888775")
    assert result.holding_after == D("0.0173611096875")
    assert result.impact_pct == D("12.5")


@pytest.mark.parametrize("change", [D("-101"), D("101"), D("NaN"), D("Infinity"), 10, "10"])
def test_invalid_changes_raise_clearly(change):
    portfolio = calculate_portfolio([trade()], prices={AAPL: D("100")})
    with pytest.raises(CalculationError):
        risk.simulate_price_change(portfolio, AAPL, change)


def test_unknown_closed_and_unpriced_holdings_are_rejected():
    for portfolio, security in [
        (calculate_portfolio([trade()]), AAPL),
        (calculate_portfolio([trade()], prices={AAPL: D("100")}), MSFT),
        (calculate_portfolio([trade(), trade("SELL", day=2)]), AAPL),
    ]:
        with pytest.raises(CalculationError):
            risk.simulate_price_change(portfolio, security, D("10"))
