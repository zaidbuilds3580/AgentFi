"""
Deterministic cash-flow forecast (method: run_rate_plus_receivables).

    projected_balance = current_balance
                      + expected_collections       (open invoices, weighted by how late they are)
                      + other_inflow_rate  × horizon_days
                      − outflow_rate       × horizon_days

* current_balance: closing balance of base-currency accounts at `as_of`.
* Run rates come from the LOOKBACK window (default 90 days ending at `as_of`), divided by the
  number of days that window actually has bank data for, so a short history isn't diluted.
  Invoice-linked inflows are left out of the run rate because they're forecast from receivables
  instead (no double counting).
* Expected collections: open invoice balances due on/before as_of + horizon, weighted by
  lateness on the as_of date. Not yet due or 1-30 days late: 100%. 31-60: 50%. 61-90: 25%. Over 90: 0%.
* risk_level:
    high    projected_balance < 0, or the cash runway (current balance / net daily burn) is shorter than the horizon
    medium  projected_balance < 30 days of outflows, or data_coverage < 0.5
    low     otherwise
* data_coverage: share of the lookback window covered by bank data (0-1).

No randomness, no clock: the same data + as_of + horizon always gives the same forecast.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import List, Optional

from pydantic import Field

from finance_engine.calculations import (
    BILLED_STATUSES,
    EngineResult,
    ZERO,
    _base_accounts,
    _txns,
    balance_on,
    money,
    share,
)
from finance_engine.data import FinanceData
from finance_engine.periods import Period
from finance_engine.schema.canonical import TransactionDirection

METHOD = "run_rate_plus_receivables"
DEFAULT_LOOKBACK_DAYS = 90
COLLECTION_WEIGHTS = ((30, Decimal("1")), (60, Decimal("0.5")), (90, Decimal("0.25")))  # days late -> weight
RATE_PLACES = Decimal("0.0001")


class CashFlowForecastReport(EngineResult):
    as_of: date
    horizon_days: int
    method: str = METHOD
    current_balance: Decimal
    expected_collections: Decimal
    expected_other_inflows: Decimal
    expected_outflows: Decimal
    projected_balance: Decimal
    daily_outflow_rate: Decimal
    daily_other_inflow_rate: Decimal
    runway_days: Optional[int] = Field(description="Days until cash runs out at the current net burn; None if not burning.")
    risk_level: str
    data_coverage: Decimal
    lookback_start: date
    lookback_end: date
    collection_refs: List[str] = Field(default_factory=list, description="Invoice IDs counted as expected collections.")


def _collection_weight(days_late: int) -> Decimal:
    if days_late <= 0:
        return Decimal("1")
    for limit, weight in COLLECTION_WEIGHTS:
        if days_late <= limit:
            return weight
    return Decimal("0")


def forecast_cash_flow(data: FinanceData, as_of: date, horizon_days: int = 30,
                       lookback_days: int = DEFAULT_LOOKBACK_DAYS) -> CashFlowForecastReport:
    if horizon_days < 1:
        raise ValueError("horizon_days must be >= 1.")
    if lookback_days < 1:
        raise ValueError("lookback_days must be >= 1.")
    warnings: List[str] = []
    notes: List[str] = []
    accounts = _base_accounts(data, warnings)
    horizon_end = as_of + timedelta(days=horizon_days)

    current = sum((balance_on(data, a, as_of) for a in accounts), ZERO)

    # ---- run rates from the lookback window ------------------------------------------------ #
    lb = Period(label="lookback", start=as_of - timedelta(days=lookback_days - 1), end=as_of)
    txns = _txns(data, accounts, lb)
    all_dates = [t.transaction_date for t in _txns(data, accounts) if t.transaction_date <= as_of]
    if all_dates:
        covered_from = max(lb.start, min(all_dates))
        covered_to = min(lb.end, max(all_dates))
        covered_days = max((covered_to - covered_from).days + 1, 0)
    else:
        covered_days = 0
    coverage = share(Decimal(covered_days), Decimal(lb.days))
    if covered_days == 0:
        warnings.append("No bank history before the forecast date; run rates are zero.")
        out_rate = other_in_rate = ZERO
    else:
        outflows = sum((t.amount for t in txns if t.direction == TransactionDirection.outflow), ZERO)
        other_in = sum((t.amount for t in txns if t.direction == TransactionDirection.inflow and not t.invoice_id), ZERO)
        out_rate = (outflows / covered_days).quantize(RATE_PLACES)
        other_in_rate = (other_in / covered_days).quantize(RATE_PLACES)
        if covered_days < lookback_days:
            notes.append(f"Run rates are based on {covered_days} day(s) of bank data "
                         f"(lookback {lookback_days} days).")
        if covered_days < 60:
            warnings.append(f"Only {covered_days} day(s) of bank history: monthly costs such as payroll "
                            f"and rent may be over- or under-represented.")

    # ---- expected collections from open invoices ------------------------------------------- #
    collections = ZERO
    refs: List[str] = []
    for inv in data.invoices:
        if inv.status not in BILLED_STATUSES or inv.balance_due <= 0 or inv.issue_date > as_of:
            continue
        if inv.due_date > horizon_end:
            continue
        if inv.currency != data.base_currency:
            warnings.append(f"Invoice {inv.invoice_number} in {inv.currency} not included in collections (no FX).")
            continue
        weight = _collection_weight((as_of - inv.due_date).days)
        if weight > 0:
            collections += inv.balance_due * weight
            refs.append(inv.invoice_id)
    notes.append("Collections weighted by lateness: not yet due or 1-30 days late 100%, "
                 "31-60 days 50%, 61-90 days 25%, over 90 days 0%.")
    notes.append("Unpaid bills are assumed to be covered by the outflow run rate.")

    exp_out = out_rate * horizon_days
    exp_other_in = other_in_rate * horizon_days
    projected = current + collections + exp_other_in - exp_out

    # ---- risk ------------------------------------------------------------------------------ #
    net_burn = out_rate - other_in_rate
    runway = int(current / net_burn) if net_burn > 0 and current > 0 else (0 if net_burn > 0 else None)
    if projected < 0 or (runway is not None and runway < horizon_days):
        risk = "high"
    elif projected < out_rate * 30 or coverage < Decimal("0.5"):
        risk = "medium"
    else:
        risk = "low"

    return CashFlowForecastReport(
        business_id=data.business_id, currency=data.base_currency,
        period=Period(label=f"next_{horizon_days}_days", start=as_of + timedelta(days=1), end=horizon_end),
        warnings=warnings, notes=notes, as_of=as_of, horizon_days=horizon_days,
        current_balance=money(current), expected_collections=money(collections),
        expected_other_inflows=money(exp_other_in), expected_outflows=money(exp_out),
        projected_balance=money(projected), daily_outflow_rate=money(out_rate),
        daily_other_inflow_rate=money(other_in_rate), runway_days=runway, risk_level=risk,
        data_coverage=coverage, lookback_start=lb.start, lookback_end=lb.end, collection_refs=sorted(refs),
    )
