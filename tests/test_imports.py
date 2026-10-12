"""Deterministic import tests: preserve records and prevent invalid inventories."""

from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from stock_portfolio_analyzer.csv_import import (
    ImportSettings, load_csv, mapping_candidates, normalize_rows, suggest_mapping,
)
from stock_portfolio_analyzer.validation import validate_transactions


def parse(text, *, source="test.csv", brokerage="Test account", exchange="NASDAQ",
          currency="USD", date_format="%Y-%m-%d", mapping=None):
    frame = load_csv(text.encode())
    settings = ImportSettings(
        mapping if mapping is not None else suggest_mapping(frame.columns),
        brokerage, exchange, currency, date_format,
    )
    return normalize_rows(frame, settings, source=source, file_id=source)


HEADER = "Date,Symbol,Action,Shares,Price,Fees,Transaction ID\n"
BUY = "2025-01-02,AAPL,BUY,10,100,1,001\n"


def test_normalization_preserves_source_and_exact_numbers():
    rows, issues = parse(HEADER + "2025-01-02, aapl ,buy,0.1,123.45,0.02,001\n")
    assert not issues
    tx = rows[0]
    assert (tx.date, tx.ticker, tx.action, tx.quantity, tx.price, tx.fees) == (
        date(2025, 1, 2), "AAPL", "BUY", Decimal("0.1"), Decimal("123.45"), Decimal("0.02")
    )
    assert tx.transaction_id == "001"
    assert tx.source_row == 2
    assert tx.raw["Symbol"] == " aapl "


def test_alternate_columns_and_missing_optional_fees():
    rows, issues = parse(
        "Trade Date,Ticker,Transaction Type,Quantity,Execution Price\n"
        "02/03/2025,MSFT,BUY,2,300\n", date_format="%d/%m/%Y"
    )
    assert not issues
    assert rows[0].date == date(2025, 3, 2)
    assert rows[0].fees == 0
    assert rows[0].transaction_id is None


@pytest.mark.parametrize("row,reason", [
    ("2025-01-02,,BUY,1,100,0,", "ticker"),
    ("2025-01-02,AAPL,BUY,-1,100,0,", "quantity"),
    ("2025-01-02,AAPL,BUY,0,100,0,", "quantity"),
    ("2025-02-30,AAPL,BUY,1,100,0,", "date"),
    ("2025-01-02,AAPL,DIVIDEND,1,100,0,", "BUY or SELL"),
    ("2025-01-02,AAPL,BUY,1,NaN,0,", "price"),
    ("2025-01-02,AAPL,BUY,Infinity,100,0,", "quantity"),
    ("2025-01-02,AAPL,BUY,1,0,0,", "price"),
    ("2025-01-02,AAPL,BUY,1,100,-1,", "fees"),
    ("2025-01-02,AAPL,BUY,1,100,,", "fees"),
    ("2025-01-02,AAPL,BUY,1e999,100,0,", "quantity"),
])
def test_invalid_fields_keep_raw_record_and_reason(row, reason):
    rows, issues = parse(HEADER + row + "\n")
    assert not rows
    assert len(issues) == 1
    assert reason in issues[0].reason
    assert issues[0].raw["Date"] == row.split(",")[0]


@pytest.mark.parametrize("payload", [
    b"", b"Date,Symbol\n", b"Date,Date\na,b\n", b"Date,\na,b\n",
    b"a,b\n1,2,3\n", b"a,b\n1\n", b"a,b\n\n", b"\xff",
    b'a,b\n"unclosed,2\n',
])
def test_bad_csv_is_reported_without_skipping_records(payload):
    with pytest.raises(ValueError):
        load_csv(payload)


def test_literal_na_and_leading_zero_identifiers_are_not_missing():
    rows, issues = parse(HEADER + "2025-01-02,NA,BUY,1,100,0,0001\n")
    assert not issues
    assert rows[0].ticker == "NA"
    assert rows[0].transaction_id == "0001"


def test_mapping_requires_fields_and_distinct_source_columns():
    with pytest.raises(ValueError, match="mapping"):
        parse(HEADER + BUY, mapping={"date": "Date"})
    mapping = suggest_mapping(load_csv((HEADER + BUY).encode()).columns)
    mapping["price"] = "Shares"
    with pytest.raises(ValueError, match="column"):
        parse(HEADER + BUY, mapping=mapping)


def test_custom_mapping_and_row_metadata_override_defaults():
    text = "when,stock,side,units,paid,Account,Exchange,Currency\n2025-01-02,TCS,BUY,4,3500,India,NSE,INR\n"
    mapping = {"date": "when", "ticker": "stock", "action": "side", "quantity": "units", "price": "paid",
               "brokerage": "Account", "exchange": "Exchange", "currency": "Currency"}
    rows, issues = parse(text, mapping=mapping)
    assert not issues
    assert (rows[0].brokerage, rows[0].exchange, rows[0].currency) == ("India", "NSE", "INR")


def test_partial_full_sales_unsorted_input_and_oversell():
    rows, _ = parse(HEADER + "2025-03-01,AAPL,SELL,7,120,1,3\n" + BUY +
                    "2025-02-01,AAPL,SELL,3,110,1,2\n2025-04-01,AAPL,SELL,1,130,0,4\n")
    result = validate_transactions(rows)
    assert len(result.accepted) == 3
    assert len(result.rejected) == 1
    assert "available quantity 0" in result.rejected[0].reason


def test_fractional_quantity_checks_do_not_use_binary_floats():
    rows, _ = parse(HEADER + "2025-01-01,AAPL,BUY,0.1,100,0,1\n"
                    "2025-01-02,AAPL,BUY,0.2,100,0,2\n2025-01-03,AAPL,SELL,0.3,100,0,3\n")
    assert len(validate_transactions(rows).accepted) == 3


@pytest.mark.parametrize("change", [{"brokerage": "Other"}, {"exchange": "NSE"}, {"currency": "INR"}])
def test_sale_cannot_borrow_inventory_from_another_account_exchange_or_currency(change):
    buy = parse(HEADER + BUY)[0][0]
    sell = replace(buy, action="SELL", date=date(2025, 2, 1), transaction_id="sell", row_key="sell", **change)
    result = validate_transactions([buy, sell])
    assert len(result.accepted) == 1
    assert len(result.rejected) == 1


def test_duplicates_across_files_and_previous_imports_are_held():
    first = parse(HEADER + BUY, source="a.csv")[0]
    second = parse(HEADER + BUY, source="b.csv")[0]
    result = validate_transactions(first + second)
    assert len(result.accepted) == 1
    assert len(result.duplicates) == 1
    again = validate_transactions(second, existing=first)
    assert not again.accepted
    assert len(again.duplicates) == 1


def test_fingerprint_duplicates_require_explicit_override_but_id_conflicts_do_not_allow_it():
    buy = parse(HEADER + BUY)[0][0]
    identical = replace(buy, transaction_id="002", row_key="second")
    assert len(validate_transactions([buy, identical]).duplicates) == 1
    allowed = validate_transactions([buy, identical], include_duplicates={"second"})
    assert len(allowed.accepted) == 2
    conflict = replace(buy, price=Decimal("101"), row_key="conflict")
    rejected = validate_transactions([buy, conflict], include_duplicates={"conflict"})
    assert len(rejected.rejected) == 1
    assert "conflicting transaction ID" in rejected.rejected[0].reason


def test_duplicate_decisions_recalculate_inventory():
    rows, _ = parse(HEADER + BUY + BUY + "2025-02-01,AAPL,SELL,15,110,0,sell\n")
    held = validate_transactions(rows)
    assert len(held.duplicates) == 1
    assert len(held.rejected) == 1
    resolved = validate_transactions(rows, include_duplicates={rows[1].row_key})
    assert len(resolved.accepted) == 3
    assert not resolved.rejected


def test_held_duplicate_cannot_hide_a_later_conflicting_transaction_id():
    original = parse(HEADER + BUY)[0][0]
    held = replace(original, transaction_id="002", row_key="held")
    conflict = replace(held, date=date(2025, 1, 3), quantity=Decimal("99"), row_key="conflict")
    result = validate_transactions([original, held, conflict])
    assert len(result.accepted) == 1
    assert len(result.duplicates) == 1
    assert len(result.rejected) == 1
    assert "conflicting transaction ID" in result.rejected[0].reason


def test_backdated_import_cannot_invalidate_existing_sale():
    existing, _ = parse(HEADER + BUY + "2025-03-01,AAPL,SELL,10,110,0,sell\n")
    incoming, _ = parse(HEADER + "2025-02-01,AAPL,SELL,1,110,0,new\n", source="new.csv")
    result = validate_transactions(incoming, existing=existing)
    assert result.blockers


def test_same_day_input_order_is_not_silently_rearranged():
    rows, _ = parse(HEADER + "2025-01-02,AAPL,SELL,1,110,0,sell\n" + BUY)
    result = validate_transactions(rows)
    assert len(result.rejected) == 1
    assert len(result.accepted) == 1


@pytest.mark.parametrize("filename,brokerage,exchange,currency,fmt", [
    ("us_brokerage_a.csv", "US Brokerage A", "NASDAQ", "USD", "%Y-%m-%d"),
    ("us_brokerage_b.csv", "US Brokerage B", "NASDAQ", "USD", "%m/%d/%Y"),
    ("india_brokerage.csv", "Indian Brokerage", "NSE", "INR", "%d/%m/%Y"),
])
def test_valid_synthetic_datasets(filename, brokerage, exchange, currency, fmt):
    content = (Path(__file__).resolve().parents[1] / "data" / filename).read_text()
    rows, issues = parse(content, source=filename, brokerage=brokerage, exchange=exchange, currency=currency, date_format=fmt)
    assert 10 <= len(rows) <= 20
    assert not issues
    result = validate_transactions(rows)
    assert len(result.accepted) == len(rows)
    assert not result.rejected and not result.duplicates and not result.blockers
    assert {tx.action for tx in rows} == {"BUY", "SELL"}


def test_invalid_fixture_contains_each_demonstration_case():
    content = (Path(__file__).resolve().parents[1] / "data/invalid_transactions.csv").read_text()
    rows, issues = parse(content)
    result = validate_transactions(rows)
    assert len(issues) == 4
    assert len(result.rejected) == 1
    assert len(result.duplicates) == 1
    assert len(result.accepted) == 2


def test_utf8_bom_and_quoted_delimiters_are_preserved():
    rows, issues = parse('\ufeffDate,Symbol,Action,Shares,Price,Account\n2025-01-02,AAPL,BUY,1,100,"Account, joint"\n')
    assert not issues
    assert rows[0].brokerage == "Account, joint"


@pytest.mark.parametrize("column,value,reason", [
    ("Brokerage", "", "brokerage"), ("Exchange", "LSE", "exchange"),
    ("Currency", "EUR", "currency"), ("Exchange", "", "exchange"),
])
def test_invalid_metadata_is_rejected_not_replaced_by_defaults(column, value, reason):
    rows, issues = parse(f"Date,Symbol,Action,Shares,Price,{column}\n2025-01-02,AAPL,BUY,1,100,{value}\n")
    assert not rows
    assert reason in issues[0].reason


def test_ambiguous_aliases_are_left_for_user_mapping():
    mapping = suggest_mapping(["Date", "Trade Date", "Ticker", "Symbol"])
    assert mapping["date"] is None and mapping["ticker"] is None


def test_mapping_candidates_are_field_specific_unless_advanced_is_enabled():
    columns = ["Date", "Account", "Broker", "Market", "Ccy", "Fees", "Mystery"]

    assert mapping_candidates(columns, "brokerage") == ["Account", "Broker"]
    assert mapping_candidates(columns, "exchange") == ["Market"]
    assert mapping_candidates(columns, "currency") == ["Ccy"]
    assert mapping_candidates(columns, "fees") == ["Fees"]
    assert mapping_candidates(columns, "transaction_id") == []
    assert mapping_candidates(columns, "brokerage", include_all=True) == columns


def test_oversized_csv_is_rejected():
    with pytest.raises(ValueError, match="2 MiB"):
        load_csv(b"x" * (2 * 1024 * 1024 + 1))


def test_row_limit_is_enforced():
    with pytest.raises(ValueError, match="10,000"):
        load_csv(b"a,b\n" + b"1,2\n" * 10_001)


def test_synthetic_prices_cover_sample_securities_and_are_labeled():
    data = Path(__file__).resolve().parents[1] / "data"
    prices = load_csv((data / "synthetic_reference_prices.csv").read_bytes())
    assert len(prices) == 10
    assert not prices.duplicated(["ticker", "exchange", "currency"]).any()
    assert all(source.startswith("SAMPLE") for source in prices["source"])
    assert all(Decimal(price) > 0 for price in prices["price"])
    assert set(prices["currency"]) == {"USD", "INR"}
    assert set(prices["ticker"]) == {"AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "TSLA", "RELIANCE", "TCS", "INFY", "HDFCBANK"}
