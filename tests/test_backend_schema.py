"""
Unit and integration tests for canonical database schema, constraints, RLS, and Pydantic DTOs.
"""

from datetime import date, datetime, timezone
from decimal import Decimal
import uuid
import pytest
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from backend.core.config import settings
from backend.database.session import SessionLocal
from backend.models import (
    Business,
    User,
    Account,
    Customer,
    Vendor,
    Transaction,
    Invoice,
    Expense,
)
from backend.models.constants import (
    ACCOUNT_TYPES,
    INVOICE_STATUSES,
    PAYMENT_METHODS,
    TRANSACTION_TYPES,
    USER_ROLES,
)
from backend.schemas import (
    AccountRead,
    BusinessRead,
    CustomerRead,
    ExpenseRead,
    InvoiceRead,
    RegisterRequest,
    TransactionRead,
    UserRead,
    VendorRead,
)

# ==============================================================================
# UNIT TESTS (Zero database dependency)
# ==============================================================================

def test_constants_definitions():
    """Verify core domain enumeration constants."""
    assert "owner" in USER_ROLES
    assert "admin" in USER_ROLES
    assert "viewer" in USER_ROLES

    assert "bank" in ACCOUNT_TYPES
    assert "cash" in ACCOUNT_TYPES

    assert "inflow" in TRANSACTION_TYPES
    assert "outflow" in TRANSACTION_TYPES

    assert "sent" in INVOICE_STATUSES
    assert "overdue" in INVOICE_STATUSES
    assert "paid" in INVOICE_STATUSES

    assert "upi" in PAYMENT_METHODS
    assert "bank_transfer" in PAYMENT_METHODS


def test_pydantic_schema_from_attributes():
    """Verify Pydantic read schemas deserialize attributes accurately with Decimal & date types."""
    now = datetime.now(timezone.utc)
    biz_id = uuid.uuid4()

    # 1. BusinessRead
    biz_dto = BusinessRead(
        id=biz_id,
        name="Test SME",
        legal_name="Test SME Pvt Ltd",
        industry="Retail",
        country="IN",
        currency="INR",
        timezone="Asia/Kolkata",
        fiscal_year_start_month=4,
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    assert biz_dto.name == "Test SME"
    assert biz_dto.currency == "INR"

    # 2. AccountRead
    acc_dto = AccountRead(
        id=uuid.uuid4(),
        business_id=biz_id,
        name="Main Account",
        account_type="bank",
        institution_name="SBI",
        account_number_last4="1234",
        currency="INR",
        opening_balance=Decimal("1000.50"),
        current_balance=Decimal("2500.75"),
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    assert isinstance(acc_dto.current_balance, Decimal)
    assert acc_dto.account_number_last4 == "1234"

    # 3. TransactionRead
    tx_dto = TransactionRead(
        id=uuid.uuid4(),
        business_id=biz_id,
        account_id=None,
        transaction_date=date.today(),
        transaction_type="inflow",
        amount=Decimal("50000.00"),
        currency="INR",
        description="Client payment",
        category="Sales",
        counterparty="Client A",
        reference="REF-01",
        source_dataset="sample_data",
        source_record_id="rec_01",
        created_at=now,
        updated_at=now,
    )
    assert tx_dto.transaction_type == "inflow"
    assert tx_dto.amount == Decimal("50000.00")


def test_register_request_password_validation():
    """Verify RegisterRequest enforces minimum 8-character password constraint."""
    # Valid registration
    req = RegisterRequest(
        email="valid@example.com",
        password="PassWord123!",
        business_name="My Company",
    )
    assert len(req.password) >= 8

    # Short password rejected
    with pytest.raises(ValidationError):
        RegisterRequest(
            email="invalid@example.com",
            password="short",
            business_name="My Company",
        )


# ==============================================================================
# INTEGRATION TESTS (Run against Supabase PostgreSQL when DATABASE_URL is set)
# ==============================================================================

@pytest.fixture
def db_session():
    """Yield a database session and rollback all operations at teardown."""
    if "placeholder" in settings.DATABASE_URL.lower():
        pytest.skip("Supabase DATABASE_URL is not configured (contains placeholder)")

    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.mark.integration
def test_all_8_tables_and_rls_enabled(db_session):
    """Verify all 8 tables exist in public schema and have Row Level Security enabled."""
    expected_tables = [
        "businesses",
        "users",
        "accounts",
        "customers",
        "vendors",
        "transactions",
        "invoices",
        "expenses",
    ]

    for table_name in expected_tables:
        query = text(
            "SELECT relrowsecurity FROM pg_class WHERE relname = :table_name"
        )
        result = db_session.execute(query, {"table_name": table_name}).scalar()
        assert result is not None, f"Table {table_name} does not exist in database"
        assert result is True, f"RLS is NOT enabled on table {table_name}"


@pytest.mark.integration
def test_duplicate_email_case_insensitive_rejected(db_session):
    """Verify lower(email) unique index prevents duplicate registrations regardless of casing."""
    biz = Business(name="Duplicate Test Biz")
    db_session.add(biz)
    db_session.flush()

    u1 = User(
        business_id=biz.id,
        email="Unique.Person@Example.com",
        password_hash="hashed_pw_1",
        role="owner",
    )
    db_session.add(u1)
    db_session.commit()

    # Attempt inserting same email in lowercase
    u2 = User(
        business_id=biz.id,
        email="unique.person@example.com",
        password_hash="hashed_pw_2",
        role="viewer",
    )
    db_session.add(u2)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


@pytest.mark.integration
def test_negative_amounts_rejected_by_check_constraint(db_session):
    """Verify check constraint rejects amount <= 0 on transactions and expenses."""
    biz = Business(name="Amount Constraint Biz")
    db_session.add(biz)
    db_session.flush()

    # 1. Transaction with zero amount
    tx_zero = Transaction(
        business_id=biz.id,
        transaction_date=date.today(),
        transaction_type="inflow",
        amount=Decimal("0.00"),
    )
    db_session.add(tx_zero)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    # 2. Expense with negative amount
    exp_negative = Expense(
        business_id=biz.id,
        expense_date=date.today(),
        category="Office",
        amount=Decimal("-150.00"),
    )
    db_session.add(exp_negative)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


@pytest.mark.integration
def test_invoice_date_and_paid_constraints(db_session):
    """Verify check constraints: due_date >= invoice_date and amount_paid <= amount_total."""
    biz = Business(name="Invoice Constraints Biz")
    db_session.add(biz)
    db_session.flush()

    # 1. due_date before invoice_date rejected
    inv_bad_date = Invoice(
        business_id=biz.id,
        invoice_number="INV-BAD-01",
        invoice_date=date(2026, 5, 20),
        due_date=date(2026, 5, 10),
        amount_total=Decimal("1000.00"),
    )
    db_session.add(inv_bad_date)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    # 2. amount_paid > amount_total rejected
    inv_overpaid = Invoice(
        business_id=biz.id,
        invoice_number="INV-OVER-01",
        invoice_date=date(2026, 5, 10),
        due_date=date(2026, 5, 20),
        amount_total=Decimal("1000.00"),
        amount_paid=Decimal("1500.00"),
    )
    db_session.add(inv_overpaid)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


@pytest.mark.integration
def test_provenance_deduplication_partial_index(db_session):
    """Verify partial unique index prevents duplicate (source_dataset, source_record_id) while allowing NULLs."""
    biz = Business(name="Provenance Test Biz")
    db_session.add(biz)
    db_session.flush()

    # 1. First import row succeeds
    tx1 = Transaction(
        business_id=biz.id,
        transaction_date=date.today(),
        transaction_type="inflow",
        amount=Decimal("100.00"),
        source_dataset="csv_upload_01",
        source_record_id="row_42",
    )
    db_session.add(tx1)
    db_session.commit()

    # 2. Duplicate provenance row for same business fails
    tx2 = Transaction(
        business_id=biz.id,
        transaction_date=date.today(),
        transaction_type="outflow",
        amount=Decimal("200.00"),
        source_dataset="csv_upload_01",
        source_record_id="row_42",
    )
    db_session.add(tx2)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    # 3. Multiple rows with NULL source_dataset/record_id are permitted
    tx3 = Transaction(
        business_id=biz.id,
        transaction_date=date.today(),
        transaction_type="inflow",
        amount=Decimal("300.00"),
        source_dataset=None,
        source_record_id=None,
    )
    tx4 = Transaction(
        business_id=biz.id,
        transaction_date=date.today(),
        transaction_type="inflow",
        amount=Decimal("400.00"),
        source_dataset=None,
        source_record_id=None,
    )
    db_session.add_all([tx3, tx4])
    db_session.commit()


@pytest.mark.integration
def test_business_cascade_delete(db_session):
    """Verify deleting a business automatically cascades and deletes related entities."""
    biz = Business(name="Cascade Test Biz")
    db_session.add(biz)
    db_session.flush()

    user = User(business_id=biz.id, email="cascade.user@test.local", password_hash="hash", role="viewer")
    acc = Account(business_id=biz.id, name="Cascade Account", account_type="cash")
    db_session.add_all([user, acc])
    db_session.commit()

    # Delete business
    db_session.delete(biz)
    db_session.commit()

    # Assert user and account are gone
    assert db_session.query(User).filter(User.id == user.id).first() is None
    assert db_session.query(Account).filter(Account.id == acc.id).first() is None
