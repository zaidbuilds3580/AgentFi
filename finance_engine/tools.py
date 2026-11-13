"""
Agent-facing finance tools (final, v1.2).

Thin, validated wrappers around the deterministic engine. Agents call these and copy the
numbers; they never compute figures themselves.

    from finance_engine.tools import get_financial_summary
    report = get_financial_summary("biz_001", period="last_month")
    report.model_dump(mode="json")   # JSON-safe dict (Decimals become strings)

Common behaviour of every tool
------------------------------
* Data: loaded with finance_engine.data.load_business (processed data, or an in-memory ETL
  run on data/raw/<id> or data/samples/<id>), cached until the files change.
  Pass `data=` to use already-loaded FinanceData (tests, batch jobs).
* Inputs are validated and normalised here; bad input raises InvalidInputError (a ValueError),
  an unknown business raises BusinessNotFoundError (a LookupError). Both are FinanceToolError.
* `today` pins the reference date (date or 'YYYY-MM-DD'); it defaults to the real date.
* Results are pydantic models with Decimal amounts, `currency`, `warnings`, `notes` and
  `calculation_version`. The same inputs always give the same output.

Reference: docs/finance_tools.md
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Optional, Tuple, Union

from finance_engine.anomalies import ExpenseAnomaliesReport, detect_expense_anomalies
from finance_engine.calculations import (
    CashFlowReport,
    ExpenseReport,
    FinancialSummaryReport,
    OverdueInvoicesReport,
    ReceivablesReport,
    RevenueReport,
    calculate_expenses,
    calculate_net_cash_flow,
    calculate_overdue_invoices,
    calculate_receivables,
    calculate_revenue,
    financial_summary,
)
from finance_engine.data import FinanceData, load_business, validate_business_id
from finance_engine.errors import InvalidInputError
from finance_engine.forecasting import CashFlowForecastReport
from finance_engine.forecasting import forecast_cash_flow as _forecast
from finance_engine.periods import GRANULARITIES, Period, resolve_period
from finance_engine.receivables import AgingReport, OverdueReport, aging_report, detect_overdue

PeriodLike = Union[str, Period]
DateLike = Union[str, date, datetime, None]

MIN_DATE = date(2000, 1, 1)
MAX_DATE = date(2100, 12, 31)
MAX_HORIZON_DAYS = 365
MAX_ANOMALY_WINDOW_DAYS = 366
MAX_GRACE_DAYS = 90


# ----------------------------------------------------------------------------- #
# input normalisation
# ----------------------------------------------------------------------------- #

def to_date(value: DateLike, name: str = "date", default: Optional[date] = None) -> date:
    """date / datetime / 'YYYY-MM-DD' -> date. Empty -> default (error if no default)."""
    if value is None or (isinstance(value, str) and not value.strip()):
        if default is None:
            raise InvalidInputError(f"{name} is required.")
        return default
    if isinstance(value, datetime):
        value = value.date()
    elif not isinstance(value, date):
        try:
            value = date.fromisoformat(str(value).strip()[:10])
        except ValueError:
            raise InvalidInputError(f"{name} '{value}' is not a valid date; expected YYYY-MM-DD.") from None
    if not (MIN_DATE <= value <= MAX_DATE):
        raise InvalidInputError(f"{name} {value.isoformat()} is outside {MIN_DATE.year}-{MAX_DATE.year}.")
    return value


def to_int(value, name: str, default: int, lo: int, hi: int) -> int:
    """Accepts ints and numeric strings ('30'); rejects bools, fractions and out-of-range values."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return default
    if isinstance(value, bool):
        raise InvalidInputError(f"{name} must be a whole number.")
    try:
        number = float(str(value).strip())
    except ValueError:
        raise InvalidInputError(f"{name} must be a whole number, got '{value}'.") from None
    if number != int(number):
        raise InvalidInputError(f"{name} must be a whole number, got '{value}'.")
    number = int(number)
    if not lo <= number <= hi:
        raise InvalidInputError(f"{name} must be between {lo} and {hi}, got {number}.")
    return number


def _today(today: DateLike) -> date:
    return to_date(today, "today", default=date.today())


def _load(business_id: str, processed_dir: Optional[Path], data: Optional[FinanceData]) -> FinanceData:
    business_id = validate_business_id(business_id)
    if data is None:
        return load_business(business_id, processed_dir)
    if data.business_id != business_id:
        raise InvalidInputError("business_id does not match the supplied data.")
    return data


def _period(data: FinanceData, period: PeriodLike, today: DateLike) -> Period:
    if isinstance(period, Period):
        return period
    return resolve_period(str(period or "current_month"), _today(today), data.business.fiscal_year_start_month)


def _window(start_date: DateLike, end_date: DateLike, today: DateLike) -> Tuple[date, date]:
    end = to_date(end_date, "end_date", default=_today(today))
    start = to_date(start_date, "start_date", default=end.replace(day=1))
    if end < start:
        raise InvalidInputError("end_date must not be before start_date.")
    if (end - start).days + 1 > MAX_ANOMALY_WINDOW_DAYS:
        raise InvalidInputError(f"the date window may be at most {MAX_ANOMALY_WINDOW_DAYS} days.")
    return start, end


# ----------------------------------------------------------------------------- #
# the four agent tools
# ----------------------------------------------------------------------------- #

def get_financial_summary(business_id: str, period: PeriodLike = "current_month", today: DateLike = None,
                          processed_dir: Optional[Path] = None,
                          data: Optional[FinanceData] = None) -> FinancialSummaryReport:
    """Revenue, expenses, inflows/outflows, net cash flow, cash balance and receivables for a period."""
    data = _load(business_id, processed_dir, data)
    return financial_summary(data, _period(data, period, today))


def get_cash_flow_forecast(business_id: str, horizon_days: Union[int, str] = 30, as_of: DateLike = None,
                           today: DateLike = None, processed_dir: Optional[Path] = None,
                           data: Optional[FinanceData] = None) -> CashFlowForecastReport:
    """Projected balance `horizon_days` (1-365) after `as_of` (default today), with risk level."""
    data = _load(business_id, processed_dir, data)
    horizon = to_int(horizon_days, "horizon_days", 30, 1, MAX_HORIZON_DAYS)
    return _forecast(data, to_date(as_of, "as_of", default=_today(today)), horizon)


def get_overdue_invoices(business_id: str, as_of_date: DateLike = None, today: DateLike = None,
                         processed_dir: Optional[Path] = None,
                         data: Optional[FinanceData] = None) -> OverdueInvoicesReport:
    """Invoices past due on `as_of_date` (default today), in bands 1-30 / 31-60 / 61+."""
    data = _load(business_id, processed_dir, data)
    return calculate_overdue_invoices(data, to_date(as_of_date, "as_of_date", default=_today(today)))


def get_expense_anomalies(business_id: str, start_date: DateLike = None, end_date: DateLike = None,
                          today: DateLike = None, processed_dir: Optional[Path] = None,
                          data: Optional[FinanceData] = None) -> ExpenseAnomaliesReport:
    """Rule-based anomalies (A1-A8) in [start_date, end_date]; default: current month to date."""
    data = _load(business_id, processed_dir, data)
    start, end = _window(start_date, end_date, today)
    return detect_expense_anomalies(data, start, end)


# ----------------------------------------------------------------------------- #
# supporting tools
# ----------------------------------------------------------------------------- #

def get_revenue(business_id: str, period: PeriodLike = "current_month", today: DateLike = None,
                processed_dir: Optional[Path] = None, data: Optional[FinanceData] = None) -> RevenueReport:
    data = _load(business_id, processed_dir, data)
    return calculate_revenue(data, _period(data, period, today))


def get_expenses(business_id: str, period: PeriodLike = "current_month", today: DateLike = None,
                 processed_dir: Optional[Path] = None, data: Optional[FinanceData] = None) -> ExpenseReport:
    data = _load(business_id, processed_dir, data)
    return calculate_expenses(data, _period(data, period, today))


def get_net_cash_flow(business_id: str, period: PeriodLike = "current_month", granularity: str = "month",
                      today: DateLike = None, processed_dir: Optional[Path] = None,
                      data: Optional[FinanceData] = None) -> CashFlowReport:
    data = _load(business_id, processed_dir, data)
    granularity = str(granularity or "month").strip().lower()
    if granularity not in GRANULARITIES:
        raise InvalidInputError(f"granularity must be one of {', '.join(GRANULARITIES)}.")
    return calculate_net_cash_flow(data, _period(data, period, today), granularity)


def get_receivables(business_id: str, as_of: DateLike = None, today: DateLike = None,
                    processed_dir: Optional[Path] = None, data: Optional[FinanceData] = None) -> ReceivablesReport:
    data = _load(business_id, processed_dir, data)
    return calculate_receivables(data, to_date(as_of, "as_of", default=_today(today)))


def get_aging_report(business_id: str, as_of_date: DateLike = None, today: DateLike = None,
                     processed_dir: Optional[Path] = None, data: Optional[FinanceData] = None) -> AgingReport:
    """Receivables aging by customer, DSO and customer payment behaviour."""
    data = _load(business_id, processed_dir, data)
    return aging_report(data, to_date(as_of_date, "as_of_date", default=_today(today)))


def get_overdue_report(business_id: str, as_of_date: DateLike = None, grace_days: Union[int, str] = 0,
                       today: DateLike = None, processed_dir: Optional[Path] = None,
                       data: Optional[FinanceData] = None) -> OverdueReport:
    """Overdue and due-soon invoices with reminder stage and priority, status fixes, overdue bills."""
    data = _load(business_id, processed_dir, data)
    grace = to_int(grace_days, "grace_days", 0, 0, MAX_GRACE_DAYS)
    return detect_overdue(data, to_date(as_of_date, "as_of_date", default=_today(today)), grace_days=grace)
