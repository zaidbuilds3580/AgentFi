"""
API route handlers for invoice querying and detail retrieval.
"""

from datetime import date
from decimal import Decimal
from typing import List, Optional
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from backend.api.deps import get_current_user, get_db
from backend.auth.schemas import TokenPayload
from backend.schemas.canonical import InvoiceListItemRead
from backend.schemas.common import Page
from backend.services.invoice_query_service import get_invoice_by_id, list_invoices

router = APIRouter(tags=["Invoices"])


@router.get(
    "/invoices",
    response_model=Page[InvoiceListItemRead],
    summary="List invoices with customer enrichment, status filters, overdue checks, and sorting",
)
def get_invoices_endpoint(
    status: Optional[List[str]] = Query(None, description="Filter by status (e.g. draft, sent, paid, overdue, void)"),
    customer_id: Optional[uuid.UUID] = Query(None, description="Filter by customer UUID"),
    invoice_date_from: Optional[date] = Query(None, description="Invoices issued on or after (YYYY-MM-DD)"),
    invoice_date_to: Optional[date] = Query(None, description="Invoices issued on or before (YYYY-MM-DD)"),
    due_date_from: Optional[date] = Query(None, description="Invoices due on or after (YYYY-MM-DD)"),
    due_date_to: Optional[date] = Query(None, description="Invoices due on or before (YYYY-MM-DD)"),
    min_amount: Optional[Decimal] = Query(None, description="Filter invoices with amount_total >= min_amount"),
    max_amount: Optional[Decimal] = Query(None, description="Filter invoices with amount_total <= max_amount"),
    overdue: Optional[bool] = Query(None, description="Filter invoices that are overdue relative to business timezone"),
    unpaid: Optional[bool] = Query(None, description="Filter invoices with amount_paid < amount_total and not void/paid"),
    q: Optional[str] = Query(None, description="Substring search on invoice_number and customer name"),
    source_dataset: Optional[str] = Query(None, description="Filter by provenance dataset"),
    sort_by: str = Query("due_date", description="Sort column (due_date, invoice_date, amount_total, created_at, invoice_number)"),
    sort_order: str = Query("asc", description="Sort direction ('asc' or 'desc')"),
    limit: int = Query(50, description="Items per page (1..200)"),
    offset: int = Query(0, description="Page offset (>= 0)"),
    current_user: TokenPayload = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Page[InvoiceListItemRead]:
    biz_id = uuid.UUID(str(current_user.business_id))
    return list_invoices(
        db=db,
        business_id=biz_id,
        statuses=status,
        customer_id=customer_id,
        invoice_date_from=invoice_date_from,
        invoice_date_to=invoice_date_to,
        due_date_from=due_date_from,
        due_date_to=due_date_to,
        min_amount=min_amount,
        max_amount=max_amount,
        overdue=overdue,
        unpaid=unpaid,
        q=q,
        source_dataset=source_dataset,
        sort_by=sort_by,
        sort_order=sort_order,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/invoices/{id}",
    response_model=InvoiceListItemRead,
    summary="Get single invoice detail by UUID",
)
def get_invoice_detail_endpoint(
    id: uuid.UUID,
    current_user: TokenPayload = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> InvoiceListItemRead:
    biz_id = uuid.UUID(str(current_user.business_id))
    return get_invoice_by_id(db=db, business_id=biz_id, invoice_id=id)


@router.get(
    "/businesses/{business_id}/invoices",
    response_model=Page[InvoiceListItemRead],
    include_in_schema=False,
)
def get_business_invoices_alias(
    business_id: uuid.UUID,
    status: Optional[List[str]] = Query(None),
    customer_id: Optional[uuid.UUID] = Query(None),
    invoice_date_from: Optional[date] = Query(None),
    invoice_date_to: Optional[date] = Query(None),
    due_date_from: Optional[date] = Query(None),
    due_date_to: Optional[date] = Query(None),
    min_amount: Optional[Decimal] = Query(None),
    max_amount: Optional[Decimal] = Query(None),
    overdue: Optional[bool] = Query(None),
    unpaid: Optional[bool] = Query(None),
    q: Optional[str] = Query(None),
    source_dataset: Optional[str] = Query(None),
    sort_by: str = Query("due_date"),
    sort_order: str = Query("asc"),
    limit: int = Query(50),
    offset: int = Query(0),
    current_user: TokenPayload = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Page[InvoiceListItemRead]:
    if str(business_id) != str(current_user.business_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot access data for another business.",
        )
    return list_invoices(
        db=db,
        business_id=business_id,
        statuses=status,
        customer_id=customer_id,
        invoice_date_from=invoice_date_from,
        invoice_date_to=invoice_date_to,
        due_date_from=due_date_from,
        due_date_to=due_date_to,
        min_amount=min_amount,
        max_amount=max_amount,
        overdue=overdue,
        unpaid=unpaid,
        q=q,
        source_dataset=source_dataset,
        sort_by=sort_by,
        sort_order=sort_order,
        limit=limit,
        offset=offset,
    )
