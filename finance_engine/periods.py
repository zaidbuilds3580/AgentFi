"""
Reporting periods for the finance engine.

Every calculation takes an explicit `Period` (inclusive start and end dates). Agents and the
UI speak in labels such as "current_month" or "Q2"; `resolve_period()` turns a label into
dates. It is deterministic: "today" is always passed in, never read from the clock inside
the engine.

Supported labels (case-insensitive):
    current_month | this_month | mtd        month-to-date
    last_month | previous_month             full previous calendar month
    last_<N>_days | last_30_days            N days ending today (inclusive)
    current_quarter | this_quarter | qtd    fiscal-quarter-to-date
    last_quarter | previous_quarter         previous full fiscal quarter
    q1 .. q4                                fiscal quarter of the current fiscal year (to date if running)
    ytd | fytd | current_fy | this_fy       fiscal-year-to-date
    last_fy | previous_fy                   previous full fiscal year
    fy2026 | fy2026-27                      fiscal year starting in 2026
    2026-10                                 calendar month
    2026-10-01..2026-10-31                  custom range (also accepts ':' or ' to ')

Fiscal quarters follow `Business.fiscal_year_start_month` (default 4 = April, Indian FY):
Q1 = Apr-Jun, Q2 = Jul-Sep, Q3 = Oct-Dec, Q4 = Jan-Mar.
"""

from __future__ import annotations

import calendar
import re
from datetime import date, timedelta
from typing import Iterator, List

from pydantic import BaseModel, ConfigDict, model_validator


class Period(BaseModel):
    """An inclusive date range [start, end]."""
    model_config = ConfigDict(frozen=True)

    label: str
    start: date
    end: date
    is_partial: bool = False  # True when the period is still running (ends at 'today')

    @model_validator(mode="after")
    def _check(self) -> "Period":
        if self.end < self.start:
            raise ValueError(f"period end {self.end} is before start {self.start}.")
        return self

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    def contains(self, d: date) -> bool:
        return self.start <= d <= self.end

    def __str__(self) -> str:
        return f"{self.label} ({self.start.isoformat()} to {self.end.isoformat()})"


class PeriodError(ValueError):
    """Raised when a period label cannot be understood."""


# ----------------------------------------------------------------------------- #
# date helpers
# ----------------------------------------------------------------------------- #

def month_start(d: date) -> date:
    return d.replace(day=1)


def month_end(d: date) -> date:
    return d.replace(day=calendar.monthrange(d.year, d.month)[1])


def add_months(d: date, months: int) -> date:
    """Shift to the 1st of the month `months` away (day is always reset to 1)."""
    idx = d.year * 12 + (d.month - 1) + months
    return date(idx // 12, idx % 12 + 1, 1)


def fiscal_year_start(d: date, fy_start_month: int = 4) -> date:
    year = d.year if d.month >= fy_start_month else d.year - 1
    return date(year, fy_start_month, 1)


def fiscal_quarter_start(d: date, fy_start_month: int = 4) -> date:
    fy = fiscal_year_start(d, fy_start_month)
    months_in = (d.year - fy.year) * 12 + d.month - fy.month
    return add_months(fy, (months_in // 3) * 3)


def _clip_to_today(label: str, start: date, end: date, today: date) -> Period:
    if start <= today <= end:
        return Period(label=label, start=start, end=today, is_partial=True)
    return Period(label=label, start=start, end=end)


# ----------------------------------------------------------------------------- #
# label -> Period
# ----------------------------------------------------------------------------- #

_RANGE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\s*(?:\.\.|:|to)\s*(\d{4}-\d{2}-\d{2})$")
_MONTH_RE = re.compile(r"^(\d{4})-(\d{2})$")
_LAST_N_RE = re.compile(r"^(?:last|past)_?(\d{1,4})_?days?$")
_FY_RE = re.compile(r"^fy_?(\d{4})(?:-(\d{2,4}))?$")
_Q_RE = re.compile(r"^q([1-4])$")


def resolve_period(label: str, today: date, fy_start_month: int = 4) -> Period:
    """Turn a period label into concrete dates. Raises PeriodError for unknown labels."""
    if isinstance(label, Period):
        return label
    raw = (label or "current_month").strip()
    key = raw.lower().replace(" ", "_")

    if key in ("current_month", "this_month", "mtd", "month_to_date"):
        return _clip_to_today(key, month_start(today), month_end(today), today)
    if key in ("last_month", "previous_month", "prev_month"):
        start = add_months(today, -1)
        return Period(label=key, start=start, end=month_end(start))

    m = _LAST_N_RE.match(key)
    if m:
        n = int(m.group(1))
        if n < 1:
            raise PeriodError("last_N_days needs N >= 1.")
        return Period(label=key, start=today - timedelta(days=n - 1), end=today)

    if key in ("current_quarter", "this_quarter", "qtd", "quarter_to_date"):
        start = fiscal_quarter_start(today, fy_start_month)
        return _clip_to_today(key, start, add_months(start, 3) - timedelta(days=1), today)
    if key in ("last_quarter", "previous_quarter", "prev_quarter"):
        start = add_months(fiscal_quarter_start(today, fy_start_month), -3)
        return Period(label=key, start=start, end=add_months(start, 3) - timedelta(days=1))

    m = _Q_RE.match(key)
    if m:
        start = add_months(fiscal_year_start(today, fy_start_month), 3 * (int(m.group(1)) - 1))
        end = add_months(start, 3) - timedelta(days=1)
        if start > today:
            raise PeriodError(f"{raw} of the current fiscal year has not started yet.")
        return _clip_to_today(key, start, end, today)

    if key in ("ytd", "fytd", "current_fy", "this_fy", "current_year", "this_year", "year_to_date"):
        start = fiscal_year_start(today, fy_start_month)
        return _clip_to_today(key, start, add_months(start, 12) - timedelta(days=1), today)
    if key in ("last_fy", "previous_fy", "last_year", "previous_year"):
        start = add_months(fiscal_year_start(today, fy_start_month), -12)
        return Period(label=key, start=start, end=add_months(start, 12) - timedelta(days=1))

    m = _FY_RE.match(key)
    if m:
        start = date(int(m.group(1)), fy_start_month, 1)
        return _clip_to_today(key, start, add_months(start, 12) - timedelta(days=1), today)

    m = _MONTH_RE.match(key)
    if m:
        try:
            start = date(int(m.group(1)), int(m.group(2)), 1)
        except ValueError as exc:
            raise PeriodError(f"invalid month '{raw}'.") from exc
        return _clip_to_today(key, start, month_end(start), today)

    m = _RANGE_RE.match(raw.lower())
    if m:
        try:
            start, end = date.fromisoformat(m.group(1)), date.fromisoformat(m.group(2))
            return Period(label="custom", start=start, end=end)
        except ValueError as exc:
            raise PeriodError(f"invalid date range '{raw}': {exc}") from exc

    raise PeriodError(
        f"unknown period '{raw}'. Use e.g. current_month, last_month, last_30_days, "
        "current_quarter, Q1-Q4, ytd, last_fy, FY2026, 2026-10, or 2026-10-01..2026-10-31."
    )


# ----------------------------------------------------------------------------- #
# buckets for time series
# ----------------------------------------------------------------------------- #

GRANULARITIES = ("day", "week", "month")


def iter_buckets(period: Period, granularity: str = "month") -> Iterator[Period]:
    """Split a period into day / ISO-week (Mon-Sun) / calendar-month buckets, clipped to the period."""
    if granularity not in GRANULARITIES:
        raise PeriodError(f"granularity must be one of {GRANULARITIES}.")
    cur = period.start
    while cur <= period.end:
        if granularity == "day":
            nxt = cur + timedelta(days=1)
            label = cur.isoformat()
        elif granularity == "week":
            nxt = cur - timedelta(days=cur.weekday()) + timedelta(days=7)
            iso = cur.isocalendar()
            label = f"{iso[0]}-W{iso[1]:02d}"
        else:
            nxt = add_months(cur, 1)
            label = cur.strftime("%Y-%m")
        end = min(nxt - timedelta(days=1), period.end)
        yield Period(label=label, start=cur, end=end)
        cur = nxt


def buckets(period: Period, granularity: str = "month") -> List[Period]:
    return list(iter_buckets(period, granularity))
