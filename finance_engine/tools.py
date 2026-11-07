"""
Agent-facing finance tools.

Thin wrappers around finance_engine.calculations that take what an agent has
(business_id, a period label, optional 'today') and return validated result models.
Agents must copy numbers from these results and never compute figures themselves.

    from finance_engine.tools import get_financial_summary
    report = get_financial_summary("biz_001", period="last_month")
    report.model_dump(mode="json")   # JSON-safe dict (Decimals become strings)

`today` defaults to the real date; pass it explicitly in tests and reproducible reports.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Optional, Union

from finance_engine.anomalies import ExpenseAnomaliesReport, detect_expense_anomalies
from finance_engine.calculations import (
    CashFlowReport,
    OverdueInvoicesReport,
    calculate_overdue_invoices,
    ExpenseReport,
    FinancialSummaryReport,
    ReceivablesReport,
    RevenueReport,
    calculate_expenses,
    calculate_net_cash_flow,
    calculate_receivables,
    calculate_revenue,
    financial_summary,
)
from finance_engine.data import FinanceData
from finance_engine.forecasting import CashFlowForecastReport
from finance_engine.forecasting import forecast_cash_flow as _forecast
from finance_engine.receivables import AgingReport, OverdueReport, aging_report, detect_overdue
from finance_engine.periods import Period, resolve_period

PeriodLike = Union[str, Period]


def _prepare(business_id: str, period: PeriodLike, today: Optional[date],
             processed_dir: Optional[Path], data: Optional[FinanceData]):
    data = data or FinanceData.from_processed(business_id, processed_dir)
    if data.business_id != (business_id or "").strip():
        raise ValueError("business_id does not match the supplied data.")
    p = period if isinstance(period, Period) else resolve_period(
        period, today or date.today(), data.business.fiscal_year_start_month)
    return data, p


def get_revenue(business_id: str, period: PeriodLike = "current_month", today: Optional[date] = None,
                processed_dir: Optional[Path] = None, data: Optional[FinanceData] = None) -> RevenueReport:
    data, p = _prepare(business_id, period, today, processed_dir, data)
    return calculate_revenue(data, p)


def get_expenses(business_id: str, period: PeriodLike = "current_month", today: Optional[date] = None,
                 processed_dir: Optional[Path] = None, data: Optional[FinanceData] = None) -> ExpenseReport:
    data, p = _prepare(business_id, period, today, processed_dir, data)
    return calculate_expenses(data, p)


def get_net_cash_flow(business_id: str, period: PeriodLike = "current_month", granularity: str = "month",
                      today: Optional[date] = None, processed_dir: Optional[Path] = None,
                      data: Optional[FinanceData] = None) -> CashFlowReport:
    data, p = _prepare(business_id, period, today, processed_dir, data)
    return calculate_net_cash_flow(data, p, granularity)


def get_receivables(business_id: str, as_of: Optional[date] = None, today: Optional[date] = None,
                    processed_dir: Optional[Path] = None, data: Optional[FinanceData] = None) -> ReceivablesReport:
    data = data or FinanceData.from_processed(business_id, processed_dir)
    return calculate_receivables(data, as_of or today or date.today())


def get_financial_summary(business_id: str, period: PeriodLike = "current_month", today: Optional[date] = None,
                          processed_dir: Optional[Path] = None,
                          data: Optional[FinanceData] = None) -> FinancialSummaryReport:
    data, p = _prepare(business_id, period, today, processed_dir, data)
    return financial_summary(data, p)


# ----------------------------------------------------------------------------- #
# Forecast, overdue invoices, expense anomalies
# ----------------------------------------------------------------------------- #


def _as_date(value: Optional[Union[str, date]], fallback: date) -> date:
    if value is None or value == "":
        return fallback
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError as exc:
        raise ValueError(f"invalid date '{value}', expected YYYY-MM-DD.") from exc


def get_cash_flow_forecast(business_id: str, horizon_days: int = 30, as_of: Optional[Union[str, date]] = None,
                           today: Optional[date] = None, processed_dir: Optional[Path] = None,
                           data: Optional[FinanceData] = None) -> CashFlowForecastReport:
    """Forecast from `as_of` (default: today) forward `horizon_days`."""
    data = data or FinanceData.from_processed(business_id, processed_dir)
    return _forecast(data, _as_date(as_of, today or date.today()), int(horizon_days or 30))


def get_overdue_invoices(business_id: str, as_of_date: Optional[Union[str, date]] = None,
                         today: Optional[date] = None, processed_dir: Optional[Path] = None,
                         data: Optional[FinanceData] = None) -> OverdueInvoicesReport:
    data = data or FinanceData.from_processed(business_id, processed_dir)
    return calculate_overdue_invoices(data, _as_date(as_of_date, today or date.today()))


def get_expense_anomalies(business_id: str, start_date: Optional[Union[str, date]] = None,
                          end_date: Optional[Union[str, date]] = None, today: Optional[date] = None,
                          processed_dir: Optional[Path] = None,
                          data: Optional[FinanceData] = None) -> ExpenseAnomaliesReport:
    """Default window: the current month to date."""
    data = data or FinanceData.from_processed(business_id, processed_dir)
    t = today or date.today()
    end = _as_date(end_date, t)
    start = _as_date(start_date, end.replace(day=1))
    if end < start:
        raise ValueError("end_date must not be before start_date.")
    return detect_expense_anomalies(data, start, end)


def get_aging_report(business_id: str, as_of_date: Optional[Union[str, date]] = None,
                     today: Optional[date] = None, processed_dir: Optional[Path] = None,
                     data: Optional[FinanceData] = None) -> AgingReport:
    """Receivables aging by customer, DSO and customer payment behaviour."""
    data = data or FinanceData.from_processed(business_id, processed_dir)
    return aging_report(data, _as_date(as_of_date, today or date.today()))


def get_overdue_report(business_id: str, as_of_date: Optional[Union[str, date]] = None, grace_days: int = 0,
                       today: Optional[date] = None, processed_dir: Optional[Path] = None,
                       data: Optional[FinanceData] = None) -> OverdueReport:
    """Overdue and due-soon invoices with reminder stage and priority, status fixes, overdue bills."""
    data = data or FinanceData.from_processed(business_id, processed_dir)
    return detect_overdue(data, _as_date(as_of_date, today or date.today()), grace_days=grace_days)
