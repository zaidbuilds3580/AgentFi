"""
Tests for the deterministic finance engine: periods, revenue, expenses, net cash flow,
receivables, determinism, tenant isolation, and the agent-facing tools.
"""

import random
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from finance_engine.calculations import (
    calculate_expenses,
    calculate_net_cash_flow,
    calculate_receivables,
    calculate_revenue,
    financial_summary,
    money,
)
from finance_engine.data import BusinessNotFoundError, FinanceData
from finance_engine.etl import run_business, write_run_outputs
from finance_engine.periods import Period, PeriodError, buckets, resolve_period
from finance_engine.schema import (
    Account,
    Business,
    Counterparty,
    CounterpartyType,
    Expense,
    ExpenseCategory,
    Invoice,
    InvoiceLineItem,
    InvoiceStatus,
    Transaction,
    TransactionDirection,
)
from finance_engine import tools

SAMPLES = Path(__file__).resolve().parents[1] / "data" / "samples" / "biz_001"
TODAY = date(2026, 11, 5)
D = Decimal


def P(start, end, label="custom"):
    return Period(label=label, start=date.fromisoformat(start), end=date.fromisoformat(end))


# ------------------------------------------------------------------------------ #
# fixtures
# ------------------------------------------------------------------------------ #

@pytest.fixture(scope="module")
def sample():
    """The ETL output of the sample business, as FinanceData."""
    return FinanceData.from_datasets(run_business(SAMPLES, today=TODAY).datasets, "biz_001")


def _txn(tid, day, amount, direction="outflow", category=None, invoice_id=None, account="acc", biz="b"):
    return Transaction(business_id=biz, transaction_id=tid, account_id=account, transaction_date=day,
                       amount=amount, direction=direction, category=category, invoice_id=invoice_id,
                       currency="USD" if account == "usd_acc" else "INR")


def _inv(iid, issue, due, price, status="sent", paid="0", tax="0.18", currency="INR", customer="cust-a"):
    return Invoice(business_id="b", invoice_id=iid, invoice_number=iid.upper(), customer_id=customer,
                   issue_date=issue, due_date=due, currency=currency, status=status, amount_paid=paid,
                   line_items=[InvoiceLineItem(description="work", unit_price=price, tax_rate=tax)])


@pytest.fixture
def small():
    """Hand-built business with every edge case and easy numbers."""
    datasets = {
        "businesses": [Business(business_id="b", name="B"), Business(business_id="other", name="Other")],
        "accounts": [
            Account(business_id="b", account_id="acc", name="Main", opening_balance="1000"),
            Account(business_id="b", account_id="usd_acc", name="USD", currency="USD", opening_balance="50"),
            Account(business_id="other", account_id="acc", name="Other main", opening_balance="999999"),
        ],
        "counterparties": [
            Counterparty(business_id="b", counterparty_id="cust-a", name="Alpha", counterparty_type=CounterpartyType.customer),
            Counterparty(business_id="b", counterparty_id="cust-b", name="Beta", counterparty_type=CounterpartyType.customer),
            Counterparty(business_id="b", counterparty_id="vend-x", name="Xeno", counterparty_type=CounterpartyType.vendor),
        ],
        "invoices": [
            _inv("inv-1", date(2026, 9, 20), date(2026, 10, 5), "1000", status="paid", paid="1180"),   # Sept
            _inv("inv-2", date(2026, 10, 2), date(2026, 10, 30), "2000", status="partially_paid", paid="500"),
            _inv("inv-3", date(2026, 10, 10), date(2026, 11, 10), "500", customer="cust-b", tax="0"),
            _inv("inv-4", date(2026, 10, 11), date(2026, 11, 10), "700", status="draft"),
            _inv("inv-5", date(2026, 10, 12), date(2026, 11, 10), "800", status="void"),
            _inv("inv-6", date(2026, 10, 13), date(2026, 11, 10), "100", currency="USD"),
        ],
        "transactions": [
            _txn("t1", date(2026, 10, 1), "1180", "inflow", invoice_id="inv-1"),
            _txn("t2", date(2026, 10, 3), "500", "inflow", invoice_id="inv-2"),
            _txn("t3", date(2026, 10, 4), "25", "inflow"),                                     # interest
            _txn("t4", date(2026, 10, 5), "300", category=ExpenseCategory.rent),
            _txn("t5", date(2026, 10, 15), "200", category=ExpenseCategory.taxes),
            _txn("t6", date(2026, 10, 20), "40"),                                              # uncategorized
            _txn("t7", date(2026, 11, 2), "100", category=ExpenseCategory.software),            # November
            _txn("t8", date(2026, 10, 6), "10", account="usd_acc"),                            # foreign account
            _txn("x1", date(2026, 10, 6), "5000", "inflow", biz="other"),                      # other tenant
        ],
        "expenses": [
            Expense(business_id="b", expense_id="e1", vendor_id="vend-x", expense_date=date(2026, 10, 5),
                    amount="300", category=ExpenseCategory.rent, is_paid=True, transaction_id="t4"),
            Expense(business_id="b", expense_id="e2", vendor_id="vend-x", expense_date=date(2026, 10, 25),
                    amount="90", category=ExpenseCategory.software),                           # unpaid bill
        ],
    }
    return FinanceData.from_datasets(datasets, "b")


OCT = P("2026-10-01", "2026-10-31", "2026-10")


# ------------------------------------------------------------------------------ #
# periods
# ------------------------------------------------------------------------------ #

@pytest.mark.parametrize("label, start, end, partial", [
    ("current_month", "2026-11-01", "2026-11-05", True),
    ("last_month", "2026-10-01", "2026-10-31", False),
    ("last_30_days", "2026-10-07", "2026-11-05", False),
    ("last_7_days", "2026-10-30", "2026-11-05", False),
    ("current_quarter", "2026-10-01", "2026-11-05", True),   # FY Q3 = Oct-Dec
    ("last_quarter", "2026-07-01", "2026-09-30", False),
    ("Q1", "2026-04-01", "2026-06-30", False),
    ("q3", "2026-10-01", "2026-11-05", True),
    ("ytd", "2026-04-01", "2026-11-05", True),
    ("last_fy", "2025-04-01", "2026-03-31", False),
    ("FY2025-26", "2025-04-01", "2026-03-31", False),
    ("2026-09", "2026-09-01", "2026-09-30", False),
    ("2026-10-01..2026-10-15", "2026-10-01", "2026-10-15", False),
    ("2026-10-01 to 2026-10-15", "2026-10-01", "2026-10-15", False),
])
def test_resolve_period(label, start, end, partial):
    p = resolve_period(label, TODAY)
    assert (p.start.isoformat(), p.end.isoformat(), p.is_partial) == (start, end, partial)


def test_fiscal_quarters_wrap_the_calendar_year():
    jan = date(2027, 1, 15)
    assert resolve_period("current_quarter", jan).start == date(2027, 1, 1)     # FY Q4 = Jan-Mar
    assert resolve_period("last_quarter", jan).start == date(2026, 10, 1)
    assert resolve_period("ytd", jan).start == date(2026, 4, 1)
    assert resolve_period("Q4", jan).start == date(2027, 1, 1)
    # calendar-year business
    assert resolve_period("Q1", TODAY, fy_start_month=1) == Period(label="q1", start=date(2026, 1, 1), end=date(2026, 3, 31))


@pytest.mark.parametrize("bad", ["next_month", "Q4", "2026-13", "2026-10-31..2026-10-01", "last_0_days"])
def test_bad_periods_raise(bad):
    with pytest.raises((PeriodError, ValueError)):
        resolve_period(bad, TODAY)   # Q4 (Jan-Mar 2027) has not started on 2026-11-05


def test_buckets_are_clipped_and_contiguous():
    weeks = buckets(P("2026-10-01", "2026-10-31"), "week")
    assert weeks[0].start == date(2026, 10, 1) and weeks[0].end == date(2026, 10, 4)   # Thu-Sun
    assert weeks[-1].end == date(2026, 10, 31)
    assert all(b.start == a.end.fromordinal(a.end.toordinal() + 1) for a, b in zip(weeks, weeks[1:]))
    assert [b.label for b in buckets(P("2026-09-15", "2026-11-02"), "month")] == ["2026-09", "2026-10", "2026-11"]


# ------------------------------------------------------------------------------ #
# reconciliation with the real bank statements (sample business)
# ------------------------------------------------------------------------------ #

def test_october_matches_hdfc_statement_summary(sample):
    """HDFC footer: opening 1,00,000.00 | debits 1,61,161.50 | credits 1,01,000.00 | closing 39,838.50."""
    cf = calculate_net_cash_flow(sample, OCT)
    assert (cf.opening_balance, cf.outflows, cf.inflows, cf.closing_balance) == (
        D("100000.00"), D("161161.50"), D("101000.00"), D("39838.50"))
    assert cf.net_cash_flow == D("-60161.50")
    assert cf.data_coverage == D("1.0000") and cf.warnings == []


def test_closing_balance_matches_second_statement(sample):
    cf = calculate_net_cash_flow(sample, resolve_period("current_month", TODAY))
    assert cf.opening_balance == D("39838.50")
    assert cf.closing_balance == D("5398.50")            # last closing balance on the Nov statement
    assert any("only through 2026-11-04" in w for w in cf.warnings)


def test_sample_revenue_and_expenses(sample):
    rev = calculate_revenue(sample, OCT)
    assert rev.billed_net == D("15000.00")                # INV-1003 only (1004 draft, 1005 USD)
    assert rev.collected_from_invoices == D("89000.00")   # INV-1001 59k + INV-1002 30k
    assert rev.other_inflows == D("12000.00")             # INV-1099 is not a known invoice
    exp = calculate_expenses(sample, OCT)
    assert exp.paid_total == D("161161.50") and exp.operating_paid == D("146761.50")
    assert exp.paid_by_category[0].key == "payroll"
    assert exp.incurred_total == D("84472.50") and exp.incurred_unpaid == D("18000.00")


# ------------------------------------------------------------------------------ #
# revenue
# ------------------------------------------------------------------------------ #

def test_revenue_excludes_tax_drafts_voids_and_foreign_currency(small):
    rev = calculate_revenue(small, OCT)
    assert rev.billed_net == D("2500.00")                 # inv-2 2000 + inv-3 500
    assert rev.billed_tax == D("360.00")                  # 18% of 2000
    assert rev.billed_gross == D("2860.00")
    assert rev.invoice_count == 2
    assert any("INV-6" in w and "USD" in w for w in rev.warnings)
    assert any("draft" in n and "INV-4" in n for n in rev.notes)
    assert any("void" in n and "INV-5" in n for n in rev.notes)


def test_revenue_cash_basis_splits_collections_and_other_inflows(small):
    rev = calculate_revenue(small, OCT)
    assert rev.collected_from_invoices == D("1680.00")    # t1 (inv-1, billed in Sept) + t2
    assert rev.other_inflows == D("25.00")
    assert rev.total_inflows == D("1705.00")
    assert [(b.key, b.amount, b.evidence_refs) for b in rev.collected_by_customer] == [("cust-a", D("1680.00"), ["t1", "t2"])]


def test_revenue_breakdown_sums_and_labels(small):
    rev = calculate_revenue(small, OCT)
    assert sum(b.amount for b in rev.billed_by_customer) == rev.billed_net
    assert [(b.label, b.share) for b in rev.billed_by_customer] == [("Alpha", D("0.8000")), ("Beta", D("0.2000"))]


# ------------------------------------------------------------------------------ #
# expenses
# ------------------------------------------------------------------------------ #

def test_expenses_cash_basis(small):
    exp = calculate_expenses(small, OCT)
    assert exp.paid_total == D("540.00")                  # t4 300 + t5 200 + t6 40 (t8 is USD account)
    assert exp.operating_paid == D("340.00")              # taxes excluded
    assert exp.uncategorized_paid == D("40.00")
    assert [b.key for b in exp.paid_by_category] == ["rent", "taxes", "uncategorized"]
    assert any("uncategorized" in w for w in exp.warnings)
    assert any("usd_acc" in w for w in exp.warnings)


def test_expenses_accrual_basis_has_no_double_counting(small):
    exp = calculate_expenses(small, OCT)
    assert exp.incurred_total == D("390.00")              # e1 300 (paid by t4) + e2 90
    assert exp.incurred_unpaid == D("90.00")
    assert exp.paid_total == D("540.00")                  # t4 counted once, on the cash side only
    assert exp.incurred_by_vendor[0].evidence_refs == ["e1", "e2"]


# ------------------------------------------------------------------------------ #
# net cash flow
# ------------------------------------------------------------------------------ #

def test_cash_flow_identities(small):
    cf = calculate_net_cash_flow(small, OCT, granularity="week")
    assert cf.opening_balance + cf.net_cash_flow == cf.closing_balance
    assert cf.inflows - cf.outflows == cf.net_cash_flow
    assert sum(p.net_cash_flow for p in cf.series) == cf.net_cash_flow
    assert cf.series[-1].closing_balance == cf.closing_balance
    assert sum(a.closing_balance for a in cf.by_account) == cf.closing_balance


def test_opening_balance_rolls_forward(small):
    oct_cf = calculate_net_cash_flow(small, OCT)
    nov_cf = calculate_net_cash_flow(small, P("2026-11-01", "2026-11-30"))
    assert oct_cf.opening_balance == D("1000.00")
    assert oct_cf.closing_balance == D("2165.00")         # 1000 + 1705 - 540
    assert nov_cf.opening_balance == oct_cf.closing_balance
    assert nov_cf.closing_balance == D("2065.00")


def test_foreign_account_and_other_tenant_excluded(small):
    cf = calculate_net_cash_flow(small, OCT)
    assert [a.account_id for a in cf.by_account] == ["acc"]
    assert cf.inflows == D("1705.00")                     # 'other' tenant's 5000 inflow ignored


def test_invalid_granularity(small):
    with pytest.raises(ValueError):
        calculate_net_cash_flow(small, OCT, granularity="hour")


def test_no_transactions_gives_zeros_with_warning():
    data = FinanceData.from_datasets({"businesses": [Business(business_id="b", name="B")],
                                      "accounts": [Account(business_id="b", account_id="a", name="A", opening_balance="10")]}, "b")
    cf = calculate_net_cash_flow(data, OCT)
    assert (cf.net_cash_flow, cf.closing_balance, cf.data_coverage) == (D("0.00"), D("10.00"), D("0.0000"))
    assert any("No bank transactions" in w for w in cf.warnings)


# ------------------------------------------------------------------------------ #
# receivables
# ------------------------------------------------------------------------------ #

def test_receivables_aging(small):
    ar = calculate_receivables(small, as_of=date(2026, 11, 10))
    by = {b.key: b for b in ar.aging}
    assert by["current"].evidence_refs == ["inv-3"]      # due today -> current
    assert by["1-30"].evidence_refs == ["inv-2"]         # 11 days late
    assert ar.total_outstanding == D("2360.00")          # inv-2 2360-500 + inv-3 500
    assert ar.overdue_amount == D("1860.00")
    assert [b.key for b in ar.aging] == ["current", "1-30", "31-60", "61-90", "90+"]


def test_receivables_ignore_future_invoices(small):
    ar = calculate_receivables(small, as_of=date(2026, 10, 5))
    assert ar.invoice_count == 1                          # only inv-2 issued by then (inv-1 is paid)


# ------------------------------------------------------------------------------ #
# determinism & rounding
# ------------------------------------------------------------------------------ #

def test_results_are_deterministic_regardless_of_input_order(small):
    shuffled = {
        "businesses": [small.business],
        "accounts": random.Random(1).sample(list(small.accounts), len(small.accounts)),
        "counterparties": list(reversed(small.counterparties)),
        "transactions": random.Random(2).sample(list(small.transactions), len(small.transactions)),
        "invoices": random.Random(3).sample(list(small.invoices), len(small.invoices)),
        "expenses": list(reversed(small.expenses)),
    }
    other = FinanceData.from_datasets(shuffled, "b")
    assert financial_summary(small, OCT) == financial_summary(other, OCT)
    assert calculate_expenses(small, OCT) == calculate_expenses(other, OCT)
    assert calculate_net_cash_flow(small, OCT, "day") == calculate_net_cash_flow(other, OCT, "day")


def test_money_rounds_half_up():
    assert money(D("2.345")) == D("2.35")
    assert money(D("2.344")) == D("2.34")
    assert money(D("-2.345")) == D("-2.35")


def test_summary_combines_reports(small):
    s = financial_summary(small, OCT)
    assert (s.revenue_billed, s.revenue_collected, s.expenses_paid, s.net_cash_flow, s.cash_balance) == (
        D("2500.00"), D("1680.00"), D("540.00"), D("1165.00"), D("2165.00"))
    assert s.receivables == D("2360.00")                  # as of 31 Oct: inv-2 and inv-3
    assert len(s.warnings) == len(set(s.warnings))        # merged without duplicates


# ------------------------------------------------------------------------------ #
# tools (agent-facing) and data loading
# ------------------------------------------------------------------------------ #

@pytest.fixture(scope="module")
def processed_dir(tmp_path_factory):
    root = tmp_path_factory.mktemp("processed")
    write_run_outputs(run_business(SAMPLES, today=TODAY), root / "biz_001")
    return root


def test_tools_read_processed_data(processed_dir):
    s = tools.get_financial_summary("biz_001", "last_month", today=TODAY, processed_dir=processed_dir)
    assert s.cash_balance == D("39838.50") and s.period.label == "last_month"
    rev = tools.get_revenue("biz_001", "2026-10", today=TODAY, processed_dir=processed_dir)
    assert rev.billed_net == D("15000.00")
    cf = tools.get_net_cash_flow("biz_001", "2026-10-01..2026-11-04", granularity="week", processed_dir=processed_dir)
    assert cf.closing_balance == D("5398.50")
    ar = tools.get_receivables("biz_001", as_of=date(2026, 10, 31), processed_dir=processed_dir)
    assert ar.total_outstanding == D("50300.00")
    js = s.model_dump(mode="json")
    assert js["cash_balance"] == "39838.50" and js["calculation_version"] == "finance-engine-1.0.0"


def test_tools_errors(processed_dir):
    with pytest.raises(BusinessNotFoundError):
        tools.get_financial_summary("biz_404", today=TODAY, processed_dir=processed_dir)
    with pytest.raises(ValueError):
        tools.get_financial_summary("  ", today=TODAY, processed_dir=processed_dir)
    with pytest.raises(PeriodError):
        tools.get_financial_summary("biz_001", "next_year", today=TODAY, processed_dir=processed_dir)


def test_cashflow_agent_adapter_matches_agent_schema(processed_dir, monkeypatch):
    from config.settings import settings
    from agents.cashflow.finance_tools import get_financial_summary
    from agents.cashflow.schemas import FinancialSummary
    monkeypatch.setattr(settings, "PROCESSED_DATA_DIR", str(processed_dir))
    s = get_financial_summary("biz_001", "last_month", today=TODAY)
    assert isinstance(s, FinancialSummary)
    assert (s.inflows, s.outflows, s.net_cash_flow, s.cash_balance, s.receivables) == (
        101000.0, 161161.5, -60161.5, 39838.5, 50300.0)
    assert s.calculation_version == "finance-engine-1.0.0"
