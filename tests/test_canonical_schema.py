"""
Tests for the AgentFi canonical data model.
"""

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from finance_engine.schema import (
    CANONICAL_ENTITIES,
    Business,
    Expense,
    ExpenseCategory,
    Invoice,
    InvoiceLineItem,
    Transaction,
    TransactionDirection,
)


def test_business_currency_normalised():
    """Currency codes are upper-cased and validated."""
    biz = Business(business_id="biz_001", name="Acme Traders", base_currency="inr")
    assert biz.base_currency == "INR"

    with pytest.raises(ValidationError):
        Business(business_id="biz_001", name="Acme Traders", base_currency="RUPEES")


def test_transaction_amount_must_be_positive():
    """Amounts are always positive; direction carries the sign."""
    txn = Transaction(
        business_id="biz_001",
        transaction_id="txn_1",
        account_id="acc_1",
        transaction_date=date(2026, 10, 1),
        amount="2500.50",
        direction=TransactionDirection.outflow,
        category=ExpenseCategory.rent,
    )
    assert txn.amount == Decimal("2500.50")

    with pytest.raises(ValidationError):
        Transaction(
            business_id="biz_001",
            transaction_id="txn_2",
            account_id="acc_1",
            transaction_date=date(2026, 10, 1),
            amount="-100",
            direction=TransactionDirection.outflow,
        )


def test_business_id_required():
    """Tenant isolation: every record needs a business_id."""
    with pytest.raises(ValidationError):
        Expense(expense_id="exp_1", expense_date=date(2026, 10, 1), amount="10")


def test_unknown_fields_rejected():
    """Canonical records forbid unexpected columns from raw sources."""
    with pytest.raises(ValidationError):
        Expense(
            business_id="biz_001",
            expense_id="exp_1",
            expense_date=date(2026, 10, 1),
            amount="10",
            random_column="x",
        )


def test_invoice_totals_are_exact():
    """Invoice totals use Decimal arithmetic including tax."""
    invoice = Invoice(
        business_id="biz_001",
        invoice_id="inv_1",
        invoice_number="INV-0001",
        customer_id="cust_1",
        issue_date=date(2026, 10, 1),
        due_date=date(2026, 10, 31),
        line_items=[
            InvoiceLineItem(description="Consulting", quantity="2", unit_price="1000", tax_rate="0.18"),
            InvoiceLineItem(description="Setup fee", unit_price="500"),
        ],
        amount_paid="1000",
    )
    assert invoice.total == Decimal("2860.00")
    assert invoice.balance_due == Decimal("1860.00")


def test_invoice_validation_rules():
    """Due date cannot precede issue date and payment cannot exceed total."""
    base = dict(
        business_id="biz_001",
        invoice_id="inv_2",
        invoice_number="INV-0002",
        customer_id="cust_1",
        line_items=[InvoiceLineItem(description="Item", unit_price="100")],
    )
    with pytest.raises(ValidationError):
        Invoice(**base, issue_date=date(2026, 10, 10), due_date=date(2026, 10, 1))
    with pytest.raises(ValidationError):
        Invoice(**base, issue_date=date(2026, 10, 1), due_date=date(2026, 10, 31), amount_paid="500")


def test_canonical_entity_registry():
    """All processed dataset names map to their models."""
    assert set(CANONICAL_ENTITIES) == {
        "businesses", "accounts", "counterparties", "transactions", "invoices", "expenses",
    }
