"""
Lookup services for lightweight UI dropdowns and metadata ranges.
"""

from typing import List, Optional
import uuid

from fastapi import HTTPException, status
from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session

from backend.models.accounts import Account
from backend.models.customers import Customer
from backend.models.expenses import Expense
from backend.models.invoices import Invoice
from backend.models.transactions import Transaction
from backend.models.vendors import Vendor
from backend.schemas.canonical import (
    AccountLookupRead,
    CustomerLookupRead,
    DataRangeResponse,
    DataRangeSummary,
    VendorLookupRead,
)
from backend.services.query_filters import escape_like_param, scoped


def get_accounts(
    db: Session,
    business_id: uuid.UUID,
) -> List[AccountLookupRead]:
    """
    Returns active accounts for the business, ordered by name, limit 500.
    """
    stmt = (
        scoped(Account, business_id)
        .where(Account.is_active.is_(True))
        .order_by(Account.name.asc())
        .limit(500)
    )
    records = db.scalars(stmt).all()
    return [AccountLookupRead.model_validate(r) for r in records]


def get_customers(
    db: Session,
    business_id: uuid.UUID,
    q: Optional[str] = None,
) -> List[CustomerLookupRead]:
    """
    Returns customers matching optional search term q, ordered by name, limit 500.
    """
    stmt = scoped(Customer, business_id)
    escaped_q = escape_like_param(q)
    if escaped_q:
        stmt = stmt.where(Customer.name.ilike(escaped_q))
    stmt = stmt.order_by(Customer.name.asc()).limit(500)
    records = db.scalars(stmt).all()
    return [CustomerLookupRead.model_validate(r) for r in records]


def get_vendors(
    db: Session,
    business_id: uuid.UUID,
    q: Optional[str] = None,
) -> List[VendorLookupRead]:
    """
    Returns vendors matching optional search term q, ordered by name, limit 500.
    """
    stmt = scoped(Vendor, business_id)
    escaped_q = escape_like_param(q)
    if escaped_q:
        stmt = stmt.where(Vendor.name.ilike(escaped_q))
    stmt = stmt.order_by(Vendor.name.asc()).limit(500)
    records = db.scalars(stmt).all()
    return [VendorLookupRead.model_validate(r) for r in records]


def get_categories(
    db: Session,
    business_id: uuid.UUID,
    kind: str,
) -> List[str]:
    """
    Returns distinct category values for transaction or expense.
    """
    cleaned_kind = (kind or "").strip().lower()
    if cleaned_kind == "transaction":
        stmt = (
            select(distinct(Transaction.category))
            .where(Transaction.business_id == business_id)
            .where(Transaction.category.is_not(None))
            .where(Transaction.category != "")
            .order_by(Transaction.category.asc())
        )
    elif cleaned_kind == "expense":
        stmt = (
            select(distinct(Expense.category))
            .where(Expense.business_id == business_id)
            .where(Expense.category.is_not(None))
            .where(Expense.category != "")
            .order_by(Expense.category.asc())
        )
    else:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "INVALID_CATEGORY_KIND",
                "message": f"kind must be 'transaction' or 'expense' (received '{kind}').",
            },
        )

    rows = db.execute(stmt).scalars().all()
    return [str(r) for r in rows if r]


def get_data_range(
    db: Session,
    business_id: uuid.UUID,
) -> DataRangeResponse:
    """
    Calculates date ranges and total records for transactions, invoices, and expenses.
    """
    t_stmt = select(
        func.min(Transaction.transaction_date).label("min_d"),
        func.max(Transaction.transaction_date).label("max_d"),
        func.count(Transaction.id).label("cnt"),
    ).where(Transaction.business_id == business_id)
    t_row = db.execute(t_stmt).one()
    t_sum = DataRangeSummary(
        min_date=t_row.min_d,
        max_date=t_row.max_d,
        count=t_row.cnt or 0,
    )

    i_stmt = select(
        func.min(Invoice.invoice_date).label("min_d"),
        func.max(Invoice.invoice_date).label("max_d"),
        func.count(Invoice.id).label("cnt"),
    ).where(Invoice.business_id == business_id)
    i_row = db.execute(i_stmt).one()
    i_sum = DataRangeSummary(
        min_date=i_row.min_d,
        max_date=i_row.max_d,
        count=i_row.cnt or 0,
    )

    e_stmt = select(
        func.min(Expense.expense_date).label("min_d"),
        func.max(Expense.expense_date).label("max_d"),
        func.count(Expense.id).label("cnt"),
    ).where(Expense.business_id == business_id)
    e_row = db.execute(e_stmt).one()
    e_sum = DataRangeSummary(
        min_date=e_row.min_d,
        max_date=e_row.max_d,
        count=e_row.cnt or 0,
    )

    return DataRangeResponse(
        transactions=t_sum,
        invoices=i_sum,
        expenses=e_sum,
    )
