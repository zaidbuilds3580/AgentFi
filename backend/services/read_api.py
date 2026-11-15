"""
Internal programmatic read API for Members 3 & 4 (Finance Engine & LangGraph Agents).
Provides fast, tenant-scoped, type-safe data access without going through HTTP endpoints.

Supports passing either a raw business_id (UUID/str) or a server-injected ToolContext.
"""

from datetime import date
from typing import List, Optional, Union
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models.accounts import Account
from backend.models.customers import Customer
from backend.models.expenses import Expense
from backend.models.invoices import Invoice
from backend.models.transactions import Transaction
from backend.models.vendors import Vendor
from backend.schemas.canonical import (
    AccountLookupRead,
    AccountRead,
    CustomerLookupRead,
    CustomerRead,
    ExpenseRead,
    InvoiceRead,
    TransactionRead,
    VendorLookupRead,
    VendorRead,
)
from backend.services.query_filters import scoped
from backend.services.tool_context import ToolContext

MAX_INTERNAL_ROWS = 100000


def _resolve_business_uuid(target: Union[uuid.UUID, str, ToolContext]) -> uuid.UUID:
    """Helper to safely extract business UUID from ToolContext, str, or UUID."""
    if isinstance(target, ToolContext):
        return target.business_uuid
    if isinstance(target, uuid.UUID):
        return target
    return uuid.UUID(str(target))


def fetch_transactions(
    db: Session,
    business_id: Union[uuid.UUID, str, ToolContext],
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    account_id: Optional[uuid.UUID] = None,
    categories: Optional[List[str]] = None,
    transaction_type: Optional[str] = None,
    limit: Optional[int] = None,
) -> List[TransactionRead]:
    """
    Programmatic fetch for transactions scoped to business_id or ToolContext.
    Used by cash flow calculations, analytics, and agent tools.
    """
    biz_uuid = _resolve_business_uuid(business_id)

    if limit is not None and limit > MAX_INTERNAL_ROWS:
        raise ValueError(
            f"Requested limit {limit} exceeds maximum allowed internal rows ({MAX_INTERNAL_ROWS})."
        )

    stmt = scoped(Transaction, biz_uuid)

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
    business_id: Union[uuid.UUID, str, ToolContext],
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    customer_id: Optional[uuid.UUID] = None,
    status: Optional[str] = None,
    limit: Optional[int] = None,
) -> List[InvoiceRead]:
    """
    Programmatic fetch for invoices scoped to business_id or ToolContext.
    Used by AR aging, revenue forecasts, and invoice agents.
    """
    biz_uuid = _resolve_business_uuid(business_id)

    if limit is not None and limit > MAX_INTERNAL_ROWS:
        raise ValueError(
            f"Requested limit {limit} exceeds maximum allowed internal rows ({MAX_INTERNAL_ROWS})."
        )

    stmt = scoped(Invoice, biz_uuid)

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
    business_id: Union[uuid.UUID, str, ToolContext],
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    category: Optional[str] = None,
    vendor_id: Optional[uuid.UUID] = None,
    limit: Optional[int] = None,
) -> List[ExpenseRead]:
    """
    Programmatic fetch for expenses scoped to business_id or ToolContext.
    Used by spend analytics, burn-rate calculators, and expense agents.
    """
    biz_uuid = _resolve_business_uuid(business_id)

    if limit is not None and limit > MAX_INTERNAL_ROWS:
        raise ValueError(
            f"Requested limit {limit} exceeds maximum allowed internal rows ({MAX_INTERNAL_ROWS})."
        )

    stmt = scoped(Expense, biz_uuid)

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


def fetch_accounts(
    db: Session,
    business_id: Union[uuid.UUID, str, ToolContext],
    is_active: bool = True,
) -> List[AccountLookupRead]:
    """
    Programmatic fetch for financial accounts scoped to business tenant.
    """
    biz_uuid = _resolve_business_uuid(business_id)
    stmt = scoped(Account, biz_uuid).where(Account.is_active == is_active).order_by(Account.name.asc())
    records = db.scalars(stmt).all()
    return [AccountLookupRead.model_validate(r) for r in records]


def fetch_customers(
    db: Session,
    business_id: Union[uuid.UUID, str, ToolContext],
) -> List[CustomerLookupRead]:
    """
    Programmatic fetch for customers scoped to business tenant.
    """
    biz_uuid = _resolve_business_uuid(business_id)
    stmt = scoped(Customer, biz_uuid).order_by(Customer.name.asc())
    records = db.scalars(stmt).all()
    return [CustomerLookupRead.model_validate(r) for r in records]


def fetch_vendors(
    db: Session,
    business_id: Union[uuid.UUID, str, ToolContext],
) -> List[VendorLookupRead]:
    """
    Programmatic fetch for vendors scoped to business tenant.
    """
    biz_uuid = _resolve_business_uuid(business_id)
    stmt = scoped(Vendor, biz_uuid).order_by(Vendor.name.asc())
    records = db.scalars(stmt).all()
    return [VendorLookupRead.model_validate(r) for r in records]
