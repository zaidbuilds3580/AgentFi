"""
API route handlers for transaction querying and detail retrieval.
"""

from datetime import date
from decimal import Decimal
from typing import List, Optional
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from backend.api.deps import get_current_user, get_db
from backend.auth.schemas import TokenPayload
from backend.schemas.canonical import TransactionRead
from backend.schemas.common import TransactionPage
from backend.services.transaction_query_service import (
    get_transaction_by_id,
    list_transactions,
)

router = APIRouter(tags=["Transactions"])


@router.get(
    "/transactions",
    response_model=TransactionPage[TransactionRead],
    summary="List transactions with filtering, sorting, pagination, and optional totals",
)
def get_transactions_endpoint(
    date_from: Optional[date] = Query(None, description="Filter transactions on or after this date (YYYY-MM-DD)"),
    date_to: Optional[date] = Query(None, description="Filter transactions on or before this date (YYYY-MM-DD)"),
    transaction_type: Optional[str] = Query(None, description="Filter by transaction type: 'inflow' or 'outflow'"),
    account_id: Optional[uuid.UUID] = Query(None, description="Filter by financial account UUID"),
    category: Optional[List[str]] = Query(None, description="Filter by category (repeatable)"),
    counterparty: Optional[str] = Query(None, description="Filter by exact counterparty name (case-insensitive)"),
    min_amount: Optional[Decimal] = Query(None, description="Filter transactions with amount >= min_amount"),
    max_amount: Optional[Decimal] = Query(None, description="Filter transactions with amount <= max_amount"),
    q: Optional[str] = Query(None, description="Substring search in description, counterparty, and reference"),
    source_dataset: Optional[str] = Query(None, description="Filter by origin dataset / batch identifier"),
    sort_by: str = Query("transaction_date", description="Column to sort by (transaction_date, amount, created_at, category)"),
    sort_order: str = Query("desc", description="Sort direction ('asc' or 'desc')"),
    limit: int = Query(50, description="Items per page (1..200)"),
    offset: int = Query(0, description="Page offset (>= 0)"),
    include_totals: bool = Query(False, description="Whether to include inflow_total, outflow_total, and net in response"),
    current_user: TokenPayload = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TransactionPage[TransactionRead]:
    biz_id = uuid.UUID(str(current_user.business_id))
    return list_transactions(
        db=db,
        business_id=biz_id,
        date_from=date_from,
        date_to=date_to,
        transaction_type=transaction_type,
        account_id=account_id,
        categories=category,
        counterparty=counterparty,
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
    "/transactions/{id}",
    response_model=TransactionRead,
    summary="Get single transaction detail by UUID",
)
def get_transaction_detail_endpoint(
    id: uuid.UUID,
    current_user: TokenPayload = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TransactionRead:
    biz_id = uuid.UUID(str(current_user.business_id))
    return get_transaction_by_id(db=db, business_id=biz_id, transaction_id=id)


@router.get(
    "/businesses/{business_id}/transactions",
    response_model=TransactionPage[TransactionRead],
    include_in_schema=False,
)
def get_business_transactions_alias(
    business_id: uuid.UUID,
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    transaction_type: Optional[str] = Query(None),
    account_id: Optional[uuid.UUID] = Query(None),
    category: Optional[List[str]] = Query(None),
    counterparty: Optional[str] = Query(None),
    min_amount: Optional[Decimal] = Query(None),
    max_amount: Optional[Decimal] = Query(None),
    q: Optional[str] = Query(None),
    source_dataset: Optional[str] = Query(None),
    sort_by: str = Query("transaction_date"),
    sort_order: str = Query("desc"),
    limit: int = Query(50),
    offset: int = Query(0),
    include_totals: bool = Query(False),
    current_user: TokenPayload = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TransactionPage[TransactionRead]:
    if str(business_id) != str(current_user.business_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot access data for another business.",
        )
    return list_transactions(
        db=db,
        business_id=business_id,
        date_from=date_from,
        date_to=date_to,
        transaction_type=transaction_type,
        account_id=account_id,
        categories=category,
        counterparty=counterparty,
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
