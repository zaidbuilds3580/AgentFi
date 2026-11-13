"""
API route handlers for lightweight UI lookups (accounts, customers, vendors, categories) and metadata ranges.
"""

from typing import List, Optional
import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.api.deps import get_current_user, get_db
from backend.auth.schemas import TokenPayload
from backend.schemas.canonical import (
    AccountLookupRead,
    CustomerLookupRead,
    DataRangeResponse,
    VendorLookupRead,
)
from backend.services.lookup_service import (
    get_accounts,
    get_categories,
    get_customers,
    get_data_range,
    get_vendors,
)

router = APIRouter(tags=["Lookups"])


@router.get(
    "/accounts",
    response_model=List[AccountLookupRead],
    summary="List active financial accounts for the business",
)
def get_accounts_endpoint(
    current_user: TokenPayload = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> List[AccountLookupRead]:
    biz_id = uuid.UUID(str(current_user.business_id))
    return get_accounts(db=db, business_id=biz_id)


@router.get(
    "/customers",
    response_model=List[CustomerLookupRead],
    summary="List customers for dropdown selection with optional search",
)
def get_customers_endpoint(
    q: Optional[str] = Query(None, description="Substring search on customer name"),
    current_user: TokenPayload = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> List[CustomerLookupRead]:
    biz_id = uuid.UUID(str(current_user.business_id))
    return get_customers(db=db, business_id=biz_id, q=q)


@router.get(
    "/vendors",
    response_model=List[VendorLookupRead],
    summary="List vendors for dropdown selection with optional search",
)
def get_vendors_endpoint(
    q: Optional[str] = Query(None, description="Substring search on vendor name"),
    current_user: TokenPayload = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> List[VendorLookupRead]:
    biz_id = uuid.UUID(str(current_user.business_id))
    return get_vendors(db=db, business_id=biz_id, q=q)


@router.get(
    "/categories",
    response_model=List[str],
    summary="List distinct categories populated for transactions or expenses",
)
def get_categories_endpoint(
    kind: str = Query(..., description="Entity kind: 'transaction' or 'expense'"),
    current_user: TokenPayload = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> List[str]:
    biz_id = uuid.UUID(str(current_user.business_id))
    return get_categories(db=db, business_id=biz_id, kind=kind)


@router.get(
    "/meta/data-range",
    response_model=DataRangeResponse,
    summary="Get min date, max date, and count across transactions, invoices, and expenses",
)
def get_data_range_endpoint(
    current_user: TokenPayload = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DataRangeResponse:
    biz_id = uuid.UUID(str(current_user.business_id))
    return get_data_range(db=db, business_id=biz_id)
