"""
Unit tests for Day 4 query helpers, filters, sorting, and envelope serialization (no DB required).
"""

from datetime import date
from decimal import Decimal
import uuid
import pytest
from fastapi import HTTPException
from sqlalchemy import Column, Integer, String, select
from sqlalchemy.orm import declarative_base

from backend.schemas.canonical import (
    AccountLookupRead,
    CustomerLookupRead,
    DataRangeResponse,
    DataRangeSummary,
    ExpenseListItemRead,
    InvoiceListItemRead,
    VendorLookupRead,
)
from backend.schemas.common import ExpensePage, ExpenseTotals, Page, TransactionPage, TransactionTotals
from backend.services.query_filters import (
    apply_sorting,
    escape_like_param,
    scoped,
    validate_amount_range,
    validate_date_range,
    validate_pagination,
)

Base = declarative_base()


class DummyModel(Base):
    __tablename__ = "dummy"
    id = Column(Integer, primary_key=True)
    business_id = Column(Integer, nullable=False)
    name = Column(String)
    amount = Column(Integer)


def test_escape_like_param():
    assert escape_like_param(None) is None
    assert escape_like_param("") is None
    assert escape_like_param("   ") is None
    assert escape_like_param("normal") == "%normal%"
    # SQL wildcards % and _ and backslash must be escaped
    assert escape_like_param("100%") == "%100\\%%"
    assert escape_like_param("user_name") == "%user\\_name%"
    assert escape_like_param("path\\to") == "%path\\\\to%"
    # 100 character capping
    long_string = "a" * 150
    escaped = escape_like_param(long_string)
    assert len(escaped) == 102  # % + 100 'a's + %


def test_validate_pagination():
    # Valid bounds
    validate_pagination(50, 0)
    validate_pagination(1, 0)
    validate_pagination(200, 100)

    # Invalid limit < 1
    with pytest.raises(HTTPException) as exc:
        validate_pagination(0, 0)
    assert exc.value.status_code == 422
    assert exc.value.detail["code"] == "INVALID_LIMIT"

    # Invalid limit > 200
    with pytest.raises(HTTPException) as exc:
        validate_pagination(201, 0)
    assert exc.value.status_code == 422
    assert exc.value.detail["code"] == "INVALID_LIMIT"

    # Invalid offset < 0
    with pytest.raises(HTTPException) as exc:
        validate_pagination(50, -1)
    assert exc.value.status_code == 422
    assert exc.value.detail["code"] == "INVALID_OFFSET"


def test_validate_date_range():
    # Valid ranges
    validate_date_range(None, None)
    validate_date_range(date(2026, 1, 1), None)
    validate_date_range(None, date(2026, 1, 31))
    validate_date_range(date(2026, 1, 1), date(2026, 1, 31))
    validate_date_range(date(2026, 1, 1), date(2026, 1, 1))

    # Inverted range
    with pytest.raises(HTTPException) as exc:
        validate_date_range(date(2026, 2, 1), date(2026, 1, 1))
    assert exc.value.status_code == 422
    assert exc.value.detail["code"] == "INVALID_DATE_RANGE"


def test_validate_amount_range():
    # Valid ranges
    validate_amount_range(None, None)
    validate_amount_range(Decimal("10.00"), None)
    validate_amount_range(None, Decimal("100.00"))
    validate_amount_range(Decimal("10.00"), Decimal("100.00"))
    validate_amount_range(Decimal("50.00"), Decimal("50.00"))

    # Negative min_amount
    with pytest.raises(HTTPException) as exc:
        validate_amount_range(Decimal("-1.00"), Decimal("100.00"))
    assert exc.value.status_code == 422
    assert exc.value.detail["code"] == "INVALID_AMOUNT"

    # Negative max_amount
    with pytest.raises(HTTPException) as exc:
        validate_amount_range(Decimal("10.00"), Decimal("-5.00"))
    assert exc.value.status_code == 422
    assert exc.value.detail["code"] == "INVALID_AMOUNT"

    # min_amount > max_amount
    with pytest.raises(HTTPException) as exc:
        validate_amount_range(Decimal("100.00"), Decimal("50.00"))
    assert exc.value.status_code == 422
    assert exc.value.detail["code"] == "INVALID_AMOUNT_RANGE"


def test_apply_sorting_whitelist():
    query = select(DummyModel)
    allowed = {"name": DummyModel.name, "amount": DummyModel.amount}

    # Valid ascending
    sorted_asc = apply_sorting(query, "name", "asc", allowed, DummyModel.id)
    assert "ORDER BY dummy.name ASC, dummy.id ASC" in str(sorted_asc)

    # Valid descending
    sorted_desc = apply_sorting(query, "amount", "desc", allowed, DummyModel.id)
    assert "ORDER BY dummy.amount DESC, dummy.id DESC" in str(sorted_desc)

    # Invalid sort column
    with pytest.raises(HTTPException) as exc:
        apply_sorting(query, "unknown_col", "asc", allowed, DummyModel.id)
    assert exc.value.status_code == 422
    assert exc.value.detail["code"] == "INVALID_SORT_BY"

    # Invalid sort order
    with pytest.raises(HTTPException) as exc:
        apply_sorting(query, "name", "side", allowed, DummyModel.id)
    assert exc.value.status_code == 422
    assert exc.value.detail["code"] == "INVALID_SORT_ORDER"


def test_scoped_query_tenant_isolation():
    b_id = uuid.uuid4()
    q = scoped(DummyModel, b_id)
    # Checks that WHERE business_id = :b_id is added
    assert "WHERE dummy.business_id =" in str(q)


def test_page_envelope_schemas():
    page = Page[str](items=["apple", "banana"], total=10, limit=2, offset=0, has_more=True)
    assert page.total == 10
    assert page.has_more is True
    assert len(page.items) == 2

    # Totals schemas
    tx_totals = TransactionTotals(
        count=5,
        inflow_total=Decimal("1500.50"),
        outflow_total=Decimal("500.00"),
        net=Decimal("1000.50"),
    )
    assert tx_totals.net == Decimal("1000.50")

    exp_totals = ExpenseTotals(count=3, total_amount=Decimal("450.00"))
    assert exp_totals.total_amount == Decimal("450.00")
