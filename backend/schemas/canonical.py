"""
Pydantic v2 canonical read schemas for SME finance data contracts.
Shared contracts for Frontend (M1), ETL / Finance Engine (M3), and Agents (M4).
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr


class BaseReadSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class BusinessRead(BaseReadSchema):
    id: UUID
    name: str
    legal_name: Optional[str] = None
    industry: Optional[str] = None
    country: str
    currency: str
    timezone: str
    fiscal_year_start_month: int
    is_active: bool
    created_at: datetime
    updated_at: datetime


class UserRead(BaseReadSchema):
    id: UUID
    business_id: UUID
    email: EmailStr
    full_name: Optional[str] = None
    role: str
    is_active: bool
    last_login_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


class AccountRead(BaseReadSchema):
    id: UUID
    business_id: UUID
    name: str
    account_type: str
    institution_name: Optional[str] = None
    account_number_last4: Optional[str] = None
    currency: str
    opening_balance: Decimal
    current_balance: Decimal
    is_active: bool
    created_at: datetime
    updated_at: datetime


class CustomerRead(BaseReadSchema):
    id: UUID
    business_id: UUID
    name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    gst_number: Optional[str] = None
    payment_terms_days: int
    is_active: bool
    created_at: datetime
    updated_at: datetime


class VendorRead(BaseReadSchema):
    id: UUID
    business_id: UUID
    name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    category: Optional[str] = None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class TransactionRead(BaseReadSchema):
    id: UUID
    business_id: UUID
    account_id: Optional[UUID] = None
    transaction_date: date
    transaction_type: str
    amount: Decimal
    currency: str
    description: Optional[str] = None
    category: Optional[str] = None
    counterparty: Optional[str] = None
    reference: Optional[str] = None
    source_dataset: Optional[str] = None
    source_record_id: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class InvoiceRead(BaseReadSchema):
    id: UUID
    business_id: UUID
    customer_id: Optional[UUID] = None
    invoice_number: str
    invoice_date: date
    due_date: date
    amount_total: Decimal
    amount_paid: Decimal
    currency: str
    status: str
    payment_date: Optional[date] = None
    notes: Optional[str] = None
    source_dataset: Optional[str] = None
    source_record_id: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class ExpenseRead(BaseReadSchema):
    id: UUID
    business_id: UUID
    vendor_id: Optional[UUID] = None
    account_id: Optional[UUID] = None
    transaction_id: Optional[UUID] = None
    expense_date: date
    category: str
    amount: Decimal
    currency: str
    description: Optional[str] = None
    payment_method: Optional[str] = None
    is_recurring: bool
    source_dataset: Optional[str] = None
    source_record_id: Optional[str] = None
    created_at: datetime
    updated_at: datetime

