"""
Day-3 ETL tests: cleaning, normalisation, de-duplication, quarantine, incremental loads.
"""

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from finance_engine.etl import (
    clean_text,
    dedupe,
    description_key,
    load_processed,
    party_key,
    run_business,
    write_run_outputs,
)
from finance_engine.etl.cleaning import display_name
from finance_engine.schema import InvoiceStatus
from finance_engine.schema.validators import Severity, validate_dataset
from scripts.run_etl import main as run_etl_main

SAMPLES = Path(__file__).resolve().parents[1] / "data" / "samples" / "biz_001"
TODAY = date(2026, 11, 5)


# ------------------------------- cleaning ----------------------------------- #

@pytest.mark.parametrize("raw, expected", [
    ("  1,000.00​ ", "1,000.00"),            # NBSP + zero-width space
    ("﻿Date", "Date"),                              # BOM glued to a header
    ("“Broadband – Nov”", '"Broadband - Nov"'),  # smart quotes, en dash
    ("１２３", "123"),                       # full-width digits
    ("MISC   DR\t5531 ", "MISC DR 5531"),
    ("'0012345", "0012345"),                             # Excel text-protect apostrophe
    (None, ""),
])
def test_clean_text(raw, expected):
    assert clean_text(raw) == expected


@pytest.mark.parametrize("a, b", [
    ("Orange Realty LLP", "M/s. Orange Realty L.L.P."),
    ("BrightPath Learning", "BRIGHTPATH LEARNING PVT. LTD."),
    ("GreenLeaf Organics", "Greenleaf Organics Private Limited"),
    ("Mehta & Associates", "Mehta and Associates"),
    ("Airtel Business ", "airtel business"),
])
def test_party_key_collapses_spelling_variants(a, b):
    assert party_key(a) == party_key(b)


def test_party_key_keeps_distinct_names_distinct():
    assert party_key("Orange Realty LLP") != party_key("Orange Retail LLP")
    assert party_key("The Company Ltd") == "company"      # never reduced to an empty key
    assert party_key("Ltd") == "ltd"


def test_display_name_prefers_readable_spelling():
    assert display_name("ORANGE REALTY LLP", "M/s. Orange Realty L.L.P.") == "Orange Realty L.L.P."
    assert display_name("BRIGHTPATH LEARNING PVT. LTD.", "BrightPath Learning") == "BrightPath Learning"


def test_description_key_ignores_case_space_punctuation():
    assert description_key("MISC  DR 5531 ") == description_key("misc dr-5531")


# ------------------------------- dedupe ------------------------------------- #

def _txn(tid, src, ref=None, desc="UBER", amount="250.00", day=10):
    return {"business_id": "b", "transaction_id": tid, "account_id": "a", "transaction_date": date(2026, 10, day),
            "amount": Decimal(amount), "direction": "outflow", "description": desc,
            "source_ref": ref, "_source_file": src}


def test_same_bank_ref_is_duplicate_across_files():
    res = dedupe({"transactions": [_txn("t1", "f1", ref="R1"), _txn("t2", "f2", ref="R1")]})
    assert [t["transaction_id"] for t in res.rows["transactions"]] == ["t1"]
    assert res.report.issues[0].rule == "D-01" and res.report.issues[0].severity == Severity.info


def test_same_bank_ref_twice_in_one_file_is_a_warning():
    res = dedupe({"transactions": [_txn("t1", "f1", ref="R1"), _txn("t2", "f1", ref="R1")]})
    assert len(res.rows["transactions"]) == 1
    assert res.report.issues[0].severity == Severity.warning


def test_identical_rows_in_one_file_are_kept():
    """Two ₹250 Uber rides on the same day are real; only the validator flags them (W-03)."""
    res = dedupe({"transactions": [_txn("t1", "f1"), _txn("t2", "f1")]})
    assert len(res.rows["transactions"]) == 2


def test_overlap_matching_is_count_aware():
    rows = [_txn("a1", "A"), _txn("a2", "A"),                     # file A: 2 identical rides
            _txn("b1", "B"), _txn("b2", "B"), _txn("b3", "B")]    # file B overlaps, has a 3rd
    res = dedupe({"transactions": rows})
    assert [t["transaction_id"] for t in res.rows["transactions"]] == ["a1", "a2", "b3"]
    assert res.report.rules_hit() == {"D-02"}


def test_different_bank_refs_are_never_merged():
    res = dedupe({"transactions": [_txn("t1", "f1", ref="R1"), _txn("t2", "f2", ref="R2")]})
    assert len(res.rows["transactions"]) == 2


def test_newest_document_wins_and_gaps_are_filled():
    old = {"business_id": "b", "invoice_id": "inv-1", "status": "sent", "amount_paid": "0",
           "customer_id": "c", "_source_file": "old.csv", "ingested_at": "2026-10-01T00:00:00Z"}
    new = {"business_id": "b", "invoice_id": "inv-1", "status": "paid", "amount_paid": "100",
           "customer_id": None, "_source_file": "new.csv"}
    res = dedupe({"invoices": [old, new]})
    (kept,) = res.rows["invoices"]
    assert kept["status"] == "paid" and kept["customer_id"] == "c"
    assert kept["ingested_at"] == "2026-10-01T00:00:00Z"   # original ingestion time preserved
    assert res.report.issues[0].rule == "D-03"


# ------------------------------- pipeline ----------------------------------- #

@pytest.fixture
def result():
    return run_business(SAMPLES, today=TODAY)


def test_stage_counts(result):
    # Oct + overlapping Oct/Nov statements (17 rows) + Jul-Sep history statement (28 rows)
    assert result.stats["extracted"] == {"counterparties": 17, "transactions": 45, "invoices": 15, "expenses": 9}
    assert result.stats["duplicates_removed"] == {"counterparties": 5, "transactions": 2, "invoices": 1, "expenses": 1}
    assert result.stats["quarantined"] == {"invoices": 1, "expenses": 1}
    assert result.stats["loaded"] == {"businesses": 1, "accounts": 1, "counterparties": 12,
                                      "transactions": 43, "invoices": 13, "expenses": 8}


def test_loaded_data_is_error_free(result):
    assert result.clean
    assert validate_dataset(result.datasets, today=TODAY).ok


def test_overlapping_statements_deduplicated(result):
    refs = [t.source_ref for t in result.datasets["transactions"] if t.source_ref]
    assert len(refs) == len(set(refs))
    misc = [t for t in result.datasets["transactions"] if "MISC" in t.description]
    assert len(misc) == 1   # no bank ref: matched on date/amount/narration across files


def test_counterparty_variants_merged(result):
    names = {c.counterparty_id: c.name for c in result.datasets["counterparties"]}
    assert names["vend-orange-realty"] == "Orange Realty L.L.P."
    inv_1003 = next(i for i in result.datasets["invoices"] if i.invoice_id == "inv-1003")
    assert inv_1003.customer_id == "cust-brightpath-learning"


def test_newer_invoice_export_wins(result):
    inv = next(i for i in result.datasets["invoices"] if i.invoice_id == "inv-1002")
    assert inv.status == InvoiceStatus.overdue and inv.amount_paid == Decimal("50000.00")
    assert "W-06" not in result.report.rules_hit()   # bank receipts (30k + 20k) now reconcile


def test_unicode_junk_cleaned_and_linked(result):
    airtel = next(e for e in result.datasets["expenses"] if e.vendor_id == "vend-airtel-business")
    assert airtel.amount == Decimal("1180.00") and airtel.currency == "INR"
    assert airtel.description == '"Broadband - Nov"'
    assert airtel.transaction_id is not None


def test_bad_rows_quarantined_with_reasons(result):
    (inv_row, inv_reasons), = result.quarantine["invoices"]
    assert inv_row["invoice_id"] == "inv-1007" and "due_date" in inv_reasons[0]
    (exp_row, exp_reasons), = result.quarantine["expenses"]
    assert exp_row["Date"] == "31/02/2026" and exp_reasons[0].startswith("M-02")


def test_quarantine_cascades_to_dependent_records(tmp_path):
    """If a record is quarantined for an X-error, records pointing at it are re-checked."""
    from finance_engine.schema import Account, Business, Expense, Transaction
    biz = Business(business_id="b", name="B")
    acc = Account(business_id="b", account_id="acc", name="A", currency="INR")
    bad_txn = Transaction(business_id="b", transaction_id="t1", account_id="acc", transaction_date=date(2026, 10, 1),
                          amount="10", currency="USD", direction="outflow", category="rent")  # X-07
    exp = Expense(business_id="b", expense_id="e1", expense_date=date(2026, 10, 1), amount="10",
                  is_paid=True, transaction_id="t1")
    from finance_engine.etl.pipeline import _validate_and_quarantine
    from finance_engine.schema.validators import ValidationReport
    rows = {"businesses": [biz.model_dump()], "accounts": [acc.model_dump()],
            "transactions": [bad_txn.model_dump()], "expenses": [exp.model_dump()]}
    quarantine = {"transactions": [], "expenses": []}
    datasets, remaining = _validate_and_quarantine(rows, quarantine, ValidationReport(), TODAY)
    assert remaining == 0
    assert datasets["transactions"] == [] and datasets["expenses"] == []
    assert len(quarantine["transactions"]) == 1 and len(quarantine["expenses"]) == 1


def test_incremental_rerun_is_idempotent(tmp_path):
    first = run_business(SAMPLES, today=TODAY, processed_dir=tmp_path)
    write_run_outputs(first, tmp_path)
    second = run_business(SAMPLES, today=TODAY, processed_dir=tmp_path)
    assert second.stats["existing"] == first.stats["loaded"]
    assert second.stats["loaded"] == first.stats["loaded"]
    ids = lambda r, e: sorted(getattr(m, k) for m in r.datasets[e] for k in [ {"transactions": "transaction_id", "expenses": "expense_id", "invoices": "invoice_id"}[e] ])
    for entity in ("transactions", "invoices", "expenses"):
        assert ids(first, entity) == ids(second, entity)


def test_run_outputs_and_reload(tmp_path, result):
    written = {p.relative_to(tmp_path).as_posix() for p in write_run_outputs(result, tmp_path)}
    assert {"transactions.csv", "_quarantine/invoices.csv", "_quarantine/expenses.csv",
            "_duplicates.csv", "_etl_report.json"} <= written
    report = json.loads((tmp_path / "_etl_report.json").read_text(encoding="utf-8"))
    assert report["clean"] is True and report["stats"]["loaded"]["transactions"] == 43
    reloaded = load_processed(tmp_path)
    assert len(reloaded["transactions"]) == 43
    assert isinstance(reloaded["invoices"][0]["line_items"], list)
    assert not list(tmp_path.glob("*.tmp"))   # atomic writes leave no temp files


def test_cli_exit_codes(tmp_path):
    args = [str(SAMPLES), "--out", str(tmp_path), "--today", "2026-11-05"]
    assert run_etl_main(args) == 2                       # rows quarantined, clean data written
    assert (tmp_path / "transactions.csv").exists()
    assert run_etl_main(["does/not/exist"]) == 1
    dry = tmp_path / "dry"
    run_etl_main([str(SAMPLES), "--out", str(dry), "--dry-run"])
    assert not dry.exists()
