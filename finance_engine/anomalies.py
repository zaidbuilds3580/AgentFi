"""
Deterministic expense anomaly detection.

Rules (all on bank OUTFLOWS dated in [start, end], compared with history BEFORE each payment):

  A1 unusual_payment   A payment is >= 2x the median of earlier payments to the same payee
                       (linked vendor, else same category), with >= 2 earlier payments in the last
                       180 days. Severity by ratio: >= 5x high, >= 3x medium, >= 2x low.
  A2 possible_duplicate  Same amount and same narration (digits ignored) within 3 days.   -> high
  A3 category_spike    The window's spend in a category is >= 1.5x its average over the 3 previous
                       full months (needs a window of >= 28 days and >= 2 months of history).
                       Severity: >= 3x high, >= 2x medium, >= 1.5x low. Ignores rises under 1,000.
  A4 bill_mismatch     A payment linked to a bill doesn't match the bill amount.                  -> medium
  A5 uncategorized     An outflow has no category, so it can't be checked against a baseline.    -> low

`expected_baseline` is the value the rule compared against (median payment, average monthly
spend, bill amount). It's 0 for A5. Results are ordered by severity, then amount.
Anomaly IDs are hashes of the rule and evidence, so the same data gives the same IDs.
"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from statistics import median
from typing import Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from finance_engine.calculations import EngineResult, ZERO, _base_accounts, _label, _txns, money
from finance_engine.data import FinanceData
from finance_engine.etl.cleaning import description_key
from finance_engine.periods import Period, add_months, month_end
from finance_engine.schema.canonical import Transaction, TransactionDirection

PAYMENT_LOOKBACK_DAYS = 180
MIN_PRIOR_PAYMENTS = 2
DUPLICATE_WINDOW_DAYS = 3
SPIKE_MIN_INCREASE = Decimal("1000")
SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


class Anomaly(BaseModel):
    anomaly_id: str
    rule_id: str
    category: str
    vendor: str
    amount: Decimal
    expected_baseline: Decimal
    severity: str
    rule: str
    evidence_refs: List[str] = Field(default_factory=list)


class ExpenseAnomaliesReport(EngineResult):
    anomalies: List[Anomaly]
    outflows_checked: int


def _anomaly_id(rule_id: str, refs: List[str]) -> str:
    return "ANOM-" + hashlib.sha1(f"{rule_id}|{'|'.join(sorted(refs))}".encode()).hexdigest()[:10].upper()


def _narration_key(t: Transaction) -> str:
    return re.sub(r"\d", "", description_key(t.description))


def _ratio_severity(ratio: Decimal, high: Decimal, medium: Decimal, low: Decimal) -> Optional[str]:
    if ratio >= high:
        return "high"
    if ratio >= medium:
        return "medium"
    if ratio >= low:
        return "low"
    return None


def detect_expense_anomalies(data: FinanceData, start: date, end: date) -> ExpenseAnomaliesReport:
    period = Period(label="custom", start=start, end=end)
    warnings: List[str] = []
    notes: List[str] = []
    accounts = _base_accounts(data, warnings)
    all_out = [t for t in _txns(data, accounts) if t.direction == TransactionDirection.outflow]
    window = [t for t in all_out if period.contains(t.transaction_date)]

    vendor_of: Dict[str, str] = {}        # transaction_id -> vendor_id (from linked expenses)
    bill_of: Dict[str, list] = defaultdict(list)
    for e in data.expenses:
        if e.transaction_id:
            bill_of[e.transaction_id].append(e)
            if e.vendor_id:
                vendor_of[e.transaction_id] = e.vendor_id

    def payee(t: Transaction) -> Tuple[str, str]:
        vid = vendor_of.get(t.transaction_id) or t.counterparty_id
        if vid:
            return f"vendor:{vid}", data.counterparty_name(vid)
        return f"category:{t.category.value if t.category else 'none'}", t.description[:60] or "Unknown"

    found: List[Anomaly] = []

    def add(rule_id, t_or_cat, vendor, amount, baseline, severity, rule, refs):
        category = _label(t_or_cat.category) if isinstance(t_or_cat, Transaction) else t_or_cat
        found.append(Anomaly(anomaly_id=_anomaly_id(rule_id, refs), rule_id=rule_id, category=category,
                             vendor=vendor, amount=money(amount), expected_baseline=money(baseline),
                             severity=severity, rule=rule, evidence_refs=sorted(refs)))

    # A1 unusual payment size
    for t in window:
        key, name = payee(t)
        if key == "category:none":
            continue
        prior = [p.amount for p in all_out
                 if payee(p)[0] == key and p.transaction_id != t.transaction_id
                 and t.transaction_date - timedelta(days=PAYMENT_LOOKBACK_DAYS) <= p.transaction_date < t.transaction_date]
        if len(prior) < MIN_PRIOR_PAYMENTS:
            continue
        base = Decimal(median(prior))
        if base <= 0:
            continue
        ratio = t.amount / base
        sev = _ratio_severity(ratio, Decimal(5), Decimal(3), Decimal(2))
        if sev:
            add("A1", t, name, t.amount, base, sev,
                f"Payment is {ratio:.1f}x the median of {len(prior)} earlier payments to this payee", [t.transaction_id])

    # A2 possible duplicates
    seen_dups = set()
    for i, a in enumerate(window):
        for b in window[i + 1:]:
            if (a.amount == b.amount and abs((b.transaction_date - a.transaction_date).days) <= DUPLICATE_WINDOW_DAYS
                    and _narration_key(a) == _narration_key(b) and a.account_id == b.account_id):
                pair = tuple(sorted((a.transaction_id, b.transaction_id)))
                if pair in seen_dups:
                    continue
                seen_dups.add(pair)
                add("A2", a, payee(a)[1], a.amount + b.amount, a.amount, "high",
                    f"Same amount and narration paid twice within {DUPLICATE_WINDOW_DAYS} days (possible duplicate)",
                    list(pair))

    # A3 category spike vs previous 3 full months
    if period.days >= 28:
        history_start = add_months(period.start.replace(day=1), -3)
        history_end = period.start.replace(day=1) - timedelta(days=1)
        hist_dates = [t.transaction_date for t in all_out if history_start <= t.transaction_date <= history_end]
        months_with_data = len({(d.year, d.month) for d in hist_dates})
        if months_with_data >= 2:
            cur: Dict[str, Decimal] = defaultdict(lambda: ZERO)
            refs: Dict[str, List[str]] = defaultdict(list)
            for t in window:
                if t.category:
                    cur[t.category.value] += t.amount
                    refs[t.category.value].append(t.transaction_id)
            for cat, amount in sorted(cur.items()):
                hist = sum((t.amount for t in all_out if t.category and t.category.value == cat
                            and history_start <= t.transaction_date <= history_end), ZERO)
                monthly_avg = hist / months_with_data
                expected = monthly_avg * Decimal(period.days) / Decimal(30)
                if expected <= 0 or amount - expected < SPIKE_MIN_INCREASE:
                    continue
                sev = _ratio_severity(amount / expected, Decimal(3), Decimal(2), Decimal("1.5"))
                if sev:
                    vendor = "Various" if len(refs[cat]) > 1 else payee(next(t for t in window if t.transaction_id == refs[cat][0]))[1]
                    add("A3", cat.replace("_", " ").title(), vendor, amount, expected, sev,
                        f"Category spend is {amount / expected:.1f}x its average of the previous {months_with_data} months",
                        refs[cat])
        else:
            notes.append("Category spike check skipped: needs at least 2 previous months of bank data.")
    else:
        notes.append("Category spike check skipped: window shorter than 28 days.")

    # A4 payment does not match the linked bill
    for t in window:
        bills = bill_of.get(t.transaction_id)
        if bills:
            billed = sum((e.amount for e in bills), ZERO)
            if billed != t.amount:
                add("A4", t, payee(t)[1], t.amount, billed, "medium",
                    "Bank payment differs from the linked bill amount", [t.transaction_id] + [e.expense_id for e in bills])

    # A5 uncategorized
    for t in window:
        if t.category is None:
            add("A5", t, payee(t)[1], t.amount, ZERO, "low",
                "Outflow has no expense category, so it cannot be checked against a baseline", [t.transaction_id])

    found.sort(key=lambda a: (SEVERITY_ORDER[a.severity], -a.amount, a.anomaly_id))
    return ExpenseAnomaliesReport(
        business_id=data.business_id, currency=data.base_currency, period=period, warnings=warnings,
        notes=notes, anomalies=found, outflows_checked=len(window),
    )
