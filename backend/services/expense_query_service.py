"""
Query service for paginated, filtered, tenant-scoped expenses.
"""

from datetime import date
from decimal import Decimal
from typing import List, Optional
import uuid

from fastapi import HTTPException, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from backend.models.constants import PAYMENT_METHODS
from backend.models.expenses import Expense
from backend.models.vendors import Vendor
from backend.schemas.canonical import ExpenseListItemRead
from backend.schemas.common import ExpensePage, ExpenseTotals
from backend.services.query_filters import (
    apply_sorting,
    escape_like_param,
    get_query_count,
    scoped,
    validate_amount_range,
    validate_date_range,
    validate_pagination,
)

EXPENSE_SORT_COLUMNS = {
    "expense_date": Expense.expense_date,
    "amount": Expense.amount,
    "category": Expense.category,
    "created_at": Expense.created_at,
}


def list_expenses(
    db: Session,
    business_id: uuid.UUID,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    categories: Optional[List[str]] = None,
    vendor_id: Optional[uuid.UUID] = None,
    account_id: Optional[uuid.UUID] = None,
    payment_methods: Optional[List[str]] = None,
    is_recurring: Optional[bool] = None,
    min_amount: Optional[Decimal] = None,
    max_amount: Optional[Decimal] = None,
    q: Optional[str] = None,
    source_dataset: Optional[str] = None,
    sort_by: str = "expense_date",
    sort_order: str = "desc",
    limit: int = 50,
    offset: int = 0,
    include_totals: bool = False,
) -> ExpensePage[ExpenseListItemRead]:
    # 1. Validation
    validate_pagination(limit, offset)
    validate_date_range(date_from, date_to)
    validate_amount_range(min_amount, max_amount)

    cleaned_pms = []
    if payment_methods:
        for pm in payment_methods:
            pm_clean = pm.strip().lower()
            if pm_clean not in PAYMENT_METHODS:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail={
                        "code": "INVALID_PAYMENT_METHOD",
                        "message": f"Invalid payment_method '{pm}'. Allowed: {list(PAYMENT_METHODS)}",
                        "allowed": list(PAYMENT_METHODS),
                    },
                )
            cleaned_pms.append(pm_clean)

    # 2. Base query strictly scoped to tenant with joined Vendor to prevent N+1
    query = (
        scoped(Expense, business_id)
        .outerjoin(Vendor, Expense.vendor_id == Vendor.id)
        .add_columns(Vendor.name.label("vendor_name"))
    )

    # 3. Predicates
    if date_from:
        query = query.where(Expense.expense_date >= date_from)
    if date_to:
        query = query.where(Expense.expense_date <= date_to)
    if categories:
        cleaned_cats = [c.strip() for c in categories if c.strip()]
        if cleaned_cats:
            query = query.where(Expense.category.in_(cleaned_cats))
    if vendor_id:
        query = query.where(Expense.vendor_id == vendor_id)
    if account_id:
        query = query.where(Expense.account_id == account_id)
    if cleaned_pms:
        query = query.where(Expense.payment_method.in_(cleaned_pms))
    if is_recurring is not None:
        query = query.where(Expense.is_recurring.is_(is_recurring))
    if min_amount is not None:
        query = query.where(Expense.amount >= min_amount)
    if max_amount is not None:
        query = query.where(Expense.amount <= max_amount)
    if source_dataset:
        query = query.where(Expense.source_dataset == source_dataset.strip())

    escaped_q = escape_like_param(q)
    if escaped_q:
        query = query.where(
            or_(
                Expense.description.ilike(escaped_q),
                Expense.category.ilike(escaped_q),
                Vendor.name.ilike(escaped_q),
            )
        )

    # 4. Count over filtered query
    total = get_query_count(db, query)

    # 5. Optional totals calculation over the entire filtered set
    totals: Optional[ExpenseTotals] = None
    if include_totals:
        sub = query.order_by(None).subquery()
        totals_stmt = select(
            func.count().label("cnt"),
            func.coalesce(func.sum(sub.c.amount), Decimal("0.00")).label("total_amt"),
        )
        t_row = db.execute(totals_stmt).one()
        totals = ExpenseTotals(
            count=t_row.cnt,
            total_amount=Decimal(str(t_row.total_amt)),
        )

    # 6. Apply safe sorting and pagination
    sorted_query = apply_sorting(
        query,
        sort_by=sort_by,
        sort_order=sort_order,
        allowed_columns=EXPENSE_SORT_COLUMNS,
        tie_breaker_col=Expense.id,
    )
    paged_query = sorted_query.offset(offset).limit(limit)

    results = db.execute(paged_query).all()
    items: List[ExpenseListItemRead] = []
    for row in results:
        exp: Expense = row[0]
        v_name: Optional[str] = row[1]
        items.append(
            ExpenseListItemRead(
                id=exp.id,
                business_id=exp.business_id,
                vendor_id=exp.vendor_id,
                account_id=exp.account_id,
                transaction_id=exp.transaction_id,
                expense_date=exp.expense_date,
                category=exp.category,
                amount=exp.amount,
                currency=exp.currency,
                description=exp.description,
                payment_method=exp.payment_method,
                is_recurring=exp.is_recurring,
                source_dataset=exp.source_dataset,
                source_record_id=exp.source_record_id,
                created_at=exp.created_at,
                updated_at=exp.updated_at,
                vendor_name=v_name,
            )
        )

    has_more = (offset + len(items)) < total

    return ExpensePage(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
        has_more=has_more,
        totals=totals,
    )


def get_expense_by_id(
    db: Session,
    business_id: uuid.UUID,
    expense_id: uuid.UUID,
) -> ExpenseListItemRead:
    stmt = (
        scoped(Expense, business_id)
        .outerjoin(Vendor, Expense.vendor_id == Vendor.id)
        .add_columns(Vendor.name.label("vendor_name"))
        .where(Expense.id == expense_id)
    )
    row = db.execute(stmt).first()
    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Expense {expense_id} not found.",
        )
    exp: Expense = row[0]
    v_name: Optional[str] = row[1]
    return ExpenseListItemRead(
        id=exp.id,
        business_id=exp.business_id,
        vendor_id=exp.vendor_id,
        account_id=exp.account_id,
        transaction_id=exp.transaction_id,
        expense_date=exp.expense_date,
        category=exp.category,
        amount=exp.amount,
        currency=exp.currency,
        description=exp.description,
        payment_method=exp.payment_method,
        is_recurring=exp.is_recurring,
        source_dataset=exp.source_dataset,
        source_record_id=exp.source_record_id,
        created_at=exp.created_at,
        updated_at=exp.updated_at,
        vendor_name=v_name,
    )
