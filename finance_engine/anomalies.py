"""
Deterministic expense anomaly detection.

Every rule looks at bank OUTFLOWS dated in [start, end] and compares them with history BEFORE
each payment. Payments are grouped by *payee*: the linked vendor (from the expense register)
when known, otherwise the bank narration with digits and month names removed
("IMPS-6275...-ORANGE REALTY LLP-OFFICE RENT OCT" and "...NOV" -> the same payee).

  A1 unusual_payment     Payment is unusually large for this payee, compared with earlier payments in
                         the last 180 days (>= 2 of them): either >= 2x their median, or (with >= 4
                         earlier payments) a robust z-score >= 3.5 (median/MAD) and >= 1.5x the median.
                         Severity by ratio to median: >= 5x high, >= 3x medium, otherwise low.
  A2 possible_duplicate  Same amount and same payee/narration within 3 days.                   -> high
  A3 category_spike      For each COMPLETE calendar month in the window: category spend >= 1.5x its
                         monthly average over the 3 previous months (needs >= 2 months of history and
                         a rise >= 1,000). >= 3x high, >= 2x medium, else low. Skipped when A1/A6
                         already explain every payment in that category.
  A4 bill_mismatch       Payment linked to a bill doesn't match the bill amount.                -> medium
  A5 uncategorized       Outflow has no category, so it can't be checked against a baseline.   -> low
  A6 recurring_change    A recurring payment (>= 3 earlier payments, roughly monthly, amounts within
                         10% of each other) changed by >= 15% from its usual amount (if A1 didn't fire).
                         >= 30% medium, else low.
  A7 missing_recurring   A recurring payment expected in the window (last payment + usual interval,
                         +/- 7 days) never happened, and the bank data covers that date.        -> low
  A8 new_payee_large     First payment to a payee not seen in the previous 180 days, while >= 60 days
                         of history exist, and the amount is >= 5,000 and >= 2x the median outflow.  -> medium

`amount` is what was paid (0 for A7), and `expected_baseline` is what the rule compared it with.
Results are sorted by severity, then amount. Anomaly IDs are hashes of the rule and evidence,
so the same data always gives the same IDs.
"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from statistics import mean, median, pstdev
from typing import Dict, List, Optional, Set, Tuple

from pydantic import BaseModel, Field

from finance_engine.calculations import EngineResult, ZERO, _base_accounts, _label, _txns, money
from finance_engine.data import FinanceData
from finance_engine.etl.cleaning import clean_text
from finance_engine.periods import Period, add_months
from finance_engine.schema.canonical import Transaction, TransactionDirection

PAYMENT_LOOKBACK_DAYS = 180
MIN_PRIOR_PAYMENTS = 2
ROBUST_MIN_PRIOR = 4
ROBUST_Z = Decimal("3.5")
DUPLICATE_WINDOW_DAYS = 3
SPIKE_MIN_INCREASE = Decimal("1000")
RECURRING_MIN_PAYMENTS = 3
RECURRING_MAX_CV = 0.10            # amounts within ~10% of each other
RECURRING_GAP_DAYS = (25, 35)      # roughly monthly
RECURRING_MAX_GAP_SPREAD = 4       # days between the shortest and longest interval
RECURRING_CHANGE = Decimal("0.15")
MISSING_TOLERANCE_DAYS = 7
NEW_PAYEE_MIN_HISTORY_DAYS = 60
NEW_PAYEE_MIN_AMOUNT = Decimal("5000")
SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}

_MONTHS = re.compile(r"\b(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|"
                     r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b")


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


def narration_payee_key(description: str) -> str:
    """Bank narration without reference numbers and month names: a stable key for the same payee."""
    text = clean_text(description).lower()
    text = re.sub(r"\d+", " ", text)
    text = _MONTHS.sub(" ", text)
    return re.sub(r"[^a-z]", "", text)


def _anomaly_id(rule_id: str, refs: List[str]) -> str:
    return "ANOM-" + hashlib.sha1(f"{rule_id}|{'|'.join(sorted(refs))}".encode()).hexdigest()[:10].upper()


def _ratio_severity(ratio: Decimal, high: Decimal, medium: Decimal, low: Decimal) -> Optional[str]:
    if ratio >= high:
        return "high"
    if ratio >= medium:
        return "medium"
    if ratio >= low:
        return "low"
    return None


def robust_z(value: Decimal, history: List[Decimal]) -> Optional[Decimal]:
    """Modified z-score (Iglewicz & Hoaglin): 0.6745 * (x - median) / MAD. None if MAD is 0."""
    med = Decimal(median(history))
    mad = Decimal(median([abs(h - med) for h in history]))
    if mad == 0:
        return None
    return (Decimal("0.6745") * (value - med) / mad).quantize(Decimal("0.01"))


class _Payees:
    """Resolves the payee of each outflow and keeps per-payee payment history."""

    def __init__(self, data: FinanceData, outflows: List[Transaction]):
        self.data = data
        vendor_of: Dict[str, str] = {}
        for e in data.expenses:
            if e.transaction_id and e.vendor_id:
                vendor_of[e.transaction_id] = e.vendor_id
        # A narration that was linked to a vendor once identifies that vendor everywhere
        # (older bank lines are often not linked to a bill).
        narr = {t.transaction_id: narration_payee_key(t.description) for t in outflows}
        vendor_by_narr: Dict[str, Set[str]] = defaultdict(set)
        for t in outflows:
            vid = vendor_of.get(t.transaction_id) or t.counterparty_id
            if vid and narr[t.transaction_id]:
                vendor_by_narr[narr[t.transaction_id]].add(vid)
        self._key: Dict[str, Tuple[str, str]] = {}
        for t in outflows:
            nk = narr[t.transaction_id]
            inferred = vendor_by_narr.get(nk, set())
            vid = (vendor_of.get(t.transaction_id) or t.counterparty_id
                   or (next(iter(inferred)) if len(inferred) == 1 else None))
            if vid:
                self._key[t.transaction_id] = (f"vendor:{vid}", data.counterparty_name(vid))
            else:
                if nk:
                    self._key[t.transaction_id] = (f"payee:{nk}", t.description[:60])
                else:
                    cat = t.category.value if t.category else "none"
                    self._key[t.transaction_id] = (f"category:{cat}", _label(t.category))
        self.history: Dict[str, List[Transaction]] = defaultdict(list)
        for t in sorted(outflows, key=lambda t: (t.transaction_date, t.transaction_id)):
            self.history[self.key(t)].append(t)

    def key(self, t: Transaction) -> str:
        return self._key[t.transaction_id][0]

    def name(self, t: Transaction) -> str:
        return self._key[t.transaction_id][1]

    def prior(self, t: Transaction, days: int = PAYMENT_LOOKBACK_DAYS) -> List[Transaction]:
        lo = t.transaction_date - timedelta(days=days)
        return [p for p in self.history[self.key(t)]
                if lo <= p.transaction_date < t.transaction_date and p.transaction_id != t.transaction_id]


def _recurring_profile(payments: List[Transaction]) -> Optional[Tuple[int, Decimal]]:
    """(usual interval in days, usual amount) if payments look like a fixed monthly bill, else None."""
    if len(payments) < RECURRING_MIN_PAYMENTS:
        return None
    amounts = [float(p.amount) for p in payments]
    if mean(amounts) <= 0 or pstdev(amounts) / mean(amounts) > RECURRING_MAX_CV:
        return None
    gaps = [(b.transaction_date - a.transaction_date).days for a, b in zip(payments, payments[1:])]
    if not all(RECURRING_GAP_DAYS[0] <= g <= RECURRING_GAP_DAYS[1] for g in gaps):
        return None
    if max(gaps) - min(gaps) > RECURRING_MAX_GAP_SPREAD:
        return None
    return int(median(gaps)), Decimal(median([p.amount for p in payments]))


def detect_expense_anomalies(data: FinanceData, start: date, end: date) -> ExpenseAnomaliesReport:
    if end < start:
        raise ValueError("end must not be before start.")
    period = Period(label="custom", start=start, end=end)
    warnings: List[str] = []
    notes: List[str] = []
    accounts = _base_accounts(data, warnings)
    all_out = sorted((t for t in _txns(data, accounts) if t.direction == TransactionDirection.outflow),
                     key=lambda t: (t.transaction_date, t.transaction_id))
    window = [t for t in all_out if period.contains(t.transaction_date)]
    payees = _Payees(data, all_out)
    data_range = data.bank_data_range(accounts)
    data_through = data_range[1] if data_range else None
    history_days = (start - data_range[0]).days if data_range else 0

    found: List[Anomaly] = []
    flagged: Dict[str, Set[str]] = defaultdict(set)  # rule -> transaction ids

    def add(rule_id, category, vendor, amount, baseline, severity, rule, refs):
        found.append(Anomaly(anomaly_id=_anomaly_id(rule_id, refs), rule_id=rule_id, category=category,
                             vendor=vendor, amount=money(amount), expected_baseline=money(baseline),
                             severity=severity, rule=rule, evidence_refs=sorted(refs)))
        flagged[rule_id].update(refs)

    # A1 unusual payment size -------------------------------------------------------------- #
    for t in window:
        if payees.key(t) == "category:none":
            continue
        prior = [p.amount for p in payees.prior(t)]
        if len(prior) < MIN_PRIOR_PAYMENTS:
            continue
        base = Decimal(median(prior))
        if base <= 0:
            continue
        ratio = t.amount / base
        z = robust_z(t.amount, prior) if len(prior) >= ROBUST_MIN_PRIOR else None
        robust_hit = z is not None and z >= ROBUST_Z and ratio >= Decimal("1.5")
        if ratio >= 2 or robust_hit:
            sev = _ratio_severity(ratio, Decimal(5), Decimal(3), Decimal(0))
            why = f"Payment is {ratio:.1f}x the median of {len(prior)} earlier payments to this payee"
            if z is not None:
                why += f" (robust z-score {z})"
            add("A1", _label(t.category), payees.name(t), t.amount, base, sev, why, [t.transaction_id])

    # A2 possible duplicates --------------------------------------------------------------- #
    seen_pairs = set()
    for i, a in enumerate(window):
        for b in window[i + 1:]:
            if (a.amount == b.amount and a.account_id == b.account_id
                    and abs((b.transaction_date - a.transaction_date).days) <= DUPLICATE_WINDOW_DAYS
                    and payees.key(a) == payees.key(b)):
                pair = tuple(sorted((a.transaction_id, b.transaction_id)))
                if pair not in seen_pairs:
                    seen_pairs.add(pair)
                    add("A2", _label(a.category), payees.name(a), a.amount + b.amount, a.amount, "high",
                        f"Same amount paid to the same payee twice within {DUPLICATE_WINDOW_DAYS} days "
                        f"(possible duplicate payment)", list(pair))

    # A4 bill mismatch ------------------------------------------------------------------- #
    bills: Dict[str, list] = defaultdict(list)
    for e in data.expenses:
        if e.transaction_id:
            bills[e.transaction_id].append(e)
    for t in window:
        if t.transaction_id in bills:
            billed = sum((e.amount for e in bills[t.transaction_id]), ZERO)
            if billed != t.amount:
                add("A4", _label(t.category), payees.name(t), t.amount, billed, "medium",
                    "Bank payment differs from the linked bill amount",
                    [t.transaction_id] + [e.expense_id for e in bills[t.transaction_id]])

    # A5 uncategorized -------------------------------------------------------------------- #
    for t in window:
        if t.category is None:
            add("A5", _label(None), payees.name(t), t.amount, ZERO, "low",
                "Outflow has no expense category, so it cannot be checked against a baseline", [t.transaction_id])

    # A6 recurring payment changed --------------------------------------------------------- #
    for t in window:
        if t.transaction_id in flagged["A1"]:
            continue
        profile = _recurring_profile(payees.prior(t))
        if profile is None:
            continue
        _, usual = profile
        change = (t.amount - usual) / usual
        if abs(change) >= RECURRING_CHANGE:
            sev = "medium" if abs(change) >= Decimal("0.30") else "low"
            direction = "higher" if change > 0 else "lower"
            add("A6", _label(t.category), payees.name(t), t.amount, usual, sev,
                f"Recurring payment is {abs(change) * 100:.0f}% {direction} than its usual amount", [t.transaction_id])

    # A7 missing recurring payment --------------------------------------------------------- #
    if data_through is not None:
        horizon = min(end, data_through)
        for key, payments in sorted(payees.history.items()):
            before = [p for p in payments if p.transaction_date < start]
            profile = _recurring_profile(before[-6:])
            if profile is None:
                continue
            gap, usual = profile
            last = before[-1].transaction_date
            expected = last + timedelta(days=gap)
            while expected <= end:
                lo, hi = expected - timedelta(days=MISSING_TOLERANCE_DAYS), expected + timedelta(days=MISSING_TOLERANCE_DAYS)
                match = next((p for p in payments if lo <= p.transaction_date <= hi and p.transaction_date > last), None)
                if match is not None:
                    last = match.transaction_date
                elif expected < start:
                    last = expected  # missed before the window: reported for that earlier window, not this one
                elif hi <= horizon:
                    ref = before[-1]
                    add("A7", _label(ref.category), payees.name(ref), ZERO, usual, "low",
                        f"Recurring payment expected around {expected.isoformat()} (every ~{gap} days) "
                        f"was not found", [ref.transaction_id, f"expected:{expected.isoformat()}"])
                    last = expected
                else:
                    break  # bank data doesn't cover this date yet
                expected = last + timedelta(days=gap)

    # A8 large first payment to a new payee -------------------------------------------------- #
    if history_days >= NEW_PAYEE_MIN_HISTORY_DAYS:
        hist_amounts = [t.amount for t in all_out if t.transaction_date < start]
        typical = Decimal(median(hist_amounts)) if hist_amounts else ZERO
        threshold = max(NEW_PAYEE_MIN_AMOUNT, typical * 2)
        for t in window:
            if payees.key(t).startswith("category:") or payees.prior(t):
                continue
            if t.amount >= threshold:
                add("A8", _label(t.category), payees.name(t), t.amount, typical, "medium",
                    f"First payment to this payee in {PAYMENT_LOOKBACK_DAYS} days and "
                    f"{t.amount / typical if typical else 0:.1f}x the typical outflow", [t.transaction_id])
    else:
        notes.append(f"New-payee check skipped: needs {NEW_PAYEE_MIN_HISTORY_DAYS} days of bank history.")

    # A3 category spike: each FULL calendar month in the window vs its previous 3 months ------ #
    # (whole months only: monthly bills make partial-month comparisons meaningless)
    months = []
    m = period.start.replace(day=1)
    while m <= period.end:
        m_end = add_months(m, 1) - timedelta(days=1)
        if period.start <= m and m_end <= period.end and (data_through is None or m_end <= data_through):
            months.append((m, m_end))
        m = add_months(m, 1)
    if not months:
        notes.append("Category spike check skipped: the window contains no complete calendar month of bank data.")
    explained = flagged["A1"] | flagged["A6"]
    for m_start, m_end in months:
        history_start, history_end = add_months(m_start, -3), m_start - timedelta(days=1)
        months_with_data = len({(t.transaction_date.year, t.transaction_date.month) for t in all_out
                                if history_start <= t.transaction_date <= history_end})
        if months_with_data < 2:
            notes.append(f"Category spike check skipped for {m_start:%Y-%m}: needs 2 previous months of bank data.")
            continue
        cur: Dict[str, Decimal] = defaultdict(lambda: ZERO)
        refs: Dict[str, List[str]] = defaultdict(list)
        for t in window:
            if t.category and m_start <= t.transaction_date <= m_end:
                cur[t.category.value] += t.amount
                refs[t.category.value].append(t.transaction_id)
        for cat, amount in sorted(cur.items()):
            if set(refs[cat]) <= explained:
                continue
            hist = sum((t.amount for t in all_out if t.category and t.category.value == cat
                        and history_start <= t.transaction_date <= history_end), ZERO)
            expected = hist / months_with_data
            if expected <= 0 or amount - expected < SPIKE_MIN_INCREASE:
                continue
            sev = _ratio_severity(amount / expected, Decimal(3), Decimal(2), Decimal("1.5"))
            if sev:
                vendor = "Various" if len(refs[cat]) > 1 else payees.name(
                    next(t for t in window if t.transaction_id == refs[cat][0]))
                add("A3", cat.replace("_", " ").title(), vendor, amount, expected, sev,
                    f"{m_start:%Y-%m} spend in this category is {amount / expected:.1f}x its monthly average "
                    f"of the previous {months_with_data} months", refs[cat])

    found.sort(key=lambda a: (SEVERITY_ORDER[a.severity], -a.amount, a.anomaly_id))
    return ExpenseAnomaliesReport(
        business_id=data.business_id, currency=data.base_currency, period=period, warnings=warnings,
        notes=notes, anomalies=found, outflows_checked=len(window),
    )
