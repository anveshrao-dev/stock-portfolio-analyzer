"""Stock-only history and conservative trade-cash-flow XIRR (actual/365)."""

import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Context, Decimal, localcontext
from typing import Mapping, Sequence

from .market_data import MarketData
from .models import Transaction
from .portfolio import CalculationError, SecurityKey, calculate_portfolio

_CONTEXT = Context(prec=50)


@dataclass(frozen=True)
class HistoricalValue:
    day: date
    value: Decimal | None
    missing: tuple[SecurityKey, ...] = ()


@dataclass(frozen=True)
class XirrResult:
    rate: float | None = None
    reason: str | None = None
    valuation_date: date | None = None


def historical_values(transactions: Sequence[Transaction], data: Mapping[SecurityKey, MarketData]) -> dict[str, tuple[HistoricalValue, ...]]:
    """Use exact-date completed closes and end-of-day quantities; never fill gaps.

    Values exclude account cash, sale proceeds, and dividends. A value trend is
    affected by trades and is not a return index. Missing quotes stay None.
    """
    calculate_portfolio(transactions)
    result = {}
    with localcontext(_CONTEXT):
        for currency in sorted({tx.currency for tx in transactions}):
            rows = sorted((tx for tx in transactions if tx.currency == currency), key=lambda tx: tx.date)
            keys = {SecurityKey(tx.exchange, tx.ticker, tx.currency) for tx in rows}
            days = {tx.date for tx in rows}
            for key in keys:
                item = data.get(key, MarketData())
                days.update(day for day in item.sessions if day >= rows[0].date)
                for day, price in item.closes.items():
                    if type(day) is not date:
                        raise CalculationError("Historical price keys must be dates.")
                    calculate_portfolio([], prices={key: price})
                    if day >= rows[0].date:
                        days.add(day)
            quantities = defaultdict(Decimal)
            cursor = 0
            points = []
            for day in sorted(days):
                while cursor < len(rows) and rows[cursor].date <= day:
                    tx = rows[cursor]
                    key = SecurityKey(tx.exchange, tx.ticker, tx.currency)
                    quantities[key] += tx.quantity if tx.action == "BUY" else -tx.quantity
                    cursor += 1
                missing = tuple(sorted(key for key, quantity in quantities.items()
                                       if quantity and day not in data.get(key, MarketData()).closes))
                value = None if missing else sum((quantity * data[key].closes[day]
                                                  for key, quantity in quantities.items() if quantity), Decimal(0))
                points.append(HistoricalValue(day, value, missing))
            result[currency] = tuple(points)
    return result


def _normalized_exponential_sum(coefficients: list[float], years: list[float],
                                log_rate: float) -> float:
    """Return a scale-free exponential sum with the same sign and roots."""
    exponents = [-log_rate * year for year in years]
    offset = max(exponents)
    terms = [coefficient * math.exp(exponent - offset)
             for coefficient, exponent in zip(coefficients, exponents)]
    denominator = math.fsum(abs(term) for term in terms)
    return math.fsum(terms) / denominator if denominator else math.nan


def _isolate_roots(coefficients: list[float], years: list[float],
                   low: float, high: float) -> list[float]:
    """Isolate all roots in log-rate space using derivative critical points.

    Between consecutive derivative roots an exponential sum is monotonic, so it
    has at most one crossing. Critical points are checked too, retaining tangent
    roots that a sign-change scan would miss.
    """
    pairs = [(coefficient, year) for coefficient, year in zip(coefficients, years)
             if coefficient]
    if len(pairs) <= 1:
        return []
    coefficients = [pair[0] for pair in pairs]
    years = [pair[1] for pair in pairs]

    # Multiplying the exponential sum by a positive exponential does not
    # change its roots. Shift the smallest exponent to zero so differentiating
    # removes at least one term and the recursive critical-point search ends.
    minimum_year = min(years)
    years = [year - minimum_year for year in years]
    pairs = list(zip(coefficients, years))
    derivative = [(-coefficient * year, year)
                  for coefficient, year in pairs if year]
    critical = (_isolate_roots([pair[0] for pair in derivative],
                               [pair[1] for pair in derivative], low, high)
                if len(derivative) > 1 else [])
    points = [low, *critical, high]
    roots = [point for point in critical
             if abs(_normalized_exponential_sum(coefficients, years, point)) < 1e-12]
    for left, right in zip(points, points[1:]):
        left_value = _normalized_exponential_sum(coefficients, years, left)
        right_value = _normalized_exponential_sum(coefficients, years, right)
        if not math.isfinite(left_value) or not math.isfinite(right_value):
            continue
        if abs(left_value) < 1e-12:
            roots.append(left)
            continue
        if abs(right_value) < 1e-12:
            roots.append(right)
            continue
        if left_value * right_value > 0:
            continue
        for _ in range(160):
            middle = (left + right) / 2
            middle_value = _normalized_exponential_sum(coefficients, years, middle)
            if abs(middle_value) < 1e-13 or right - left < 1e-13:
                left = right = middle
                break
            if left_value * middle_value <= 0:
                right = middle
            else:
                left = middle
                left_value = middle_value
        roots.append((left + right) / 2)
    unique = []
    for root in sorted(roots):
        if not unique or abs(root - unique[-1]) > 1e-8:
            unique.append(root)
    return unique


def xirr(flows: Sequence[tuple[date, Decimal]]) -> XirrResult:
    """Calculate XIRR only when exactly one root is verified.

    Same-day flows net first. Roots are isolated in log(1+r) space over the
    bounded interval (-1+exp(-23), exp(23)-1). Scaling each evaluation prevents
    overflow across long date spans. Multiple verified roots remain unavailable.
    """
    aggregated = defaultdict(Decimal)
    with localcontext(_CONTEXT):
        for day, amount in flows:
            if type(day) is not date or not isinstance(amount, Decimal) or not amount.is_finite():
                return XirrResult(reason="Cash flows require valid dates and finite Decimal amounts.")
            aggregated[day] += amount
    ordered = sorted((day, amount) for day, amount in aggregated.items() if amount)
    if len(ordered) < 2 or ordered[0][1] >= 0 or ordered[-1][1] <= 0:
        return XirrResult(reason="XIRR needs an initial outflow and later inflow on distinct dates.")
    scale = max(abs(amount) for _, amount in ordered)
    with localcontext(_CONTEXT):
        amounts = [float(amount / scale) for _, amount in ordered]
    years = [(day - ordered[0][0]).days / 365 for day, _ in ordered]

    low, high = -23.0, 23.0
    roots = _isolate_roots(amounts, years, low, high)
    if not roots:
        return XirrResult(reason="XIRR lies outside the verified numerical range or cannot be bracketed.")
    if len(roots) > 1:
        return XirrResult(reason="Cash flows produce multiple XIRR roots; no unique annualized return is reported.")
    return XirrResult(rate=math.expm1(roots[0]))


def portfolio_xirr(transactions: Sequence[Transaction], data: Mapping[SecurityKey, MarketData],
                   currency: str, *, confirmed: bool = False) -> XirrResult:
    """Dated buys (negative), net sales (positive), and common-date stock value.

    This measures the selected stock investments as if every trade were external
    cash flow. It does not measure a brokerage account including retained cash.
    """
    if not confirmed:
        return XirrResult(reason="Confirm complete history and the trade-cash-flow assumptions to calculate XIRR.")
    rows = [tx for tx in transactions if tx.currency == currency]
    if not rows:
        return XirrResult(reason="No transactions in this currency.")
    portfolio = calculate_portfolio(rows)
    keys = {SecurityKey(tx.exchange, tx.ticker, tx.currency) for tx in rows}
    blockers = sorted({reason for key in keys for reason in data.get(key, MarketData()).performance_blockers})
    if blockers:
        return XirrResult(reason=" ".join(blockers))
    last_trade = max(tx.date for tx in rows)
    open_stocks = [stock for stock in portfolio.securities if stock.quantity]
    terminal_day = last_trade
    terminal = Decimal(0)
    if open_stocks:
        dated = {}
        for stock in open_stocks:
            item = data.get(stock.security, MarketData())
            candidates = dict(item.closes)
            # Real intraday quotes may not follow same-day trades. Only completed
            # daily closes (or explicitly dated fictional reference prices) qualify.
            if item.quote and item.quote.synthetic:
                candidates[item.quote.valuation_date] = item.quote.price
            dated[stock.security] = {day: value for day, value in candidates.items() if day >= last_trade}
        common = set.intersection(*(set(prices) for prices in dated.values()))
        if not common:
            return XirrResult(reason="No complete common-date terminal valuation at or after the last transaction.")
        terminal_day = max(common)
        terminal_prices = {key: prices[terminal_day] for key, prices in dated.items()}
        terminal = calculate_portfolio(rows, prices=terminal_prices).totals[currency].market_value
    with localcontext(_CONTEXT):
        flows = [(tx.date, -(tx.quantity * tx.price + tx.fees) if tx.action == "BUY"
                  else tx.quantity * tx.price - tx.fees) for tx in rows]
        flows.append((terminal_day, terminal))
    result = xirr(flows)
    return XirrResult(result.rate, result.reason, terminal_day)
