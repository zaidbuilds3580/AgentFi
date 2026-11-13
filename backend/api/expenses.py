"""
API route handlers for expense querying and detail retrieval.
"""

from datetime import date
from decimal import Decimal
from typing import List, Optional
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from backend.api.deps import get_current_user, get_db
from backend.auth.schemas import TokenPayload
from backend.schemas.canonical import ExpenseListItemRead
from backend.schemas.common import ExpensePage
from backend.services.expense_query_service import get_expense_by_id, list_expenses

router = APIRouter(tags=["Expenses"])


@router.get(
    "/expenses",
    response_model=ExpensePage[ExpenseListItemRead],
    summary="List expenses with vendor enrichment, category/method filters, and optional totals",
)
def get_expenses_endpoint(
    date_from: Optional[date] = Query(None, description="Expenses on or after this date (YYYY-MM-DD)"),
    date_to: Optional[date] = Query(None, description="Expenses on or before this date (YYYY-MM-DD)"),
    category: Optional[List[str]] = Query(None, description="Filter by category (repeatable)"),
    vendor_id: Optional[uuid.UUID] = Query(None, description="Filter by vendor UUID"),
    account_id: Optional[uuid.UUID] = Query(None, description="Filter by financial account UUID"),
    payment_method: Optional[List[str]] = Query(None, description="Filter by payment method (repeatable)"),
    is_recurring: Optional[bool] = Query(None, description="Filter recurring vs one-off expenses"),
    min_amount: Optional[Decimal] = Query(None, description="Filter expenses with amount >= min_amount"),
    max_amount: Optional[Decimal] = Query(None, description="Filter expenses with amount <= max_amount"),
    q: Optional[str] = Query(None, description="Substring search on description, category, and vendor name"),
    source_dataset: Optional[str] = Query(None, description="Filter by provenance dataset"),
    sort_by: str = Query("expense_date", description="Sort column (expense_date, amount, category, created_at)"),
    sort_order: str = Query("desc", description="Sort direction ('asc' or 'desc')"),
    limit: int = Query(50, description="Items per page (1..200)"),
    offset: int = Query(0, description="Page offset (>= 0)"),
    include_totals: bool = Query(False, description="Whether to include total_amount and count in response envelope"),
    current_user: TokenPayload = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ExpensePage[ExpenseListItemRead]:
    biz_id = uuid.UUID(str(current_user.business_id))
    return list_expenses(
        db=db,
        business_id=biz_id,
        date_from=date_from,
        date_to=date_to,
        categories=category,
        vendor_id=vendor_id,
        account_id=account_id,
        payment_methods=payment_method,
        is_recurring=is_recurring,
        min_amount=min_amount,
        max_amount=max_amount,
        q=q,
        source_dataset=source_dataset,
        sort_by=sort_by,
        sort_order=sort_order,
        limit=limit,
        offset=offset,
        include_totals=include_totals,
    )


@router.get(
    "/expenses/{id}",
    response_model=ExpenseListItemRead,
    summary="Get single expense detail by UUID",
)
def get_expense_detail_endpoint(
    id: uuid.UUID,
    current_user: TokenPayload = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ExpenseListItemRead:
    biz_id = uuid.UUID(str(current_user.business_id))
    return get_expense_by_id(db=db, business_id=biz_id, expense_id=id)


@router.get(
    "/businesses/{business_id}/expenses",
    response_model=ExpensePage[ExpenseListItemRead],
    include_in_schema=False,
)
def get_business_expenses_alias(
    business_id: uuid.UUID,
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    category: Optional[List[str]] = Query(None),
    vendor_id: Optional[uuid.UUID] = Query(None),
    account_id: Optional[uuid.UUID] = Query(None),
    payment_method: Optional[List[str]] = Query(None),
    is_recurring: Optional[bool] = Query(None),
    min_amount: Optional[Decimal] = Query(None),
    max_amount: Optional[Decimal] = Query(None),
    q: Optional[str] = Query(None),
    source_dataset: Optional[str] = Query(None),
    sort_by: str = Query("expense_date"),
    sort_order: str = Query("desc"),
    limit: int = Query(50),
    offset: int = Query(0),
    include_totals: bool = Query(False),
    current_user: TokenPayload = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ExpensePage[ExpenseListItemRead]:
    if str(business_id) != str(current_user.business_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot access data for another business.",
        )
    return list_expenses(
        db=db,
        business_id=business_id,
        date_from=date_from,
        date_to=date_to,
        categories=category,
        vendor_id=vendor_id,
        account_id=account_id,
        payment_methods=payment_method,
        is_recurring=is_recurring,
        min_amount=min_amount,
        max_amount=max_amount,
        q=q,
        source_dataset=source_dataset,
        sort_by=sort_by,
        sort_order=sort_order,
        limit=limit,
        offset=offset,
        include_totals=include_totals,
    )
