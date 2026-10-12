"""Hypothetical single-security shocks. No forecasts or input mutations."""

from dataclasses import dataclass
from decimal import Context, Decimal, ROUND_HALF_EVEN, localcontext

from .portfolio import CalculationError, Portfolio, SecurityKey

_CONTEXT = Context(prec=50, rounding=ROUND_HALF_EVEN)


@dataclass(frozen=True)
class PriceScenario:
    security: SecurityKey
    change_pct: Decimal
    quantity: Decimal
    baseline_price: Decimal
    scenario_price: Decimal
    holding_before: Decimal
    holding_after: Decimal
    change_amount: Decimal
    portfolio_before: Decimal | None
    portfolio_after: Decimal | None
    impact_pct: Decimal | None
    missing_prices: tuple[SecurityKey, ...]


def simulate_price_change(portfolio: Portfolio, security: SecurityKey,
                          change_pct: Decimal) -> PriceScenario:
    """Change one consolidated security in the supplied, already-filtered portfolio.

    All other prices and quantities stay fixed. Portfolio totals and percentages
    use only the security's currency. A missing quote elsewhere withholds complete
    portfolio effects, but the selected priced holding can still be simulated.
    """
    if not isinstance(change_pct, Decimal) or not change_pct.is_finite() or not -100 <= change_pct <= 100:
        raise CalculationError("Hypothetical price change must be a finite Decimal between -100% and +100%.")
    holding = next((position for position in portfolio.securities if position.security == security and position.quantity > 0), None)
    if holding is None:
        raise CalculationError("Select an existing open holding in this portfolio.")
    if holding.price is None:
        raise CalculationError("A baseline price is required for the selected holding.")
    totals = portfolio.totals[security.currency]
    with localcontext(_CONTEXT):
        scenario_price = holding.price * (1 + change_pct / 100)
        before = holding.quantity * holding.price
        after = holding.quantity * scenario_price
        change = after - before
        total_before = totals.market_value
        total_after = None if total_before is None else total_before + change
        impact = None if total_before is None or total_before == 0 else change / total_before * 100
    return PriceScenario(security, change_pct, holding.quantity, holding.price,
                         scenario_price, before, after, change, total_before,
                         total_after, impact, totals.missing_prices)
