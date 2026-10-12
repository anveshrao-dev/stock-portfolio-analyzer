"""Session-only transaction staging, review, and import interface."""

import csv
import hashlib
import io
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st

from .csv_import import (
    CURRENCIES, DATE_FORMATS, EXCHANGES, FIELDS, REQUIRED_FIELDS, ImportSettings,
    load_csv, mapping_candidates, normalize_rows, suggest_mapping,
)
from .models import Issue, Transaction, ValidationResult
from .dashboard import transaction_summary
from .validation import validate_transactions

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
SAMPLES = {
    "us_brokerage_a.csv": ("US Brokerage A", "NASDAQ", "USD", "YYYY-MM-DD"),
    "us_brokerage_b.csv": ("US Brokerage B", "NASDAQ", "USD", "MM/DD/YYYY"),
    "india_brokerage.csv": ("Indian Brokerage", "NSE", "INR", "DD/MM/YYYY"),
    "invalid_transactions.csv": ("Invalid sample account", "NASDAQ", "USD", "YYYY-MM-DD"),
}
LABELS = {
    "date": "Transaction date", "ticker": "Ticker", "action": "BUY/SELL",
    "quantity": "Quantity", "price": "Price per share", "fees": "Fees",
    "brokerage": "Brokerage", "exchange": "Exchange", "currency": "Currency",
    "transaction_id": "Transaction ID",
}


def transaction_frame(rows: list[Transaction]) -> pd.DataFrame:
    """Display exact decimals as text rather than rounding via binary floats."""
    return pd.DataFrame([{
        "Date": tx.date.isoformat(), "Brokerage": tx.brokerage,
        "Exchange": tx.exchange, "Ticker": tx.ticker, "Type": tx.action,
        "Quantity": str(tx.quantity), "Price per share": str(tx.price),
        "Currency": tx.currency, "Fees": str(tx.fees),
        "Transaction ID": tx.transaction_id or "", "Source": tx.source,
        "Source record": tx.source_row,
    } for tx in rows])


def issue_frame(issues: list[Issue]) -> pd.DataFrame:
    return pd.DataFrame([{
        "Source": item.source, "Source record": item.source_row, "Reason": item.reason,
        **{f"Original: {key}": value for key, value in item.raw.items()},
    } for item in issues])


def _stage(files: list[dict]) -> None:
    st.session_state["stage_version"] = st.session_state.get("stage_version", 0) + 1
    st.session_state["staged_files"] = files
    st.session_state["review_signature"] = None
    st.session_state["current_review"] = None


def _sources() -> None:
    st.subheader("Add transactions")
    uploads = st.file_uploader("Upload one or more transaction CSVs", type=["csv"],
                               accept_multiple_files=True, max_upload_size=2, key="uploads")
    if st.button("Stage uploaded files", key="stage_uploads", disabled=not uploads,
                 help="Replaces the staged files. Imported transactions are kept."):
        _stage([{"name": upload.name, "content": upload.getvalue(), "synthetic": False,
                 "defaults": ("", "NASDAQ", "USD", "YYYY-MM-DD")} for upload in uploads])

    with st.expander("Try the sample portfolio", expanded=not st.session_state["staged_files"] and not st.session_state["transactions"]):
        st.caption("Explore with fictional transactions.")
        selected = st.multiselect("Sample files", list(SAMPLES), default=["us_brokerage_a.csv"], key="sample_files")
        if st.button("Load selected samples", key="stage_samples", disabled=not selected):
            try:
                files = [{"name": name, "content": (DATA_DIR / name).read_bytes(),
                          "synthetic": True, "defaults": SAMPLES[name]} for name in selected]
            except OSError:
                st.error("Unable to load a sample file. Run from the complete source checkout with the data directory present. Your imported transactions and existing preview are unchanged.")
            else:
                _stage(files)

    with st.expander("Enter a BUY or SELL manually"):
        with st.form("manual_transaction"):
            first, second, third = st.columns(3)
            tx_date = first.date_input("Transaction date", value=date.today(), key="manual_date")
            brokerage = second.text_input("Brokerage / account", key="manual_brokerage")
            ticker = third.text_input("Ticker", key="manual_ticker")
            action = first.selectbox("Type", ["BUY", "SELL"], key="manual_action")
            exchange = second.selectbox("Exchange", EXCHANGES, key="manual_exchange")
            currency = third.selectbox("Currency", CURRENCIES, key="manual_currency")
            quantity = first.text_input("Quantity", value="1", key="manual_quantity")
            price = second.text_input("Price per share", value="100", key="manual_price")
            fees = third.text_input("Fees", value="0", key="manual_fees")
            tx_id = st.text_input("Transaction ID (optional)", key="manual_id")
            submitted = st.form_submit_button("Preview manual transaction", key="stage_manual")
        if submitted:
            buffer = io.StringIO()
            writer = csv.writer(buffer)
            writer.writerow(["Date", "Brokerage", "Exchange", "Ticker", "Action", "Quantity", "Price", "Currency", "Fees", "Transaction ID"])
            writer.writerow([tx_date.isoformat(), brokerage, exchange, ticker, action, quantity, price, currency, fees, tx_id])
            _stage([{"name": "Manual entry", "content": buffer.getvalue().encode(),
                     "synthetic": False, "defaults": (brokerage, exchange, currency, "YYYY-MM-DD")}])


def _mapping(file: dict, index: int) -> tuple[pd.DataFrame, ImportSettings, str, str]:
    frame = load_csv(file["content"])
    digest = hashlib.sha256(file["content"]).hexdigest()[:16]
    file_id = f"{index}:{digest}"
    widget_key = f"file:{st.session_state['stage_version']}:{file_id}"
    source = f"Sample / {file['name']}" if file["synthetic"] else file["name"]
    suggestions = suggest_mapping(frame.columns)
    mapping_keys = {field: f"{widget_key}:map:{field}" for field in FIELDS}
    selected = {
        field: st.session_state.get(mapping_keys[field], suggestions.get(field))
        for field in FIELDS
    }
    broker_default, exchange_default, currency_default, format_default = file["defaults"]
    missing_account = not selected["brokerage"] and not st.session_state.get(
        f"{widget_key}:brokerage", broker_default
    ).strip()
    missing_mapping = any(not selected[field] for field in REQUIRED_FIELDS)
    with st.container(border=True):
        st.write(f"**{source}** · {len(frame)} records")
        summary = st.empty()
        with st.expander("Account and file settings", expanded=missing_account):
            st.caption("Use the same account name for files from the same account. Mapped CSV values override these defaults.")
            cols = st.columns(4)
            brokerage = cols[0].text_input("Brokerage / account", value=broker_default, key=f"{widget_key}:brokerage")
            exchange = cols[1].selectbox("Default exchange", EXCHANGES, index=EXCHANGES.index(exchange_default), key=f"{widget_key}:exchange")
            currency = cols[2].selectbox("Default currency", CURRENCIES, index=CURRENCIES.index(currency_default), key=f"{widget_key}:currency")
            formats = list(DATE_FORMATS)
            date_format = cols[3].selectbox("Date format", formats, index=formats.index(format_default), key=f"{widget_key}:date_format")
        mapping = {}
        with st.expander("Review column mappings", expanded=missing_mapping):
            st.caption("Recognized columns are mapped automatically. Review or change any selection below.")
            advanced = st.checkbox(
                "Show all source columns (advanced)",
                help="Enable this for unfamiliar column names.",
                key=f"{widget_key}:advanced_mapping",
            )

            def options_for(field: str) -> list[str | None]:
                current = selected[field]
                used_elsewhere = {value for other, value in selected.items()
                                  if other != field and value is not None}
                candidates = [column for column in mapping_candidates(
                    frame.columns, field, include_all=advanced
                ) if column not in used_elsewhere]
                if current is not None and current not in candidates:
                    candidates.append(current)
                return [None, *candidates]

            required_columns = st.columns(3)
            for position, field in enumerate(REQUIRED_FIELDS):
                options = options_for(field)
                mapping[field] = required_columns[position % 3].selectbox(
                    f"{LABELS[field]} column", options, index=options.index(selected[field]),
                    format_func=lambda option: "— Not mapped —" if option is None else option,
                    key=mapping_keys[field],
                )
            with st.expander("Optional column mappings"):
                st.caption("Unmapped brokerage, exchange, and currency fields use the defaults selected above. A mapped column overrides its default; blank mapped cells are rejected. Unmapped fees use zero, and transaction ID may be omitted.")
                optional_fields = [field for field in FIELDS if field not in REQUIRED_FIELDS]
                optional_options = {field: options_for(field) for field in optional_fields}
                visible_fields = [field for field in optional_fields
                                  if len(optional_options[field]) > 1]
                hidden_fields = [field for field in optional_fields if field not in visible_fields]
                if hidden_fields:
                    names = ["transaction ID" if field == "transaction_id"
                             else LABELS[field].lower() for field in hidden_fields]
                    hidden_names = names[0] if len(names) == 1 else (
                        f"{names[0]} or {names[1]}" if len(names) == 2
                        else f"{', '.join(names[:-1])}, or {names[-1]}"
                    )
                    st.caption(f"No {hidden_names} column was detected; the file defaults or optional empty value will be used.")
                optional_columns = st.columns(min(3, max(1, len(visible_fields))))
                for position, field in enumerate(visible_fields):
                    options = optional_options[field]
                    mapping[field] = optional_columns[position % 3].selectbox(
                        f"{LABELS[field]} column", options, index=options.index(selected[field]),
                        format_func=lambda option: "— Not mapped —" if option is None else option,
                        key=mapping_keys[field],
                    )
                for field in hidden_fields:
                    mapping[field] = None
            st.caption("Source preview · first 20 records. Review checks every record and preserves the original values.")
            st.dataframe(frame.head(20), hide_index=True, width="stretch")
        details = []
        for field, label, default in (
            ("brokerage", "Account", brokerage),
            ("exchange", "Exchange", exchange),
            ("currency", "Currency", currency),
        ):
            value = f"CSV column {mapping[field]}" if mapping[field] else default or "Required"
            details.append(f"{label}: {value}")
        summary.caption(" · ".join([*details, f"Dates: {date_format}"]))
        if not mapping["brokerage"] and not brokerage.strip():
            st.warning("Enter a brokerage / account in the file settings, or map an account column.")
        if any(not mapping[field] for field in REQUIRED_FIELDS):
            st.warning("Complete the required column mappings before reviewing this file.")
        if mapping["fees"] is None:
            st.warning("Fees are not mapped: zero fees will be used. Map the fee column if one exists.")
    return frame, ImportSettings(mapping, brokerage, exchange, currency, DATE_FORMATS[date_format]), source, file_id


def _review_tables(result: ValidationResult) -> None:
    st.caption(
        f"{len(result.accepted)} accepted · {len(result.rejected)} rejected · "
        f"{len(result.duplicates)} potential duplicates held"
    )
    if result.accepted:
        with st.expander(f"Preview accepted transactions ({len(result.accepted)})"):
            st.dataframe(transaction_frame(result.accepted), hide_index=True, width="stretch")
            with st.expander("Original values for accepted records"):
                st.dataframe(issue_frame([Issue.for_transaction(tx, "Accepted") for tx in result.accepted]), hide_index=True, width="stretch")
    if result.rejected:
        st.error("Rejected records are excluded. Correct the source and review again, or import the accepted rows.")
        st.dataframe(issue_frame(result.rejected), hide_index=True, width="stretch")
    if result.duplicates:
        st.warning("Potential duplicates are held out of the accepted preview.")
        st.dataframe(issue_frame(result.duplicates), hide_index=True, width="stretch")
    for blocker in result.blockers:
        st.error(blocker)


def _review() -> None:
    if not st.session_state["staged_files"]:
        return
    st.subheader("Review import")
    prepared, errors = [], []
    for index, file in enumerate(st.session_state["staged_files"]):
        try:
            prepared.append(_mapping(file, index))
        except ValueError as exc:
            errors.append(f"{file['name']}: {exc}")
    for error in errors:
        st.error(error)
    signature = hashlib.sha256(repr([(settings, source, file_id) for _, settings, source, file_id in prepared]).encode()).hexdigest()
    if st.button("Review transactions", key="validate_batch", disabled=bool(errors),
                 help="Checks every row, duplicate, and available quantity using the selected mappings and date formats."):
        st.session_state["review_signature"] = signature
    if errors or st.session_state.get("review_signature") != signature:
        if st.session_state.get("review_signature"):
            st.info("The source details or mapping changed. Review again before importing.")
        st.session_state["current_review"] = None
        return
    incoming, field_issues = [], []
    for frame, settings, source, file_id in prepared:
        try:
            rows, issues = normalize_rows(frame, settings, source=source, file_id=file_id)
            incoming.extend(rows)
            field_issues.extend(issues)
        except ValueError as exc:
            st.error(f"{source}: {exc}")
            st.session_state["current_review"] = None
            return
    existing = st.session_state["transactions"]
    initial = validate_transactions(incoming, existing=existing)
    included = []
    decisions_complete = True
    if initial.duplicates:
        labels = {item.row_key: f"{item.source}, record {item.source_row}" for item in initial.duplicates}
        included = st.multiselect("Include only duplicates you know are distinct trades", list(labels),
                                  format_func=labels.get, key=f"include:{signature}")
        if included:
            decisions_complete = st.checkbox("I confirm the selected duplicates are distinct transactions.", key=f"confirm:{signature}")
        if len(included) < len(initial.duplicates):
            excluded = st.checkbox("Exclude the remaining potential duplicates from this import.", key=f"exclude:{signature}")
            decisions_complete = decisions_complete and excluded
    result = validate_transactions(incoming, existing=existing, include_duplicates=included)
    result.rejected = field_issues + result.rejected
    st.session_state["current_review"] = result
    _review_tables(result)
    if included:
        st.warning(f"{len(included)} potential duplicate record(s) explicitly selected for inclusion; inventory has been rechecked.")
    if st.button(f"Import {len(result.accepted)} accepted transactions", key="import_batch", type="primary",
                 disabled=not result.accepted or not decisions_complete or bool(result.blockers),
                 help="Imports only accepted rows. Rejected rows and excluded duplicates are kept in the import report."):
        st.session_state["transactions"] = [*existing, *result.accepted]
        st.session_state["import_reports"].append({"review": result, "included_duplicates": included})
        _stage([])
        st.rerun()


def _imported_transactions() -> None:
    transactions = st.session_state["transactions"]
    if not transactions:
        return
    frame = transaction_frame(transactions)
    summary = transaction_summary(transactions)
    st.caption(
        f"{summary.transaction_count} transactions · {summary.security_count} securities · "
        f"{summary.first_date.isoformat()} to {summary.last_date.isoformat()} · "
        f"{summary.buy_count} BUY · {summary.sell_count} SELL"
    )
    with st.expander("View imported transactions"):
        filtered = frame
        columns = st.columns(4)
        for column, label in zip(columns, ("Brokerage", "Exchange", "Ticker", "Type")):
            choices = column.multiselect(label, sorted(frame[label].unique()), key=f"filter:{label}")
            if choices:
                filtered = filtered[filtered[label].isin(choices)]
        st.caption(f"Showing {len(filtered)} of {len(frame)} imported records. Empty filters show all values.")
        st.dataframe(filtered, hide_index=True, width="stretch")


def render_transactions() -> None:
    st.session_state.setdefault("transactions", [])
    st.session_state.setdefault("staged_files", [])
    st.session_state.setdefault("import_reports", [])
    st.header("Transactions")
    st.caption("Transactions stay in this session. A browser reload or server restart may clear them; keep your source files.")
    reports = st.session_state["import_reports"]
    if reports:
        result = reports[-1]["review"]
        st.success(f"Last import: {len(result.accepted)} accepted, {len(result.rejected)} rejected, {len(result.duplicates)} duplicates excluded.")
        with st.expander("Session import reports"):
            report_index = st.selectbox("Import to inspect", list(range(len(reports))),
                                        index=len(reports) - 1,
                                        format_func=lambda index: f"Import {index + 1}", key=f"report:{len(reports)}")
            receipt = reports[report_index]
            _review_tables(receipt["review"])
            if receipt["included_duplicates"]:
                st.caption(f"Explicitly included potential duplicates: {len(receipt['included_duplicates'])}.")
    _sources()
    _review()
    _imported_transactions()
