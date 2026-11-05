"""
Real finance tool for the Expense Agent (Member 3's deterministic finance engine).

Drop-in replacement for mock_tools.get_expense_anomalies: same name, arguments and return type.
Switch with one import in agents/expense/agent.py:

    from agents.expense.finance_tools import get_expense_anomalies

Differences from the mock:
* Default window is the current month to date (the mock defaults to 2026-10-01..2026-10-31).
* The anomaly list can legitimately be EMPTY. The agent should say "no anomalies found".
* Category labels come from the canonical enum ('Software', 'Rent', 'Payroll', ...).
* Rules are listed in finance_engine/anomalies.py (A1-A5); `rule` is a plain-English sentence.
"""

from datetime import date
from typing import Optional

from agents.expense.schemas import ExpenseAnomaliesResult, ExpenseAnomaly
from finance_engine.tools import get_expense_anomalies as _engine_anomalies


def get_expense_anomalies(business_id: str, start_date: Optional[str] = None,
                          end_date: Optional[str] = None,
                          today: Optional[date] = None) -> ExpenseAnomaliesResult:
    if not business_id or not str(business_id).strip():
        raise ValueError("business_id must not be empty.")
    report = _engine_anomalies(str(business_id).strip(), start_date=start_date, end_date=end_date, today=today)
    return ExpenseAnomaliesResult(
        business_id=report.business_id,
        start_date=report.period.start.isoformat(),
        end_date=report.period.end.isoformat(),
        anomalies=[ExpenseAnomaly(anomaly_id=a.anomaly_id, category=a.category, vendor=a.vendor,
                                  amount=float(a.amount), expected_baseline=float(a.expected_baseline),
                                  severity=a.severity, rule=a.rule, evidence_refs=a.evidence_refs)
                   for a in report.anomalies],
        calculation_version=report.calculation_version,
    )
