"""
Internal programmatic read API for Members 3 & 4 (Finance Engine & LangGraph Agents).
Provides fast, tenant-scoped, type-safe data access without going through HTTP endpoints.
"""

from datetime import date
from typing import List, Optional
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models.expenses import Expense
from backend.models.invoices import Invoice
from backend.models.transactions import Transaction
from backend.schemas.canonical import ExpenseRead, InvoiceRead, TransactionRead

MAX_INTERNAL_ROWS = 100000


def fetch_transactions(
    db: Session,
    business_id: uuid.UUID,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    account_id: Optional[uuid.UUID] = None,
    categories: Optional[List[str]] = None,
    transaction_type: Optional[str] = None,
    limit: Optional[int] = None,
) -> List[TransactionRead]:
    """
    Programmatic fetch for transactions scoped to business_id.
    Used by cash flow calculations, analytics, and agent tools.
    """
    if limit is not None and limit > MAX_INTERNAL_ROWS:
        raise ValueError(
            f"Requested limit {limit} exceeds maximum allowed internal rows ({MAX_INTERNAL_ROWS})."
        )

    stmt = select(Transaction).where(Transaction.business_id == business_id)

    if start_date:
        stmt = stmt.where(Transaction.transaction_date >= start_date)
    if end_date:
        stmt = stmt.where(Transaction.transaction_date <= end_date)
    if account_id:
        stmt = stmt.where(Transaction.account_id == account_id)
    if categories:
        cleaned = [c.strip() for c in categories if c.strip()]
        if cleaned:
            stmt = stmt.where(Transaction.category.in_(cleaned))
    if transaction_type:
        stmt = stmt.where(Transaction.transaction_type == transaction_type.strip().lower())

    stmt = stmt.order_by(Transaction.transaction_date.asc(), Transaction.id.asc())

    effective_limit = limit or MAX_INTERNAL_ROWS
    stmt = stmt.limit(effective_limit + 1)

    records = db.scalars(stmt).all()
    if len(records) > MAX_INTERNAL_ROWS:
        raise ValueError(
            f"Query result exceeds maximum allowed internal rows ({MAX_INTERNAL_ROWS}). Please narrow your date range."
        )

    return [TransactionRead.model_validate(r) for r in records[:effective_limit]]


def fetch_invoices(
    db: Session,
    business_id: uuid.UUID,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    customer_id: Optional[uuid.UUID] = None,
    status: Optional[str] = None,
    limit: Optional[int] = None,
) -> List[InvoiceRead]:
    """
    Programmatic fetch for invoices scoped to business_id.
    Used by AR aging, revenue forecasts, and invoice agents.
    """
    if limit is not None and limit > MAX_INTERNAL_ROWS:
        raise ValueError(
            f"Requested limit {limit} exceeds maximum allowed internal rows ({MAX_INTERNAL_ROWS})."
        )

    stmt = select(Invoice).where(Invoice.business_id == business_id)

    if start_date:
        stmt = stmt.where(Invoice.invoice_date >= start_date)
    if end_date:
        stmt = stmt.where(Invoice.invoice_date <= end_date)
    if customer_id:
        stmt = stmt.where(Invoice.customer_id == customer_id)
    if status:
        stmt = stmt.where(Invoice.status == status.strip().lower())

    stmt = stmt.order_by(Invoice.due_date.asc(), Invoice.id.asc())

    effective_limit = limit or MAX_INTERNAL_ROWS
    stmt = stmt.limit(effective_limit + 1)

    records = db.scalars(stmt).all()
    if len(records) > MAX_INTERNAL_ROWS:
        raise ValueError(
            f"Query result exceeds maximum allowed internal rows ({MAX_INTERNAL_ROWS}). Please narrow your date range."
        )

    return [InvoiceRead.model_validate(r) for r in records[:effective_limit]]


def fetch_expenses(
    db: Session,
    business_id: uuid.UUID,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    category: Optional[str] = None,
    vendor_id: Optional[uuid.UUID] = None,
    limit: Optional[int] = None,
) -> List[ExpenseRead]:
    """
    Programmatic fetch for expenses scoped to business_id.
    Used by spend analytics, burn-rate calculators, and expense agents.
    """
    if limit is not None and limit > MAX_INTERNAL_ROWS:
        raise ValueError(
            f"Requested limit {limit} exceeds maximum allowed internal rows ({MAX_INTERNAL_ROWS})."
        )

    stmt = select(Expense).where(Expense.business_id == business_id)

    if start_date:
        stmt = stmt.where(Expense.expense_date >= start_date)
    if end_date:
        stmt = stmt.where(Expense.expense_date <= end_date)
    if category:
        stmt = stmt.where(Expense.category == category.strip())
    if vendor_id:
        stmt = stmt.where(Expense.vendor_id == vendor_id)

    stmt = stmt.order_by(Expense.expense_date.asc(), Expense.id.asc())

    effective_limit = limit or MAX_INTERNAL_ROWS
    stmt = stmt.limit(effective_limit + 1)

    records = db.scalars(stmt).all()
    if len(records) > MAX_INTERNAL_ROWS:
        raise ValueError(
            f"Query result exceeds maximum allowed internal rows ({MAX_INTERNAL_ROWS}). Please narrow your date range."
        )

    return [ExpenseRead.model_validate(r) for r in records[:effective_limit]]
