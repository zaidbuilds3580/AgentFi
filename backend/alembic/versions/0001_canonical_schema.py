"""canonical schema

Revision ID: 0001_canonical_schema
Revises: 
Create Date: 2026-11-06 23:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '0001_canonical_schema'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. businesses table
    op.create_table(
        'businesses',
        sa.Column('id', postgresql.UUID(as_uuid=True), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('legal_name', sa.String(length=255), nullable=True),
        sa.Column('industry', sa.String(length=100), nullable=True),
        sa.Column('country', sa.String(length=2), server_default='IN', nullable=False),
        sa.Column('currency', sa.String(length=3), server_default='INR', nullable=False),
        sa.Column('timezone', sa.String(length=50), server_default='Asia/Kolkata', nullable=False),
        sa.Column('fiscal_year_start_month', sa.Integer(), server_default='4', nullable=False),
        sa.Column('is_active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint('fiscal_year_start_month BETWEEN 1 AND 12', name='ck_businesses_fiscal_year_start_month'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_businesses'))
    )

    # 2. users table
    op.create_table(
        'users',
        sa.Column('id', postgresql.UUID(as_uuid=True), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('business_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('email', sa.String(length=255), nullable=False),
        sa.Column('password_hash', sa.String(length=255), nullable=False),
        sa.Column('full_name', sa.String(length=255), nullable=True),
        sa.Column('role', sa.String(length=20), server_default='viewer', nullable=False),
        sa.Column('is_active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column('last_login_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("role IN ('owner', 'admin', 'viewer')", name='ck_users_role'),
        sa.ForeignKeyConstraint(['business_id'], ['businesses.id'], name=op.f('fk_users_business_id_businesses'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_users'))
    )
    op.create_index(op.f('ix_users_business_id'), 'users', ['business_id'], unique=False)
    op.execute("CREATE UNIQUE INDEX uq_users_lower_email ON users (lower(email));")

    # 3. accounts table
    op.create_table(
        'accounts',
        sa.Column('id', postgresql.UUID(as_uuid=True), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('business_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('account_type', sa.String(length=20), nullable=False),
        sa.Column('institution_name', sa.String(length=100), nullable=True),
        sa.Column('account_number_last4', sa.String(length=4), nullable=True),
        sa.Column('currency', sa.String(length=3), server_default='INR', nullable=False),
        sa.Column('opening_balance', sa.Numeric(precision=18, scale=2), server_default='0', nullable=False),
        sa.Column('current_balance', sa.Numeric(precision=18, scale=2), server_default='0', nullable=False),
        sa.Column('is_active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("account_type IN ('bank', 'cash', 'credit_card', 'wallet')", name='ck_accounts_account_type'),
        sa.ForeignKeyConstraint(['business_id'], ['businesses.id'], name=op.f('fk_accounts_business_id_businesses'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_accounts')),
        sa.UniqueConstraint('business_id', 'name', name='uq_accounts_business_id_name')
    )
    op.create_index(op.f('ix_accounts_business_id'), 'accounts', ['business_id'], unique=False)

    # 4. customers table
    op.create_table(
        'customers',
        sa.Column('id', postgresql.UUID(as_uuid=True), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('business_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('email', sa.String(length=255), nullable=True),
        sa.Column('phone', sa.String(length=50), nullable=True),
        sa.Column('gst_number', sa.String(length=50), nullable=True),
        sa.Column('payment_terms_days', sa.Integer(), server_default='30', nullable=False),
        sa.Column('is_active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['business_id'], ['businesses.id'], name=op.f('fk_customers_business_id_businesses'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_customers'))
    )
    op.create_index(op.f('ix_customers_business_id'), 'customers', ['business_id'], unique=False)
    op.create_index('ix_customers_business_id_name', 'customers', ['business_id', 'name'], unique=False)

    # 5. vendors table
    op.create_table(
        'vendors',
        sa.Column('id', postgresql.UUID(as_uuid=True), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('business_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('email', sa.String(length=255), nullable=True),
        sa.Column('phone', sa.String(length=50), nullable=True),
        sa.Column('category', sa.String(length=100), nullable=True),
        sa.Column('is_active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['business_id'], ['businesses.id'], name=op.f('fk_vendors_business_id_businesses'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_vendors'))
    )
    op.create_index(op.f('ix_vendors_business_id'), 'vendors', ['business_id'], unique=False)
    op.create_index('ix_vendors_business_id_name', 'vendors', ['business_id', 'name'], unique=False)

    # 6. transactions table
    op.create_table(
        'transactions',
        sa.Column('id', postgresql.UUID(as_uuid=True), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('business_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('account_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('transaction_date', sa.Date(), nullable=False),
        sa.Column('transaction_type', sa.String(length=10), nullable=False),
        sa.Column('amount', sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column('currency', sa.String(length=3), server_default='INR', nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('category', sa.String(length=100), nullable=True),
        sa.Column('counterparty', sa.Text(), nullable=True),
        sa.Column('reference', sa.String(length=100), nullable=True),
        sa.Column('source_dataset', sa.Text(), nullable=True),
        sa.Column('source_record_id', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("transaction_type IN ('inflow', 'outflow')", name='ck_transactions_transaction_type'),
        sa.CheckConstraint('amount > 0', name='ck_transactions_amount_positive'),
        sa.ForeignKeyConstraint(['account_id'], ['accounts.id'], name=op.f('fk_transactions_account_id_accounts'), ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['business_id'], ['businesses.id'], name=op.f('fk_transactions_business_id_businesses'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_transactions'))
    )
    op.create_index(op.f('ix_transactions_account_id'), 'transactions', ['account_id'], unique=False)
    op.create_index(op.f('ix_transactions_business_id'), 'transactions', ['business_id'], unique=False)
    op.create_index('ix_transactions_business_id_date', 'transactions', ['business_id', 'transaction_date'], unique=False)
    op.create_index('ix_transactions_business_id_category', 'transactions', ['business_id', 'category'], unique=False)
    op.create_index('ix_transactions_account_id_date', 'transactions', ['account_id', 'transaction_date'], unique=False)
    op.execute(
        "CREATE UNIQUE INDEX uq_transactions_provenance ON transactions (business_id, source_dataset, source_record_id) "
        "WHERE source_dataset IS NOT NULL AND source_record_id IS NOT NULL;"
    )

    # 7. invoices table
    op.create_table(
        'invoices',
        sa.Column('id', postgresql.UUID(as_uuid=True), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('business_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('customer_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('invoice_number', sa.String(length=100), nullable=False),
        sa.Column('invoice_date', sa.Date(), nullable=False),
        sa.Column('due_date', sa.Date(), nullable=False),
        sa.Column('amount_total', sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column('amount_paid', sa.Numeric(precision=18, scale=2), server_default='0', nullable=False),
        sa.Column('currency', sa.String(length=3), server_default='INR', nullable=False),
        sa.Column('status', sa.String(length=20), server_default='sent', nullable=False),
        sa.Column('payment_date', sa.Date(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('source_dataset', sa.Text(), nullable=True),
        sa.Column('source_record_id', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint('due_date >= invoice_date', name='ck_invoices_due_date_after_invoice_date'),
        sa.CheckConstraint('amount_total >= 0', name='ck_invoices_amount_total_non_negative'),
        sa.CheckConstraint('amount_paid >= 0 AND amount_paid <= amount_total', name='ck_invoices_amount_paid_valid'),
        sa.CheckConstraint("status IN ('draft', 'sent', 'partially_paid', 'paid', 'overdue', 'void')", name='ck_invoices_status'),
        sa.ForeignKeyConstraint(['business_id'], ['businesses.id'], name=op.f('fk_invoices_business_id_businesses'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['customer_id'], ['customers.id'], name=op.f('fk_invoices_customer_id_customers'), ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_invoices')),
        sa.UniqueConstraint('business_id', 'invoice_number', name='uq_invoices_business_id_invoice_number')
    )
    op.create_index(op.f('ix_invoices_business_id'), 'invoices', ['business_id'], unique=False)
    op.create_index(op.f('ix_invoices_customer_id'), 'invoices', ['customer_id'], unique=False)
    op.create_index('ix_invoices_business_id_status_due_date', 'invoices', ['business_id', 'status', 'due_date'], unique=False)
    op.create_index('ix_invoices_business_id_customer_id', 'invoices', ['business_id', 'customer_id'], unique=False)
    op.execute(
        "CREATE UNIQUE INDEX uq_invoices_provenance ON invoices (business_id, source_dataset, source_record_id) "
        "WHERE source_dataset IS NOT NULL AND source_record_id IS NOT NULL;"
    )

    # 8. expenses table
    op.create_table(
        'expenses',
        sa.Column('id', postgresql.UUID(as_uuid=True), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('business_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('vendor_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('account_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('transaction_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('expense_date', sa.Date(), nullable=False),
        sa.Column('category', sa.String(length=100), nullable=False),
        sa.Column('amount', sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column('currency', sa.String(length=3), server_default='INR', nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('payment_method', sa.String(length=20), nullable=True),
        sa.Column('is_recurring', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('source_dataset', sa.Text(), nullable=True),
        sa.Column('source_record_id', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint('amount > 0', name='ck_expenses_amount_positive'),
        sa.CheckConstraint("payment_method IS NULL OR payment_method IN ('cash', 'bank_transfer', 'card', 'upi', 'cheque', 'other')", name='ck_expenses_payment_method'),
        sa.ForeignKeyConstraint(['account_id'], ['accounts.id'], name=op.f('fk_expenses_account_id_accounts'), ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['business_id'], ['businesses.id'], name=op.f('fk_expenses_business_id_businesses'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['transaction_id'], ['transactions.id'], name=op.f('fk_expenses_transaction_id_transactions'), ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['vendor_id'], ['vendors.id'], name=op.f('fk_expenses_vendor_id_vendors'), ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_expenses'))
    )
    op.create_index(op.f('ix_expenses_account_id'), 'expenses', ['account_id'], unique=False)
    op.create_index(op.f('ix_expenses_business_id'), 'expenses', ['business_id'], unique=False)
    op.create_index(op.f('ix_expenses_transaction_id'), 'expenses', ['transaction_id'], unique=False)
    op.create_index(op.f('ix_expenses_vendor_id'), 'expenses', ['vendor_id'], unique=False)
    op.create_index('ix_expenses_business_id_date', 'expenses', ['business_id', 'expense_date'], unique=False)
    op.create_index('ix_expenses_business_id_category', 'expenses', ['business_id', 'category'], unique=False)
    op.create_index('ix_expenses_business_id_vendor_id', 'expenses', ['business_id', 'vendor_id'], unique=False)
    op.execute(
        "CREATE UNIQUE INDEX uq_expenses_provenance ON expenses (business_id, source_dataset, source_record_id) "
        "WHERE source_dataset IS NOT NULL AND source_record_id IS NOT NULL;"
    )

    # Enable Row Level Security (RLS) on all 8 tables with zero public policies.
    # Protects Supabase auto-generated PostgREST HTTP API from exposure.
    tables = ['businesses', 'users', 'accounts', 'customers', 'vendors', 'transactions', 'invoices', 'expenses']
    for t in tables:
        op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY;")


def downgrade() -> None:
    # Drop tables in reverse dependency order
    op.drop_table('expenses')
    op.drop_table('invoices')
    op.drop_table('transactions')
    op.drop_table('vendors')
    op.drop_table('customers')
    op.drop_table('accounts')
    op.drop_table('users')
    op.drop_table('businesses')

