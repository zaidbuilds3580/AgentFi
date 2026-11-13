"""
Query service for paginated, filtered, tenant-scoped transactions.
"""

from datetime import date
from decimal import Decimal
from typing import List, Optional, Tuple
import uuid

from fastapi import HTTPException, status
from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session

from backend.models.constants import (
    TRANSACTION_TYPE_INFLOW,
    TRANSACTION_TYPE_OUTFLOW,
    TRANSACTION_TYPES,
)
from backend.models.transactions import Transaction
from backend.schemas.canonical import TransactionRead
from backend.schemas.common import TransactionPage, TransactionTotals
from backend.services.query_filters import (
    apply_sorting,
    escape_like_param,
    get_query_count,
    scoped,
    validate_amount_range,
    validate_date_range,
    validate_pagination,
)

TRANSACTION_SORT_COLUMNS = {
    "transaction_date": Transaction.transaction_date,
    "amount": Transaction.amount,
    "created_at": Transaction.created_at,
    "category": Transaction.category,
}


def list_transactions(
    db: Session,
    business_id: uuid.UUID,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    transaction_type: Optional[str] = None,
    account_id: Optional[uuid.UUID] = None,
    categories: Optional[List[str]] = None,
    counterparty: Optional[str] = None,
    min_amount: Optional[Decimal] = None,
    max_amount: Optional[Decimal] = None,
    q: Optional[str] = None,
    source_dataset: Optional[str] = None,
    sort_by: str = "transaction_date",
    sort_order: str = "desc",
    limit: int = 50,
    offset: int = 0,
    include_totals: bool = False,
) -> TransactionPage[TransactionRead]:
    # 1. Validation
    validate_pagination(limit, offset)
    validate_date_range(date_from, date_to)
    validate_amount_range(min_amount, max_amount)

    if transaction_type:
        tt = transaction_type.strip().lower()
        if tt not in TRANSACTION_TYPES:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "INVALID_TRANSACTION_TYPE",
                    "message": f"Invalid transaction_type '{transaction_type}'. Allowed: {list(TRANSACTION_TYPES)}",
                },
            )

    # 2. Base query strictly scoped to tenant
    query = scoped(Transaction, business_id)

    # 3. Predicates
    if date_from:
        query = query.where(Transaction.transaction_date >= date_from)
    if date_to:
        query = query.where(Transaction.transaction_date <= date_to)
    if transaction_type:
        query = query.where(Transaction.transaction_type == transaction_type.strip().lower())
    if account_id:
        query = query.where(Transaction.account_id == account_id)
    if categories:
        cleaned_cats = [c.strip() for c in categories if c.strip()]
        if cleaned_cats:
            query = query.where(Transaction.category.in_(cleaned_cats))
    if counterparty:
        query = query.where(func.lower(Transaction.counterparty) == counterparty.strip().lower())
    if min_amount is not None:
        query = query.where(Transaction.amount >= min_amount)
    if max_amount is not None:
        query = query.where(Transaction.amount <= max_amount)
    if source_dataset:
        query = query.where(Transaction.source_dataset == source_dataset.strip())

    escaped_q = escape_like_param(q)
    if escaped_q:
        query = query.where(
            or_(
                Transaction.description.ilike(escaped_q),
                Transaction.counterparty.ilike(escaped_q),
                Transaction.reference.ilike(escaped_q),
            )
        )

    # 4. Count over filtered query
    total = get_query_count(db, query)

    # 5. Optional totals calculation over the entire filtered set
    totals: Optional[TransactionTotals] = None
    if include_totals:
        sub = query.order_by(None).subquery()
        totals_stmt = select(
            func.count().label("cnt"),
            func.coalesce(
                func.sum(
                    case(
                        (sub.c.transaction_type == TRANSACTION_TYPE_INFLOW, sub.c.amount),
                        else_=Decimal("0.00"),
                    )
                ),
                Decimal("0.00"),
            ).label("inflow"),
            func.coalesce(
                func.sum(
                    case(
                        (sub.c.transaction_type == TRANSACTION_TYPE_OUTFLOW, sub.c.amount),
                        else_=Decimal("0.00"),
                    )
                ),
                Decimal("0.00"),
            ).label("outflow"),
        )
        t_row = db.execute(totals_stmt).one()
        inflow_val = Decimal(str(t_row.inflow))
        outflow_val = Decimal(str(t_row.outflow))
        totals = TransactionTotals(
            count=t_row.cnt,
            inflow_total=inflow_val,
            outflow_total=outflow_val,
            net=inflow_val - outflow_val,
        )

    # 6. Apply safe sorting and pagination
    sorted_query = apply_sorting(
        query,
        sort_by=sort_by,
        sort_order=sort_order,
        allowed_columns=TRANSACTION_SORT_COLUMNS,
        tie_breaker_col=Transaction.id,
    )
    paged_query = sorted_query.offset(offset).limit(limit)

    results = db.scalars(paged_query).all()
    items = [TransactionRead.model_validate(r) for r in results]
    has_more = (offset + len(items)) < total

    return TransactionPage(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
        has_more=has_more,
        totals=totals,
    )


def get_transaction_by_id(
    db: Session,
    business_id: uuid.UUID,
    transaction_id: uuid.UUID,
) -> TransactionRead:
    stmt = (
        scoped(Transaction, business_id)
        .where(Transaction.id == transaction_id)
    )
    txn = db.scalars(stmt).first()
    if not txn:
        # Cross-tenant access or missing record returns 404 (tenant privacy)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Transaction {transaction_id} not found.",
        )
    return TransactionRead.model_validate(txn)
