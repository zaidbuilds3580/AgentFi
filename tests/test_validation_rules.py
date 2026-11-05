"""
Tests for single-record rules (canonical.py) and dataset rules (validators.py).
Rule IDs match docs/validation_rules.md.
"""

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

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
from finance_engine.schema.validators import Severity, validate_dataset, validate_rows

BIZ = "biz_001"
TODAY = date(2026, 11, 5)


def txn(**kw):
    base = dict(business_id=BIZ, transaction_id="t1", account_id="acc_1", transaction_date=date(2026, 10, 1),
                amount="100", direction=TransactionDirection.outflow, category=ExpenseCategory.rent)
    base.update(kw)
    return Transaction(**base)


def invoice(**kw):
    base = dict(business_id=BIZ, invoice_id="inv-1", invoice_number="INV-1", customer_id="cust-a",
                issue_date=date(2026, 10, 1), due_date=date(2026, 10, 31),
                line_items=[InvoiceLineItem(description="Work", unit_price="1000", tax_rate="0.18")])
    base.update(kw)
    return Invoice(**base)


def base_dataset(**extra):
    data = {
        "businesses": [Business(business_id=BIZ, name="Acme")],
        "accounts": [Account(business_id=BIZ, account_id="acc_1", name="HDFC")],
        "counterparties": [
            Counterparty(business_id=BIZ, counterparty_id="cust-a", name="A", counterparty_type=CounterpartyType.customer),
            Counterparty(business_id=BIZ, counterparty_id="vend-b", name="B", counterparty_type=CounterpartyType.vendor),
        ],
    }
    data.update(extra)
    return data


# ---------------------------- single-record rules ---------------------------- #

def test_money_max_two_decimals():
    with pytest.raises(ValidationError):
        txn(amount="10.555")


def test_date_floor_rejects_placeholder_dates():
    with pytest.raises(ValidationError):
        txn(transaction_date=date(1900, 1, 1))


def test_category_only_on_outflows():
    with pytest.raises(ValidationError):
        txn(direction=TransactionDirection.inflow)  # category=rent on an inflow


def test_invoice_link_only_on_inflows():
    with pytest.raises(ValidationError):
        txn(invoice_id="inv-1")
    assert txn(direction=TransactionDirection.inflow, category=None, invoice_id="inv-1").invoice_id == "inv-1"


def test_invoice_status_must_match_payment():
    assert invoice(amount_paid="1180", status=InvoiceStatus.paid).balance_due == 0
    with pytest.raises(ValidationError):
        invoice(amount_paid="500", status=InvoiceStatus.paid)
    with pytest.raises(ValidationError):
        invoice(amount_paid="0", status=InvoiceStatus.partially_paid)
    with pytest.raises(ValidationError):
        invoice(amount_paid="1180", status=InvoiceStatus.partially_paid)


def test_line_total_rounded_to_paise():
    item = InvoiceLineItem(description="x", quantity="3", unit_price="33.33", tax_rate="0.18")
    assert item.total == Decimal("117.99")  # 99.99 * 1.18 = 117.9882


def test_expense_rules():
    with pytest.raises(ValidationError):
        Expense(business_id=BIZ, expense_id="e1", expense_date=date(2026, 10, 10),
                due_date=date(2026, 10, 1), amount="10")
    with pytest.raises(ValidationError):
        Expense(business_id=BIZ, expense_id="e1", expense_date=date(2026, 10, 10),
                amount="10", transaction_id="t1", is_paid=False)


def test_email_validated_and_lowercased():
    c = Counterparty(business_id=BIZ, counterparty_id="c", name="C", email="AP@Example.COM")
    assert c.email == "ap@example.com"
    with pytest.raises(ValidationError):
        Counterparty(business_id=BIZ, counterparty_id="c", name="C", email="not-an-email")


def test_validate_rows_reports_bad_rows_and_keeps_good_ones():
    rows = [
        {"business_id": BIZ, "account_id": "acc_1", "name": "HDFC"},
        {"business_id": BIZ, "account_id": "acc_2", "name": "Bad", "currency": "RUPEES"},
    ]
    models, report = validate_rows("accounts", rows)
    assert [m.account_id for m in models] == ["acc_1"]
    assert report.errors[0].rule == "F-00" and report.errors[0].record_id == "acc_2"


# ------------------------------ dataset rules -------------------------------- #

def test_clean_dataset_has_no_issues():
    data = base_dataset(
        transactions=[txn(), txn(transaction_id="t2", direction=TransactionDirection.inflow, category=None,
                                 invoice_id="inv-1", amount="1180")],
        invoices=[invoice(amount_paid="1180", status=InvoiceStatus.paid)],
        expenses=[Expense(business_id=BIZ, expense_id="e1", vendor_id="vend-b", expense_date=date(2026, 10, 1),
                          amount="100", category=ExpenseCategory.rent, is_paid=True, transaction_id="t1")],
    )
    report = validate_dataset(data, today=TODAY)
    assert report.issues == []


def test_duplicate_primary_key_and_source_ref():
    data = base_dataset(transactions=[
        txn(source="hdfc_csv", source_ref="R1"),
        txn(source="hdfc_csv", source_ref="R1"),                         # X-01
        txn(transaction_id="t3", source="hdfc_csv", source_ref="R1"),    # X-03
    ])
    assert {"X-01", "X-03"} <= validate_dataset(data, today=TODAY).rules_hit()


def test_unknown_business_and_broken_references():
    data = base_dataset(
        transactions=[txn(account_id="acc_missing"), txn(transaction_id="t2", business_id="biz_999")],
        invoices=[invoice(customer_id="cust-missing")],
    )
    report = validate_dataset(data, today=TODAY)
    assert {"X-02", "X-04"} <= report.rules_hit()
    assert not report.ok


def test_counterparty_role_mismatch():
    data = base_dataset(invoices=[invoice(customer_id="vend-b")])
    assert "X-05" in validate_dataset(data, today=TODAY).rules_hit()


def test_currency_must_match_account():
    data = base_dataset(transactions=[txn(currency="USD")])
    assert "X-07" in validate_dataset(data, today=TODAY).rules_hit()


def test_expense_paid_by_inflow_is_error():
    data = base_dataset(
        transactions=[txn(direction=TransactionDirection.inflow, category=None)],
        expenses=[Expense(business_id=BIZ, expense_id="e1", expense_date=date(2026, 10, 1), amount="100",
                          is_paid=True, transaction_id="t1")],
    )
    assert "X-08" in validate_dataset(data, today=TODAY).rules_hit()


def test_soft_rules_are_warnings_only():
    data = base_dataset(
        transactions=[
            txn(category=None),                                            # W-02 uncategorised
            txn(transaction_id="t2", category=None),                       # W-03 probable duplicate
            txn(transaction_id="t3", transaction_date=date(2026, 12, 1)),  # W-01 future
        ],
        invoices=[
            invoice(status=InvoiceStatus.sent),                                          # W-04 should be overdue
            invoice(invoice_id="inv-2", amount_paid="1180", status=InvoiceStatus.sent),  # W-05 paid but 'sent'
            invoice(invoice_id="inv-3", currency="USD"),                                 # W-07 FX
        ],
        expenses=[
            Expense(business_id=BIZ, expense_id="e1", expense_date=date(2026, 10, 1), amount="10",
                    is_paid=True),                                         # W-08
            Expense(business_id=BIZ, expense_id="e2", expense_date=date(2026, 10, 1), amount="10",
                    due_date=date(2026, 10, 15)),                          # W-10 unpaid past due
        ],
    )
    report = validate_dataset(data, today=TODAY)
    assert report.ok
    assert {"W-01", "W-02", "W-03", "W-04", "W-05", "W-07", "W-08", "W-10"} <= report.rules_hit()
    assert all(i.severity == Severity.warning for i in report.issues)


def test_invoice_payments_reconcile_with_bank():
    data = base_dataset(
        transactions=[txn(direction=TransactionDirection.inflow, category=None, invoice_id="inv-1", amount="500")],
        invoices=[invoice(amount_paid="1180", status=InvoiceStatus.paid)],
    )
    assert "W-06" in validate_dataset(data, today=TODAY).rules_hit()
