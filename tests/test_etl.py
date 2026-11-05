"""
Tests for the ETL layer: parsing helpers, source mappers and the end-to-end pipeline.
"""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from finance_engine.etl import (
    BANK_LAYOUTS,
    map_bank_statement,
    map_expense_sheet,
    map_invoice_export,
    run_business,
    write_processed,
)
from finance_engine.etl.parsing import (
    categorize,
    find_invoice_number,
    parse_amount,
    parse_date,
    parse_invoice_status,
    parse_percent,
)
from finance_engine.schema import ExpenseCategory, InvoiceStatus, TransactionDirection

SAMPLES = Path(__file__).resolve().parents[1] / "data" / "samples" / "biz_001"
TODAY = date(2026, 11, 5)


# ------------------------------- parsing ------------------------------------ #

@pytest.mark.parametrize("raw, expected", [
    ("1,00,000.00", (Decimal("100000.00"), None)),
    ("₹ 2,500.5", (Decimal("2500.50"), None)),
    ("1,234.00 Dr", (Decimal("1234.00"), "dr")),
    ("500 CR", (Decimal("500.00"), "cr")),
    ("(750.00)", (Decimal("750.00"), "-")),
    ("-42", (Decimal("42.00"), "-")),
    ("", None),
    ("  -  ", None),
])
def test_parse_amount(raw, expected):
    assert parse_amount(raw) == expected


def test_parse_amount_rejects_garbage():
    with pytest.raises(ValueError):
        parse_amount("twelve")


def test_parse_date_day_first():
    assert parse_date("05/10/26") == date(2026, 10, 5)
    assert parse_date("05 Oct 2026") == date(2026, 10, 5)
    assert parse_date("2026-10-05") == date(2026, 10, 5)
    with pytest.raises(ValueError):
        parse_date("Opening Balance")


def test_parse_percent_and_status():
    assert parse_percent("18%") == Decimal("0.18")
    assert parse_percent("0.05") == Decimal("0.05")
    assert parse_invoice_status("Partially Paid") == InvoiceStatus.partially_paid
    assert parse_invoice_status("Cancelled") == InvoiceStatus.void
    assert parse_invoice_status("weird") is None


def test_categorize_uses_word_boundaries():
    assert categorize("SALARY OCT26 BULK UPLOAD") == ExpenseCategory.payroll
    assert categorize("ACH D- AMAZON WEB SERVICES") == ExpenseCategory.software
    assert categorize("Office Rent") == ExpenseCategory.rent
    assert categorize("current account charges") is None   # 'rent' inside 'current'
    assert categorize("premium widgets") is None           # 'emi' inside 'premium'


def test_find_invoice_number():
    assert find_invoice_number("NEFT CR-XYZ-INV-1001") == "INV-1001"
    assert find_invoice_number("payment for inv/42") == "INV-42"
    assert find_invoice_number("no reference") is None


# ------------------------------- mappers ------------------------------------ #

def test_hdfc_statement_mapping():
    res = map_bank_statement(SAMPLES / "bank_csv" / "hdfc_statement_oct_2026.csv", "biz_001", "acc_hdfc_current",
                             opening_balance=Decimal("100000.00"))
    txns = res.rows["transactions"]
    assert len(txns) == 11                         # preamble, separators, footer, zero-amount row skipped
    first = txns[0]
    assert first["direction"] == TransactionDirection.inflow
    assert first["amount"] == Decimal("59000.00")
    assert first["source_ref"] == "N274261234567"  # leading zeros stripped
    assert first["invoice_id"] == "inv-1001"
    rent = txns[1]
    assert rent["direction"] == TransactionDirection.outflow and rent["category"] == ExpenseCategory.rent
    assert res.report.rules_hit() == {"M-04"}      # only the zero-amount interest row; balances reconcile


def test_running_balance_mismatch_detected(tmp_path):
    f = tmp_path / "hdfc.csv"
    f.write_text(
        "Date,Narration,Chq./Ref.No.,Value Dt,Withdrawal Amt.,Deposit Amt.,Closing Balance\n"
        "01/10/26,A,1,01/10/26,100.00,,900.00\n"
        "02/10/26,B,2,02/10/26,100.00,,700.00\n",   # a 100.00 row is missing
        encoding="utf-8",
    )
    res = map_bank_statement(f, "biz_001", "acc_1", opening_balance=Decimal("1000"))
    assert "W-12" in res.report.rules_hit()


def test_signed_amount_layout(tmp_path):
    f = tmp_path / "generic.csv"
    f.write_text(
        "Date,Description,Reference,Amount,Type,Balance\n"
        "2026-10-01,Client payment,R1,5000,CR,5000\n"
        "2026-10-02,Uber trip,R2,-250,,4750\n",
        encoding="utf-8",
    )
    res = map_bank_statement(f, "biz_001", "acc_1", layout=BANK_LAYOUTS["generic_bank_csv"],
                             opening_balance=Decimal("0"))
    t1, t2 = res.rows["transactions"]
    assert t1["direction"] == TransactionDirection.inflow
    assert t2["direction"] == TransactionDirection.outflow and t2["category"] == ExpenseCategory.travel
    assert res.report.issues == []


def test_missing_header_is_reported(tmp_path):
    f = tmp_path / "bad.csv"
    f.write_text("foo,bar\n1,2\n", encoding="utf-8")
    res = map_bank_statement(f, "biz_001", "acc_1")
    assert res.report.rules_hit() == {"M-01"}


def test_invoice_export_groups_line_items():
    res = map_invoice_export(SAMPLES / "invoices" / "zoho_invoices_export.csv", "biz_001")
    invoices = {i["invoice_id"]: i for i in res.rows["invoices"]}
    assert len(invoices) == 5
    assert len(invoices["inv-1002"]["line_items"]) == 2
    assert invoices["inv-1002"]["status"] == InvoiceStatus.partially_paid
    customers = {c["counterparty_id"]: c for c in res.rows["counterparties"]}
    assert customers["cust-brightpath-learning"]["payment_terms_days"] == 15
    assert res.report.issues == []


def test_expense_sheet_mapping():
    res = map_expense_sheet(SAMPLES / "expenses" / "expense_register.csv", "biz_001")
    cats = [e["category"] for e in res.rows["expenses"]]
    assert cats[:4] == [ExpenseCategory.rent, ExpenseCategory.software,
                        ExpenseCategory.utilities, ExpenseCategory.supplies]
    assert len(res.rows["expenses"]) == 9      # 31/02/2026 row rejected
    assert res.report.rules_hit() == {"M-07", "M-02"}  # unknown 'Team snacks' category; impossible date
    assert res.rejected[0][2].startswith("M-02")


# ------------------------------- pipeline ----------------------------------- #
# Full end-to-end behaviour (dedupe, quarantine, incremental runs) is in test_etl_pipeline.py.

def test_write_processed_writes_one_file_per_entity(tmp_path):
    result = run_business(SAMPLES, today=TODAY)
    written = write_processed(result.datasets, tmp_path)
    assert {p.name for p in written} == {f"{k}.csv" for k in result.datasets}
