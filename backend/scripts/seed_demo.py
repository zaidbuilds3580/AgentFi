"""
==============================================================================
NOTE: This seed script is strictly for smoke-testing and local UI previews.
It is NOT the project's primary dataset; real-world data and official benchmark
data are managed and loaded exclusively by Member 3's ETL pipeline.
==============================================================================
"""

import os
import sys
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy.orm import Session
from backend.database.session import SessionLocal
from backend.auth.security import hash_password
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

DEMO_DATASET_TAG = "demo_seed"
DEMO_BIZ_NAME = "DEMO - Sample Traders"
DEMO_OWNER_EMAIL = "demo.owner@sampletraders.com"


def seed_demo_data(db: Session) -> None:
    print(f"Checking for existing demo tenant '{DEMO_BIZ_NAME}'...")
    existing_biz = db.query(Business).filter(Business.name == DEMO_BIZ_NAME).first()

    if existing_biz:
        print(f"Idempotency check: Demo business exists ({existing_biz.id}). Removing previous demo seed data...")
        # Cascade will remove users, accounts, customers, vendors, transactions, invoices, expenses
        db.delete(existing_biz)
        db.commit()
        print("Previous demo tenant wiped clean.")

    demo_password = os.getenv("DEMO_USER_PASSWORD", "DemoSecurePassword123!")

    # 1. Create Business
    biz = Business(
        name=DEMO_BIZ_NAME,
        legal_name="Sample Traders Private Limited",
        industry="Wholesale & Distribution",
        country="IN",
        currency="INR",
        timezone="Asia/Kolkata",
        fiscal_year_start_month=4,
        is_active=True,
    )
    db.add(biz)
    db.flush()
    biz_id = biz.id
    print(f"Created demo business: {biz_id}")

    # 2. Create Owner User
    owner = User(
        business_id=biz_id,
        email=DEMO_OWNER_EMAIL,
        password_hash=hash_password(demo_password),
        full_name="Vikram Malhotra",
        role="owner",
        is_active=True,
    )
    db.add(owner)

    # 3. Create Accounts (2)
    acc_bank = Account(
        business_id=biz_id,
        name="Operating Checking Account",
        account_type="bank",
        institution_name="HDFC Bank",
        account_number_last4="4821",
        currency="INR",
        opening_balance=Decimal("250000.00"),
        current_balance=Decimal("415000.00"),
        is_active=True,
    )
    acc_cash = Account(
        business_id=biz_id,
        name="Petty Cash Wallet",
        account_type="cash",
        institution_name="Main Office Safe",
        account_number_last4=None,
        currency="INR",
        opening_balance=Decimal("15000.00"),
        current_balance=Decimal("9500.00"),
        is_active=True,
    )
    db.add_all([acc_bank, acc_cash])
    db.flush()

    # 4. Create Customers (3)
    c1 = Customer(business_id=biz_id, name="Apex Retailers Ltd", email="billing@apexretail.in", phone="+91-9820011223", gst_number="27AAPCA1234F1Z5", payment_terms_days=30)
    c2 = Customer(business_id=biz_id, name="Nexus Distributors", email="accounts@nexusdist.com", phone="+91-9820022334", gst_number="27BBPCA5678F1Z6", payment_terms_days=15)
    c3 = Customer(business_id=biz_id, name="Zenith Stores", email="info@zenithstores.in", phone="+91-9820033445", gst_number="27CCPCA9012F1Z7", payment_terms_days=45)
    db.add_all([c1, c2, c3])
    db.flush()

    # 5. Create Vendors (3)
    v1 = Vendor(business_id=biz_id, name="Office Supplies Co", email="orders@officesupplies.co", phone="+91-9811122334", category="Stationery")
    v2 = Vendor(business_id=biz_id, name="Cloud Hosting Solutions", email="billing@cloudhost.net", phone="+91-9811133445", category="Software")
    v3 = Vendor(business_id=biz_id, name="Prime Logistics", email="ops@primelogistics.in", phone="+91-9811144556", category="Transportation")
    db.add_all([v1, v2, v3])
    db.flush()

    # 6. Create Transactions (10)
    today = date.today()
    tx_data = [
        ("inflow", Decimal("75000.00"), "Invoice #INV-2026-001 settlement", "Customer Payment", "Apex Retailers Ltd", "TXN-101", -20),
        ("inflow", Decimal("120000.00"), "Invoice #INV-2026-002 payment", "Customer Payment", "Nexus Distributors", "TXN-102", -15),
        ("outflow", Decimal("12500.00"), "Quarterly stationery & printing", "Office Expenses", "Office Supplies Co", "TXN-103", -14),
        ("outflow", Decimal("28000.00"), "AWS Server hosting charges", "Software & Cloud", "Cloud Hosting Solutions", "TXN-104", -12),
        ("inflow", Decimal("45000.00"), "Advance payment for upcoming order", "Customer Deposit", "Zenith Stores", "TXN-105", -10),
        ("outflow", Decimal("18500.00"), "Express courier and freight", "Logistics", "Prime Logistics", "TXN-106", -8),
        ("outflow", Decimal("8500.00"), "Staff team lunch & refreshments", "Meals & Entertainment", "Local Caterers", "TXN-107", -6),
        ("inflow", Decimal("95000.00"), "Invoice #INV-2026-004 full settlement", "Customer Payment", "Apex Retailers Ltd", "TXN-108", -4),
        ("outflow", Decimal("42000.00"), "Monthly warehouse lease", "Rent", "Commercial Realty Trust", "TXN-109", -2),
        ("outflow", Decimal("5500.00"), "Broadband and telecom utility", "Utilities", "Telecom Services Ltd", "TXN-110", -1),
    ]

    tx_objects = []
    for i, (ttype, amt, desc, cat, cpty, ref, days_ago) in enumerate(tx_data, start=1):
        tx = Transaction(
            business_id=biz_id,
            account_id=acc_bank.id,
            transaction_date=today + timedelta(days=days_ago),
            transaction_type=ttype,
            amount=amt,
            currency="INR",
            description=desc,
            category=cat,
            counterparty=cpty,
            reference=ref,
            source_dataset=DEMO_DATASET_TAG,
            source_record_id=f"demo_txn_{i:03d}",
        )
        tx_objects.append(tx)
    db.add_all(tx_objects)
    db.flush()

    # 7. Create Invoices (5: mix of paid, partially_paid, sent, overdue, draft)
    inv_data = [
        ("INV-2026-001", c1.id, -25, -5, Decimal("75000.00"), Decimal("75000.00"), "paid", today - timedelta(days=5)),
        ("INV-2026-002", c2.id, -20, -5, Decimal("140000.00"), Decimal("120000.00"), "partially_paid", today - timedelta(days=5)),
        ("INV-2026-003", c3.id, -15, 15, Decimal("65000.00"), Decimal("0.00"), "sent", None),
        ("INV-2026-004", c1.id, -30, -2, Decimal("95000.00"), Decimal("95000.00"), "paid", today - timedelta(days=4)),
        ("INV-2026-005", c2.id, -40, -10, Decimal("88000.00"), Decimal("0.00"), "overdue", None),
    ]

    for i, (inv_no, cust_id, inv_offset, due_offset, total, paid, status, pay_date) in enumerate(inv_data, start=1):
        inv = Invoice(
            business_id=biz_id,
            customer_id=cust_id,
            invoice_number=inv_no,
            invoice_date=today + timedelta(days=inv_offset),
            due_date=today + timedelta(days=due_offset),
            amount_total=total,
            amount_paid=paid,
            currency="INR",
            status=status,
            payment_date=pay_date,
            notes=f"Demo invoice record #{i}",
            source_dataset=DEMO_DATASET_TAG,
            source_record_id=f"demo_inv_{i:03d}",
        )
        db.add(inv)

    # 8. Create Expenses (8)
    exp_data = [
        (v1.id, acc_bank.id, -14, "Stationery", Decimal("12500.00"), "Office stationery replenishment", "bank_transfer", False),
        (v2.id, acc_bank.id, -12, "Software", Decimal("28000.00"), "AWS Server & Database Hosting", "card", True),
        (v3.id, acc_bank.id, -8, "Logistics", Decimal("18500.00"), "Regional dispatch courier", "bank_transfer", False),
        (None, acc_cash.id, -6, "Meals & Entertainment", Decimal("8500.00"), "Staff catering and lunch", "cash", False),
        (None, acc_bank.id, -2, "Rent", Decimal("42000.00"), "Warehouse and depot rental", "bank_transfer", True),
        (None, acc_bank.id, -1, "Utilities", Decimal("5500.00"), "Office internet and phones", "upi", True),
        (v1.id, acc_cash.id, -3, "Office Supplies", Decimal("3200.00"), "Printer cartridges & paper", "cash", False),
        (v2.id, acc_bank.id, -5, "Software", Decimal("14000.00"), "Slack and Google Workspace licenses", "card", True),
    ]

    for i, (ven_id, acc_id, exp_offset, cat, amt, desc, pmethod, rec) in enumerate(exp_data, start=1):
        exp = Expense(
            business_id=biz_id,
            vendor_id=ven_id,
            account_id=acc_id,
            expense_date=today + timedelta(days=exp_offset),
            category=cat,
            amount=amt,
            currency="INR",
            description=desc,
            payment_method=pmethod,
            is_recurring=rec,
            source_dataset=DEMO_DATASET_TAG,
            source_record_id=f"demo_exp_{i:03d}",
        )
        db.add(exp)

    db.commit()
    print("Demo dataset seeded successfully!")
    print(f"Summary: 1 Business, 1 Owner User ({DEMO_OWNER_EMAIL}), 2 Accounts, 3 Customers, 3 Vendors, 10 Transactions, 5 Invoices, 8 Expenses.")


if __name__ == "__main__":
    db = SessionLocal()
    try:
        seed_demo_data(db)
    finally:
        db.close()

