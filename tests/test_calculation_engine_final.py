"""
Final unit tests for the deterministic calculation engine (Member 3).

Covers the gaps left by test_finance_engine.py, test_receivables_anomalies.py and
test_agent_tools.py:

* period helpers: month arithmetic, leap years, fiscal years other than April, bucket partitions
* calculations: rounding once at output, breakdown ordering/shares, balance_on, series identities,
  receivables aging boundaries
* forecast: collection-weight edges, horizon boundary, run-rate rules, runway, lookback
* engine-wide guarantees: input-order independence on the real sample, no clock / randomness in the
  engine source, money always exactly 2 decimal places, tenant isolation for every engine
"""

import inspect
import random
import re
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from finance_engine import anomalies, calculations, forecasting, periods, receivables
from finance_engine.anomalies import detect_expense_anomalies
from finance_engine.calculations import (
    balance_on,
    calculate_expenses,
    calculate_net_cash_flow,
    calculate_overdue_invoices,
    calculate_receivables,
    calculate_revenue,
    financial_summary,
    money,
    share,
)
from finance_engine.data import FinanceData
from finance_engine.etl import run_business
from finance_engine.forecasting import _collection_weight, forecast_cash_flow
from finance_engine.periods import (
    Period,
    PeriodError,
    add_months,
    buckets,
    fiscal_quarter_start,
    fiscal_year_start,
    month_end,
    month_start,
    resolve_period,
)
from finance_engine.receivables import aging_report, detect_overdue, payment_behaviour
from finance_engine.schema import (
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

SAMPLES = Path(__file__).resolve().parents[1] / "data" / "samples" / "biz_001"
TODAY = date(2026, 11, 5)
D = Decimal


def P(start, end, label="custom"):
    return Period(label=label, start=date.fromisoformat(start), end=date.fromisoformat(end))


# ------------------------------------------------------------------------------ #
# builders
# ------------------------------------------------------------------------------ #

def _txn(tid, day, amount, direction="outflow", category=None, invoice_id=None, account="acc", biz="b", desc=""):
    return Transaction(business_id=biz, transaction_id=tid, account_id=account, transaction_date=day,
                       amount=amount, direction=direction, category=category, invoice_id=invoice_id,
                       description=desc)


def _inv(iid, due, price, issue=date(2026, 6, 1), status="sent", paid="0", tax="0", qty="1",
         currency="INR", customer="cust-a", biz="b"):
    return Invoice(business_id=biz, invoice_id=iid, invoice_number=iid.upper(), customer_id=customer,
                   issue_date=issue, due_date=due, status=status, amount_paid=paid, currency=currency,
                   line_items=[InvoiceLineItem(description="work", quantity=qty, unit_price=price, tax_rate=tax)])


def _build(transactions=(), invoices=(), expenses=(), opening="0", accounts=None, fy=4, extra=()):
    """One business 'b' (plus any other-tenant rows passed in `extra`)."""
    rows = {
        "businesses": [Business(business_id="b", name="B", fiscal_year_start_month=fy),
                       Business(business_id="other", name="Other")],
        "accounts": accounts or [Account(business_id="b", account_id="acc", name="Main", opening_balance=opening)],
        "counterparties": [
            Counterparty(business_id="b", counterparty_id="cust-a", name="Alpha", counterparty_type=CounterpartyType.customer),
            Counterparty(business_id="b", counterparty_id="cust-b", name="Beta", counterparty_type=CounterpartyType.customer),
        ],
        "transactions": list(transactions), "invoices": list(invoices), "expenses": list(expenses),
    }
    for r in extra:
        key = {Transaction: "transactions", Invoice: "invoices", Expense: "expenses", Account: "accounts"}[type(r)]
        rows[key].append(r)
    return FinanceData.from_datasets(rows, "b")


# ============================================================================== #
# 1. Period helpers
# ============================================================================== #

@pytest.mark.parametrize("d, end", [
    (date(2026, 2, 10), date(2026, 2, 28)),
    (date(2028, 2, 10), date(2028, 2, 29)),     # leap year
    (date(2100, 2, 1), date(2100, 2, 28)),      # century, not a leap year
    (date(2000, 2, 1), date(2000, 2, 29)),      # divisible by 400, leap year
    (date(2026, 12, 31), date(2026, 12, 31)),
    (date(2026, 4, 15), date(2026, 4, 30)),
])
def test_month_end(d, end):
    assert month_end(d) == end and month_start(d) == d.replace(day=1)


@pytest.mark.parametrize("d, n, expected", [
    (date(2026, 1, 31), 1, date(2026, 2, 1)),     # day always resets to 1, so no "Feb 31"
    (date(2026, 11, 5), 2, date(2027, 1, 1)),     # wraps forward over the year end
    (date(2026, 1, 15), -1, date(2025, 12, 1)),   # wraps backward
    (date(2026, 6, 30), -24, date(2024, 6, 1)),
    (date(2026, 6, 30), 0, date(2026, 6, 1)),
])
def test_add_months(d, n, expected):
    assert add_months(d, n) == expected


@pytest.mark.parametrize("d, fy, fy_start, q_start", [
    (date(2026, 3, 31), 4, date(2025, 4, 1), date(2026, 1, 1)),   # last day of Indian FY (Q4)
    (date(2026, 4, 1), 4, date(2026, 4, 1), date(2026, 4, 1)),    # first day of Indian FY
    (date(2026, 12, 31), 4, date(2026, 4, 1), date(2026, 10, 1)),
    (date(2026, 11, 5), 1, date(2026, 1, 1), date(2026, 10, 1)),  # calendar fiscal year
    (date(2026, 11, 5), 7, date(2026, 7, 1), date(2026, 10, 1)),  # July fiscal year
    (date(2026, 6, 30), 7, date(2025, 7, 1), date(2026, 4, 1)),
])
def test_fiscal_year_and_quarter_start(d, fy, fy_start, q_start):
    assert fiscal_year_start(d, fy) == fy_start
    assert fiscal_quarter_start(d, fy) == q_start


@pytest.mark.parametrize("label, fy, start, end, partial", [
    ("q1", 1, "2026-01-01", "2026-03-31", False),
    ("ytd", 1, "2026-01-01", "2026-11-05", True),
    ("last_quarter", 1, "2026-07-01", "2026-09-30", False),
    ("last_fy", 1, "2025-01-01", "2025-12-31", False),
    ("ytd", 7, "2026-07-01", "2026-11-05", True),
    ("q2", 7, "2026-10-01", "2026-11-05", True),
    ("fy2026", 7, "2026-07-01", "2026-11-05", True),
])
def test_resolve_period_with_other_fiscal_years(label, fy, start, end, partial):
    p = resolve_period(label, TODAY, fy_start_month=fy)
    assert (p.start.isoformat(), p.end.isoformat(), p.is_partial) == (start, end, partial)


@pytest.mark.parametrize("today, label, start, end", [
    (date(2026, 1, 15), "last_month", "2025-12-01", "2025-12-31"),   # across the year boundary
    (date(2026, 3, 31), "last_month", "2026-02-01", "2026-02-28"),
    (date(2028, 3, 1), "last_month", "2028-02-01", "2028-02-29"),
    (date(2026, 11, 1), "current_month", "2026-11-01", "2026-11-01"),  # first of the month = 1 day
    (date(2026, 11, 5), "last_1_days", "2026-11-05", "2026-11-05"),
])
def test_resolve_period_calendar_edges(today, label, start, end):
    p = resolve_period(label, today)
    assert (p.start.isoformat(), p.end.isoformat()) == (start, end)


def test_q_label_for_future_quarter_raises():
    with pytest.raises(PeriodError):
        resolve_period("q4", TODAY)          # Indian FY Q4 (Jan-Mar 2027) hasn't started


def test_period_passthrough_and_validation():
    p = P("2026-10-01", "2026-10-31")
    assert resolve_period(p, TODAY) is p
    assert p.days == 31 and p.contains(date(2026, 10, 1)) and p.contains(date(2026, 10, 31))
    assert not p.contains(date(2026, 11, 1))
    with pytest.raises(ValueError):
        P("2026-10-02", "2026-10-01")


def test_week_buckets_use_iso_weeks_across_year_end():
    b = buckets(P("2026-12-24", "2027-01-06"), "week")
    assert [(x.label, x.start.isoformat(), x.end.isoformat()) for x in b] == [
        ("2026-W52", "2026-12-24", "2026-12-27"),     # Thursday start, clipped
        ("2026-W53", "2026-12-28", "2027-01-03"),     # 2026 has 53 ISO weeks
        ("2027-W01", "2027-01-04", "2027-01-06"),     # clipped at the end
    ]


@pytest.mark.parametrize("granularity", ["day", "week", "month"])
def test_buckets_partition_any_period(granularity):
    """Random periods: buckets are contiguous, non-overlapping and cover every day exactly once."""
    rng = random.Random(7)
    for _ in range(40):
        start = date(2025, 1, 1) + timedelta(days=rng.randrange(700))
        p = Period(label="x", start=start, end=start + timedelta(days=rng.randrange(120)))
        b = buckets(p, granularity)
        assert b[0].start == p.start and b[-1].end == p.end
        assert sum(x.days for x in b) == p.days
        assert all(nxt.start == cur.end + timedelta(days=1) for cur, nxt in zip(b, b[1:]))
        if granularity == "week":
            assert all(x.start.isocalendar()[:2] == x.end.isocalendar()[:2] for x in b)
        if granularity == "month":
            assert all((x.start.year, x.start.month) == (x.end.year, x.end.month) for x in b)


# ============================================================================== #
# 2. Rounding and shares
# ============================================================================== #

@pytest.mark.parametrize("value, expected", [
    ("0.005", "0.01"), ("-0.005", "-0.01"), ("2.675", "2.68"), ("1.004999", "1.00"), ("10", "10.00"),
])
def test_money_half_up_both_signs(value, expected):
    assert money(D(value)) == D(expected) and money(D(value)).as_tuple().exponent == -2


@pytest.mark.parametrize("part, total, expected", [
    ("0", "0", "0.0000"), ("5", "0", "0.0000"),           # no division by zero
    ("1", "3", "0.3333"), ("2", "3", "0.6667"),
    ("1", "20000", "0.0001"),                              # 0.00005 rounds half up
    ("3", "3", "1.0000"),
])
def test_share(part, total, expected):
    assert share(D(part), D(total)) == D(expected)


def test_revenue_rounds_once_at_output_not_per_invoice():
    # two invoices of 0.005 each: rounding per invoice would give 0.02; the engine sums first
    data = _build(invoices=[
        _inv("a", date(2026, 10, 30), "0.005", issue=date(2026, 10, 1)),
        _inv("b", date(2026, 10, 30), "0.005", issue=date(2026, 10, 2)),
    ])
    assert calculate_revenue(data, P("2026-10-01", "2026-10-31")).billed_net == D("0.01")


def test_revenue_quantity_tax_and_period_boundaries():
    data = _build(invoices=[
        _inv("first-day", date(2026, 11, 30), "100", qty="3", tax="0.18", issue=date(2026, 10, 1)),
        _inv("last-day", date(2026, 11, 30), "50", qty="2", tax="0.05", issue=date(2026, 10, 31)),
        _inv("before", date(2026, 10, 30), "999", issue=date(2026, 9, 30)),
        _inv("after", date(2026, 11, 30), "999", issue=date(2026, 11, 1)),
    ])
    r = calculate_revenue(data, P("2026-10-01", "2026-10-31"))
    assert r.invoice_count == 2                                  # both boundary days are inclusive
    assert r.billed_net == D("400.00")                           # 3 x 100 + 2 x 50
    assert r.billed_tax == D("59.00")                            # 54 + 5
    assert r.billed_gross == r.billed_net + r.billed_tax


def test_breakdown_ordering_and_shares():
    data = _build(invoices=[
        _inv("i1", date(2026, 11, 30), "300", customer="cust-b", issue=date(2026, 10, 3)),
        _inv("i2", date(2026, 11, 30), "300", customer="cust-a", issue=date(2026, 10, 4)),
        _inv("i3", date(2026, 11, 30), "400", customer="cust-c", issue=date(2026, 10, 5)),
    ])
    rows = calculate_revenue(data, P("2026-10-01", "2026-10-31")).billed_by_customer
    # amount desc, then key asc on ties; unknown counterparty falls back to its id as the label
    assert [(b.key, b.label, b.amount, b.share) for b in rows] == [
        ("cust-c", "cust-c", D("400.00"), D("0.4000")),
        ("cust-a", "Alpha", D("300.00"), D("0.3000")),
        ("cust-b", "Beta", D("300.00"), D("0.3000")),
    ]
    assert sum(b.amount for b in rows) == D("1000.00")


def test_expenses_loan_repayment_is_non_operating_and_categories_sum():
    period = P("2026-10-01", "2026-10-31")
    data = _build(transactions=[
        _txn("loan", date(2026, 10, 2), "1000", category=ExpenseCategory.loan_repayment),
        _txn("tax", date(2026, 10, 3), "250", category=ExpenseCategory.taxes),
        _txn("rent", date(2026, 10, 4), "500", category=ExpenseCategory.rent),
        _txn("in", date(2026, 10, 5), "9999", "inflow"),                 # inflows never count as expenses
    ])
    r = calculate_expenses(data, period)
    assert (r.paid_total, r.operating_paid, r.uncategorized_paid) == (D("1750.00"), D("500.00"), D("0.00"))
    assert sum(b.amount for b in r.paid_by_category) == r.paid_total
    assert [b.key for b in r.paid_by_category] == ["loan_repayment", "rent", "taxes"]
    assert r.incurred_total == D("0.00") and r.incurred_by_vendor == []


# ============================================================================== #
# 3. Balances and cash-flow series
# ============================================================================== #

@pytest.fixture
def two_accounts():
    accounts = [Account(business_id="b", account_id="acc", name="Main", opening_balance="1000"),
                Account(business_id="b", account_id="acc2", name="Savings", opening_balance="500")]
    txns = [
        _txn("a1", date(2026, 9, 30), "100", "inflow"),
        _txn("a2", date(2026, 10, 1), "200"),
        _txn("a3", date(2026, 10, 12), "50", "inflow"),
        _txn("a4", date(2026, 10, 31), "30"),
        _txn("b1", date(2026, 10, 7), "70", account="acc2"),
        _txn("a5", date(2026, 11, 2), "999"),
    ]
    return _build(transactions=txns, accounts=accounts)


def test_balance_on_is_inclusive_and_per_account(two_accounts):
    acc, acc2 = two_accounts.accounts
    assert balance_on(two_accounts, acc, date(2026, 9, 29)) == D("1000")        # before any flow
    assert balance_on(two_accounts, acc, date(2026, 9, 30)) == D("1100")        # same-day flow included
    assert balance_on(two_accounts, acc, date(2026, 10, 31)) == D("920")
    assert balance_on(two_accounts, acc2, date(2026, 10, 31)) == D("430")       # other account's flows ignored


@pytest.mark.parametrize("granularity, n", [("day", 31), ("week", 5), ("month", 1)])
def test_series_identities(two_accounts, granularity, n):
    r = calculate_net_cash_flow(two_accounts, P("2026-10-01", "2026-10-31"), granularity)
    assert len(r.series) == n
    assert r.opening_balance == D("1600.00")                                    # 1100 + 500
    assert sum(p.inflows for p in r.series) == r.inflows == D("50.00")
    assert sum(p.outflows for p in r.series) == r.outflows == D("300.00")
    assert r.series[-1].closing_balance == r.closing_balance == D("1350.00")
    running = r.opening_balance
    for p in r.series:
        running += p.net_cash_flow
        assert p.closing_balance == running
    assert sum(a.closing_balance for a in r.by_account) == r.closing_balance
    assert any("Transfers between" in n for n in r.notes)


def test_quiet_period_rolls_balance_forward(two_accounts):
    r = calculate_net_cash_flow(two_accounts, P("2026-10-20", "2026-10-30"))
    assert (r.inflows, r.outflows) == (D("0.00"), D("0.00"))
    assert r.opening_balance == r.closing_balance == D("1380.00")               # 1600 - 200 + 50 - 70


def test_coverage_and_missing_data_warnings(two_accounts):
    r = calculate_net_cash_flow(two_accounts, P("2026-09-21", "2026-11-09"))    # 50 days, data 30 Sep..2 Nov
    assert r.data_coverage == share(D(34), D(50))
    assert any("Bank data starts on 2026-09-30" in w for w in r.warnings)
    assert any("only through 2026-11-02" in w for w in r.warnings)


# ============================================================================== #
# 4. Receivables aging
# ============================================================================== #

def test_receivables_aging_bucket_boundaries():
    as_of = TODAY
    days = {"f": -10, "d0": 0, "d1": 1, "d30": 30, "d31": 31, "d60": 60, "d61": 61, "d90": 90, "d91": 91}
    data = _build(invoices=[_inv(k, as_of - timedelta(days=v), "100", issue=as_of - timedelta(days=200))
                            for k, v in days.items()])
    r = calculate_receivables(data, as_of)
    aging = {b.key: (b.count, sorted(b.evidence_refs)) for b in r.aging}
    assert aging == {
        "current": (2, ["d0", "f"]),
        "1-30": (2, ["d1", "d30"]),
        "31-60": (2, ["d31", "d60"]),
        "61-90": (2, ["d61", "d90"]),
        "90+": (1, ["d91"]),
    }
    assert r.total_outstanding == D("900.00") and r.overdue_amount == D("700.00")
    assert abs(sum(b.share for b in r.aging) - 1) <= D("0.0001") * len(r.aging)


def test_receivables_use_balance_due_and_skip_settled():
    data = _build(invoices=[
        _inv("part", TODAY - timedelta(days=5), "1000", status="partially_paid", paid="400"),
        _inv("paid", TODAY - timedelta(days=5), "1000", status="paid", paid="1000"),
        _inv("draft", TODAY - timedelta(days=5), "1000", status="draft"),
        _inv("void", TODAY - timedelta(days=5), "1000", status="void"),
    ])
    r = calculate_receivables(data, TODAY)
    assert (r.invoice_count, r.total_outstanding, r.overdue_amount) == (1, D("600.00"), D("600.00"))


# ============================================================================== #
# 5. Forecast
# ============================================================================== #

@pytest.mark.parametrize("days_late, weight", [
    (-30, "1"), (0, "1"), (1, "1"), (30, "1"),
    (31, "0.5"), (60, "0.5"),
    (61, "0.25"), (90, "0.25"),
    (91, "0"), (365, "0"),
])
def test_collection_weight_boundaries(days_late, weight):
    assert _collection_weight(days_late) == D(weight)


def test_forecast_horizon_boundary_and_invoice_filters():
    data = _build(invoices=[
        _inv("on-edge", TODAY + timedelta(days=30), "100"),                     # due on the last day: counted
        _inv("past-edge", TODAY + timedelta(days=31), "100"),                   # one day later: not counted
        _inv("part", TODAY + timedelta(days=5), "1000", status="partially_paid", paid="250"),
        _inv("paid", TODAY + timedelta(days=5), "1000", status="paid", paid="1000"),
        _inv("draft", TODAY + timedelta(days=5), "1000", status="draft"),
        _inv("future", TODAY + timedelta(days=9), "1000", issue=TODAY + timedelta(days=1)),
        _inv("usd", TODAY + timedelta(days=5), "1000", currency="USD"),
    ])
    f = forecast_cash_flow(data, TODAY, horizon_days=30)
    assert f.collection_refs == ["on-edge", "part"]
    assert f.expected_collections == D("850.00")
    assert any("USD" in w for w in f.warnings)
    assert (f.period.start, f.period.end, f.period.label) == (TODAY + timedelta(days=1), TODAY + timedelta(days=30),
                                                              "next_30_days")


def test_forecast_run_rate_excludes_invoice_collections_and_future_txns():
    start = TODAY - timedelta(days=89)
    txns = [_txn(f"o{i}", start + timedelta(days=i), "10") for i in range(90)]
    txns += [
        _txn("coll", TODAY - timedelta(days=3), "9000", "inflow", invoice_id="inv-x"),   # forecast via receivables
        _txn("int", TODAY - timedelta(days=3), "180", "inflow"),                       # other inflow: 2/day
        _txn("later", TODAY + timedelta(days=2), "5000"),                              # after as_of: ignored
    ]
    f = forecast_cash_flow(_build(transactions=txns, opening="1000"), TODAY, 30)
    assert f.daily_outflow_rate == D("10.00") and f.daily_other_inflow_rate == D("2.00")
    assert f.current_balance == D("1000") - D("900") + D("9000") + D("180")
    assert f.expected_outflows == D("300.00") and f.expected_other_inflows == D("60.00")


def test_forecast_short_history_uses_covered_days():
    txns = [_txn(f"o{i}", TODAY - timedelta(days=i), "100") for i in range(30)]    # 30 days of data
    f = forecast_cash_flow(_build(transactions=txns, opening="100000"), TODAY, 30)
    assert f.daily_outflow_rate == D("100.00")             # 3000 / 30 covered days, not / 90
    assert f.data_coverage == D("0.3333")
    assert f.risk_level == "medium"                        # coverage < 0.5
    assert any("Only 30 day(s)" in w for w in f.warnings)
    assert any("based on 30 day(s)" in n for n in f.notes)


def test_forecast_lookback_window_changes_rates():
    start = TODAY - timedelta(days=89)
    txns = [_txn(f"o{i}", start + timedelta(days=i), "100" if i < 60 else "400") for i in range(90)]
    data = _build(transactions=txns, opening="1000000")
    assert forecast_cash_flow(data, TODAY, 30).daily_outflow_rate == D("200.00")             # (6000+12000)/90
    assert forecast_cash_flow(data, TODAY, 30, lookback_days=30).daily_outflow_rate == D("400.00")
    with pytest.raises(ValueError):
        forecast_cash_flow(data, TODAY, 30, lookback_days=0)


def test_forecast_runway_cases():
    days = range(90)
    burning_broke = _build(transactions=[_txn(f"o{i}", TODAY - timedelta(days=i), "10") for i in days])
    f = forecast_cash_flow(burning_broke, TODAY, 30)
    assert f.current_balance == D("-900.00") and f.runway_days == 0 and f.risk_level == "high"

    earning = _build(transactions=[_txn(f"i{i}", TODAY - timedelta(days=i), "10", "inflow") for i in days])
    f = forecast_cash_flow(earning, TODAY, 30)
    assert f.runway_days is None and f.risk_level == "low"
    assert f.projected_balance == D("900.00") + D("300.00")


def test_forecast_horizon_scales_linearly():
    txns = [_txn(f"o{i}", TODAY - timedelta(days=i), "10") for i in range(90)]
    data = _build(transactions=txns, opening="100000")
    f30, f90 = forecast_cash_flow(data, TODAY, 30), forecast_cash_flow(data, TODAY, 90)
    assert f90.expected_outflows == 3 * f30.expected_outflows
    assert f30.current_balance == f90.current_balance


# ============================================================================== #
# 6. Engine-wide guarantees
# ============================================================================== #

@pytest.fixture(scope="module")
def sample_datasets():
    return run_business(SAMPLES, today=TODAY).datasets


def _all_engines(data):
    """Every deterministic engine, run on one dataset, as plain JSON-safe dicts."""
    oct_ = P("2026-10-01", "2026-10-31", "2026-10")
    ytd = resolve_period("ytd", TODAY)
    out = {
        "revenue": calculate_revenue(data, oct_),
        "expenses": calculate_expenses(data, oct_),
        "cash_flow_week": calculate_net_cash_flow(data, ytd, "week"),
        "receivables": calculate_receivables(data, TODAY),
        "summary": financial_summary(data, oct_),
        "overdue_invoices": calculate_overdue_invoices(data, TODAY),
        "forecast": forecast_cash_flow(data, TODAY, 30),
        "aging": aging_report(data, TODAY),
        "overdue": detect_overdue(data, TODAY),
        "anomalies": detect_expense_anomalies(data, oct_.start, oct_.end),
    }
    dumped = {k: v.model_dump(mode="json") for k, v in out.items()}
    dumped["behaviour"] = [b.model_dump(mode="json") for b in payment_behaviour(data, TODAY)]
    return dumped


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_every_engine_ignores_input_order_on_the_sample(sample_datasets, seed):
    baseline = _all_engines(FinanceData.from_datasets(sample_datasets, "biz_001"))
    rng = random.Random(seed)
    shuffled = {name: rng.sample(list(rows), len(rows)) for name, rows in sample_datasets.items()}
    assert _all_engines(FinanceData.from_datasets(shuffled, "biz_001")) == baseline


def test_repeated_runs_are_identical(sample_datasets):
    data = FinanceData.from_datasets(sample_datasets, "biz_001")
    assert _all_engines(data) == _all_engines(data)


@pytest.mark.parametrize("module", [calculations, forecasting, receivables, anomalies, periods])
def test_engine_source_has_no_clock_or_randomness(module):
    src = inspect.getsource(module)
    forbidden = [r"\bdate\.today\(", r"\bdatetime\.(now|utcnow|today)\(", r"\btime\.time\(",
                 r"^\s*import random", r"^\s*from random ", r"\buuid\d?\(", r"\bos\.environ"]
    hits = [p for p in forbidden if re.search(p, src, re.M)]
    assert hits == [], f"{module.__name__} must not depend on the clock/randomness/environment: {hits}"


_FOUR_DP = {"share", "data_coverage"}


def _decimals(obj, path=""):
    if hasattr(obj, "model_fields"):
        for name in type(obj).model_fields:
            yield from _decimals(getattr(obj, name), f"{path}.{name}")
    elif isinstance(obj, (list, tuple)):
        for i, x in enumerate(obj):
            yield from _decimals(x, f"{path}[{i}]")
    elif isinstance(obj, Decimal):
        yield path, obj


def test_money_fields_are_exactly_two_decimals(sample_datasets):
    data = FinanceData.from_datasets(sample_datasets, "biz_001")
    oct_ = P("2026-10-01", "2026-10-31", "2026-10")
    reports = [
        calculate_revenue(data, oct_), calculate_expenses(data, oct_), calculate_net_cash_flow(data, oct_, "day"),
        calculate_receivables(data, TODAY), financial_summary(data, oct_), calculate_overdue_invoices(data, TODAY),
        forecast_cash_flow(data, TODAY, 45),
    ]
    checked = 0
    for report in reports:
        for path, value in _decimals(report):
            places = 4 if path.rsplit(".", 1)[-1] in _FOUR_DP else 2
            assert value.as_tuple().exponent == -places, f"{type(report).__name__}{path} = {value}"
            checked += 1
    assert checked > 100


def test_other_tenant_rows_never_change_any_result():
    base_txns = [_txn(f"o{i}", TODAY - timedelta(days=i), "10") for i in range(40)]
    base_invs = [_inv("mine", TODAY - timedelta(days=12), "500")]
    intruders = [
        Account(business_id="other", account_id="acc", name="Other", opening_balance="777777"),
        _txn("x1", TODAY - timedelta(days=1), "50000", "inflow", biz="other"),
        _txn("x2", TODAY - timedelta(days=2), "40000", biz="other"),
        _inv("theirs", TODAY - timedelta(days=12), "99999", biz="other"),
        Expense(business_id="other", expense_id="xe", expense_date=TODAY - timedelta(days=3), amount="1234",
                category=ExpenseCategory.software),
    ]
    clean = _build(transactions=base_txns, invoices=base_invs, opening="1000")
    mixed = _build(transactions=base_txns, invoices=base_invs, opening="1000", extra=intruders)
    assert _all_engines(mixed) == _all_engines(clean)
