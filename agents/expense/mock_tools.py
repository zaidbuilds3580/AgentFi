"""
Mock financial tools for Expense Agent.

MOCK - replace with Member 3's real tools.
These tools return fixed deterministic sample numbers so the Expense Agent
can be developed, tested, and integrated independently before Member 3's
production finance engine tools are connected.
"""

from typing import Optional
from agents.expense.schemas import ExpenseAnomaliesResult, ExpenseAnomaly


def get_expense_anomalies(
    business_id: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> ExpenseAnomaliesResult:
    """
    MOCK - replace with Member 3's real tools

    Detects spending anomalies for the specified business within the given date window.

    Args:
        business_id: Tenant business identifier.
        start_date: Start date of inspection window (defaults to '2026-10-01').
        end_date: End date of inspection window (defaults to '2026-10-31').

    Returns:
        ExpenseAnomaliesResult with fixed sample anomaly figures across low, medium, and high severities.
    """
    if not business_id or not str(business_id).strip():
        raise ValueError("business_id must not be empty.")

    s_date = start_date or "2026-10-01"
    e_date = end_date or "2026-10-31"

    # Sample data: 3 anomalies covering high, medium, and low severities
    anomalies = [
        ExpenseAnomaly(
            anomaly_id="ANOM-2024-001",
            category="Software & SaaS",
            vendor="CloudScale Hosting",
            amount=8450.0,
            expected_baseline=2100.0,
            severity="high",
            rule="Monthly spend exceeds 300% of 90-day baseline",
            evidence_refs=["tx_9011", "tx_9012"],
        ),
        ExpenseAnomaly(
            anomaly_id="ANOM-2024-002",
            category="Office Supplies",
            vendor="Prime Staples Direct",
            amount=2650.0,
            expected_baseline=850.0,
            severity="medium",
            rule="Unusual spike in consumable office purchase volume",
            evidence_refs=["tx_9025"],
        ),
        ExpenseAnomaly(
            anomaly_id="ANOM-2024-003",
            category="Travel & Entertainment",
            vendor="Metro Airport Cabs",
            amount=420.0,
            expected_baseline=250.0,
            severity="low",
            rule="Slight variance above historical departmental travel cap",
            evidence_refs=["tx_9038", "tx_9041"],
        ),
    ]

    return ExpenseAnomaliesResult(
        business_id=business_id.strip(),
        start_date=s_date,
        end_date=e_date,
        anomalies=anomalies,
        calculation_version="v1.0.0-mock",
    )
