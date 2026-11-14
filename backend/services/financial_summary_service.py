"""
Service layer coordinating period resolution, data fetching via read_api,
engine adapter invocation, comparison calculations, and response assembly.
"""

from datetime import date, datetime, timezone
from decimal import Decimal
import logging
from typing import List, Optional
import uuid
from zoneinfo import ZoneInfo

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from backend.models.businesses import Business
from backend.schemas.financial_summary import (
    BusinessSummaryHeader,
    ComparisonSummary,
    FinancialSummaryResponse,
    PeriodHeader,
    PreviousPeriodHeader,
    SummaryMeta,
)
from backend.services.finance_engine_adapter import (
    FinanceSummaryProvider,
    SummaryInputs,
    get_summary_provider,
)
from backend.services.lookup_service import (
    get_accounts,
    get_customers,
    get_data_range,
    get_vendors,
)
from backend.services.read_api import (
    fetch_expenses,
    fetch_invoices,
    fetch_transactions,
)
from backend.utils.periods import resolve_summary_period

logger = logging.getLogger(__name__)


def get_financial_summary_service(
    db: Session,
    business_id: uuid.UUID,
    period: str = "last_30_days",
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    as_of: Optional[date] = None,
    compare_previous: bool = True,
    include: Optional[str] = None,
    provider: Optional[FinanceSummaryProvider] = None,
) -> FinancialSummaryResponse:
    # 1. Retrieve tenant business configuration
    biz = db.get(Business, business_id)
    if not biz:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Business {business_id} not found.",
        )

    tz_str = biz.timezone or "Asia/Kolkata"
    try:
        biz_tz = ZoneInfo(tz_str)
    except Exception:
        biz_tz = ZoneInfo("Asia/Kolkata")

    effective_as_of = as_of or datetime.now(biz_tz).date()
    currency = biz.currency or "INR"
    fy_month = getattr(biz, "fiscal_year_start_month", 4) or 4

    # 2. Resolve target and preceding periods
    current_range, prev_range = resolve_summary_period(
        period=period,
        as_of=effective_as_of,
        date_from=date_from,
        date_to=date_to,
        fy_start_month=fy_month,
        compare_previous=compare_previous,
    )

    # 3. Fetch data scoped strictly to business_id via read_api & lookups
    try:
        accounts = get_accounts(db, business_id)
        customers = get_customers(db, business_id)
        vendors = get_vendors(db, business_id)
        data_coverage_res = get_data_range(db, business_id)

        # Invoices: fetch invoices up to max(period.end, effective_as_of) so AR balance is complete
        max_inv_date = max(current_range.date_to, effective_as_of)
        if prev_range:
            max_inv_date = max(max_inv_date, prev_range.date_to)

        invoices = fetch_invoices(db, business_id, end_date=max_inv_date)

        curr_transactions = fetch_transactions(
            db, business_id, start_date=current_range.date_from, end_date=current_range.date_to
        )
        curr_expenses = fetch_expenses(
            db, business_id, start_date=current_range.date_from, end_date=current_range.date_to
        )
    except ValueError as ve:
        # Exceeded MAX_INTERNAL_ROWS guard
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail={"code": "PERIOD_TOO_LARGE", "message": str(ve)},
        )

    # 4. Check for data gaps / warnings
    warnings: List[str] = []
    total_db_records = (
        data_coverage_res.transactions.count
        + data_coverage_res.invoices.count
        + data_coverage_res.expenses.count
    )

    if total_db_records == 0:
        warnings.append("no_data")
    elif data_coverage_res.transactions.count > 0 and len(curr_transactions) == 0:
        warnings.append("period_outside_data_coverage")

    active_provider = provider or get_summary_provider()

    # 5. Execute engine calculation for current period
    curr_inputs = SummaryInputs(
        business=biz,
        accounts=accounts,
        transactions=curr_transactions,
        invoices=invoices,
        expenses=curr_expenses,
        customers=customers,
        vendors=vendors,
    )

    try:
        curr_summary = active_provider.get_summary(
            business_id=business_id,
            date_from=current_range.date_from,
            date_to=current_range.date_to,
            as_of=effective_as_of,
            currency=currency,
            data=curr_inputs,
        )
    except Exception as exc:
        logger.error(
            f"Finance Engine failure during get_summary for business {business_id}: {exc}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "code": "ENGINE_UNAVAILABLE",
                "message": "Finance calculation engine is temporarily unavailable.",
            },
        )

    # Combine warnings
    all_warnings = list(dict.fromkeys(warnings + curr_summary.warnings))

    # 6. Execute engine calculation for previous period (if requested)
    comparison: Optional[ComparisonSummary] = None
    if compare_previous and prev_range is not None:
        try:
            prev_transactions = fetch_transactions(
                db, business_id, start_date=prev_range.date_from, end_date=prev_range.date_to
            )
            prev_expenses = fetch_expenses(
                db, business_id, start_date=prev_range.date_from, end_date=prev_range.date_to
            )

            prev_inputs = SummaryInputs(
                business=biz,
                accounts=accounts,
                transactions=prev_transactions,
                invoices=invoices,
                expenses=prev_expenses,
                customers=customers,
                vendors=vendors,
            )

            prev_summary = active_provider.get_summary(
                business_id=business_id,
                date_from=prev_range.date_from,
                date_to=prev_range.date_to,
                as_of=effective_as_of,
                currency=currency,
                data=prev_inputs,
            )

            # Compute percentage and absolute changes
            ncf_diff = curr_summary.kpis.net_cash_flow - prev_summary.kpis.net_cash_flow
            ncf_pct = None
            if prev_summary.kpis.net_cash_flow != Decimal("0.00"):
                ncf_pct = (
                    (ncf_diff / abs(prev_summary.kpis.net_cash_flow)) * Decimal("100.00")
                ).quantize(Decimal("0.01"))

            inflow_diff = curr_summary.kpis.total_inflow - prev_summary.kpis.total_inflow
            inflow_pct = None
            if prev_summary.kpis.total_inflow != Decimal("0.00"):
                inflow_pct = (
                    (inflow_diff / prev_summary.kpis.total_inflow) * Decimal("100.00")
                ).quantize(Decimal("0.01"))

            outflow_diff = curr_summary.kpis.total_outflow - prev_summary.kpis.total_outflow
            outflow_pct = None
            if prev_summary.kpis.total_outflow != Decimal("0.00"):
                outflow_pct = (
                    (outflow_diff / prev_summary.kpis.total_outflow) * Decimal("100.00")
                ).quantize(Decimal("0.01"))

            comparison = ComparisonSummary(
                net_cash_flow_change_abs=ncf_diff,
                net_cash_flow_change_pct=ncf_pct,
                inflow_change_pct=inflow_pct,
                outflow_change_pct=outflow_pct,
            )
        except Exception as exc:
            logger.warning(
                f"Failed to calculate previous period comparison for business {business_id}: {exc}"
            )
            all_warnings.append("partial_engine_output")
            comparison = None

    # 7. Apply payload trimming via `include` parameter
    included_set = (
        {s.strip().lower() for s in include.split(",") if s.strip()}
        if include
        else {"kpis", "receivables", "expenses", "cash", "comparison"}
    )

    kpis_payload = curr_summary.kpis if "kpis" in included_set else None
    cash_payload = curr_summary.cash if "cash" in included_set else None
    receivables_payload = curr_summary.receivables if "receivables" in included_set else None
    expenses_payload = curr_summary.expenses if "expenses" in included_set else None
    comparison_payload = comparison if "comparison" in included_set else None

    # 8. Assemble response
    coverage_dict = {
        "transactions": {
            "min_date": data_coverage_res.transactions.min_date.isoformat()
            if data_coverage_res.transactions.min_date
            else None,
            "max_date": data_coverage_res.transactions.max_date.isoformat()
            if data_coverage_res.transactions.max_date
            else None,
            "count": data_coverage_res.transactions.count,
        },
        "invoices": {
            "min_date": data_coverage_res.invoices.min_date.isoformat()
            if data_coverage_res.invoices.min_date
            else None,
            "max_date": data_coverage_res.invoices.max_date.isoformat()
            if data_coverage_res.invoices.max_date
            else None,
            "count": data_coverage_res.invoices.count,
        },
        "expenses": {
            "min_date": data_coverage_res.expenses.min_date.isoformat()
            if data_coverage_res.expenses.min_date
            else None,
            "max_date": data_coverage_res.expenses.max_date.isoformat()
            if data_coverage_res.expenses.max_date
            else None,
            "count": data_coverage_res.expenses.count,
        },
    }

    meta = SummaryMeta(
        computed_by=curr_summary.computed_by,
        engine_version=curr_summary.engine_version,
        generated_at=datetime.now(timezone.utc),
        data_coverage=coverage_dict,
        warnings=all_warnings,
    )

    return FinancialSummaryResponse(
        business=BusinessSummaryHeader(
            id=biz.id,
            name=biz.name,
            currency=currency,
        ),
        period=PeriodHeader(
            label=current_range.label,
            date_from=current_range.date_from,
            date_to=current_range.date_to,
            as_of=current_range.as_of,
            days=current_range.days,
        ),
        previous_period=PreviousPeriodHeader(
            date_from=prev_range.date_from,
            date_to=prev_range.date_to,
        )
        if prev_range
        else None,
        kpis=kpis_payload,
        cash=cash_payload,
        receivables=receivables_payload,
        expenses=expenses_payload,
        comparison=comparison_payload,
        meta=meta,
    )
