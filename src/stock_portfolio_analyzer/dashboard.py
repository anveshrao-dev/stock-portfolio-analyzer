"""Portfolio display transformations and Plotly figures, without Streamlit."""

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Context, Decimal, ROUND_HALF_EVEN, localcontext
from html import escape
from typing import Mapping, Sequence

import pandas as pd
import plotly.graph_objects as go

from .models import Transaction
from .portfolio import ConsolidatedPosition, Portfolio, SecurityKey

_DISPLAY_CONTEXT = Context(prec=60, rounding=ROUND_HALF_EVEN)
ZERO = Decimal(0)
UNAVAILABLE = "Unavailable"
HOLDING_COLUMNS = [
    "Ticker", "Exchange", "Brokerage", "Currency", "Quantity", "Average purchase cost",
    "Purchase cost of shares held", "Selected price", "Market value",
    "Unrealized gain/loss", "Unrealized return %",
]
HOLDING_SORT_FIELDS = {
    "Ticker": None, "Quantity": "quantity", "Average purchase cost": "average_cost",
    "Purchase cost of shares held": "cost_basis", "Market value": "market_value",
    "Unrealized gain/loss": "unrealized_gain", "Unrealized return %": "return_pct",
}


@dataclass(frozen=True)
class TransactionSummary:
    transaction_count: int
    security_count: int
    first_date: date
    last_date: date
    buy_count: int
    sell_count: int


@dataclass(frozen=True)
class PortfolioInsights:
    open_position_return_pct: Decimal
    best: ConsolidatedPosition
    worst: ConsolidatedPosition
    largest_move: ConsolidatedPosition


def transaction_summary(transactions: Sequence[Transaction]) -> TransactionSummary:
    """Summarize an accepted history without requiring market prices."""
    if not transactions:
        raise ValueError("A transaction summary requires at least one transaction.")
    actions = Counter(tx.action for tx in transactions)
    return TransactionSummary(
        transaction_count=len(transactions),
        security_count=len({(tx.exchange, tx.ticker, tx.currency) for tx in transactions}),
        first_date=min(tx.date for tx in transactions),
        last_date=max(tx.date for tx in transactions),
        buy_count=actions["BUY"],
        sell_count=actions["SELL"],
    )


def portfolio_insights(portfolio: Portfolio, currency: str) -> PortfolioInsights | None:
    """Compare open-position cost with a complete set of selected prices."""
    totals = portfolio.totals.get(currency)
    positions = [position for position in portfolio.securities
                 if position.security.currency == currency and position.quantity]
    if (totals is None or totals.missing_prices or not positions or
            totals.cost_basis <= ZERO or totals.unrealized_gain is None):
        return None
    priced = [position for position in positions
              if position.return_pct is not None and position.unrealized_gain is not None]
    if not priced:
        return None
    with localcontext(_DISPLAY_CONTEXT):
        overall = totals.unrealized_gain / totals.cost_basis * 100
    ordered = sorted(priced, key=lambda item: item.security)
    return PortfolioInsights(
        open_position_return_pct=overall,
        best=max(ordered, key=lambda item: item.return_pct),
        worst=min(ordered, key=lambda item: item.return_pct),
        largest_move=max(ordered, key=lambda item: abs(item.unrealized_gain)),
    )


def select_transactions(transactions: Sequence[Transaction], *,
                        brokerage: str | None = None,
                        exchange: str | None = None) -> list[Transaction]:
    """Keep complete histories for selected accounts/exchanges, in original order."""
    return [tx for tx in transactions if
            (brokerage is None or tx.brokerage == brokerage) and
            (exchange is None or tx.exchange == exchange)]


def format_money(value: Decimal | None, currency: str) -> str:
    """Round for display only; use USD thousands and INR lakh/crore grouping."""
    if currency not in ("USD", "INR"):
        raise ValueError(f"Unsupported display currency: {currency}")
    if value is None:
        return UNAVAILABLE
    with localcontext(_DISPLAY_CONTEXT):
        rounded = value.quantize(Decimal("0.01"))
        sign = "-" if rounded < 0 else ""
        whole, fraction = format(abs(rounded), ".2f").split(".")
    if currency == "USD":
        grouped = f"{int(whole):,}"
        symbol = "$"
    else:
        tail, head = whole[-3:], whole[:-3]
        groups = []
        while head:
            groups.insert(0, head[-2:])
            head = head[:-2]
        grouped = ",".join([*groups, tail])
        symbol = "₹"
    return f"{sign}{symbol}{grouped}.{fraction}"


def format_percent(value: Decimal | None) -> str:
    if value is None:
        return UNAVAILABLE
    with localcontext(_DISPLAY_CONTEXT):
        rounded = value.quantize(Decimal("0.01"))
        return f"{abs(rounded) if rounded == 0 else rounded:.2f}%"


def security_label(security: SecurityKey) -> str:
    return f"{security.ticker} · {security.exchange}"


def holdings_frame(portfolio: Portfolio, currency: str, *, consolidated: bool,
                   sort_by: str = "Ticker", descending: bool = False) -> pd.DataFrame:
    """Format open positions without converting exact accounting values to floats."""
    if sort_by not in HOLDING_SORT_FIELDS:
        raise ValueError("Unsupported holdings sort field.")
    positions = [position for position in (portfolio.securities if consolidated else portfolio.holdings)
                 if position.security.currency == currency and position.quantity]
    attribute = HOLDING_SORT_FIELDS[sort_by]
    if attribute is None:
        positions.sort(key=lambda item: (item.security.ticker, item.security.exchange, getattr(item, "brokerage", "")), reverse=descending)
    else:
        known = [position for position in positions if getattr(position, attribute) is not None]
        missing = [position for position in positions if getattr(position, attribute) is None]
        positions = sorted(known, key=lambda item: getattr(item, attribute), reverse=descending) + missing
    rows = []
    for position in positions:
        if position.security.currency != currency or not position.quantity:
            continue
        if consolidated:
            brokerages = sorted(account.brokerage for account in position.accounts if account.quantity)
            brokerage = ", ".join(brokerages)
        else:
            brokerage = position.brokerage
        quantity = format(position.quantity, "f")
        if "." in quantity:
            quantity = quantity.rstrip("0").rstrip(".")
        rows.append({
            "Ticker": position.security.ticker, "Exchange": position.security.exchange,
            "Brokerage": brokerage, "Currency": currency, "Quantity": quantity,
            "Average purchase cost": format_money(position.average_cost, currency),
            "Purchase cost of shares held": format_money(position.cost_basis, currency),
            "Selected price": format_money(position.price, currency),
            "Market value": format_money(position.market_value, currency),
            "Unrealized gain/loss": format_money(position.unrealized_gain, currency),
            "Unrealized return %": format_percent(position.return_pct),
        })
    return pd.DataFrame(rows, columns=HOLDING_COLUMNS)


def allocation_data(portfolio: Portfolio, currency: str, *, basis: str,
                    group: str) -> dict[str, Decimal] | None:
    """Return whole-currency allocation, or None if any required quote is absent."""
    if basis not in ("Cost basis", "Market value") or group not in ("security", "brokerage"):
        raise ValueError("Expected Cost basis/Market value and security/brokerage.")
    totals = portfolio.totals.get(currency)
    if totals is None:
        return {}
    if basis == "Market value" and totals.missing_prices:
        return None
    values: dict[str, Decimal] = defaultdict(lambda: ZERO)
    with localcontext(_DISPLAY_CONTEXT):
        for account in portfolio.holdings:
            if account.security.currency != currency:
                continue
            label = security_label(account.security) if group == "security" else account.brokerage
            amount = account.cost_basis if basis == "Cost basis" else account.market_value
            values[label] += amount
    return dict(sorted(values.items()))


def concentration(amounts: Mapping[str, Decimal] | None) -> tuple[str, Decimal] | None:
    """Largest weight in a complete, positive allocation; deterministic on ties."""
    if not amounts:
        return None
    with localcontext(_DISPLAY_CONTEXT):
        total = sum(amounts.values(), ZERO)
        if total <= 0:
            return None
        label = max(sorted(amounts), key=lambda name: amounts[name])
        return label, amounts[label] / total * 100


def allocation_figure(amounts: Mapping[str, Decimal], currency: str, title: str) -> go.Figure:
    if not concentration(amounts):
        raise ValueError("Allocation charts require a positive, complete allocation.")
    figure = go.Figure(go.Pie(
        labels=[escape(label) for label in amounts], values=[float(value) for value in amounts.values()],
        customdata=[format_money(value, currency) for value in amounts.values()],
        hole=0.55, textinfo="percent", textposition="inside",
        hovertemplate="%{label}<br>%{customdata}<br>%{percent}<extra></extra>",
        sort=False,
    ))
    figure.update_layout(title=f"{title} ({currency})", height=380,
                         margin=dict(l=16, r=16, t=55, b=16),
                         legend=dict(orientation="h", y=-0.12),
                         colorway=["#2563EB", "#0D9488", "#7C3AED", "#D97706", "#DB2777", "#64748B"])
    return figure


def gains_figure(portfolio: Portfolio, currency: str) -> go.Figure:
    """Include closed history; absent unrealized results are gaps, not zero bars."""
    positions = [position for position in portfolio.securities if position.security.currency == currency]
    labels = [escape(security_label(position.security)) for position in positions]
    figure = go.Figure()
    for label, attribute, color in (
        ("Realized", "realized_gain", "#2563EB"),
        ("Unrealized", "unrealized_gain", "#0D9488"),
    ):
        amounts = [getattr(position, attribute) for position in positions]
        figure.add_bar(name=label, x=labels,
                       y=[None if amount is None else float(amount) for amount in amounts],
                       customdata=[format_money(amount, currency) for amount in amounts],
                       marker_color=color,
                       hovertemplate="%{x}<br>%{fullData.name}: %{customdata}<extra></extra>")
    figure.update_layout(title=f"Gains/losses by stock ({currency})", barmode="group",
                         yaxis_title=f"Gain/loss ({currency})", height=380,
                         margin=dict(l=16, r=16, t=55, b=16),
                         legend=dict(orientation="h"))
    figure.add_hline(y=0, line_color="#94A3B8", line_width=1)
    return figure
