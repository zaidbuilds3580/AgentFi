"""
Real finance tool for the Invoice Agent (Member 3's deterministic finance engine).

Drop-in replacement for mock_tools.get_overdue_invoices: same name, arguments and return type.
Switch with one import in agents/invoice/agent.py:

    from agents.invoice.finance_tools import get_overdue_invoices

Differences from the mock:
* as_of_date defaults to today (the mock defaults to '2026-11-03').
* invoice_id is the canonical ID (e.g. 'inv-1002'); customer_id is e.g. 'cust-greenleaf-organics'.
* All three bands ('1-30', '31-60', '61+') are always returned, even with count 0.
* An invoice due ON as_of_date is not overdue (days_overdue >= 1).
* Foreign-currency invoices are excluded (no FX conversion yet).
"""

from datetime import date
from typing import Optional

from agents.invoice.schemas import AgeingBand, OverdueInvoice, OverdueInvoicesResult
from finance_engine.tools import get_overdue_invoices as _engine_overdue


def get_overdue_invoices(business_id: str, as_of_date: Optional[str] = None,
                         today: Optional[date] = None) -> OverdueInvoicesResult:
    if not business_id or not str(business_id).strip():
        raise ValueError("business_id must not be empty.")
    report = _engine_overdue(str(business_id).strip(), as_of_date=as_of_date, today=today)
    return OverdueInvoicesResult(
        business_id=report.business_id,
        as_of_date=report.as_of.isoformat(),
        count=report.count,
        total_amount=float(report.total_amount),
        ageing_bands=[AgeingBand(band=b.band, count=b.count, total_amount=float(b.total_amount))
                      for b in report.ageing_bands],
        invoices=[OverdueInvoice(invoice_id=i.invoice_id, customer_id=i.customer_id,
                                 due_date=i.due_date.isoformat(), amount=float(i.amount),
                                 days_overdue=i.days_overdue)
                  for i in report.invoices],
        calculation_version=report.calculation_version,
        currency=report.currency,
        warnings=list(report.warnings),
    )
