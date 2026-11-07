"""
Invoice aging and overdue detection.

    aging_report(data, as_of)        accounts-receivable aging by customer, DSO, customer payment behaviour
    detect_overdue(data, as_of)      overdue / due-soon invoices with reminder stage, collection priority,
                                     status corrections and overdue bills (payables)
    payment_history(data)            when each invoice was actually settled, from linked bank inflows

All functions are deterministic: the same data and as_of date always give the same output.
Amounts are outstanding balances (total incl. tax − amount_paid) in the base currency.
Definitions and thresholds: docs/finance_engine.md ("Invoice aging & overdue detection").
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from statistics import median
from typing import Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from finance_engine.calculations import (
    AGING_BUCKETS,
    BILLED_STATUSES,
    ZERO,
    EngineResult,
    money,
    share,
)
from finance_engine.data import FinanceData
from finance_engine.periods import Period
from finance_engine.schema.canonical import Invoice, InvoiceStatus, TransactionDirection

BUCKET_NAMES: Tuple[str, ...] = tuple(k for k, _, _ in AGING_BUCKETS)  # current, 1-30, 31-60, 61-90, 90+
ON_TIME_GRACE_DAYS = 3        # paid up to 3 days after due date still counts as on time
DSO_WINDOW_DAYS = 90
DUE_SOON_DAYS = 3

# (min days overdue, max days overdue, stage, recommended action)
REMINDER_STAGES: Tuple[Tuple[int, Optional[int], str, str], ...] = (
    (1, 7, "gentle_reminder", "Send a friendly payment reminder."),
    (8, 30, "firm_reminder", "Send a firm reminder and follow up by phone."),
    (31, 60, "final_notice", "Send a final notice; consider pausing further work for this customer."),
    (61, None, "escalate", "Escalate: stop credit, involve a collections agency or legal notice."),
)
PRIORITY_ORDER = {"critical": 0, "high": 1, "normal": 2}


def aging_bucket(days_overdue: int) -> str:
    """'current' (not yet due, incl. due today), '1-30', '31-60', '61-90', '90+'."""
    for key, lo, hi in AGING_BUCKETS:
        if days_overdue >= lo and (hi is None or days_overdue <= hi):
            return key
    return BUCKET_NAMES[-1]


def _open_invoices(data: FinanceData, as_of: date, warnings: List[str]) -> List[Invoice]:
    """Billed, unpaid, base-currency invoices issued on/before as_of."""
    out = []
    for inv in data.invoices:
        if inv.status not in BILLED_STATUSES or inv.balance_due <= 0 or inv.issue_date > as_of:
            continue
        if inv.currency != data.base_currency:
            warnings.append(f"Invoice {inv.invoice_number} ({inv.currency} {money(inv.balance_due)}) "
                            f"excluded (no FX conversion).")
            continue
        out.append(inv)
    return out


# ============================================================================= #
# Payment behaviour
# ============================================================================= #

class SettledInvoice(BaseModel):
    invoice_id: str
    customer_id: str
    due_date: date
    settled_on: date
    days_late: int = Field(description="Settlement date minus due date (negative = paid early).")
    payment_refs: List[str]


class PaymentBehaviour(BaseModel):
    customer_id: str
    customer_name: str
    invoices_settled: int
    avg_days_late: Optional[Decimal] = None
    median_days_late: Optional[Decimal] = None
    max_days_late: Optional[int] = None
    on_time_rate: Optional[Decimal] = Field(None, description=f"Share settled within {ON_TIME_GRACE_DAYS} days of due date.")
    reliability: str = Field(description="reliable | slow | unreliable | unknown")
    evidence_refs: List[str] = Field(default_factory=list)


def payment_history(data: FinanceData, as_of: Optional[date] = None) -> List[SettledInvoice]:
    """
    Settlement date of each invoice = the date its linked bank inflows (cumulatively) reached the
    invoice total. Only invoices whose payments can be seen in the bank data are included.
    """
    by_invoice: Dict[str, list] = defaultdict(list)
    for t in data.transactions:
        if t.invoice_id and t.direction == TransactionDirection.inflow and (as_of is None or t.transaction_date <= as_of):
            by_invoice[t.invoice_id].append(t)
    settled = []
    for inv in data.invoices:
        payments = sorted(by_invoice.get(inv.invoice_id, []), key=lambda t: (t.transaction_date, t.transaction_id))
        total, running, refs = inv.total, ZERO, []
        for t in payments:
            running += t.amount
            refs.append(t.transaction_id)
            if running >= total:
                settled.append(SettledInvoice(invoice_id=inv.invoice_id, customer_id=inv.customer_id,
                                              due_date=inv.due_date, settled_on=t.transaction_date,
                                              days_late=(t.transaction_date - inv.due_date).days, payment_refs=refs))
                break
    return sorted(settled, key=lambda s: (s.settled_on, s.invoice_id))


def _reliability(days: List[int]) -> Tuple[str, Optional[Decimal], Optional[Decimal]]:
    if not days:
        return "unknown", None, None
    avg = Decimal(sum(days)) / len(days)
    on_time = share(Decimal(sum(1 for d in days if d <= ON_TIME_GRACE_DAYS)), Decimal(len(days)))
    if avg <= 5 and on_time >= Decimal("0.7"):
        label = "reliable"
    elif avg <= 30:
        label = "slow"
    else:
        label = "unreliable"
    return label, avg.quantize(Decimal("0.1")), on_time


def payment_behaviour(data: FinanceData, as_of: Optional[date] = None) -> List[PaymentBehaviour]:
    """
    Per customer: how late they usually pay, from settled invoices.
      reliable    average <= 5 days late and >= 70% paid within 3 days of the due date
      slow        average <= 30 days late
      unreliable  average  > 30 days late
      unknown     no settled invoices in the bank data
    """
    history: Dict[str, List[SettledInvoice]] = defaultdict(list)
    for s in payment_history(data, as_of):
        history[s.customer_id].append(s)
    customers = sorted({i.customer_id for i in data.invoices} | set(history))
    out = []
    for cid in customers:
        items = history.get(cid, [])
        days = [s.days_late for s in items]
        label, avg, on_time = _reliability(days)
        out.append(PaymentBehaviour(
            customer_id=cid, customer_name=data.counterparty_name(cid), invoices_settled=len(items),
            avg_days_late=avg, median_days_late=Decimal(str(median(days))).quantize(Decimal("0.1")) if days else None,
            max_days_late=max(days) if days else None, on_time_rate=on_time, reliability=label,
            evidence_refs=sorted(s.invoice_id for s in items),
        ))
    return out


# ============================================================================= #
# Aging report
# ============================================================================= #

class CustomerAging(BaseModel):
    customer_id: str
    customer_name: str
    buckets: Dict[str, Decimal] = Field(description="Outstanding per aging bucket (all buckets present).")
    total: Decimal
    overdue: Decimal
    invoice_count: int
    oldest_days_overdue: int
    reliability: str
    evidence_refs: List[str]


class AgingReport(EngineResult):
    as_of: date
    bucket_names: List[str]
    customers: List[CustomerAging]
    totals: Dict[str, Decimal]
    total_outstanding: Decimal
    total_overdue: Decimal
    overdue_share: Decimal = Field(description="Overdue / total outstanding (0-1).")
    weighted_avg_days_overdue: Decimal = Field(description="Balance-weighted average days past due of overdue invoices.")
    dso: Optional[Decimal] = Field(None, description="Days sales outstanding over dso_window_days.")
    dso_window_days: int
    payment_behaviour: List[PaymentBehaviour]


def aging_report(data: FinanceData, as_of: date, dso_window_days: int = DSO_WINDOW_DAYS) -> AgingReport:
    """
    Accounts-receivable aging by customer and bucket, plus:
      DSO = outstanding receivables / gross billed in the last `dso_window_days` × dso_window_days
    """
    warnings: List[str] = []
    notes = ["Balances use the latest amount_paid on each invoice (a snapshot, not historical)."]
    open_invs = _open_invoices(data, as_of, warnings)
    behaviour = {b.customer_id: b for b in payment_behaviour(data, as_of)}

    per_customer: Dict[str, List[Invoice]] = defaultdict(list)
    for inv in open_invs:
        per_customer[inv.customer_id].append(inv)

    customers = []
    totals = {b: ZERO for b in BUCKET_NAMES}
    for cid, invs in per_customer.items():
        buckets = {b: ZERO for b in BUCKET_NAMES}
        oldest = 0
        for inv in invs:
            days = (as_of - inv.due_date).days
            buckets[aging_bucket(days)] += inv.balance_due
            oldest = max(oldest, days)
        for b in BUCKET_NAMES:
            totals[b] += buckets[b]
        total = sum(buckets.values(), ZERO)
        customers.append(CustomerAging(
            customer_id=cid, customer_name=data.counterparty_name(cid),
            buckets={b: money(v) for b, v in buckets.items()}, total=money(total),
            overdue=money(total - buckets["current"]), invoice_count=len(invs), oldest_days_overdue=max(oldest, 0),
            reliability=behaviour[cid].reliability if cid in behaviour else "unknown",
            evidence_refs=sorted(i.invoice_id for i in invs),
        ))
    customers.sort(key=lambda c: (-c.total, c.customer_id))

    total_out = sum(totals.values(), ZERO)
    total_overdue = total_out - totals["current"]
    weighted_num = sum((inv.balance_due * (as_of - inv.due_date).days for inv in open_invs if inv.due_date < as_of), ZERO)
    weighted = (weighted_num / total_overdue).quantize(Decimal("0.1")) if total_overdue else Decimal("0.0")

    window_start = as_of - timedelta(days=dso_window_days - 1)
    billed = sum((inv.total for inv in data.invoices
                  if inv.status in BILLED_STATUSES and inv.currency == data.base_currency
                  and window_start <= inv.issue_date <= as_of), ZERO)
    if billed > 0:
        dso = (total_out / billed * dso_window_days).quantize(Decimal("0.1"))
    else:
        dso = None
        notes.append(f"DSO not available: nothing billed in the last {dso_window_days} days.")
    first_txn = data.bank_data_range()
    if first_txn and first_txn[0] > window_start:
        warnings.append(f"Bank/invoice history starts {first_txn[0].isoformat()}; payment behaviour and DSO "
                        f"are based on a shorter history.")

    return AgingReport(
        business_id=data.business_id, currency=data.base_currency,
        period=Period(label="as_of", start=as_of, end=as_of), warnings=warnings, notes=notes,
        as_of=as_of, bucket_names=list(BUCKET_NAMES), customers=customers,
        totals={b: money(v) for b, v in totals.items()}, total_outstanding=money(total_out),
        total_overdue=money(total_overdue), overdue_share=share(total_overdue, total_out),
        weighted_avg_days_overdue=weighted, dso=dso, dso_window_days=dso_window_days,
        payment_behaviour=list(behaviour.values()),
    )


# ============================================================================= #
# Overdue detection
# ============================================================================= #

class OverdueItem(BaseModel):
    invoice_id: str
    invoice_number: str
    customer_id: str
    customer_name: str
    issue_date: date
    due_date: date
    total: Decimal
    amount_paid: Decimal
    balance: Decimal
    days_overdue: int = Field(description="Negative = days until due.")
    bucket: str
    partially_paid: bool
    stage: str
    recommended_action: str
    priority: str = Field(description="critical | high | normal")
    priority_reasons: List[str]
    customer_reliability: str


class StatusCorrection(BaseModel):
    invoice_id: str
    invoice_number: str
    recorded_status: str
    derived_status: str
    reason: str


class OverdueBill(BaseModel):
    expense_id: str
    vendor_id: Optional[str]
    vendor_name: str
    description: str
    category: str
    amount: Decimal
    due_date: date
    days_overdue: int


class OverdueReport(EngineResult):
    as_of: date
    grace_days: int
    overdue: List[OverdueItem]
    due_soon: List[OverdueItem]
    status_corrections: List[StatusCorrection]
    overdue_bills: List[OverdueBill]
    total_overdue: Decimal
    total_due_soon: Decimal
    total_bills_overdue: Decimal


def derive_invoice_status(inv: Invoice, as_of: date, grace_days: int = 0) -> InvoiceStatus:
    """The status an invoice SHOULD have on as_of, from its dates and payments."""
    if inv.status in (InvoiceStatus.draft, InvoiceStatus.void):
        return inv.status
    if inv.balance_due <= 0:
        return InvoiceStatus.paid
    if (as_of - inv.due_date).days > grace_days:
        return InvoiceStatus.overdue
    if inv.amount_paid > 0:
        return InvoiceStatus.partially_paid
    return InvoiceStatus.sent


def _stage(days: int) -> Tuple[str, str]:
    if days <= 0:
        return "due_soon", "Send a courtesy reminder before the due date."
    for lo, hi, stage, action in REMINDER_STAGES:
        if days >= lo and (hi is None or days <= hi):
            return stage, action
    return REMINDER_STAGES[-1][2], REMINDER_STAGES[-1][3]


def _priority(days: int, balance: Decimal, total_ar: Decimal, reliability: str) -> Tuple[str, List[str]]:
    """
    critical  > 60 days overdue, or >= 25% of all outstanding receivables
    high      > 30 days overdue, or >= 10% of receivables, or customer usually pays late
    normal    otherwise
    """
    reasons, level = [], "normal"
    pct = share(balance, total_ar)
    if days > 60:
        reasons.append(f"{days} days overdue")
        level = "critical"
    if pct >= Decimal("0.25"):
        reasons.append(f"{pct * 100:.0f}% of outstanding receivables")
        level = "critical"
    if level != "critical":
        if days > 30:
            reasons.append(f"{days} days overdue")
            level = "high"
        if pct >= Decimal("0.10"):
            reasons.append(f"{pct * 100:.0f}% of outstanding receivables")
            level = "high"
        if reliability in ("slow", "unreliable"):
            reasons.append(f"customer is a {reliability} payer")
            level = "high"
    return level, reasons


def detect_overdue(data: FinanceData, as_of: date, grace_days: int = 0,
                   due_soon_days: int = DUE_SOON_DAYS) -> OverdueReport:
    """
    * overdue:   open invoices more than `grace_days` past due, with reminder stage and priority
    * due_soon:  open invoices due within the next `due_soon_days` days (incl. today)
    * status_corrections: invoices whose recorded status disagrees with dates/payments
    * overdue_bills: unpaid expenses (payables) past their due date
    """
    if grace_days < 0 or due_soon_days < 0:
        raise ValueError("grace_days and due_soon_days must be >= 0.")
    warnings: List[str] = []
    notes = [f"Invoices count as overdue more than {grace_days} day(s) after the due date."]
    open_invs = _open_invoices(data, as_of, warnings)
    total_ar = sum((i.balance_due for i in open_invs), ZERO)
    reliability = {b.customer_id: b.reliability for b in payment_behaviour(data, as_of)}

    overdue, due_soon = [], []
    for inv in open_invs:
        days = (as_of - inv.due_date).days
        is_overdue = days > grace_days
        if not is_overdue and not (-due_soon_days <= days <= grace_days):
            continue
        rel = reliability.get(inv.customer_id, "unknown")
        stage, action = _stage(days if is_overdue else 0)
        level, reasons = _priority(days, inv.balance_due, total_ar, rel) if is_overdue else ("normal", [])
        item = OverdueItem(
            invoice_id=inv.invoice_id, invoice_number=inv.invoice_number, customer_id=inv.customer_id,
            customer_name=data.counterparty_name(inv.customer_id), issue_date=inv.issue_date, due_date=inv.due_date,
            total=money(inv.total), amount_paid=money(inv.amount_paid), balance=money(inv.balance_due),
            days_overdue=days, bucket=aging_bucket(days), partially_paid=inv.amount_paid > 0,
            stage=stage, recommended_action=action, priority=level, priority_reasons=reasons,
            customer_reliability=rel,
        )
        (overdue if is_overdue else due_soon).append(item)
    overdue.sort(key=lambda i: (PRIORITY_ORDER[i.priority], -i.days_overdue, -i.balance, i.invoice_id))
    due_soon.sort(key=lambda i: (i.due_date, i.invoice_id))

    corrections = []
    for inv in data.invoices:
        if inv.issue_date > as_of:
            continue
        derived = derive_invoice_status(inv, as_of, grace_days)
        if derived != inv.status:
            if derived == InvoiceStatus.overdue:
                reason = f"due {inv.due_date.isoformat()} and {money(inv.balance_due)} still outstanding"
            elif derived == InvoiceStatus.paid:
                reason = "fully paid"
            elif derived == InvoiceStatus.partially_paid:
                reason = f"{money(inv.amount_paid)} received, not yet due"
            else:
                reason = "not yet due and nothing received"
            corrections.append(StatusCorrection(invoice_id=inv.invoice_id, invoice_number=inv.invoice_number,
                                                recorded_status=inv.status.value, derived_status=derived.value,
                                                reason=reason))

    bills = []
    for e in data.expenses:
        if e.is_paid or e.due_date is None or e.due_date >= as_of or e.currency != data.base_currency:
            continue
        bills.append(OverdueBill(expense_id=e.expense_id, vendor_id=e.vendor_id,
                                 vendor_name=data.counterparty_name(e.vendor_id), description=e.description,
                                 category=e.category.value, amount=money(e.amount), due_date=e.due_date,
                                 days_overdue=(as_of - e.due_date).days))
    bills.sort(key=lambda b: (-b.days_overdue, b.expense_id))

    return OverdueReport(
        business_id=data.business_id, currency=data.base_currency,
        period=Period(label="as_of", start=as_of, end=as_of), warnings=warnings, notes=notes,
        as_of=as_of, grace_days=grace_days, overdue=overdue, due_soon=due_soon,
        status_corrections=corrections, overdue_bills=bills,
        total_overdue=money(sum((i.balance for i in overdue), ZERO)),
        total_due_soon=money(sum((i.balance for i in due_soon), ZERO)),
        total_bills_overdue=money(sum((b.amount for b in bills), ZERO)),
    )
