"""
Tests for the forecast, overdue-invoice and expense-anomaly engines, and for the real agent
tools (agents/*/finance_tools.py) honouring the mock tool contracts exactly.
"""

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from finance_engine.anomalies import detect_expense_anomalies
from finance_engine.calculations import calculate_overdue_invoices
from finance_engine.data import BusinessNotFoundError, FinanceData
from finance_engine.etl import run_business, write_run_outputs
from finance_engine.forecasting import forecast_cash_flow
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


def _txn(tid, day, amount, direction="outflow", category=None, desc="", invoice_id=None):
    return Transaction(business_id="b", transaction_id=tid, account_id="acc", transaction_date=day, amount=amount,
                       direction=direction, category=category, description=desc, invoice_id=invoice_id)


def _inv(iid, due, total, paid="0", status="sent", currency="INR", issue=date(2026, 6, 1)):
    return Invoice(business_id="b", invoice_id=iid, invoice_number=iid.upper(), customer_id="cust-a",
                   issue_date=issue, due_date=due, status=status, amount_paid=paid, currency=currency,
                   line_items=[InvoiceLineItem(description="x", unit_price=total, tax_rate="0")])


def _data(transactions=(), invoices=(), expenses=(), opening="0"):
    return FinanceData.from_datasets({
        "businesses": [Business(business_id="b", name="B")],
        "accounts": [Account(business_id="b", account_id="acc", name="Main", opening_balance=opening)],
        "counterparties": [
            Counterparty(business_id="b", counterparty_id="cust-a", name="Alpha", counterparty_type=CounterpartyType.customer),
            Counterparty(business_id="b", counterparty_id="vend-aws", name="AWS", counterparty_type=CounterpartyType.vendor),
        ],
        "transactions": list(transactions), "invoices": list(invoices), "expenses": list(expenses),
    }, "b")


# ------------------------------------------------------------------------------ #
# overdue invoices
# ------------------------------------------------------------------------------ #

def test_overdue_bands_and_edges():
    as_of = date(2026, 11, 5)
    data = _data(invoices=[
        _inv("due-today", as_of, "100"),                                  # not overdue
        _inv("d1", as_of - timedelta(days=1), "100"),                     # 1-30
        _inv("d30", as_of - timedelta(days=30), "200", paid="50", status="partially_paid"),
        _inv("d31", as_of - timedelta(days=31), "300"),                   # 31-60
        _inv("d61", as_of - timedelta(days=61), "400", status="overdue"), # 61+
        _inv("paid", as_of - timedelta(days=90), "500", paid="500", status="paid"),
        _inv("draft", as_of - timedelta(days=90), "600", status="draft"),
        _inv("usd", as_of - timedelta(days=10), "700", currency="USD"),
        _inv("future", as_of + timedelta(days=20), "800", issue=as_of + timedelta(days=1)),  # issued later
    ])
    r = calculate_overdue_invoices(data, as_of)
    assert [i.invoice_id for i in r.invoices] == ["d61", "d31", "d30", "d1"]      # most overdue first
    assert [(b.band, b.count, b.total_amount) for b in r.ageing_bands] == [
        ("1-30", 2, D("250.00")), ("31-60", 1, D("300.00")), ("61+", 1, D("400.00"))]
    assert r.count == 4 and r.total_amount == D("950.00")
    assert sum(b.total_amount for b in r.ageing_bands) == r.total_amount
    assert any("USD" in w for w in r.warnings)


def test_no_overdue_returns_empty_bands():
    r = calculate_overdue_invoices(_data(), TODAY)
    assert r.count == 0 and [b.count for b in r.ageing_bands] == [0, 0, 0]


# ------------------------------------------------------------------------------ #
# forecast
# ------------------------------------------------------------------------------ #

def _steady_business(daily_out="100", balance="100000", with_invoice=True):
    """90 days of identical outflows (+1/day other inflow); optionally one invoice due within the horizon."""
    start = TODAY - timedelta(days=89)
    txns = [_txn(f"t{i}", start + timedelta(days=i), daily_out, category=ExpenseCategory.supplies) for i in range(90)]
    txns.append(_txn("int", start, "90", "inflow", desc="interest"))        # other inflow: 1/day
    return _data(transactions=txns, opening=balance,
                 invoices=[_inv("soon", TODAY + timedelta(days=10), "5000")] if with_invoice else [])


def test_forecast_arithmetic():
    data = _steady_business()
    f = forecast_cash_flow(data, TODAY, horizon_days=30)
    assert f.current_balance == D("100000") - D("9000") + D("90")             # 91,090.00
    assert f.daily_outflow_rate == D("100.00") and f.daily_other_inflow_rate == D("1.00")
    assert f.expected_collections == D("5000.00") and f.collection_refs == ["soon"]
    assert f.projected_balance == f.current_balance + D("5000") + D("30") - D("3000")
    assert f.risk_level == "low" and f.data_coverage == D("1.0000")
    assert f.method == "run_rate_plus_receivables"


def test_forecast_risk_levels():
    # current = opening - 8,910 (90 days x 100 out, 90 in); net burn 99/day
    runway = forecast_cash_flow(_steady_business(balance="11000"), TODAY, 30)       # current 2,090
    assert runway.runway_days == 21 and runway.projected_balance > 0 and runway.risk_level == "high"
    negative = forecast_cash_flow(_steady_business(balance="6000"), TODAY, 30)      # current -2,910
    assert negative.projected_balance < 0 and negative.risk_level == "high"
    # daily_out 10: current = opening - 810, burn 9/day; projected 130 < 30 days of outflows (300)
    medium = forecast_cash_flow(_steady_business(balance="1210", daily_out="10", with_invoice=False), TODAY, 30)
    assert (medium.current_balance, medium.projected_balance, medium.risk_level) == (D("400.00"), D("130.00"), "medium")


def test_forecast_collection_weights_by_lateness():
    data = _data(invoices=[
        _inv("late20", TODAY - timedelta(days=20), "1000"),    # 100%
        _inv("late45", TODAY - timedelta(days=45), "1000"),    # 50%
        _inv("late75", TODAY - timedelta(days=75), "1000"),    # 25%
        _inv("late120", TODAY - timedelta(days=120), "1000"),  # 0%
        _inv("later", TODAY + timedelta(days=60), "1000"),     # due after the horizon
    ])
    f = forecast_cash_flow(data, TODAY, horizon_days=30)
    assert f.expected_collections == D("1750.00")
    assert f.collection_refs == ["late20", "late45", "late75"]


def test_forecast_without_history_is_flagged():
    f = forecast_cash_flow(_data(opening="500"), TODAY, 30)
    assert f.projected_balance == D("500.00") and f.data_coverage == D("0.0000")
    assert f.risk_level == "medium"                     # low coverage
    assert any("No bank history" in w for w in f.warnings)


def test_forecast_validates_horizon():
    with pytest.raises(ValueError):
        forecast_cash_flow(_data(), TODAY, 0)


# ------------------------------------------------------------------------------ #
# expense anomalies
# ------------------------------------------------------------------------------ #

def _history_txns():
    """Three months of normal AWS bills (via linked expenses) and software spend."""
    txns, exps = [], []
    for i, month in enumerate((7, 8, 9)):
        tid = f"aws{month}"
        txns.append(_txn(tid, date(2026, month, 3), "1000", category=ExpenseCategory.software, desc=f"AWS {month}"))
        exps.append(Expense(business_id="b", expense_id=f"e{month}", vendor_id="vend-aws",
                            expense_date=date(2026, month, 3), amount="1000", category=ExpenseCategory.software,
                            is_paid=True, transaction_id=tid))
    return txns, exps


def test_anomaly_rules_fire_on_crafted_data():
    txns, exps = _history_txns()
    txns += [
        _txn("aws10", date(2026, 10, 3), "6000", category=ExpenseCategory.software, desc="AWS 10"),  # A1 6x + A3
        _txn("uber1", date(2026, 10, 10), "1500", category=ExpenseCategory.travel, desc="UBER TRIP 123"),
        _txn("uber2", date(2026, 10, 11), "1500", category=ExpenseCategory.travel, desc="UBER TRIP 456"),  # A2
        _txn("rent", date(2026, 10, 5), "900", category=ExpenseCategory.rent, desc="RENT"),
        _txn("misc", date(2026, 10, 20), "77", desc="MISC"),                                                # A5
    ]
    exps += [
        Expense(business_id="b", expense_id="e10", vendor_id="vend-aws", expense_date=date(2026, 10, 3),
                amount="6000", category=ExpenseCategory.software, is_paid=True, transaction_id="aws10"),
        Expense(business_id="b", expense_id="erent", expense_date=date(2026, 10, 5), amount="1000",
                category=ExpenseCategory.rent, is_paid=True, transaction_id="rent"),                       # A4
    ]
    r = detect_expense_anomalies(_data(transactions=txns, expenses=exps), date(2026, 10, 1), date(2026, 10, 31))
    by_rule = {a.rule_id: a for a in r.anomalies}
    # A3 (software spike) is suppressed: the single AWS payment that causes it is already A1
    assert set(by_rule) == {"A1", "A2", "A4", "A5"}

    a1 = by_rule["A1"]
    assert (a1.vendor, a1.amount, a1.expected_baseline, a1.severity) == ("AWS", D("6000.00"), D("1000.00"), "high")
    assert by_rule["A2"].evidence_refs == ["uber1", "uber2"] and by_rule["A2"].severity == "high"
    assert (by_rule["A4"].amount, by_rule["A4"].expected_baseline) == (D("900.00"), D("1000.00"))
    assert by_rule["A5"].severity == "low"
    severities = [a.severity for a in r.anomalies]
    assert severities == sorted(severities, key={"high": 0, "medium": 1, "low": 2}.get)


def test_normal_spending_has_no_anomalies():
    txns, exps = _history_txns()
    txns.append(_txn("aws10", date(2026, 10, 3), "1100", category=ExpenseCategory.software, desc="AWS 10"))
    exps.append(Expense(business_id="b", expense_id="e10", vendor_id="vend-aws", expense_date=date(2026, 10, 3),
                        amount="1100", category=ExpenseCategory.software, is_paid=True, transaction_id="aws10"))
    r = detect_expense_anomalies(_data(transactions=txns, expenses=exps), date(2026, 10, 1), date(2026, 10, 31))
    assert r.anomalies == [] and r.outflows_checked == 1


def test_anomaly_ids_are_deterministic():
    txns, exps = _history_txns()
    txns.append(_txn("misc", date(2026, 10, 20), "77", desc="MISC"))
    data = _data(transactions=txns, expenses=exps)
    a = detect_expense_anomalies(data, date(2026, 10, 1), date(2026, 10, 31))
    b = detect_expense_anomalies(data, date(2026, 10, 1), date(2026, 10, 31))
    assert a == b and a.anomalies[0].anomaly_id.startswith("ANOM-")


def test_short_window_skips_spike_check():
    r = detect_expense_anomalies(_data(), date(2026, 10, 1), date(2026, 10, 7))
    assert any("no complete calendar month" in n for n in r.notes)


# ------------------------------------------------------------------------------ #
# agent contracts: real tools return exactly the mock schemas
# ------------------------------------------------------------------------------ #

@pytest.fixture(scope="module")
def processed(tmp_path_factory):
    root = tmp_path_factory.mktemp("processed")
    write_run_outputs(run_business(SAMPLES, today=TODAY), root / "biz_001")
    return root


@pytest.fixture
def use_processed(processed, monkeypatch):
    from config.settings import settings
    monkeypatch.setattr(settings, "PROCESSED_DATA_DIR", str(processed))


def test_contract_financial_summary(use_processed):
    from agents.cashflow import finance_tools, mock_tools
    real = finance_tools.get_financial_summary("biz_001", "last_month", today=TODAY)
    mock = mock_tools.get_financial_summary("biz_001", "last_month")
    assert type(real) is type(mock) and set(real.model_fields) == set(mock.model_fields)
    assert real.cash_balance == 39838.5 and real.calculation_version == "finance-engine-1.1.0"


def test_contract_forecast(use_processed):
    from agents.cashflow import finance_tools, mock_tools
    real = finance_tools.forecast_cash_flow("biz_001", 30, today=TODAY)
    assert type(real) is type(mock_tools.forecast_cash_flow("biz_001", 30))
    assert real.horizon_days == 30 and real.risk_level in ("low", "medium", "high")
    assert real.method == "run_rate_plus_receivables" and 0 <= real.data_coverage <= 1


def test_contract_overdue_invoices(use_processed):
    from agents.invoice import finance_tools, mock_tools
    real = finance_tools.get_overdue_invoices("biz_001", "2026-11-05")
    assert type(real) is type(mock_tools.get_overdue_invoices("biz_001"))
    assert (real.count, real.total_amount) == (2, 50300.0)
    assert [b.band for b in real.ageing_bands] == ["1-30", "31-60", "61+"]
    assert [(i.invoice_id, i.days_overdue, i.amount) for i in real.invoices] == [
        ("inv-1002", 16, 32600.0), ("inv-1003", 5, 17700.0)]


def test_contract_expense_anomalies(use_processed):
    from agents.expense import finance_tools, mock_tools
    real = finance_tools.get_expense_anomalies("biz_001", "2026-10-01", "2026-11-04")
    assert type(real) is type(mock_tools.get_expense_anomalies("biz_001"))
    assert (real.start_date, real.end_date) == ("2026-10-01", "2026-11-04")
    assert [(a.category, a.vendor, a.severity) for a in real.anomalies] == [
        ("Software", "Amazon Web Services", "medium"),   # A1 Oct: 3.3x usual AWS bill
        ("Software", "Amazon Web Services", "medium"),   # A1 Nov: still 3.3x
        ("Taxes", "GST PMT CBDT-OCT26 CPIN 26101234567", "low"),   # A6 +20%
        ("Uncategorized", "MISC DR 5531", "low"),        # A5
        ("Utilities", "Airtel Business", "low"),         # A7 October payment missing
    ]


@pytest.mark.parametrize("call", [
    lambda: __import__("agents.cashflow.finance_tools", fromlist=["x"]).get_financial_summary("biz_404"),
    lambda: __import__("agents.cashflow.finance_tools", fromlist=["x"]).forecast_cash_flow("biz_404"),
    lambda: __import__("agents.invoice.finance_tools", fromlist=["x"]).get_overdue_invoices("biz_404"),
    lambda: __import__("agents.expense.finance_tools", fromlist=["x"]).get_expense_anomalies("biz_404"),
])
def test_unknown_business_raises(use_processed, call):
    with pytest.raises(BusinessNotFoundError):
        call()


def test_bad_inputs_raise_value_errors(use_processed):
    from agents.invoice.finance_tools import get_overdue_invoices
    from agents.expense.finance_tools import get_expense_anomalies
    with pytest.raises(ValueError):
        get_overdue_invoices("biz_001", "05/11/2026")
    with pytest.raises(ValueError):
        get_expense_anomalies("biz_001", "2026-11-05", "2026-11-01")
    with pytest.raises(ValueError):
        get_overdue_invoices("  ")


def test_agent_falls_back_gracefully_with_real_tool(use_processed, monkeypatch):
    """With the real tool wired in, an unknown business gives the agent's 'could not calculate' path."""
    import agents.invoice.agent as invoice_agent
    from agents.invoice.finance_tools import get_overdue_invoices
    monkeypatch.setattr(invoice_agent, "get_overdue_invoices", get_overdue_invoices)
    result = invoice_agent.invoice_agent({"business_id": "biz_404"})
    assert "could not" in result.summary.lower()
