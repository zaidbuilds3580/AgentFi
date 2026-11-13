"""
Reusable query filtering, sorting, pagination, and tenant scoping helpers.
"""

from datetime import date
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple, Type
import uuid

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from sqlalchemy.sql import Select


def scoped(model: Type[Any], business_id: uuid.UUID) -> Select:
    """
    Creates a base Select query for any business-owned model strictly filtered by business_id.
    Makes tenant isolation structurally enforced across all query services.
    """
    return select(model).where(model.business_id == business_id)


def escape_like_param(q: Optional[str]) -> Optional[str]:
    """
    Escapes special SQL wildcard characters (%, _, \\) in user input so substring
    ILIKE searches match literally, capped at 100 characters.
    """
    if not q:
        return None
    trimmed = q.strip()[:100]
    if not trimmed:
        return None
    escaped = trimmed.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def validate_date_range(
    date_from: Optional[date],
    date_to: Optional[date],
) -> None:
    """
    Validates that date_from <= date_to. Raises 422 if inverted.
    """
    if date_from and date_to and date_from > date_to:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "INVALID_DATE_RANGE",
                "message": f"date_from ({date_from}) cannot be after date_to ({date_to}).",
            },
        )


def validate_amount_range(
    min_amount: Optional[Decimal],
    max_amount: Optional[Decimal],
) -> None:
    """
    Validates non-negative amounts and min_amount <= max_amount. Raises 422 if invalid.
    """
    if min_amount is not None and min_amount < Decimal("0.00"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "INVALID_AMOUNT", "message": "min_amount must be >= 0."},
        )
    if max_amount is not None and max_amount < Decimal("0.00"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "INVALID_AMOUNT", "message": "max_amount must be >= 0."},
        )
    if min_amount is not None and max_amount is not None and min_amount > max_amount:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "INVALID_AMOUNT_RANGE",
                "message": f"min_amount ({min_amount}) cannot be greater than max_amount ({max_amount}).",
            },
        )


def validate_pagination(limit: int, offset: int) -> None:
    """
    Validates pagination bounds (limit 1..200, offset >= 0). Raises 422 without silent clamping.
    """
    if limit < 1 or limit > 200:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "INVALID_LIMIT", "message": f"limit must be between 1 and 200 (received {limit})."},
        )
    if offset < 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "INVALID_OFFSET", "message": f"offset must be >= 0 (received {offset})."},
        )


def apply_sorting(
    query: Select,
    sort_by: str,
    sort_order: str,
    allowed_columns: Dict[str, Any],
    tie_breaker_col: Any,
) -> Select:
    """
    Applies safe sort expression from an explicit column whitelist.
    Always adds primary key / tie_breaker_col as a stable second ordering rule.
    """
    sort_key = sort_by.strip().lower()
    if sort_key not in allowed_columns:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "INVALID_SORT_BY",
                "message": f"Invalid sort_by '{sort_by}'. Allowed: {sorted(list(allowed_columns.keys()))}",
                "allowed": sorted(list(allowed_columns.keys())),
            },
        )

    order_key = sort_order.strip().lower()
    if order_key not in ("asc", "desc"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "INVALID_SORT_ORDER", "message": "sort_order must be 'asc' or 'desc'."},
        )

    col = allowed_columns[sort_key]
    if order_key == "desc":
        return query.order_by(col.desc(), tie_breaker_col.desc())
    else:
        return query.order_by(col.asc(), tie_breaker_col.asc())


def get_query_count(session: Session, query: Select) -> int:
    """
    Executes an efficient COUNT query over the filtered Select statement without ORDER BY.
    """
    count_stmt = select(func.count()).select_from(query.order_by(None).subquery())
    return session.scalar(count_stmt) or 0
