"""
Period resolution utilities for financial summary calculations.
Provides deterministic, pure functions for computing current and preceding period ranges,
fiscal year boundaries (Indian default April start), month-ends, leap years, and timezone offsets.
"""

import calendar
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional, Tuple

from fastapi import HTTPException, status

ALLOWED_PERIOD_PRESETS = (
    "last_30_days",
    "this_month",
    "last_month",
    "this_quarter",
    "last_quarter",
    "this_fy",
    "last_fy",
    "ytd",
    "custom",
)

MAX_PERIOD_SPAN_DAYS = 731  # 2 years maximum span


@dataclass(frozen=True)
class PeriodRange:
    label: str
    date_from: date
    date_to: date
    as_of: date
    days: int


def _month_start(d: date) -> date:
    return d.replace(day=1)


def _month_end(d: date) -> date:
    last_day = calendar.monthrange(d.year, d.month)[1]
    return d.replace(day=last_day)


def _add_months(d: date, months: int) -> date:
    """Shift to the 1st of the month `months` away."""
    idx = d.year * 12 + (d.month - 1) + months
    return date(idx // 12, idx % 12 + 1, 1)


def _fiscal_year_start(d: date, fy_start_month: int = 4) -> date:
    year = d.year if d.month >= fy_start_month else d.year - 1
    return date(year, fy_start_month, 1)


def _fiscal_quarter_start(d: date, fy_start_month: int = 4) -> date:
    fy = _fiscal_year_start(d, fy_start_month)
    months_in = (d.year - fy.year) * 12 + d.month - fy.month
    return _add_months(fy, (months_in // 3) * 3)


def resolve_summary_period(
    period: str = "last_30_days",
    as_of: Optional[date] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    fy_start_month: int = 4,
    compare_previous: bool = True,
) -> Tuple[PeriodRange, Optional[PeriodRange]]:
    """
    Resolves the target period and the immediately preceding period of equal length.
    All calculations are pure and deterministic relative to `as_of`.
    """
    ref_date = as_of or date.today()
    preset = (period or "last_30_days").strip().lower()

    if preset not in ALLOWED_PERIOD_PRESETS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "INVALID_PERIOD_PRESET",
                "message": f"Invalid period '{period}'. Allowed presets: {list(ALLOWED_PERIOD_PRESETS)}",
                "allowed": list(ALLOWED_PERIOD_PRESETS),
            },
        )

    if preset == "custom":
        if date_from is None or date_to is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "MISSING_CUSTOM_DATES",
                    "message": "date_from and date_to are both required when period=custom.",
                },
            )
        if date_from > date_to:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "INVALID_DATE_RANGE",
                    "message": f"date_from ({date_from}) cannot be after date_to ({date_to}).",
                },
            )
        span = (date_to - date_from).days + 1
        if span > MAX_PERIOD_SPAN_DAYS:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "PERIOD_TOO_LARGE",
                    "message": f"Requested period span ({span} days) exceeds maximum allowed ({MAX_PERIOD_SPAN_DAYS} days).",
                },
            )
        curr_from = date_from
        curr_to = date_to

    elif preset == "last_30_days":
        curr_to = ref_date
        curr_from = ref_date - timedelta(days=29)

    elif preset == "this_month":
        curr_from = _month_start(ref_date)
        curr_to = ref_date

    elif preset == "last_month":
        start_prev = _add_months(ref_date, -1)
        curr_from = start_prev
        curr_to = _month_end(start_prev)

    elif preset == "this_quarter":
        curr_from = _fiscal_quarter_start(ref_date, fy_start_month)
        curr_to = ref_date

    elif preset == "last_quarter":
        curr_quarter_start = _fiscal_quarter_start(ref_date, fy_start_month)
        curr_from = _add_months(curr_quarter_start, -3)
        curr_to = curr_quarter_start - timedelta(days=1)

    elif preset in ("this_fy", "ytd"):
        curr_from = _fiscal_year_start(ref_date, fy_start_month)
        curr_to = ref_date

    elif preset == "last_fy":
        curr_fy_start = _fiscal_year_start(ref_date, fy_start_month)
        curr_from = _add_months(curr_fy_start, -12)
        curr_to = curr_fy_start - timedelta(days=1)

    else:
        curr_to = ref_date
        curr_from = ref_date - timedelta(days=29)

    days = (curr_to - curr_from).days + 1
    current_range = PeriodRange(
        label=preset,
        date_from=curr_from,
        date_to=curr_to,
        as_of=ref_date,
        days=days,
    )

    prev_range: Optional[PeriodRange] = None
    if compare_previous:
        prev_to = curr_from - timedelta(days=1)
        prev_from = prev_to - timedelta(days=days - 1)
        prev_range = PeriodRange(
            label=f"previous_{preset}",
            date_from=prev_from,
            date_to=prev_to,
            as_of=ref_date,
            days=days,
        )

    return current_range, prev_range
