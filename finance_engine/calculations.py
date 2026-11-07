"""
Deterministic finance calculations: revenue, expenses, net cash flow, receivables.

Principles
----------
* Pure functions of (FinanceData, Period). No clock, no randomness, no LLM, no I/O.
  The same inputs always produce the same outputs, in the same order.
* Decimal arithmetic throughout; rounding (ROUND_HALF_UP to 0.01) happens once, at output.
* Only base-currency records are summed. Foreign-currency records are excluded and named
  in `warnings`. FX conversion is a separate, explicit step that doesn't exist yet.
* Every figure is traceable: breakdown rows carry `evidence_refs` (record IDs).
* Cash basis comes from bank transactions; accrual basis comes from invoices and expenses.
  The two are never mixed, so linked records (an expense and the bank line that paid it)
  are never double-counted.

Definitions: docs/finance_engine.md
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Dict, Iterable, List, Optional, Tuple

from pydantic import BaseModel, Field

from finance_engine.data import FinanceData
from finance_engine.periods import GRANULARITIES, Period, iter_buckets
from finance_engine.schema.canonical import (
    Account,
    ExpenseCategory,
    InvoiceStatus,
    Transaction,
    TransactionDirection,
)

CALCULATION_VERSION = "finance-engine-1.1.0"

ZERO = Decimal("0")
CENT = Decimal("0.01")

# Invoice statuses that count as billed revenue (draft = not issued, void = cancelled)
BILLED_STATUSES = frozenset({InvoiceStatus.sent, InvoiceStatus.partially_paid, InvoiceStatus.paid, InvoiceStatus.overdue})
# Cash outflows that are not operating expenses (tax remittances, loan principal)
NON_OPERATING_CATEGORIES = frozenset({ExpenseCategory.taxes, ExpenseCategory.loan_repayment})
UNCATEGORIZED = "uncategorized"
AGING_BUCKETS: Tuple[Tuple[str, int, Optional[int]], ...] = (
    ("current", -10**6, 0), ("1-30", 1, 30), ("31-60", 31, 60), ("61-90", 61, 90), ("90+", 91, None),
)


def money(value: Decimal) -> Decimal:
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def share(part: Decimal, total: Decimal) -> Decimal:
    """Fraction of total, 4 decimal places (0.2512 = 25.12%)."""
    if total == 0:
        return Decimal("0.0000")
    return (Decimal(part) / Decimal(total)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


# ============================================================================= #
# Result models
# ============================================================================= #

class Breakdown(BaseModel):
    """One row of a grouped total (by category, customer, vendor, aging bucket...)."""
    key: str
    label: str
    amount: Decimal
    count: int
    share: Decimal = Field(description="Fraction of the parent total (0-1).")
    evidence_refs: List[str] = Field(default_factory=list, description="IDs of the records summed.")


class EngineResult(BaseModel):
    business_id: str
    currency: str
    period: Period
    calculation_version: str = CALCULATION_VERSION
    warnings: List[str] = Field(default_factory=list, description="Data gaps that affect the figures.")
    notes: List[str] = Field(default_factory=list, description="What was excluded and why.")


class RevenueReport(EngineResult):
    # accrual basis: invoices issued in the period
    billed_net: Decimal = Field(description="Invoiced revenue excluding tax (GST is not revenue).")
    billed_tax: Decimal
    billed_gross: Decimal
    invoice_count: int
    billed_by_customer: List[Breakdown]
    # cash basis: money received in the period
    collected_from_invoices: Decimal = Field(description="Bank inflows linked to an invoice.")
    other_inflows: Decimal = Field(description="Bank inflows not linked to any invoice (interest, capital, unmatched).")
    total_inflows: Decimal
    collected_by_customer: List[Breakdown]


class ExpenseReport(EngineResult):
    # cash basis: bank outflows in the period
    paid_total: Decimal
    operating_paid: Decimal = Field(description="paid_total excluding taxes and loan repayments.")
    paid_by_category: List[Breakdown]
    uncategorized_paid: Decimal
    # accrual basis: expense records dated in the period
    incurred_total: Decimal
    incurred_unpaid: Decimal
    incurred_by_category: List[Breakdown]
    incurred_by_vendor: List[Breakdown]


class AccountCashFlow(BaseModel):
    account_id: str
    name: str
    opening_balance: Decimal
    inflows: Decimal
    outflows: Decimal
    net_cash_flow: Decimal
    closing_balance: Decimal


class CashFlowPoint(BaseModel):
    label: str
    start: date
    end: date
    inflows: Decimal
    outflows: Decimal
    net_cash_flow: Decimal
    closing_balance: Decimal


class CashFlowReport(EngineResult):
    opening_balance: Decimal
    inflows: Decimal
    outflows: Decimal
    net_cash_flow: Decimal
    closing_balance: Decimal
    by_account: List[AccountCashFlow]
    granularity: str
    series: List[CashFlowPoint]
    data_coverage: Decimal = Field(description="Share of the period's days covered by bank data (0-1).")


class ReceivablesReport(EngineResult):
    as_of: date
    total_outstanding: Decimal
    overdue_amount: Decimal
    invoice_count: int
    aging: List[Breakdown]


class FinancialSummaryReport(EngineResult):
    revenue_billed: Decimal
    revenue_collected: Decimal
    expenses_paid: Decimal
    operating_expenses_paid: Decimal
    inflows: Decimal
    outflows: Decimal
    net_cash_flow: Decimal
    opening_balance: Decimal
    cash_balance: Decimal
    receivables: Decimal
    overdue_receivables: Decimal
    data_coverage: Decimal


# ============================================================================= #
# Helpers
# ============================================================================= #

def _base_accounts(data: FinanceData, warnings: List[str]) -> List[Account]:
    accounts = []
    for a in data.accounts:
        if a.currency == data.base_currency:
            accounts.append(a)
        else:
            warnings.append(f"Account {a.account_id} is in {a.currency}; excluded (no FX conversion).")
    return accounts


def _txns(data: FinanceData, accounts: Iterable[Account], period: Optional[Period] = None) -> List[Transaction]:
    ids = {a.account_id for a in accounts}
    return [t for t in data.transactions
            if t.account_id in ids and (period is None or period.contains(t.transaction_date))]


def _signed(t: Transaction) -> Decimal:
    return t.amount if t.direction == TransactionDirection.inflow else -t.amount


def _group(items: Iterable[Tuple[str, str, Decimal, str]], total: Decimal) -> List[Breakdown]:
    """items: (key, label, amount, record_id) -> Breakdown rows sorted by amount desc, key asc."""
    acc: Dict[str, dict] = {}
    for key, label, amount, rid in items:
        row = acc.setdefault(key, {"label": label, "amount": ZERO, "refs": []})
        row["amount"] += amount
        row["refs"].append(rid)
    rows = [
        Breakdown(key=k, label=v["label"], amount=money(v["amount"]), count=len(v["refs"]),
                  share=share(v["amount"], total), evidence_refs=sorted(v["refs"]))
        for k, v in acc.items()
    ]
    return sorted(rows, key=lambda b: (-b.amount, b.key))


def _coverage(data: FinanceData, accounts: List[Account], period: Period, warnings: List[str]) -> Decimal:
    rng = data.bank_data_range(accounts)
    if rng is None:
        warnings.append("No bank transactions available; cash figures are zero.")
        return Decimal("0.0000")
    first, last = rng
    if period.end > last:
        warnings.append(f"Bank data available only through {last.isoformat()}; "
                        f"later activity in this period is missing.")
    if period.start < first:
        warnings.append(f"Bank data starts on {first.isoformat()}; earlier activity in this period is missing.")
    overlap = (min(period.end, last) - max(period.start, first)).days + 1
    return share(Decimal(max(overlap, 0)), Decimal(period.days))


def _label(category) -> str:
    return category.value.replace("_", " ").title() if category else "Uncategorized"


# ============================================================================= #
# Revenue
# ============================================================================= #

def calculate_revenue(data: FinanceData, period: Period) -> RevenueReport:
    """
    Accrual: invoices ISSUED in the period with status sent/partially_paid/paid/overdue,
             net of tax (line subtotal), tax shown separately.
    Cash:    bank inflows in the period; split into collections linked to an invoice
             and other inflows.
    """
    warnings: List[str] = []
    notes: List[str] = []
    cur = data.base_currency

    billed_net = billed_tax = ZERO
    billed_items = []
    skipped: Dict[str, List[str]] = defaultdict(list)
    for inv in data.invoices:
        if not period.contains(inv.issue_date):
            continue
        if inv.status not in BILLED_STATUSES:
            skipped[inv.status.value].append(inv.invoice_number)
            continue
        if inv.currency != cur:
            warnings.append(f"Invoice {inv.invoice_number} is in {inv.currency} "
                            f"({money(inv.total)}); excluded from revenue (no FX conversion).")
            continue
        net = sum((li.subtotal for li in inv.line_items), ZERO)
        gross = inv.total
        billed_net += net
        billed_tax += gross - net
        billed_items.append((inv.customer_id, data.counterparty_name(inv.customer_id), net, inv.invoice_id))
    for status, numbers in sorted(skipped.items()):
        notes.append(f"{len(numbers)} {status} invoice(s) not counted as revenue: {', '.join(numbers)}.")

    accounts = _base_accounts(data, warnings)
    inflows = [t for t in _txns(data, accounts, period) if t.direction == TransactionDirection.inflow]
    invoices = {i.invoice_id: i for i in data.invoices}
    collected = ZERO
    other = ZERO
    collected_items = []
    for t in inflows:
        inv = invoices.get(t.invoice_id) if t.invoice_id else None
        if inv is not None:
            collected += t.amount
            collected_items.append((inv.customer_id, data.counterparty_name(inv.customer_id), t.amount, t.transaction_id))
        else:
            other += t.amount
    if other:
        notes.append("Inflows not linked to an invoice are reported as other_inflows, not revenue.")
    _coverage(data, accounts, period, warnings)

    return RevenueReport(
        business_id=data.business_id, currency=cur, period=period, warnings=warnings, notes=notes,
        billed_net=money(billed_net), billed_tax=money(billed_tax), billed_gross=money(billed_net + billed_tax),
        invoice_count=len(billed_items), billed_by_customer=_group(billed_items, billed_net),
        collected_from_invoices=money(collected), other_inflows=money(other),
        total_inflows=money(collected + other), collected_by_customer=_group(collected_items, collected),
    )


# ============================================================================= #
# Expenses
# ============================================================================= #

def calculate_expenses(data: FinanceData, period: Period) -> ExpenseReport:
    """
    Cash:    bank outflows in the period, grouped by category.
    Accrual: expense records (bills) dated in the period, by category and vendor,
             including unpaid ones.
    """
    warnings: List[str] = []
    notes: List[str] = []
    cur = data.base_currency

    accounts = _base_accounts(data, warnings)
    outflows = [t for t in _txns(data, accounts, period) if t.direction == TransactionDirection.outflow]
    paid_total = sum((t.amount for t in outflows), ZERO)
    non_operating = sum((t.amount for t in outflows if t.category in NON_OPERATING_CATEGORIES), ZERO)
    uncategorized = [t for t in outflows if t.category is None]
    uncategorized_total = sum((t.amount for t in uncategorized), ZERO)
    if uncategorized:
        warnings.append(f"{len(uncategorized)} outflow(s) totalling {money(uncategorized_total)} are uncategorized.")
    if non_operating:
        notes.append(f"operating_paid excludes taxes and loan repayments ({money(non_operating)}).")
    paid_items = [(t.category.value if t.category else UNCATEGORIZED, _label(t.category), t.amount, t.transaction_id)
                  for t in outflows]

    incurred = []
    for e in data.expenses:
        if not period.contains(e.expense_date):
            continue
        if e.currency != cur:
            warnings.append(f"Expense {e.expense_id} is in {e.currency} ({e.amount}); excluded (no FX conversion).")
            continue
        incurred.append(e)
    incurred_total = sum((e.amount for e in incurred), ZERO)
    unpaid = sum((e.amount for e in incurred if not e.is_paid), ZERO)
    _coverage(data, accounts, period, warnings)

    return ExpenseReport(
        business_id=data.business_id, currency=cur, period=period, warnings=warnings, notes=notes,
        paid_total=money(paid_total), operating_paid=money(paid_total - non_operating),
        paid_by_category=_group(paid_items, paid_total), uncategorized_paid=money(uncategorized_total),
        incurred_total=money(incurred_total), incurred_unpaid=money(unpaid),
        incurred_by_category=_group(((e.category.value, _label(e.category), e.amount, e.expense_id) for e in incurred),
                                    incurred_total),
        incurred_by_vendor=_group(((e.vendor_id or "unknown", data.counterparty_name(e.vendor_id), e.amount, e.expense_id)
                                   for e in incurred), incurred_total),
    )


# ============================================================================= #
# Net cash flow
# ============================================================================= #

def balance_on(data: FinanceData, account: Account, as_of: date) -> Decimal:
    """
    Closing balance of an account at the end of `as_of`:
    opening_balance (balance before the first transaction) + all flows up to and including as_of.
    """
    return account.opening_balance + sum(
        (_signed(t) for t in data.transactions if t.account_id == account.account_id and t.transaction_date <= as_of),
        ZERO,
    )


def calculate_net_cash_flow(data: FinanceData, period: Period, granularity: str = "month") -> CashFlowReport:
    """
    Opening balance + inflows - outflows = closing balance, per account and in total,
    with a time series of day / week / month buckets.
    """
    if granularity not in GRANULARITIES:
        raise ValueError(f"granularity must be one of {GRANULARITIES}.")
    warnings: List[str] = []
    notes: List[str] = []
    accounts = _base_accounts(data, warnings)
    in_period = _txns(data, accounts, period)

    by_account = []
    for a in accounts:
        txns = [t for t in in_period if t.account_id == a.account_id]
        inflow = sum((t.amount for t in txns if t.direction == TransactionDirection.inflow), ZERO)
        outflow = sum((t.amount for t in txns if t.direction == TransactionDirection.outflow), ZERO)
        opening = balance_on(data, a, period.start - timedelta(days=1))
        by_account.append(AccountCashFlow(
            account_id=a.account_id, name=a.name, opening_balance=money(opening), inflows=money(inflow),
            outflows=money(outflow), net_cash_flow=money(inflow - outflow), closing_balance=money(opening + inflow - outflow),
        ))

    opening_total = sum((a.opening_balance for a in by_account), ZERO)
    series = []
    running = opening_total
    for b in iter_buckets(period, granularity):
        txns = [t for t in in_period if b.contains(t.transaction_date)]
        i = sum((t.amount for t in txns if t.direction == TransactionDirection.inflow), ZERO)
        o = sum((t.amount for t in txns if t.direction == TransactionDirection.outflow), ZERO)
        running += i - o
        series.append(CashFlowPoint(label=b.label, start=b.start, end=b.end, inflows=money(i), outflows=money(o),
                                    net_cash_flow=money(i - o), closing_balance=money(running)))

    inflows = sum((a.inflows for a in by_account), ZERO)
    outflows = sum((a.outflows for a in by_account), ZERO)
    coverage = _coverage(data, accounts, period, warnings)
    if len(accounts) > 1:
        notes.append("Transfers between the business's own accounts appear as both an inflow and an outflow.")

    return CashFlowReport(
        business_id=data.business_id, currency=data.base_currency, period=period, warnings=warnings, notes=notes,
        opening_balance=money(opening_total), inflows=money(inflows), outflows=money(outflows),
        net_cash_flow=money(inflows - outflows), closing_balance=money(opening_total + inflows - outflows),
        by_account=by_account, granularity=granularity, series=series, data_coverage=coverage,
    )


# ============================================================================= #
# Receivables
# ============================================================================= #

def calculate_receivables(data: FinanceData, as_of: date, period: Optional[Period] = None) -> ReceivablesReport:
    """
    Outstanding customer balances on invoices issued on/before `as_of`, aged by days past due.
    Uses each invoice's current amount_paid (a snapshot from the latest export).
    """
    warnings: List[str] = []
    notes = ["Balances use the latest amount_paid on each invoice (a snapshot, not historical)."]
    period = period or Period(label="as_of", start=as_of, end=as_of)
    open_items = []
    for inv in data.invoices:
        if inv.issue_date > as_of or inv.status not in BILLED_STATUSES or inv.balance_due <= 0:
            continue
        if inv.currency != data.base_currency:
            warnings.append(f"Invoice {inv.invoice_number} ({inv.currency} {money(inv.balance_due)}) "
                            f"excluded from receivables (no FX conversion).")
            continue
        open_items.append(inv)

    total = sum((i.balance_due for i in open_items), ZERO)
    items = []
    for inv in open_items:
        days = (as_of - inv.due_date).days
        key = next(k for k, lo, hi in AGING_BUCKETS if days >= lo and (hi is None or days <= hi))
        items.append((key, key, inv.balance_due, inv.invoice_id))
    grouped = {b.key: b for b in _group(items, total)}
    aging = [grouped.get(k) or Breakdown(key=k, label=k, amount=money(ZERO), count=0, share=share(ZERO, total))
             for k, _, _ in AGING_BUCKETS]
    overdue = sum((b.amount for b in aging if b.key != "current"), ZERO)

    return ReceivablesReport(
        business_id=data.business_id, currency=data.base_currency, period=period, warnings=warnings, notes=notes,
        as_of=as_of, total_outstanding=money(total), overdue_amount=money(overdue),
        invoice_count=len(open_items), aging=aging,
    )


# ============================================================================= #
# Summary
# ============================================================================= #

def financial_summary(data: FinanceData, period: Period) -> FinancialSummaryReport:
    """Headline figures for a period, as used by the Cash Flow Agent and dashboard."""
    rev = calculate_revenue(data, period)
    exp = calculate_expenses(data, period)
    cf = calculate_net_cash_flow(data, period)
    ar = calculate_receivables(data, as_of=period.end, period=period)
    warnings = list(dict.fromkeys(rev.warnings + exp.warnings + cf.warnings + ar.warnings))
    notes = list(dict.fromkeys(rev.notes + exp.notes + cf.notes + ar.notes))
    return FinancialSummaryReport(
        business_id=data.business_id, currency=data.base_currency, period=period, warnings=warnings, notes=notes,
        revenue_billed=rev.billed_net, revenue_collected=rev.collected_from_invoices,
        expenses_paid=exp.paid_total, operating_expenses_paid=exp.operating_paid,
        inflows=cf.inflows, outflows=cf.outflows, net_cash_flow=cf.net_cash_flow,
        opening_balance=cf.opening_balance, cash_balance=cf.closing_balance,
        receivables=ar.total_outstanding, overdue_receivables=ar.overdue_amount, data_coverage=cf.data_coverage,
    )


# ============================================================================= #
# Overdue invoices (Invoice Agent contract)
# ============================================================================= #

OVERDUE_BANDS: Tuple[Tuple[str, int, Optional[int]], ...] = (("1-30", 1, 30), ("31-60", 31, 60), ("61+", 61, None))


class OverdueInvoiceLine(BaseModel):
    invoice_id: str
    invoice_number: str
    customer_id: str
    customer_name: str
    issue_date: date
    due_date: date
    total: Decimal
    amount: Decimal = Field(description="Outstanding balance (total - amount_paid).")
    days_overdue: int
    status: str


class OverdueBand(BaseModel):
    band: str
    count: int
    total_amount: Decimal
    evidence_refs: List[str] = Field(default_factory=list)


class OverdueInvoicesReport(EngineResult):
    as_of: date
    count: int
    total_amount: Decimal
    ageing_bands: List[OverdueBand]
    invoices: List[OverdueInvoiceLine]


def calculate_overdue_invoices(data: FinanceData, as_of: date) -> OverdueInvoicesReport:
    """
    Billed invoices with an outstanding balance whose due date is BEFORE `as_of`
    (an invoice due today is not overdue). Bands: 1-30, 31-60, 61+ days; all three
    are always returned. Invoices are ordered most overdue first.
    """
    warnings: List[str] = []
    notes = ["Balances use the latest amount_paid on each invoice (a snapshot, not historical)."]
    lines: List[OverdueInvoiceLine] = []
    for inv in data.invoices:
        if inv.status not in BILLED_STATUSES or inv.balance_due <= 0 or inv.issue_date > as_of:
            continue
        days = (as_of - inv.due_date).days
        if days < 1:
            continue
        if inv.currency != data.base_currency:
            warnings.append(f"Invoice {inv.invoice_number} ({inv.currency} {money(inv.balance_due)}, "
                            f"{days} days overdue) excluded (no FX conversion).")
            continue
        lines.append(OverdueInvoiceLine(
            invoice_id=inv.invoice_id, invoice_number=inv.invoice_number, customer_id=inv.customer_id,
            customer_name=data.counterparty_name(inv.customer_id), issue_date=inv.issue_date,
            due_date=inv.due_date, total=money(inv.total), amount=money(inv.balance_due),
            days_overdue=days, status=inv.status.value,
        ))
    lines.sort(key=lambda l: (-l.days_overdue, l.invoice_id))
    bands = []
    for name, lo, hi in OVERDUE_BANDS:
        members = [l for l in lines if l.days_overdue >= lo and (hi is None or l.days_overdue <= hi)]
        bands.append(OverdueBand(band=name, count=len(members),
                                 total_amount=money(sum((l.amount for l in members), ZERO)),
                                 evidence_refs=sorted(l.invoice_id for l in members)))
    return OverdueInvoicesReport(
        business_id=data.business_id, currency=data.base_currency,
        period=Period(label="as_of", start=as_of, end=as_of), warnings=warnings, notes=notes,
        as_of=as_of, count=len(lines), total_amount=money(sum((l.amount for l in lines), ZERO)),
        ageing_bands=bands, invoices=lines,
    )
