"""Exercise the actual Streamlit entry point without a browser or network."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest


def test_app_starts_with_three_navigation_tabs():
    app_path = Path(__file__).resolve().parents[1] / "app.py"
    app = AppTest.from_file(str(app_path)).run(timeout=15)

    assert not app.exception
    assert [title.value for title in app.title] == ["Stock Portfolio Analyzer"]
    assert [tab.label for tab in app.tabs] == [
        "Transactions",
        "Portfolio Overview",
        "Historical Performance",
    ]


def test_missing_sample_file_shows_error_and_preserves_imports(monkeypatch, tmp_path):
    from stock_portfolio_analyzer import transactions_ui
    app = stage_samples(start_app(), ["us_brokerage_a.csv"])
    app.button(key="validate_batch").click().run()
    app.button(key="import_batch").click().run()
    monkeypatch.setattr(transactions_ui, "DATA_DIR", tmp_path)
    app.button(key="stage_samples").click().run()
    assert not app.exception
    assert any("sample" in item.value.lower() for item in app.error)
    assert len(app.session_state["transactions"]) == 16


def test_invalid_stored_history_displays_error_instead_of_uncaught_exception():
    from test_portfolio import trade
    app = start_app()
    app.session_state["transactions"] = [trade("SELL")]
    app.run()
    assert not app.exception
    assert app.error and not app.metric


def start_app():
    return AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py")).run(timeout=20)


def stage_samples(app, names):
    app.multiselect(key="sample_files").set_value(names)
    return app.button(key="stage_samples").click().run()


def test_samples_are_previewed_before_explicit_import_and_survive_rerun():
    app = stage_samples(start_app(), ["us_brokerage_a.csv"])
    assert not app.exception
    assert app.session_state["transactions"] == []
    app.button(key="validate_batch").click().run()
    assert not app.exception
    assert app.session_state["transactions"] == []
    app.button(key="import_batch").click().run()
    assert not app.exception
    assert len(app.session_state["transactions"]) == 16
    assert app.session_state["staged_files"] == []
    assert app.session_state["current_review"] is None
    assert app.session_state["review_signature"] is None
    assert not any(button.key in {"validate_batch", "import_batch"} for button in app.button)
    assert len(app.session_state["import_reports"]) == 1
    assert len(app.session_state["import_reports"][0]["review"].accepted) == 16
    app.run()
    assert len(app.session_state["transactions"]) == 16
    assert app.session_state["staged_files"] == []


def test_mapping_keeps_required_fields_visible_and_groups_optional_fields():
    app = stage_samples(start_app(), ["us_brokerage_a.csv"])

    assert any(item.label == "Optional column mappings" for item in app.expander)
    assert any(
        "Unmapped brokerage, exchange, and currency" in item.value
        for item in app.caption
    )
    mapping_labels = {item.label for item in app.selectbox if ":map:" in (item.key or "")}
    assert {"Transaction date column", "Ticker column", "BUY/SELL column",
            "Quantity column", "Price per share column"} <= mapping_labels
    mapping_boxes = {
        item.label: item for item in app.selectbox if ":map:" in (item.key or "")
    }
    assert mapping_boxes["Transaction date column"].options == ["— Not mapped —", "Date"]
    assert mapping_boxes["Ticker column"].options == ["— Not mapped —", "Symbol"]
    assert mapping_boxes["Fees column"].options == ["— Not mapped —", "Fees"]
    for label in ("Brokerage column", "Exchange column", "Currency column",
                  "Transaction ID column"):
        assert label not in mapping_boxes
    assert any("No brokerage, exchange, currency, or transaction ID column" in item.value
               for item in app.caption)
    assert any(box.label == "Show all source columns (advanced)" for box in app.checkbox)


def test_multiple_samples_preserve_all_accounts_and_currencies():
    app = stage_samples(start_app(), ["us_brokerage_a.csv", "us_brokerage_b.csv", "india_brokerage.csv"])
    app.button(key="validate_batch").click().run()
    app.button(key="import_batch").click().run()
    assert not app.exception
    rows = app.session_state["transactions"]
    assert len(rows) == 48
    assert {tx.currency for tx in rows} == {"USD", "INR"}
    assert {tx.brokerage for tx in rows} == {"US Brokerage A", "US Brokerage B", "Indian Brokerage"}
    assert len([tx for tx in rows if tx.ticker == "AAPL" and tx.action == "BUY"]) == 6


def test_reimport_does_not_double_count():
    app = stage_samples(start_app(), ["us_brokerage_a.csv"])
    app.button(key="validate_batch").click().run()
    app.button(key="import_batch").click().run()
    app = stage_samples(app, ["us_brokerage_a.csv"])
    app.button(key="validate_batch").click().run()
    assert not app.exception
    assert app.button(key="import_batch").disabled
    assert len(app.session_state["transactions"]) == 16


def test_invalid_sample_requires_duplicate_decision_and_keeps_rejections_visible():
    app = stage_samples(start_app(), ["invalid_transactions.csv"])
    app.button(key="validate_batch").click().run()
    assert app.button(key="import_batch").disabled
    review = app.session_state["current_review"]
    assert len(review.rejected) == 5
    assert len(review.duplicates) == 1
    # The choice applies only to this review, with the signature in its key.
    next(box for box in app.checkbox if box.label.startswith("Exclude")).check().run()
    app.button(key="import_batch").click().run()
    assert not app.exception
    assert len(app.session_state["transactions"]) == 2
    assert len(app.session_state["import_reports"][-1]["review"].rejected) == 5


def test_changed_mapping_settings_require_fresh_validation():
    app = stage_samples(start_app(), ["us_brokerage_a.csv"])
    app.button(key="validate_batch").click().run()
    next(box for box in app.text_input if box.key.startswith("file:") and box.key.endswith(":brokerage")).set_value("Another account").run()
    assert not app.exception
    assert not any(button.key == "import_batch" for button in app.button)
    assert not app.session_state["transactions"]


def test_manual_sell_uses_the_same_validation_pipeline():
    app = start_app()
    app.text_input(key="manual_brokerage").set_value("Manual account")
    app.text_input(key="manual_ticker").set_value("AAPL")
    app.selectbox(key="manual_action").select("SELL")
    app.button(key="stage_manual").click().run()
    app.button(key="validate_batch").click().run()
    assert not app.exception
    assert len(app.session_state["current_review"].rejected) == 1
    assert app.button(key="import_batch").disabled


@pytest.mark.parametrize("count", [1, 2])
def test_real_file_uploader_handles_single_and_multiple_files(count):
    app = start_app()
    payload = b"Date,Symbol,Action,Shares,Price,Brokerage,Exchange,Currency\n2025-01-02,AAPL,BUY,10,100,Account %d,NASDAQ,USD\n"
    files = [(f"account_{i}.csv", payload % i, "text/csv") for i in range(count)]
    app.file_uploader(key="uploads").set_value(files).run()
    app.button(key="stage_uploads").click().run()
    app.button(key="validate_batch").click().run()
    assert not app.exception
    assert len(app.session_state["current_review"].accepted) == count
    app.button(key="import_batch").click().run()
    assert len(app.session_state["transactions"]) == count
    app.run()
    assert len(app.session_state["transactions"]) == count


def test_malformed_upload_blocks_validation_without_changing_imports():
    app = start_app()
    app.file_uploader(key="uploads").set_value(("bad.csv", b"a,b\n1,2,3\n", "text/csv")).run()
    app.button(key="stage_uploads").click().run()
    assert not app.exception
    assert app.error
    assert app.button(key="validate_batch").disabled
    assert app.session_state["transactions"] == []


def test_including_duplicate_requires_confirmation_and_revalidates_quantity():
    app = stage_samples(start_app(), ["invalid_transactions.csv"])
    app.button(key="validate_batch").click().run()
    selector = next(box for box in app.multiselect if box.key.startswith("include:"))
    duplicate_key = app.session_state["current_review"].duplicates[0].row_key
    selector.set_value([duplicate_key]).run()
    assert app.button(key="import_batch").disabled
    next(box for box in app.checkbox if box.key.startswith("confirm:")).check().run()
    assert len(app.session_state["current_review"].accepted) == 3
    app.button(key="import_batch").click().run()
    assert not app.exception
    assert len(app.session_state["transactions"]) == 3


def test_brokerage_filter_changes_display_without_deleting_transactions():
    app = stage_samples(start_app(), ["us_brokerage_a.csv", "india_brokerage.csv"])
    app.button(key="validate_batch").click().run()
    app.button(key="import_batch").click().run()
    app.multiselect(key="filter:Brokerage").set_value(["Indian Brokerage"]).run()
    assert not app.exception
    assert len(app.session_state["transactions"]) == 32
    displayed = app.tabs[0].dataframe[-1].value
    assert len(displayed) == 16
    assert set(displayed["Currency"]) == {"INR"}


def test_imported_transactions_show_a_compact_history_summary():
    app = stage_samples(start_app(), ["us_brokerage_a.csv"])
    app.button(key="validate_batch").click().run()
    app.button(key="import_batch").click().run()

    captions = [item.value for item in app.tabs[0].caption]
    assert any("16 transactions" in value and "4 securities" in value
               and "BUY" in value and "SELL" in value for value in captions)


def test_later_import_preserves_prior_rejection_and_duplicate_evidence():
    app = stage_samples(start_app(), ["invalid_transactions.csv"])
    app.button(key="validate_batch").click().run()
    next(box for box in app.checkbox if box.label.startswith("Exclude")).check().run()
    app.button(key="import_batch").click().run()
    app = stage_samples(app, ["us_brokerage_a.csv"])
    app.button(key="validate_batch").click().run()
    app.button(key="import_batch").click().run()
    assert not app.exception
    reports = app.session_state["import_reports"]
    assert len(reports) == 2
    assert len(reports[0]["review"].rejected) == 5
    assert len(reports[0]["review"].duplicates) == 1
    assert len(reports[1]["review"].accepted) == 16
    app.selectbox(key="report:2").select(0).run()
    assert any("Rejected records" in error.value for error in app.error)
