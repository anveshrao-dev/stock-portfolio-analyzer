# Stock Portfolio Analyzer

Combine brokerage CSVs to see what you own, what you paid for the shares you still
hold, and how their value has changed. Keep account details while consolidating
overlapping securities, with separate USD and INR totals.

Built with Python 3.12, Streamlit, pandas, Plotly, yfinance, and pytest. Dependencies
and the environment are managed with uv.

## Run locally

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), clone this
repository, and run from its directory:

```bash
uv sync --locked
uv run streamlit run app.py
```

Open <http://localhost:8501>. Stop the server with **Control+C** in the terminal.
For another port, add `--server.port 8502` to the run command.

## Use the app

1. **Transactions:** upload one or more CSVs, load sample files, or enter a trade.
   Review each file's account, exchange, currency, and date format. Recognized
   columns are suggested; expand the mapping controls to change them. Choose
   **Review transactions**, inspect any rejected or duplicate rows, then import.
2. **Price data:** choose a source in the sidebar. With Yahoo Finance, select
   **Load market data** after importing.
3. **Portfolio Overview:** review holdings, purchase costs, gains, and stock
   allocation. Expand the filters to inspect one account or exchange, or use the
   account view. Table sorting is beside the holdings table and remembers your
   choice when switching filters. Additional charts and price-change scenarios
   are available below.
4. **Historical Performance:** review purchases, sales, and gains. A value trend
   needs historical prices. Expand **Calculate annualized return** for XIRR and
   its assumptions.

Potential duplicates are held for an explicit decision. Uploading, staging, or
reviewing a file never imports it automatically. Imported transactions remain in
the current browser session; retain the original files.

## Price sources

| Source | What it provides |
| --- | --- |
| Cost basis only | Holdings, purchase costs, and realized gains without a network request. |
| Yahoo Finance | Latest available quotes and daily history for supported securities, requested explicitly and cached for 15 minutes. |
| Sample reference prices | Fictional prices for the included sample securities, usable offline. The reference date is displayed. |

Yahoo quotes may be delayed or unavailable; they are not a guaranteed real-time
feed. Quote dates, timestamps, and sources are available in the sidebar. Failed
quote requests can be retried and never silently switch to sample prices.

Purchase prices come from your original transactions. Market value uses the
selected quotes, so older purchases can be compared with the latest available
value. A detected split withholds the affected valuation because share quantities
are not split-adjusted. Dividends leave price-based gains available but prevent
XIRR when their cash flows are missing.

## Understanding the numbers

| Metric | Meaning |
| --- | --- |
| Purchase cost of shares held | The recorded purchase cost, including fees, allocated to shares you still own. Sales reduce this amount. |
| Average purchase cost | Purchase cost of shares held divided by their quantity. |
| Market value | Shares currently held multiplied by the selected price. |
| Realized gain/loss | Sale proceeds after fees minus the purchase cost allocated to sold shares. |
| Unrealized gain/loss | Market value minus purchase cost of shares held. |
| Total gain/loss | Realized plus unrealized gains or losses. |

The calculation engine uses weighted-average cost separately for each account,
exchange, ticker, and currency. Buys add `quantity × price + fees` to cost. Sales
remove their quantity at the existing average cost; proceeds are
`quantity × price − fees`. Decimal arithmetic preserves fractional shares and
rounding occurs for display. Overselling is rejected.

Trade-cash-flow XIRR is an annualized, money-weighted return based on dated
purchases, sales, and a valid terminal stock valuation. It treats purchases as
contributions and sales as withdrawals, excludes brokerage cash, and requires
confirmation of complete history and supported cash flows. It is withheld when a
unique result or valid valuation cannot be established. A daily value chart needs
historical prices; a single reference price does not create a historical trend.

The price-change scenario varies one open holding's price and shows the effect
on that holding and the selected currency portfolio. It leaves imported trades
and stored prices unchanged. Scenarios are hypothetical, not forecasts.

## Supported CSVs and markets

The initial scope is NASDAQ and NYSE securities in USD, and NSE securities in INR.
Securities are identified by exchange, ticker, and currency. There is no combined
USD/INR total or implicit currency conversion.

CSV files must be UTF-8 and comma-delimited, up to 2 MiB and 10,000 rows per file.
Required fields are date, ticker, BUY/SELL, quantity, and price. Account, exchange,
and currency may come from mapped columns or file defaults. Fees and transaction
IDs are optional. Mapped values override defaults; blank mapped cells are
rejected. Recognized mapping options are field-specific; advanced mapping allows
unfamiliar headers. Date formats are selected explicitly.

See [data/README.md](data/README.md) for the included fictional datasets and their
different column formats.

## Structure and testing

```text
app.py                          Streamlit entry point
src/stock_portfolio_analyzer/
  models.py                     Transaction records
  csv_import.py, validation.py  Loading, normalization, validation
  portfolio.py                  Holdings and weighted-average accounting
  market_data.py                Price providers and quote validation
  performance.py                Historical valuation and XIRR
  risk.py                       Hypothetical price scenarios
  dashboard.py                  Table, chart, and insight helpers
  *_ui.py                       Interface and session state
tests/                          Deterministic unit and Streamlit workflow tests
data/                           Fictional transactions and reference prices
screenshots/                    Product screenshots
```

Financial calculations are independent of Streamlit. No API key is required.

```bash
uv run --locked pytest
```

Tests cover imports and mappings, validation and duplicates, partial/full sales,
overselling, cost basis, consolidation, currencies and exchanges, missing prices,
provider failures, historical performance, XIRR, scenarios, and Streamlit workflows.
Automated tests use no external network.

## Scope and privacy

- Dividends, transfers, splits, short selling, taxes, and account cash balances are
  not modeled. Incomplete transaction histories can prevent meaningful results.
- Weighted-average accounting may differ from a brokerage's tax accounting.
- Yahoo access through yfinance is unofficial and has no availability guarantee.
  Historical valuations require sufficient actual prices; missing values are not
  invented.
- Data is session-based, with no database or authentication. Transactions are
  processed by the Streamlit server; running locally keeps this processing on
  your computer.
- Yahoo receives symbols and date ranges when you load market data. Account names,
  quantities, purchase prices, and raw CSV records are not sent to Yahoo.
- Only fictional sample data belongs in this repository. Environment files,
  Streamlit secrets, `data/private/`, uploads, and generated caches are ignored.
  Keep personal brokerage exports and credentials out of Git.

Possible extensions include corporate-action handling, user-controlled saved
portfolios, and additional documented CSV formats.

## License

This project is available under the [MIT License](LICENSE).
