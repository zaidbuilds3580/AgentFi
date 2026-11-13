"""
Query service for paginated, filtered, tenant-scoped invoices.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional
import uuid
from zoneinfo import ZoneInfo

from fastapi import HTTPException, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from backend.models.businesses import Business
from backend.models.constants import (
    INVOICE_STATUS_PAID,
    INVOICE_STATUS_VOID,
    INVOICE_STATUSES,
)
from backend.models.customers import Customer
from backend.models.invoices import Invoice
from backend.schemas.canonical import InvoiceListItemRead
from backend.schemas.common import Page
from backend.services.query_filters import (
    apply_sorting,
    escape_like_param,
    get_query_count,
    scoped,
    validate_amount_range,
    validate_date_range,
    validate_pagination,
)

INVOICE_SORT_COLUMNS = {
    "due_date": Invoice.due_date,
    "invoice_date": Invoice.invoice_date,
    "amount_total": Invoice.amount_total,
    "created_at": Invoice.created_at,
    "invoice_number": Invoice.invoice_number,
}


def list_invoices(
    db: Session,
    business_id: uuid.UUID,
    statuses: Optional[List[str]] = None,
    customer_id: Optional[uuid.UUID] = None,
    invoice_date_from: Optional[date] = None,
    invoice_date_to: Optional[date] = None,
    due_date_from: Optional[date] = None,
    due_date_to: Optional[date] = None,
    min_amount: Optional[Decimal] = None,
    max_amount: Optional[Decimal] = None,
    overdue: Optional[bool] = None,
    unpaid: Optional[bool] = None,
    q: Optional[str] = None,
    source_dataset: Optional[str] = None,
    sort_by: str = "due_date",
    sort_order: str = "asc",
    limit: int = 50,
    offset: int = 0,
) -> Page[InvoiceListItemRead]:
    # 1. Validation
    validate_pagination(limit, offset)
    validate_date_range(invoice_date_from, invoice_date_to)
    validate_date_range(due_date_from, due_date_to)
    validate_amount_range(min_amount, max_amount)

    cleaned_statuses = []
    if statuses:
        for st in statuses:
            s_clean = st.strip().lower()
            if s_clean not in INVOICE_STATUSES:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail={
                        "code": "INVALID_INVOICE_STATUS",
                        "message": f"Invalid invoice status '{st}'. Allowed: {list(INVOICE_STATUSES)}",
                        "allowed": list(INVOICE_STATUSES),
                    },
                )
            cleaned_statuses.append(s_clean)

    # 2. Base query strictly scoped to tenant with joined Customer to prevent N+1
    query = (
        scoped(Invoice, business_id)
        .outerjoin(Customer, Invoice.customer_id == Customer.id)
        .add_columns(Customer.name.label("customer_name"))
    )

    # 3. Predicates
    if cleaned_statuses:
        query = query.where(Invoice.status.in_(cleaned_statuses))
    if customer_id:
        query = query.where(Invoice.customer_id == customer_id)
    if invoice_date_from:
        query = query.where(Invoice.invoice_date >= invoice_date_from)
    if invoice_date_to:
        query = query.where(Invoice.invoice_date <= invoice_date_to)
    if due_date_from:
        query = query.where(Invoice.due_date >= due_date_from)
    if due_date_to:
        query = query.where(Invoice.due_date <= due_date_to)
    if min_amount is not None:
        query = query.where(Invoice.amount_total >= min_amount)
    if max_amount is not None:
        query = query.where(Invoice.amount_total <= max_amount)
    if source_dataset:
        query = query.where(Invoice.source_dataset == source_dataset.strip())

    if overdue is not None:
        # Determine current date in business timezone
        biz = db.get(Business, business_id)
        tz_name = biz.timezone if biz and biz.timezone else "Asia/Kolkata"
        try:
            biz_tz = ZoneInfo(tz_name)
        except Exception:
            biz_tz = ZoneInfo("Asia/Kolkata")
        today_in_tz = datetime.now(biz_tz).date()

        overdue_filter = (
            (Invoice.due_date < today_in_tz)
            & (Invoice.amount_paid < Invoice.amount_total)
            & (~Invoice.status.in_([INVOICE_STATUS_PAID, INVOICE_STATUS_VOID]))
        )
        if overdue is True:
            query = query.where(overdue_filter)
        else:
            query = query.where(~overdue_filter)

    if unpaid is not None:
        unpaid_filter = (Invoice.amount_paid < Invoice.amount_total) & (
            ~Invoice.status.in_([INVOICE_STATUS_PAID, INVOICE_STATUS_VOID])
        )
        if unpaid is True:
            query = query.where(unpaid_filter)
        else:
            query = query.where(~unpaid_filter)

    escaped_q = escape_like_param(q)
    if escaped_q:
        query = query.where(
            or_(
                Invoice.invoice_number.ilike(escaped_q),
                Customer.name.ilike(escaped_q),
            )
        )

    # 4. Count over filtered query
    total = get_query_count(db, query)

    # 5. Apply safe sorting and pagination
    sorted_query = apply_sorting(
        query,
        sort_by=sort_by,
        sort_order=sort_order,
        allowed_columns=INVOICE_SORT_COLUMNS,
        tie_breaker_col=Invoice.id,
    )
    paged_query = sorted_query.offset(offset).limit(limit)

    results = db.execute(paged_query).all()
    items: List[InvoiceListItemRead] = []
    for row in results:
        inv: Invoice = row[0]
        cust_name: Optional[str] = row[1]
        outstanding = inv.amount_total - inv.amount_paid
        item_data = InvoiceListItemRead(
            id=inv.id,
            business_id=inv.business_id,
            customer_id=inv.customer_id,
            invoice_number=inv.invoice_number,
            invoice_date=inv.invoice_date,
            due_date=inv.due_date,
            amount_total=inv.amount_total,
            amount_paid=inv.amount_paid,
            currency=inv.currency,
            status=inv.status,
            payment_date=inv.payment_date,
            notes=inv.notes,
            source_dataset=inv.source_dataset,
            source_record_id=inv.source_record_id,
            created_at=inv.created_at,
            updated_at=inv.updated_at,
            customer_name=cust_name,
            outstanding_amount=outstanding,
        )
        items.append(item_data)

    has_more = (offset + len(items)) < total

    return Page(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
        has_more=has_more,
    )


def get_invoice_by_id(
    db: Session,
    business_id: uuid.UUID,
    invoice_id: uuid.UUID,
) -> InvoiceListItemRead:
    stmt = (
        scoped(Invoice, business_id)
        .outerjoin(Customer, Invoice.customer_id == Customer.id)
        .add_columns(Customer.name.label("customer_name"))
        .where(Invoice.id == invoice_id)
    )
    row = db.execute(stmt).first()
    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Invoice {invoice_id} not found.",
        )
    inv: Invoice = row[0]
    cust_name: Optional[str] = row[1]
    outstanding = inv.amount_total - inv.amount_paid
    return InvoiceListItemRead(
        id=inv.id,
        business_id=inv.business_id,
        customer_id=inv.customer_id,
        invoice_number=inv.invoice_number,
        invoice_date=inv.invoice_date,
        due_date=inv.due_date,
        amount_total=inv.amount_total,
        amount_paid=inv.amount_paid,
        currency=inv.currency,
        status=inv.status,
        payment_date=inv.payment_date,
        notes=inv.notes,
        source_dataset=inv.source_dataset,
        source_record_id=inv.source_record_id,
        created_at=inv.created_at,
        updated_at=inv.updated_at,
        customer_name=cust_name,
        outstanding_amount=outstanding,
    )
