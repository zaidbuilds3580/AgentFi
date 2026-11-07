"""
Unified tool source for AgentFi domain agents.

Provides financial tools to Cash Flow, Expense, and Invoice agents, switching
between Member 3's real deterministic finance engine (agents/*/finance_tools.py)
and mock tools (agents/*/mock_tools.py) based on settings.USE_REAL_FINANCE_TOOLS.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Optional

from config.settings import settings

if TYPE_CHECKING:
    from agents.cashflow.schemas import CashFlowForecast, FinancialSummary
    from agents.expense.schemas import ExpenseAnomaliesResult
    from agents.invoice.schemas import OverdueInvoicesResult


def get_currency_symbol(currency: str) -> str:
    """Returns standard display symbol for currency code."""
    symbols = {
        "INR": "₹",
        "USD": "$",
        "EUR": "€",
        "GBP": "£",
    }
    return symbols.get((currency or "INR").upper(), f"{currency} ")


def get_financial_summary(
    business_id: str,
    period: str = "current_month",
    today: Optional[date] = None,
) -> FinancialSummary:
    """Fetch financial summary using real or mock tools depending on settings."""
    if settings.USE_REAL_FINANCE_TOOLS:
        from agents.cashflow import finance_tools
        return finance_tools.get_financial_summary(business_id, period=period, today=today)
    else:
        from agents.cashflow import mock_tools
        return mock_tools.get_financial_summary(business_id, period=period)


def forecast_cash_flow(
    business_id: str,
    horizon_days: int = 30,
    today: Optional[date] = None,
) -> CashFlowForecast:
    """Generate cash flow forecast using real or mock tools depending on settings."""
    if settings.USE_REAL_FINANCE_TOOLS:
        from agents.cashflow import finance_tools
        return finance_tools.forecast_cash_flow(business_id, horizon_days=horizon_days, today=today)
    else:
        from agents.cashflow import mock_tools
        return mock_tools.forecast_cash_flow(business_id, horizon_days=horizon_days)


def get_overdue_invoices(
    business_id: str,
    as_of_date: Optional[str] = None,
    today: Optional[date] = None,
) -> OverdueInvoicesResult:
    """Retrieve overdue invoices using real or mock tools depending on settings."""
    if settings.USE_REAL_FINANCE_TOOLS:
        from agents.invoice import finance_tools
        return finance_tools.get_overdue_invoices(business_id, as_of_date=as_of_date, today=today)
    else:
        from agents.invoice import mock_tools
        return mock_tools.get_overdue_invoices(business_id, as_of_date=as_of_date)


def get_expense_anomalies(
    business_id: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    today: Optional[date] = None,
) -> ExpenseAnomaliesResult:
    """Detect expense anomalies using real or mock tools depending on settings."""
    if settings.USE_REAL_FINANCE_TOOLS:
        from agents.expense import finance_tools
        return finance_tools.get_expense_anomalies(
            business_id, start_date=start_date, end_date=end_date, today=today
        )
    else:
        from agents.expense import mock_tools
        return mock_tools.get_expense_anomalies(
            business_id, start_date=start_date, end_date=end_date
        )
