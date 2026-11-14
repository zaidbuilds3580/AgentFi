"""
API route handlers for business financial summary and KPI analytics.
Exposes GET /api/businesses/{business_id}/financial-summary and GET /api/financial-summary.
"""

from datetime import date
from typing import Optional
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from backend.api.deps import get_current_user, get_db
from backend.auth.schemas import TokenPayload
from backend.schemas.financial_summary import FinancialSummaryResponse
from backend.services.financial_summary_service import get_financial_summary_service

router = APIRouter(tags=["Financial Summary"])


@router.get(
    "/businesses/{business_id}/financial-summary",
    response_model=FinancialSummaryResponse,
    summary="Get business financial summary and KPIs backed by the deterministic Finance Engine",
)
def get_business_financial_summary_endpoint(
    business_id: uuid.UUID,
    period: str = Query("last_30_days", description="Period preset (last_30_days, this_month, last_month, this_quarter, last_quarter, this_fy, last_fy, ytd, custom)"),
    date_from: Optional[date] = Query(None, description="Start date when period=custom (YYYY-MM-DD)"),
    date_to: Optional[date] = Query(None, description="End date when period=custom (YYYY-MM-DD)"),
    as_of: Optional[date] = Query(None, description="Reference anchor date for calculations (defaults to today in business timezone)"),
    compare_previous: bool = Query(True, description="Whether to compute the immediately preceding period of equal length"),
    include: Optional[str] = Query(None, description="Comma-separated sections to include: kpis,receivables,expenses,cash,comparison (default all)"),
    current_user: TokenPayload = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> FinancialSummaryResponse:
    # Enforce multi-tenant access control: path business_id must match authenticated user's claim
    if str(business_id) != str(current_user.business_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot access data for another business.",
        )

    return get_financial_summary_service(
        db=db,
        business_id=business_id,
        period=period,
        date_from=date_from,
        date_to=date_to,
        as_of=as_of,
        compare_previous=compare_previous,
        include=include,
    )


@router.get(
    "/financial-summary",
    response_model=FinancialSummaryResponse,
    summary="Get financial summary for the authenticated user's business (convenience alias)",
)
def get_current_business_financial_summary_alias(
    period: str = Query("last_30_days", description="Period preset (last_30_days, this_month, last_month, this_quarter, last_quarter, this_fy, last_fy, ytd, custom)"),
    date_from: Optional[date] = Query(None, description="Start date when period=custom (YYYY-MM-DD)"),
    date_to: Optional[date] = Query(None, description="End date when period=custom (YYYY-MM-DD)"),
    as_of: Optional[date] = Query(None, description="Reference anchor date for calculations (defaults to today in business timezone)"),
    compare_previous: bool = Query(True, description="Whether to compute the immediately preceding period of equal length"),
    include: Optional[str] = Query(None, description="Comma-separated sections to include: kpis,receivables,expenses,cash,comparison (default all)"),
    current_user: TokenPayload = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> FinancialSummaryResponse:
    biz_id = uuid.UUID(str(current_user.business_id))
    return get_financial_summary_service(
        db=db,
        business_id=biz_id,
        period=period,
        date_from=date_from,
        date_to=date_to,
        as_of=as_of,
        compare_previous=compare_previous,
        include=include,
    )
