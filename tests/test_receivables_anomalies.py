"""
Day-5 tests: invoice aging, payment behaviour, overdue detection, and the expense anomaly
rules added on top of the Day-4 detector (robust z-score, A6 recurring change, A7 missing
recurring payment, A8 new payee, month-based A3, payee inference).
"""

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from finance_engine.anomalies import detect_expense_anomalies, narration_payee_key, robust_z
from finance_engine.data import FinanceData
from finance_engine.etl import run_business
from finance_engine.receivables import (
    aging_bucket,
    aging_report,
    derive_invoice_status,
    detect_overdue,
    payment_behaviour,
    payment_history,
)
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
)
from finance_engine import tools

SAMPLES = Path(__file__).resolve().parents[1] / "data" / "samples" / "biz_001"
AS_OF = date(2026, 11, 5)
D = Decimal


def inv(iid, due, total, paid="0", status="sent", customer="cust-a", issue=None, currency="INR"):
    return Invoice(business_id="b", invoice_id=iid, invoice_number=iid.upper(), customer_id=customer,
                   issue_date=issue or (due - timedelta(days=30)), due_date=due, status=status,
                   amount_paid=paid, currency=currency,
                   line_items=[InvoiceLineItem(description="work", unit_price=total, tax_rate="0")])


def txn(tid, day, amount, direction="outflow", category=None, desc="", invoice_id=None):
    return Transaction(business_id="b", transaction_id=tid, account_id="acc", transaction_date=day,
                       amount=amount, direction=direction, category=category, description=desc,
                       invoice_id=invoice_id)


def make(invoices=(), transactions=(), expenses=()):
    return FinanceData.from_datasets({
        "businesses": [Business(business_id="b", name="B")],
        "accounts": [Account(business_id="b", account_id="acc", name="Main", opening_balance="1000000")],
        "counterparties": [
            Counterparty(business_id="b", counterparty_id=c, name=c.upper(), counterparty_type=CounterpartyType.customer)
            for c in ("cust-a", "cust-b", "cust-c", "cust-d")
        ] + [Counterparty(business_id="b", counterparty_id="vend-x", name="Vendor X",
                          counterparty_type=CounterpartyType.vendor)],
        "invoices": list(invoices), "transactions": list(transactions), "expenses": list(expenses),
    }, "b")


@pytest.fixture(scope="module")
def sample():
    return FinanceData.from_datasets(run_business(SAMPLES, today=AS_OF).datasets, "biz_001")


# ============================================================================== #
# aging & payment behaviour
# ============================================================================== #

@pytest.mark.parametrize("days, bucket", [
    (-10, "current"), (0, "current"), (1, "1-30"), (30, "1-30"), (31, "31-60"),
    (60, "31-60"), (61, "61-90"), (90, "61-90"), (91, "90+"), (400, "90+"),
])
def test_aging_bucket_edges(days, bucket):
    assert aging_bucket(days) == bucket


def test_settlement_date_uses_cumulative_payments():
    data = make(
        invoices=[inv("i1", date(2026, 9, 30), "1000", paid="1000", status="paid"),
                  inv("i2", date(2026, 9, 30), "500", paid="500", status="paid")],
        transactions=[txn("p1", date(2026, 9, 20), "400", "inflow", invoice_id="i1"),
                      txn("p2", date(2026, 10, 10), "600", "inflow", invoice_id="i1"),   # completes i1
                      txn("p3", date(2026, 9, 25), "500", "inflow", invoice_id="i2")],   # early
    )
    hist = {s.invoice_id: s for s in payment_history(data)}
    assert (hist["i1"].settled_on, hist["i1"].days_late, hist["i1"].payment_refs) == (date(2026, 10, 10), 10, ["p1", "p2"])
    assert hist["i2"].days_late == -5


def _settled(customer, prefix, days_late_list):
    invoices, txns = [], []
    for n, late in enumerate(days_late_list):
        due = date(2026, 6, 1) + timedelta(days=30 * n)
        iid = f"{prefix}{n}"
        invoices.append(inv(iid, due, "100", paid="100", status="paid", customer=customer))
        txns.append(txn(f"t-{iid}", due + timedelta(days=late), "100", "inflow", invoice_id=iid))
    return invoices, txns


def test_payment_behaviour_classification():
    ia, ta = _settled("cust-a", "a", [0, 2, -1, 5])        # avg 1.5, 75% within 3 days -> reliable
    ib, tb = _settled("cust-b", "b", [20, 25, 10])         # avg 18.3 -> slow
    ic, tc = _settled("cust-c", "c", [40, 50])             # avg 45 -> unreliable
    data = make(invoices=ia + ib + ic + [inv("d0", date(2026, 10, 1), "100", customer="cust-d")],
                transactions=ta + tb + tc)
    pb = {b.customer_id: b for b in payment_behaviour(data)}
    assert (pb["cust-a"].reliability, pb["cust-a"].avg_days_late, pb["cust-a"].on_time_rate) == ("reliable", D("1.5"), D("0.7500"))
    assert (pb["cust-b"].reliability, pb["cust-b"].avg_days_late, pb["cust-b"].max_days_late) == ("slow", D("18.3"), 25)
    assert pb["cust-c"].reliability == "unreliable"
    assert (pb["cust-d"].reliability, pb["cust-d"].invoices_settled) == ("unknown", 0)


def test_aging_report_by_customer_and_dso():
    data = make(invoices=[
        inv("a1", AS_OF + timedelta(days=5), "1000", issue=AS_OF - timedelta(days=10)),            # current
        inv("a2", AS_OF - timedelta(days=10), "2000", paid="500", status="partially_paid",
            issue=AS_OF - timedelta(days=40)),                                                       # 1-30: 1500
        inv("b1", AS_OF - timedelta(days=45), "3000", customer="cust-b",
            issue=AS_OF - timedelta(days=75)),                                                       # 31-60
        inv("b2", AS_OF - timedelta(days=100), "500", customer="cust-b",
            issue=AS_OF - timedelta(days=130)),                                                      # 90+, billed before window
        inv("usd", AS_OF - timedelta(days=5), "999", currency="USD"),
    ])
    r = aging_report(data, AS_OF)
    by = {c.customer_id: c for c in r.customers}
    assert by["cust-b"].buckets == {"current": D("0.00"), "1-30": D("0.00"), "31-60": D("3000.00"),
                                    "61-90": D("0.00"), "90+": D("500.00")}
    assert (by["cust-a"].total, by["cust-a"].overdue, by["cust-a"].oldest_days_overdue) == (D("2500.00"), D("1500.00"), 10)
    assert [c.customer_id for c in r.customers] == ["cust-b", "cust-a"]          # largest balance first
    assert sum(r.totals.values()) == r.total_outstanding == D("6000.00")
    assert r.total_overdue == D("5000.00") and r.overdue_share == D("0.8333")
    # weighted days overdue: (1500*10 + 3000*45 + 500*100) / 5000 = 40.0
    assert r.weighted_avg_days_overdue == D("40.0")
    # DSO: 6000 outstanding / 6000 billed in the last 90 days (a1, a2, b1) * 90
    assert r.dso == D("90.0")
    assert any("USD" in w for w in r.warnings)


def test_dso_none_without_recent_billing():
    r = aging_report(make(invoices=[inv("old", date(2026, 1, 31), "100", issue=date(2026, 1, 1))]), AS_OF)
    assert r.dso is None and any("DSO not available" in n for n in r.notes)


def test_sample_aging(sample):
    r = aging_report(sample, AS_OF)
    assert (r.total_outstanding, r.total_overdue, r.dso) == (D("64460.00"), D("50300.00"), D("22.7"))
    rel = {b.customer_name: b.reliability for b in r.payment_behaviour}
    assert rel["BrightPath Learning"] == "reliable"
    assert rel["Greenleaf Organics Pvt Ltd"] == "slow"           # pays ~22 days late on average
    assert r.customers[0].customer_name == "Greenleaf Organics Pvt Ltd"


# ============================================================================== #
# overdue detection
# ============================================================================== #

@pytest.mark.parametrize("days, stage", [
    (1, "gentle_reminder"), (7, "gentle_reminder"), (8, "firm_reminder"), (30, "firm_reminder"),
    (31, "final_notice"), (60, "final_notice"), (61, "escalate"), (200, "escalate"),
])
def test_reminder_stages(days, stage):
    filler = inv("big", AS_OF + timedelta(days=60), "100000")          # keeps each invoice a small share of AR
    r = detect_overdue(make(invoices=[inv("x", AS_OF - timedelta(days=days), "100"), filler]), AS_OF)
    assert [(i.invoice_id, i.stage) for i in r.overdue] == [("x", stage)]


def test_due_soon_and_grace_period():
    data = make(invoices=[
        inv("today", AS_OF, "100"), inv("in3", AS_OF + timedelta(days=3), "100"),
        inv("in4", AS_OF + timedelta(days=4), "100"), inv("late2", AS_OF - timedelta(days=2), "100"),
    ])
    r = detect_overdue(data, AS_OF)
    assert [i.invoice_id for i in r.due_soon] == ["today", "in3"]
    assert [i.invoice_id for i in r.overdue] == ["late2"]
    g = detect_overdue(data, AS_OF, grace_days=5)
    assert [i.invoice_id for i in g.overdue] == []                       # still inside the grace period
    assert "late2" in [i.invoice_id for i in g.due_soon]
    with pytest.raises(ValueError):
        detect_overdue(data, AS_OF, grace_days=-1)


def test_priority_rules_and_order():
    ib, tb = _settled("cust-b", "h", [20, 25, 10])                      # cust-b is a slow payer
    data = make(invoices=ib + [
        inv("old", AS_OF - timedelta(days=70), "100"),                   # critical: > 60 days
        inv("huge", AS_OF - timedelta(days=5), "9000"),                  # critical: >= 25% of AR
        inv("mid", AS_OF - timedelta(days=40), "100"),                   # high: > 30 days
        inv("slowpayer", AS_OF - timedelta(days=3), "100", customer="cust-b"),  # high: slow payer
        inv("small", AS_OF - timedelta(days=3), "100"),                  # normal
        inv("future", AS_OF + timedelta(days=90), "20000", issue=AS_OF - timedelta(days=1)),  # not overdue, in AR
    ], transactions=tb)
    r = detect_overdue(data, AS_OF)
    assert [(i.invoice_id, i.priority) for i in r.overdue] == [
        ("old", "critical"), ("huge", "critical"), ("mid", "high"), ("slowpayer", "high"), ("small", "normal")]
    assert r.overdue[1].priority_reasons == ["31% of outstanding receivables"]
    assert r.overdue[3].priority_reasons == ["customer is a slow payer"]
    assert r.total_overdue == D("9400.00")


def test_status_corrections():
    data = make(invoices=[
        inv("stale", AS_OF - timedelta(days=10), "100", status="sent"),                       # -> overdue
        inv("paidoff", AS_OF - timedelta(days=10), "100", paid="100", status="overdue"),       # -> paid
        inv("early", AS_OF + timedelta(days=10), "100", status="overdue"),                     # -> sent
        inv("ok", AS_OF - timedelta(days=10), "100", status="overdue"),
        inv("draft", AS_OF - timedelta(days=10), "100", status="draft"),
    ])
    r = detect_overdue(data, AS_OF)
    assert {(c.invoice_id, c.recorded_status, c.derived_status) for c in r.status_corrections} == {
        ("stale", "sent", "overdue"), ("paidoff", "overdue", "paid"), ("early", "overdue", "sent")}
    assert derive_invoice_status(data.invoices[0], AS_OF) in InvoiceStatus


def test_overdue_bills():
    data = make(expenses=[
        Expense(business_id="b", expense_id="e1", vendor_id="vend-x", expense_date=date(2026, 10, 1),
                due_date=date(2026, 10, 20), amount="500", category=ExpenseCategory.rent),
        Expense(business_id="b", expense_id="e2", expense_date=date(2026, 10, 1), due_date=date(2026, 11, 20),
                amount="50"),                                                                  # not due yet
        Expense(business_id="b", expense_id="e3", expense_date=date(2026, 10, 1), due_date=date(2026, 10, 2),
                amount="70", is_paid=True),                                                    # paid
    ])
    r = detect_overdue(data, AS_OF)
    assert [(b.expense_id, b.vendor_name, b.days_overdue) for b in r.overdue_bills] == [("e1", "Vendor X", 16)]
    assert r.total_bills_overdue == D("500.00")


def test_sample_overdue(sample):
    r = detect_overdue(sample, AS_OF)
    assert [(i.invoice_number, i.stage, i.priority) for i in r.overdue] == [
        ("INV-1002", "firm_reminder", "critical"), ("INV-1003", "gentle_reminder", "critical")]
    assert r.overdue[0].partially_paid and r.overdue[0].customer_reliability == "slow"
    assert [(c.invoice_number, c.derived_status) for c in r.status_corrections] == [("INV-1003", "overdue")]
    assert [b.vendor_name for b in r.overdue_bills] == ["Mehta & Associates"]


# ============================================================================== #
# expense anomalies: new rules
# ============================================================================== #

def monthly(prefix, amounts, start=date(2026, 6, 3), desc=None, category=ExpenseCategory.software, gap=30):
    return [txn(f"{prefix}{n}", start + timedelta(days=gap * n), a, category=category,
                desc=desc or f"ACH {prefix.upper()} REF{n}9{n}") for n, a in enumerate(amounts)]


def oct_window(data):
    return detect_expense_anomalies(data, date(2026, 10, 1), date(2026, 10, 31))


def rules(report):
    return sorted(a.rule_id for a in report.anomalies)


def test_narration_payee_key_ignores_refs_and_months():
    assert narration_payee_key("IMPS-627512345678-ORANGE REALTY LLP-OFFICE RENT OCT") == \
        narration_payee_key("IMPS-630612345678-ORANGE REALTY LLP-OFFICE RENT NOV")
    assert narration_payee_key("SALARY OCT26 BULK UPLOAD") == narration_payee_key("SALARY SEPTEMBER BULK UPLOAD")
    assert narration_payee_key("AWS") != narration_payee_key("GCP")


def test_robust_z():
    assert robust_z(D("1600"), [D("1000"), D("1010"), D("990"), D("1005"), D("995")]) == D("80.94")
    assert robust_z(D("5"), [D("1"), D("1"), D("1")]) is None          # MAD 0


def test_a1_robust_z_catches_moderate_jump_on_stable_payee():
    # 1.6x is below the 2x ratio rule, but far outside a very stable history
    data = make(transactions=monthly("sub", ["1000", "1010", "990", "1005", "1600"], gap=30))
    a = [x for x in oct_window(data).anomalies if x.rule_id == "A1"]
    assert len(a) == 1 and a[0].severity == "low" and "robust z-score" in a[0].rule


def test_a6_recurring_change_both_directions():
    up = make(transactions=monthly("rent", ["1000", "1000", "1000", "1000", "1250"]))
    down = make(transactions=monthly("rent", ["1000", "1000", "1000", "1000", "600"]))
    small = make(transactions=monthly("rent", ["1000", "1000", "1000", "1000", "1100"]))
    a_up = [x for x in oct_window(up).anomalies if x.rule_id == "A6"]
    assert len(a_up) == 1 and a_up[0].severity == "low" and "25% higher" in a_up[0].rule
    assert a_up[0].expected_baseline == D("1000.00")
    a_down = [x for x in oct_window(down).anomalies if x.rule_id == "A6"]
    assert a_down[0].severity == "medium" and "40% lower" in a_down[0].rule
    assert "A6" not in rules(oct_window(small))                          # 10% change is normal


def test_a6_ignores_irregular_payees():
    data = make(transactions=monthly("cab", ["300", "900", "450", "700", "1200"]))
    assert "A6" not in rules(oct_window(data))


def test_a7_missing_recurring_payment():
    hist = monthly("isp", ["500", "500", "500", "500"], start=date(2026, 6, 4))   # Jun 4 .. Sep 1
    covered = make(transactions=hist + [txn("other", date(2026, 10, 30), "10", category=ExpenseCategory.travel,
                                            desc="CAB")])
    missing = [x for x in oct_window(covered).anomalies if x.rule_id == "A7"]
    assert len(missing) == 1 and missing[0].amount == D("0.00") and missing[0].expected_baseline == D("500.00")
    assert "2026-10-02" in missing[0].rule          # Sep 2 + usual 30-day interval
    # paid 5 days late -> within tolerance, not missing
    paid = make(transactions=hist + [txn("isp4", date(2026, 10, 6), "500", category=ExpenseCategory.software,
                                         desc="ACH ISP REF4949")])
    assert "A7" not in rules(oct_window(paid))
    # bank data ends before the expected date + tolerance -> cannot tell yet
    assert "A7" not in rules(oct_window(make(transactions=hist)))


def test_a8_new_payee_large_payment():
    hist = monthly("aws", ["1000", "1000", "1000", "1000"], start=date(2026, 6, 3))
    data = make(transactions=hist + [txn("new", date(2026, 10, 15), "9000", category=ExpenseCategory.marketing,
                                         desc="NEFT DR-SHINY ADS AGENCY")])
    a8 = [x for x in oct_window(data).anomalies if x.rule_id == "A8"]
    assert len(a8) == 1 and a8[0].severity == "medium" and a8[0].expected_baseline == D("1000.00")
    small = make(transactions=hist + [txn("new", date(2026, 10, 15), "1500", desc="NEFT DR-SHINY ADS AGENCY",
                                          category=ExpenseCategory.marketing)])
    assert "A8" not in rules(oct_window(small))                          # below 5,000
    short = make(transactions=[txn("new", date(2026, 10, 15), "9000", desc="NEW")])
    assert any("needs 60 days" in n for n in oct_window(short).notes)


def test_a3_category_spike_from_many_new_payees():
    hist = []
    for m in (7, 8, 9):
        hist.append(txn(f"h{m}", date(2026, m, 10), "1000", category=ExpenseCategory.supplies, desc=f"SHOP{m}A"))
    october = [txn(f"o{n}", date(2026, 10, 2 + n), "1000", category=ExpenseCategory.supplies, desc=f"STORE {chr(65 + n)}")
               for n in range(5)]
    october.append(txn("eom", date(2026, 10, 31), "10", category=ExpenseCategory.travel, desc="CAB"))  # data covers Oct
    a3 = [x for x in oct_window(make(transactions=hist + october)).anomalies if x.rule_id == "A3"]
    assert len(a3) == 1
    assert (a3[0].category, a3[0].vendor, a3[0].amount, a3[0].expected_baseline, a3[0].severity) == (
        "Supplies", "Various", D("5000.00"), D("1000.00"), "high")


def test_a3_only_compares_complete_months():
    hist = [txn(f"r{m}", date(2026, m, 2), "45000", category=ExpenseCategory.rent, desc="RENT") for m in (7, 8, 9, 10, 11)]
    r = detect_expense_anomalies(make(transactions=hist), date(2026, 10, 1), date(2026, 11, 4))
    assert "A3" not in rules(r)            # two rent payments in a 35-day window are not a spike


def test_vendor_inferred_from_linked_narration():
    """Older bank lines without a linked bill still count as the same vendor."""
    hist = monthly("rent", ["1000", "1000", "1000"], start=date(2026, 7, 2), desc="IMPS ORANGE REALTY RENT")
    oct_txn = txn("rent-oct", date(2026, 10, 2), "1000", category=ExpenseCategory.software, desc="IMPS ORANGE REALTY RENT")
    bill = Expense(business_id="b", expense_id="e-oct", vendor_id="vend-x", expense_date=date(2026, 10, 2),
                   amount="1000", category=ExpenseCategory.software, is_paid=True, transaction_id="rent-oct")
    r = oct_window(make(transactions=hist + [oct_txn], expenses=[bill]))
    assert [a.rule_id for a in r.anomalies] == []         # no false "new payee" / "missing payment"


def test_sample_october_anomalies(sample):
    r = oct_window(sample)
    summary = [(a.rule_id, a.vendor, a.severity) for a in r.anomalies]
    assert summary == [
        ("A1", "Amazon Web Services", "medium"),
        ("A6", "GST PMT CBDT-OCT26 CPIN 26101234567", "low"),
        ("A5", "MISC DR 5531", "low"),
        ("A7", "Airtel Business", "low"),
    ]


# ============================================================================== #
# tools
# ============================================================================== #

def test_new_tools(sample):
    aging = tools.get_aging_report("biz_001", "2026-11-05", data=sample)
    assert aging.dso == D("22.7") and aging.calculation_version == "finance-engine-1.1.0"
    od = tools.get_overdue_report("biz_001", "2026-11-05", data=sample)
    assert od.total_overdue == D("50300.00") and od.total_bills_overdue == D("18000.00")
    assert od.model_dump(mode="json")["overdue"][0]["stage"] == "firm_reminder"
