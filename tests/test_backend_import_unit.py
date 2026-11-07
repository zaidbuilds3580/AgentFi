"""
Unit tests for CSV import parsing, validation, and sanitization (no DB required).
"""

from decimal import Decimal
import hashlib
from unittest.mock import MagicMock
import uuid
import pytest
from fastapi import HTTPException

from backend.api.imports import _sanitize_filename
from backend.models.constants import (
    ERR_DUPLICATE_HEADERS,
    ERR_EMPTY_FILE,
    ERR_HEADER_ONLY,
    ERR_INVALID_AMOUNT,
    ERR_INVALID_DATE,
    ERR_INVALID_TYPE,
    ERR_MISSING_REQUIRED,
    TRANSACTION_TYPE_INFLOW,
    TRANSACTION_TYPE_OUTFLOW,
)
from backend.services.import_processing import (
    DefaultRowProcessor,
    ProcessingContext,
    RowError,
)
from backend.services.import_service import ImportService


@pytest.fixture
def dummy_context():
    return ProcessingContext(
        business_id=uuid.uuid4(),
        business_currency="INR",
        session=MagicMock(),
        account_map={"primary current": uuid.uuid4()},
        customer_cache={},
        vendor_cache={},
    )


@pytest.fixture
def processor():
    return DefaultRowProcessor()


def test_sanitize_filename():
    assert _sanitize_filename("valid_statement.csv") == "valid_statement.csv"
    assert _sanitize_filename("../../../etc/passwd.csv") == "passwd.csv"
    assert _sanitize_filename("C:\\Users\\admin\\secret.csv") == "secret.csv"
    assert _sanitize_filename("") == "upload.csv"
    assert _sanitize_filename(None) == "upload.csv"


def test_parse_date_formats(processor, dummy_context):
    raw_row_iso = {"transaction_date": "2026-10-15", "transaction_type": "inflow", "amount": "100.00"}
    row, errs = processor.normalize_row("transactions", raw_row_iso, 2, dummy_context)
    assert not errs
    assert str(row["transaction_date"]) == "2026-10-15"

    raw_row_slash = {"transaction_date": "15/10/2026", "transaction_type": "inflow", "amount": "100.00"}
    row, errs = processor.normalize_row("transactions", raw_row_slash, 3, dummy_context)
    assert not errs
    assert str(row["transaction_date"]) == "2026-10-15"

    raw_row_dash = {"transaction_date": "15-10-2026", "transaction_type": "inflow", "amount": "100.00"}
    row, errs = processor.normalize_row("transactions", raw_row_dash, 4, dummy_context)
    assert not errs
    assert str(row["transaction_date"]) == "2026-10-15"

    raw_row_invalid = {"transaction_date": "10.15.2026", "transaction_type": "inflow", "amount": "100.00"}
    row, errs = processor.normalize_row("transactions", raw_row_invalid, 5, dummy_context)
    assert len(errs) == 1
    assert errs[0].code == ERR_INVALID_DATE


def test_parse_amount_rules(processor, dummy_context):
    # Stripping currency symbols and thousands commas
    row, errs = processor.normalize_row(
        "transactions",
        {"transaction_date": "2026-10-01", "transaction_type": "inflow", "amount": "₹ 1,25,000.50"},
        2,
        dummy_context,
    )
    assert not errs
    assert row["amount"] == Decimal("125000.50")

    # Rounding half-up with warning for >2 dp
    row, errs = processor.normalize_row(
        "transactions",
        {"transaction_date": "2026-10-01", "transaction_type": "inflow", "amount": "100.555"},
        3,
        dummy_context,
    )
    assert not errs
    assert row["amount"] == Decimal("100.56")
    assert any("rounded" in w for w in dummy_context.warnings)

    # Reject negative
    _, errs = processor.normalize_row(
        "transactions",
        {"transaction_date": "2026-10-01", "transaction_type": "inflow", "amount": "-500.00"},
        4,
        dummy_context,
    )
    assert any(e.code == ERR_INVALID_AMOUNT for e in errs)

    # Reject zero for transactions
    _, errs = processor.normalize_row(
        "transactions",
        {"transaction_date": "2026-10-01", "transaction_type": "inflow", "amount": "0.00"},
        5,
        dummy_context,
    )
    assert any(e.code == ERR_INVALID_AMOUNT for e in errs)

    # Reject scientific notation
    _, errs = processor.normalize_row(
        "transactions",
        {"transaction_date": "2026-10-01", "transaction_type": "inflow", "amount": "1e4"},
        6,
        dummy_context,
    )
    assert any(e.code == ERR_INVALID_AMOUNT for e in errs)


def test_transaction_type_aliases(processor, dummy_context):
    for alias in ["inflow", "credit", "cr", "income", "deposit", "received"]:
        row, errs = processor.normalize_row(
            "transactions",
            {"transaction_date": "2026-10-01", "transaction_type": alias, "amount": "100"},
            2,
            dummy_context,
        )
        assert not errs, f"Failed for alias {alias}"
        assert row["transaction_type"] == TRANSACTION_TYPE_INFLOW

    for alias in ["outflow", "debit", "dr", "expense", "withdrawal", "paid"]:
        row, errs = processor.normalize_row(
            "transactions",
            {"transaction_date": "2026-10-01", "transaction_type": alias, "amount": "100"},
            3,
            dummy_context,
        )
        assert not errs, f"Failed for alias {alias}"
        assert row["transaction_type"] == TRANSACTION_TYPE_OUTFLOW

    _, errs = processor.normalize_row(
        "transactions",
        {"transaction_date": "2026-10-01", "transaction_type": "random_direction", "amount": "100"},
        4,
        dummy_context,
    )
    assert any(e.code == ERR_INVALID_TYPE for e in errs)


def test_invoice_validation_rules(processor, dummy_context):
    # Due date before invoice date
    _, errs = processor.normalize_row(
        "invoices",
        {
            "invoice_number": "INV-101",
            "customer_name": "Test Customer",
            "invoice_date": "2026-10-20",
            "due_date": "2026-10-10",
            "amount_total": "1000",
        },
        2,
        dummy_context,
    )
    assert any(e.code == "DUE_BEFORE_INVOICE" for e in errs)

    # Paid exceeds total
    _, errs = processor.normalize_row(
        "invoices",
        {
            "invoice_number": "INV-102",
            "customer_name": "Test Customer",
            "invoice_date": "2026-10-01",
            "due_date": "2026-10-20",
            "amount_total": "1000",
            "amount_paid": "1500",
        },
        3,
        dummy_context,
    )
    assert any(e.code == "PAID_EXCEEDS_TOTAL" for e in errs)


def test_header_validation_and_alias_mapping():
    service = ImportService(MagicMock())

    # Empty file
    with pytest.raises(HTTPException) as exc:
        service._parse_and_validate_csv("", "transactions")
    assert exc.value.detail["code"] == ERR_EMPTY_FILE

    # Header only
    with pytest.raises(HTTPException) as exc:
        service._parse_and_validate_csv("date,type,amount\n", "transactions")
    assert exc.value.detail["code"] == ERR_HEADER_ONLY

    # Duplicate headers
    with pytest.raises(HTTPException) as exc:
        service._parse_and_validate_csv("date,amount,amount\n2026-10-01,100,100", "transactions")
    assert exc.value.detail["code"] == ERR_DUPLICATE_HEADERS

    # Missing required headers
    with pytest.raises(HTTPException) as exc:
        service._parse_and_validate_csv("description,reference\nFoo,Bar", "transactions")
    assert exc.value.detail["code"] == ERR_MISSING_REQUIRED

    # Aliases properly mapped
    mapping, rows, warnings = service._parse_and_validate_csv(
        "date,type,amount,account,ref\n2026-10-01,cr,5000,Primary,TX101",
        "transactions",
    )
    assert mapping["date"] == "transaction_date"
    assert mapping["type"] == "transaction_type"
    assert mapping["account"] == "account_name"
    assert mapping["ref"] == "reference"
    assert len(rows) == 1


def test_source_record_id_determinism():
    row_1 = {"transaction_date": "2026-10-01", "transaction_type": "inflow", "amount": Decimal("100.00"), "reference": "REF1"}
    key_repr = f"{row_1['transaction_date']}:{row_1['transaction_type']}:{row_1['amount']}:{row_1['reference']}"

    h1 = hashlib.sha1(f"transactions:{key_repr}:1".encode("utf-8")).hexdigest()
    h1_repeat = hashlib.sha1(f"transactions:{key_repr}:1".encode("utf-8")).hexdigest()
    h2_occurrence = hashlib.sha1(f"transactions:{key_repr}:2".encode("utf-8")).hexdigest()

    assert h1 == h1_repeat
    assert h1 != h2_occurrence
