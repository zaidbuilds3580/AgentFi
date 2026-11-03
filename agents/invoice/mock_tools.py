"""
Mock financial tools for Invoice Agent.

MOCK - replace with Member 3's real tools.
These tools return fixed deterministic sample numbers so the Invoice Agent
can be developed, tested, and integrated independently before Member 3's
production finance engine tools are connected.
"""

from typing import Optional
from agents.invoice.schemas import AgeingBand, OverdueInvoice, OverdueInvoicesResult


def get_overdue_invoices(
    business_id: str,
    as_of_date: Optional[str] = None,
) -> OverdueInvoicesResult:
    """
    MOCK - replace with Member 3's real tools

    Retrieves aggregated overdue invoice information and ageing bands for the business.

    Args:
        business_id: Tenant business identifier.
        as_of_date: Reference date for ageing calculation (defaults to '2026-11-03').

    Returns:
        OverdueInvoicesResult with fixed sample invoice figures and ageing breakdown.
    """
    if not business_id or not str(business_id).strip():
        raise ValueError("business_id must not be empty.")

    ref_date = as_of_date or "2026-11-03"

    # Sample invoices: 4 invoices spanning 3 ageing bands
    invoices = [
        OverdueInvoice(
            invoice_id="INV-2024-001",
            customer_id="CUST-ALPHA",
            due_date="2026-10-15",
            amount=4500.0,
            days_overdue=19,
        ),
        OverdueInvoice(
            invoice_id="INV-2024-002",
            customer_id="CUST-BETA",
            due_date="2026-10-05",
            amount=3200.0,
            days_overdue=29,
        ),
        OverdueInvoice(
            invoice_id="INV-2024-003",
            customer_id="CUST-GAMMA",
            due_date="2026-09-18",
            amount=6800.0,
            days_overdue=46,
        ),
        OverdueInvoice(
            invoice_id="INV-2024-004",
            customer_id="CUST-DELTA",
            due_date="2026-08-15",
            amount=5500.0,
            days_overdue=80,
        ),
    ]

    # Sample ageing bands matching the invoices:
    # 1-30 days: INV-2024-001 ($4,500) + INV-2024-002 ($3,200) = $7,700 (count: 2)
    # 31-60 days: INV-2024-003 ($6,800) = $6,800 (count: 1)
    # 61+ days: INV-2024-004 ($5,500) = $5,500 (count: 1)
    # Total count = 4, Total amount = $20,000.0
    ageing_bands = [
        AgeingBand(band="1-30", count=2, total_amount=7700.0),
        AgeingBand(band="31-60", count=1, total_amount=6800.0),
        AgeingBand(band="61+", count=1, total_amount=5500.0),
    ]

    return OverdueInvoicesResult(
        business_id=business_id.strip(),
        as_of_date=ref_date,
        count=4,
        total_amount=20000.0,
        ageing_bands=ageing_bands,
        invoices=invoices,
        calculation_version="v1.0.0-mock",
    )
