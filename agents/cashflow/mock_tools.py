"""
Mock financial tools for Cash Flow Agent.

MOCK - replace with Member 3's real tools.
These tools return fixed deterministic sample numbers so the Cash Flow Agent
can be developed, tested, and integrated independently before Member 3's
production finance engine tools are connected.
"""

from agents.cashflow.schemas import CashFlowForecast, FinancialSummary


def get_financial_summary(business_id: str, period: str = "current_month") -> FinancialSummary:
    """
    MOCK - replace with Member 3's real tools

    Fetches the aggregated financial summary for the specified business and period.

    Args:
        business_id: Tenant business identifier.
        period: Reporting period (defaults to 'current_month').

    Returns:
        FinancialSummary with fixed sample financial figures.
    """
    if not business_id or not str(business_id).strip():
        raise ValueError("business_id must not be empty.")

    return FinancialSummary(
        business_id=business_id.strip(),
        period=period or "current_month",
        inflows=45000.0,
        outflows=32000.0,
        net_cash_flow=13000.0,
        cash_balance=78000.0,
        receivables=15000.0,
        calculation_version="v1.0.0-mock",
        currency="USD",
        warnings=[],
    )


def forecast_cash_flow(business_id: str, horizon_days: int = 30) -> CashFlowForecast:
    """
    MOCK - replace with Member 3's real tools

    Generates a forward-looking cash flow forecast for the specified horizon.

    Args:
        business_id: Tenant business identifier.
        horizon_days: Forecast horizon in days (defaults to 30).

    Returns:
        CashFlowForecast with fixed sample forecast figures.
    """
    if not business_id or not str(business_id).strip():
        raise ValueError("business_id must not be empty.")

    return CashFlowForecast(
        business_id=business_id.strip(),
        horizon_days=horizon_days or 30,
        projected_balance=91000.0,
        method="linear_runway",
        risk_level="low",
        data_coverage=0.95,
        currency="USD",
        warnings=[],
    )
