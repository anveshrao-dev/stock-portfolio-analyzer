# Sample data

Every transaction, fee, and reference price in this directory is fictional.
Ticker symbols identify real securities, but the transactions and price values
are invented. These files contain no personal financial information and are not
live or historical market-data feeds.

| File | Records | Brokerage/account default | Exchange | Currency | Date format |
| --- | ---: | --- | --- | --- | --- |
| `us_brokerage_a.csv` | 16 | US Brokerage A | NASDAQ | USD | YYYY-MM-DD |
| `us_brokerage_b.csv` | 16 | US Brokerage B | NASDAQ | USD | MM/DD/YYYY |
| `india_brokerage.csv` | 16 | Indian Brokerage | NSE | INR | DD/MM/YYYY |
| `invalid_transactions.csv` | 8 | Invalid sample account | NASDAQ | USD | YYYY-MM-DD |

Valid datasets include repeated purchases and partial sales on different dates.
Brokerage B includes a full TSLA sale followed by a new purchase. Brokerage A
and B overlap in AAPL and MSFT while retaining separate accounts. Each valid
file is internally consistent and can be validated independently or together.

The invalid fixture demonstrates missing ticker, negative quantity, invalid date,
unsupported transaction type, overselling, and a duplicate BUY. With duplicates
held, expected results are 2 accepted, 5 rejected, and 1 potential duplicate.
Do not treat this file as an additional valid brokerage history.

`synthetic_reference_prices.csv` provides one fictional price for each of the
10 distinct exchange/ticker/currency securities, labelled `SAMPLE` in every row.
Its `2026-09-30` as-of date is a fictional valuation date, not an actual quote timestamp.
It is not an importable transaction file. Select **Sample reference prices**
in the sidebar to use it for fictional valuation. It contains no daily history;
the app never interpolates it into a historical trend. A reference price dated
before a security's latest transaction is withheld from holdings valuation.

Keep personal brokerage exports in the Git-ignored `data/private/` directory.
