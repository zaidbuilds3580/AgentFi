"""
Real finance tools for the Cash Flow Agent (Member 3's deterministic finance engine).

Drop-in replacements for mock_tools: same names, same arguments, same return types
(agents.cashflow.schemas). To switch the agent over, change one import in agents/cashflow/agent.py:

    from agents.cashflow.finance_tools import forecast_cash_flow, get_financial_summary

Requires processed data for the business: python -m scripts.run_etl
Raises BusinessNotFoundError (LookupError), PeriodError (ValueError) or ValueError, which the
agent already turns into a "could not calculate right now" message.

The optional `today` argument pins the reference date (tests, reproducible demos).
"""

from datetime import date
from typing import Optional

from agents.cashflow.schemas import CashFlowForecast, FinancialSummary
from finance_engine.tools import get_cash_flow_forecast as _engine_forecast
from finance_engine.tools import get_financial_summary as _engine_summary


def _require_id(business_id: str) -> str:
    if not business_id or not str(business_id).strip():
        raise ValueError("business_id must not be empty.")
    return str(business_id).strip()


def get_financial_summary(business_id: str, period: str = "current_month",
                          today: Optional[date] = None) -> FinancialSummary:
    report = _engine_summary(_require_id(business_id), period=period or "current_month", today=today)
    # The agent schema uses floats; Decimal -> float only here, at the boundary.
    return FinancialSummary(
        business_id=report.business_id,
        period=str(report.period),
        inflows=float(report.inflows),
        outflows=float(report.outflows),
        net_cash_flow=float(report.net_cash_flow),
        cash_balance=float(report.cash_balance),
        receivables=float(report.receivables),
        calculation_version=report.calculation_version,
        currency=report.currency,
        warnings=list(report.warnings),
    )


def forecast_cash_flow(business_id: str, horizon_days: int = 30,
                       today: Optional[date] = None) -> CashFlowForecast:
    report = _engine_forecast(_require_id(business_id), horizon_days=horizon_days or 30, today=today)
    return CashFlowForecast(
        business_id=report.business_id,
        horizon_days=report.horizon_days,
        projected_balance=float(report.projected_balance),
        method=report.method,
        risk_level=report.risk_level,
        data_coverage=float(report.data_coverage),
        currency=report.currency,
        warnings=list(report.warnings),
    )
