"""
Deterministic data fixtures that back the Reporting Agent (and anything else that needs
"real engine numbers" without a database).

Each scenario is a small, fake business built from canonical models, designed so the
deterministic engine produces one clear reporting situation on the demo date (2026-11-05):

    biz_fx_steady    healthy: growing cash, every invoice paid on time, no anomalies   -> risk low
    biz_fx_crunch    cash crunch: runway under a week, invoices overdue in all bands  -> risk high
    biz_fx_anomaly   normal cash, but November spend trips anomaly rules A1/A2/A4/A5
    biz_fx_thin      brand-new business: 13 days of bank history, warnings           -> risk medium

The real sample business `biz_001` (data/samples/biz_001) is the fifth reporting scenario; it
needs no builder because the tools auto-build it from the sample files.

Nothing here reads the clock or uses randomness: the same code always produces byte-identical
CSVs and therefore identical engine results (see tests/test_reporting_fixtures.py).

Usage
-----
    from finance_engine.fixtures import build_scenario, write_fixtures
    datasets = build_scenario("biz_fx_crunch")             # {entity: [canonical models]}
    write_fixtures("data/processed")                      # processed CSVs the agent tools can load

    python -m scripts.build_fixtures                      # same, from the command line
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence

from finance_engine.schema.canonical import (
    Account,
    Business,
    Counterparty,
    CounterpartyType,
    Expense,
    ExpenseCategory,
    Invoice,
    InvoiceLineItem,
    Transaction,
)

AS_OF = date(2026, 11, 5)          # the project's fixed demo date (config.settings.DEMO_AS_OF_DATE)
HISTORY_MONTHS = (7, 8, 9, 10)     # July-October 2026 give every rule enough history
GST = "0.18"
# lineage on every record; fixed (not "now") so the CSVs are byte-identical on every run
LINEAGE = {"source": "fixture", "ingested_at": datetime(2026, 11, 5, tzinfo=timezone.utc)}

Datasets = Dict[str, list]


@dataclass(frozen=True)
class Scenario:
    business_id: str
    name: str
    description: str
    builder: Optional[Callable[[], Datasets]]   # None = loaded from data/samples by the tools


# ============================================================================== #
# record helpers
# ============================================================================== #

class _Book:
    """Collects one business's records with short, readable constructors."""

    def __init__(self, business_id: str, name: str, opening: str, industry: str):
        self.biz = business_id
        self.rows: Datasets = {
            "businesses": [Business(business_id=business_id, name=name, industry=industry)],
            "accounts": [Account(business_id=business_id, account_id=f"{business_id}-bank", name="Current A/c",
                                 opening_balance=opening, **LINEAGE)],
            "counterparties": [], "transactions": [], "invoices": [], "expenses": [],
        }
        self.account = f"{business_id}-bank"

    def party(self, cid: str, name: str, kind: CounterpartyType, terms: Optional[int] = None) -> str:
        self.rows["counterparties"].append(Counterparty(
            business_id=self.biz, counterparty_id=cid, name=name, counterparty_type=kind, payment_terms_days=terms,
            **LINEAGE))
        return cid

    def txn(self, tid: str, day: date, amount: str, direction: str, desc: str,
            category: Optional[ExpenseCategory] = None, invoice_id: Optional[str] = None) -> str:
        self.rows["transactions"].append(Transaction(
            business_id=self.biz, transaction_id=tid, account_id=self.account, transaction_date=day,
            amount=amount, direction=direction, description=desc, category=category, invoice_id=invoice_id,
            **LINEAGE))
        return tid

    def pay(self, tid: str, day: date, amount: str, desc: str, category: Optional[ExpenseCategory],
            vendor: Optional[str] = None, bill_amount: Optional[str] = None) -> str:
        """An outflow; with `vendor`, also the paid bill (expense record) it settles."""
        self.txn(tid, day, amount, "outflow", desc, category)
        if vendor:
            self.rows["expenses"].append(Expense(
                business_id=self.biz, expense_id=f"bill-{tid}", vendor_id=vendor, expense_date=day,
                amount=bill_amount or amount, category=category or ExpenseCategory.other,
                description=desc, is_paid=True, transaction_id=tid, **LINEAGE))
        return tid

    def bill(self, eid: str, day: date, amount: str, category: ExpenseCategory, vendor: str, due: date) -> str:
        """An unpaid bill (accrual only, no bank line yet)."""
        self.rows["expenses"].append(Expense(
            business_id=self.biz, expense_id=eid, vendor_id=vendor, expense_date=day, due_date=due,
            amount=amount, category=category, is_paid=False, **LINEAGE))
        return eid

    def invoice(self, iid: str, customer: str, issue: date, due: date, net: str,
                paid_on: Optional[date] = None, partial: Optional[str] = None) -> str:
        """An invoice (net + 18% GST). paid_on: fully paid by a linked inflow; partial: amount received so far."""
        item = InvoiceLineItem(description="Professional services", unit_price=net, tax_rate=GST)
        total = item.total
        if paid_on is not None:
            status, paid = "paid", total
            self.txn(f"rcpt-{iid}", paid_on, str(total), "inflow", f"NEFT CR {customer.upper()} {iid.upper()}",
                     invoice_id=iid)
        elif partial is not None:
            status, paid = "partially_paid", partial
            self.txn(f"rcpt-{iid}", issue + timedelta(days=10), partial, "inflow",
                     f"NEFT CR {customer.upper()} {iid.upper()} PART", invoice_id=iid)
        else:
            status, paid = "sent", "0"
        self.rows["invoices"].append(Invoice(
            business_id=self.biz, invoice_id=iid, invoice_number=iid.upper(), customer_id=customer,
            issue_date=issue, due_date=due, line_items=[item], amount_paid=paid, status=status,
            **LINEAGE))
        return iid

    def monthly_costs(self, months: Sequence[int], payroll: str, rent: str, cloud: str, year: int = 2026) -> None:
        """Fixed monthly costs: payroll on the 1st, cloud on the 2nd, rent (with its bill) on the 3rd."""
        for m in months:
            self.pay(f"pay-{year}{m:02d}", date(year, m, 1), payroll, "SALARY PAYOUT TEAM", ExpenseCategory.payroll)
            self.pay(f"aws-{year}{m:02d}", date(year, m, 2), cloud, "AWS CLOUD SERVICES", ExpenseCategory.software,
                     vendor="v-aws")
            self.pay(f"rent-{year}{m:02d}", date(year, m, 3), rent, "IMPS ORANGE REALTY OFFICE RENT",
                     ExpenseCategory.rent, vendor="v-realty")

    def bank_charge(self, day: date = AS_OF) -> str:
        return self.pay(f"chg-{day.isoformat()}", day, "236", "BANK CHARGES INCL GST", ExpenseCategory.other)

    def standard_vendors(self) -> None:
        self.party("v-aws", "Amazon Web Services", CounterpartyType.vendor)
        self.party("v-realty", "Orange Realty LLP", CounterpartyType.vendor)


# ============================================================================== #
# scenarios
# ============================================================================== #

def _steady() -> Datasets:
    b = _Book("biz_fx_steady", "Steady Studio Pvt Ltd", opening="800000", industry="design")
    b.standard_vendors()
    a = b.party("c-aurora", "Aurora Retail", CounterpartyType.customer, terms=30)
    n = b.party("c-nimbus", "Nimbus Labs", CounterpartyType.customer, terms=30)
    b.monthly_costs((*HISTORY_MONTHS, 11), payroll="150000", rent="50000", cloud="10000")
    for m in HISTORY_MONTHS:                      # issued on the 5th, paid on the due date
        issue = date(2026, m, 5)
        due = issue + timedelta(days=30)
        b.invoice(f"st-{m:02d}a", a, issue, due, "200000", paid_on=due)
        b.invoice(f"st-{m:02d}n", n, issue, due, "100000", paid_on=due - timedelta(days=2))
    b.invoice("st-11a", a, date(2026, 11, 2), date(2026, 12, 2), "200000")         # open, not yet due
    for m in HISTORY_MONTHS:
        b.txn(f"int-{m:02d}", date(2026, m, 28), "1500", "inflow", "SB INTEREST CREDIT")
    b.bank_charge()                                # bank data runs through the as-of date
    return b.rows


def _crunch() -> Datasets:
    b = _Book("biz_fx_crunch", "Crunch Traders LLP", opening="280000", industry="wholesale")
    b.standard_vendors()
    k = b.party("c-kestrel", "Kestrel Foods", CounterpartyType.customer, terms=30)
    m_ = b.party("c-marlin", "Marlin Hotels", CounterpartyType.customer, terms=30)
    b.monthly_costs((*HISTORY_MONTHS, 11), payroll="180000", rent="60000", cloud="8000")
    for m in HISTORY_MONTHS:                      # the reliable customer still pays
        issue = date(2026, m, 1)
        b.invoice(f"cr-{m:02d}k", k, issue, issue + timedelta(days=30), "200000", paid_on=issue + timedelta(days=33))
    # Marlin stopped paying: one invoice in each overdue band on 2026-11-05
    b.invoice("cr-07m", m_, date(2026, 7, 20), date(2026, 8, 21), "90000")                    # 76 days late
    b.invoice("cr-08m", m_, date(2026, 8, 20), date(2026, 9, 21), "90000", partial="40000")   # 45 days late
    b.invoice("cr-09m", m_, date(2026, 9, 20), date(2026, 10, 21), "90000")                   # 15 days late
    b.invoice("cr-10m", m_, date(2026, 10, 20), date(2026, 11, 20), "90000")                  # not yet due
    g = b.party("v-gst", "GST Department", CounterpartyType.vendor)
    b.bill("gst-q2", date(2026, 10, 20), "45000", ExpenseCategory.taxes, g, due=date(2026, 11, 20))   # unpaid
    b.bank_charge()                                # bank data runs through the as-of date
    return b.rows


def _anomaly() -> Datasets:
    b = _Book("biz_fx_anomaly", "Spiky Software Pvt Ltd", opening="600000", industry="software")
    b.standard_vendors()
    b.party("v-fastprint", "FastPrint Services", CounterpartyType.vendor)
    c = b.party("c-orbit", "Orbit Finance", CounterpartyType.customer, terms=15)
    b.monthly_costs(HISTORY_MONTHS, payroll="120000", rent="50000", cloud="12000")
    for m in HISTORY_MONTHS:
        issue = date(2026, m, 10)
        b.invoice(f"an-{m:02d}", c, issue, issue + timedelta(days=15), "250000", paid_on=issue + timedelta(days=15))
    # November: what the expense rules should catch (default window = 1-5 Nov)
    b.pay("pay-202611", date(2026, 11, 1), "120000", "SALARY PAYOUT TEAM", ExpenseCategory.payroll)   # normal
    b.pay("aws-202611", date(2026, 11, 2), "72000", "AWS CLOUD SERVICES", ExpenseCategory.software,
          vendor="v-aws")                                                                           # A1: 6x usual
    b.pay("rent-202611", date(2026, 11, 3), "50000", "IMPS ORANGE REALTY OFFICE RENT", ExpenseCategory.rent,
          vendor="v-realty", bill_amount="55000")                                                   # A4: bill says 55k
    b.pay("print-1", date(2026, 11, 3), "4500", "UPI FASTPRINT BROCHURES", ExpenseCategory.marketing)
    b.pay("print-2", date(2026, 11, 4), "4500", "UPI FASTPRINT BROCHURES", ExpenseCategory.marketing)  # A2: duplicate
    b.pay("misc-1", date(2026, 11, 4), "2350", "POS 4471 CASH WITHDRAWAL", None)                    # A5: uncategorized
    b.bank_charge()                                # bank data runs through the as-of date
    return b.rows


def _thin() -> Datasets:
    b = _Book("biz_fx_thin", "Fresh Start Foods", opening="250000", industry="food")
    b.standard_vendors()
    c = b.party("c-greenleaf", "Greenleaf Cafe", CounterpartyType.customer, terms=15)
    b.txn("cap-1", date(2026, 10, 21), "100000", "inflow", "CAPITAL INTRODUCED BY PROMOTER")
    b.pay("rent-202610", date(2026, 10, 22), "40000", "IMPS ORANGE REALTY OFFICE RENT", ExpenseCategory.rent,
          vendor="v-realty")
    b.pay("kit-1", date(2026, 10, 25), "65000", "KITCHEN EQUIPMENT PURCHASE", ExpenseCategory.supplies)
    b.pay("aws-202611", date(2026, 11, 2), "3000", "AWS CLOUD SERVICES", ExpenseCategory.software, vendor="v-aws")
    b.invoice("th-01", c, date(2026, 10, 28), date(2026, 11, 12), "30000")             # first invoice, not yet due
    return b.rows


SCENARIOS: Dict[str, Scenario] = {s.business_id: s for s in (
    Scenario("biz_fx_steady", "Steady", "Healthy business: growing cash, invoices paid on time, no anomalies.", _steady),
    Scenario("biz_fx_crunch", "Cash crunch", "Heavy burn and a customer who stopped paying: overdue in every band, "
                                             "cash runway shorter than the 30-day horizon.", _crunch),
    Scenario("biz_fx_anomaly", "Expense anomalies", "Normal cash, but November spend trips rules A1, A2, A4 and A5.",
             _anomaly),
    Scenario("biz_fx_thin", "Thin data", "New business with 13 days of bank history: low coverage warnings.", _thin),
    Scenario("biz_001", "Sample business", "The fake demo business from data/samples/biz_001 (built by the ETL).", None),
)}

FIXTURE_IDS: List[str] = [k for k, s in SCENARIOS.items() if s.builder is not None]


def build_scenario(business_id: str) -> Datasets:
    """Canonical records for one built scenario: {entity_name: [models]}."""
    scenario = SCENARIOS.get(business_id)
    if scenario is None or scenario.builder is None:
        raise KeyError(f"no fixture builder for '{business_id}'. Built fixtures: {', '.join(FIXTURE_IDS)}.")
    return scenario.builder()


def write_fixtures(processed_root, business_ids: Optional[Sequence[str]] = None) -> List[Path]:
    """
    Write built scenarios as processed CSVs (<processed_root>/<business_id>/<entity>.csv), the
    same format the ETL writes, so the finance tools and agents load them like real data.
    """
    from finance_engine.etl.pipeline import write_processed

    root = Path(processed_root)
    written: List[Path] = []
    for biz in business_ids or FIXTURE_IDS:
        written += write_processed(build_scenario(biz), root / biz)
    return written
