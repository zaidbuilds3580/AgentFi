"""
Canonical data model for AgentFi.

Every raw source (bank CSV exports, accounting tool dumps, invoice spreadsheets) is
normalised by the ETL layer into these models before it reaches data/processed/.
The deterministic finance engine and agent tools consume ONLY these models.

CORE RULES:
1. Every record carries 'business_id' for tenant isolation.
2. Monetary amounts use Decimal (never float) so finance calculations are exact.
3. Amounts are always positive; direction is expressed by 'direction' (inflow/outflow).
4. Currency codes are ISO 4217 (3 uppercase letters).

Validation is layered (see docs/validation_rules.md):
- Field & single-record rules (errors) live in this module as Pydantic validators.
- Cross-record / dataset rules (errors + warnings) live in finance_engine/schema/validators.py.
"""

import re
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum
from typing import Annotated, List, Optional
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator


SCHEMA_VERSION = "1.1.0"

# Earliest date accepted for any business event (rule F-07).
MIN_RECORD_DATE = date(2000, 1, 1)

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")


def _validate_currency(v: str) -> str:
    """Normalise and validate an ISO 4217 currency code."""
    v = v.upper()
    if len(v) != 3 or not v.isalpha():
        raise ValueError("currency must be a 3-letter ISO 4217 code (e.g., 'INR', 'USD').")
    return v


# ISO 4217 currency code, upper-cased on input
CurrencyCode = Annotated[str, AfterValidator(_validate_currency)]


def _validate_record_date(v: date) -> date:
    """Reject obviously wrong dates (e.g. 1900-01-01 placeholders, 2-digit year mistakes)."""
    if v < MIN_RECORD_DATE:
        raise ValueError(f"date {v.isoformat()} is before {MIN_RECORD_DATE.isoformat()}; likely a parsing error.")
    return v


# Date of a business event (transaction, invoice, expense)
RecordDate = Annotated[date, AfterValidator(_validate_record_date)]


def _validate_email(v: Optional[str]) -> Optional[str]:
    if v is None or v == "":
        return None
    v = v.lower()
    if not _EMAIL_RE.match(v):
        raise ValueError(f"'{v}' is not a valid email address.")
    return v


EmailStr = Annotated[Optional[str], AfterValidator(_validate_email)]

# Money with at most 2 decimal places (paise / cents)
Money = Annotated[Decimal, Field(decimal_places=2)]


class TransactionDirection(str, Enum):
    """Whether money entered or left the business."""
    inflow = "inflow"
    outflow = "outflow"


class AccountType(str, Enum):
    """Kind of financial account a transaction belongs to."""
    bank = "bank"
    cash = "cash"
    credit_card = "credit_card"
    loan = "loan"
    other = "other"


class CounterpartyType(str, Enum):
    """Role of the external party in a transaction."""
    customer = "customer"
    vendor = "vendor"
    employee = "employee"
    tax_authority = "tax_authority"
    other = "other"


class InvoiceStatus(str, Enum):
    """Lifecycle status of a customer invoice."""
    draft = "draft"
    sent = "sent"
    partially_paid = "partially_paid"
    paid = "paid"
    overdue = "overdue"
    void = "void"


class ExpenseCategory(str, Enum):
    """Standard SME expense categories used across the finance engine."""
    payroll = "payroll"
    rent = "rent"
    utilities = "utilities"
    software = "software"
    marketing = "marketing"
    travel = "travel"
    supplies = "supplies"
    inventory = "inventory"
    professional_services = "professional_services"
    taxes = "taxes"
    loan_repayment = "loan_repayment"
    other = "other"


class CanonicalRecord(BaseModel):
    """
    Base class for all canonical records.

    Provides tenant scoping, source lineage, and strict validation.
    """
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    business_id: str = Field(
        ...,
        min_length=1,
        description="Tenant identifier of the SME business that owns this record."
    )
    source: str = Field(
        default="manual",
        description="Origin of the record (e.g., 'bank_csv', 'quickbooks', 'manual')."
    )
    source_ref: Optional[str] = Field(
        default=None,
        description="Identifier of the record in the source system, for lineage and de-duplication."
    )
    ingested_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC timestamp when the record was normalised by the ETL layer."
    )


class Business(BaseModel):
    """An SME tenant using AgentFi."""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    business_id: str = Field(..., min_length=1, description="Unique tenant identifier.")
    name: str = Field(..., min_length=1, description="Registered business name.")
    base_currency: CurrencyCode = Field(default="INR", description="Reporting currency (ISO 4217).")
    industry: Optional[str] = Field(default=None, description="Industry or sector.")
    fiscal_year_start_month: int = Field(
        default=4, ge=1, le=12,
        description="Month the fiscal year starts (4 = April, Indian FY)."
    )


class Account(CanonicalRecord):
    """A bank, cash, card, or loan account held by the business."""
    account_id: str = Field(..., min_length=1, description="Unique account identifier.")
    name: str = Field(..., min_length=1, description="Display name (e.g., 'HDFC Current A/c').")
    account_type: AccountType = Field(default=AccountType.bank)
    currency: CurrencyCode = Field(default="INR", description="Account currency (ISO 4217).")
    opening_balance: Decimal = Field(default=Decimal("0"), description="Balance at the start of the data period.")


class Counterparty(CanonicalRecord):
    """A customer, vendor, or other external party."""
    counterparty_id: str = Field(..., min_length=1, description="Unique counterparty identifier.")
    name: str = Field(..., min_length=1, description="Counterparty display name.")
    counterparty_type: CounterpartyType = Field(default=CounterpartyType.other)
    email: EmailStr = Field(default=None, description="Contact email, used for reminders.")
    payment_terms_days: Optional[int] = Field(
        default=None, ge=0,
        description="Agreed payment terms in days (e.g., 30 for Net-30)."
    )


class Transaction(CanonicalRecord):
    """A single money movement in or out of an account."""
    transaction_id: str = Field(..., min_length=1, description="Unique transaction identifier.")
    account_id: str = Field(..., min_length=1, description="Account the transaction belongs to.")
    transaction_date: RecordDate = Field(..., description="Date the transaction was posted.")
    amount: Money = Field(..., gt=0, description="Absolute transaction amount (always positive).")
    currency: CurrencyCode = Field(default="INR", description="Transaction currency (ISO 4217).")
    direction: TransactionDirection = Field(..., description="Inflow or outflow.")
    description: str = Field(default="", description="Bank narration or memo text.")
    counterparty_id: Optional[str] = Field(default=None, description="Linked counterparty, if resolved.")
    category: Optional[ExpenseCategory] = Field(
        default=None,
        description="Expense category for outflows, if classified."
    )
    invoice_id: Optional[str] = Field(default=None, description="Invoice this transaction settles, if any.")

    @model_validator(mode="after")
    def check_direction_links(self) -> "Transaction":
        # R-T01: expense categories only make sense for money going out.
        if self.category is not None and self.direction == TransactionDirection.inflow:
            raise ValueError("category can only be set on outflow transactions.")
        # R-T02: an invoice is settled by money coming in.
        if self.invoice_id is not None and self.direction == TransactionDirection.outflow:
            raise ValueError("invoice_id can only be set on inflow transactions.")
        return self


class InvoiceLineItem(BaseModel):
    """A single billable line on an invoice."""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    description: str = Field(..., min_length=1)
    quantity: Decimal = Field(default=Decimal("1"), gt=0)
    unit_price: Decimal = Field(..., ge=0)
    tax_rate: Decimal = Field(
        default=Decimal("0"), ge=0, le=1,
        description="Tax rate as a fraction (0.18 = 18% GST)."
    )

    @property
    def subtotal(self) -> Decimal:
        return self.quantity * self.unit_price

    @property
    def tax_amount(self) -> Decimal:
        return self.subtotal * self.tax_rate

    @property
    def total(self) -> Decimal:
        """Line total rounded to paise/cents (tax is rounded per line, as on GST invoices)."""
        return (self.subtotal + self.tax_amount).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


class Invoice(CanonicalRecord):
    """A customer invoice (accounts receivable)."""
    invoice_id: str = Field(..., min_length=1, description="Unique invoice identifier.")
    invoice_number: str = Field(..., min_length=1, description="Human-facing invoice number.")
    customer_id: str = Field(..., min_length=1, description="Counterparty ID of the customer.")
    issue_date: RecordDate = Field(...)
    due_date: RecordDate = Field(...)
    currency: CurrencyCode = Field(default="INR", description="Invoice currency (ISO 4217).")
    line_items: List[InvoiceLineItem] = Field(..., min_length=1)
    amount_paid: Money = Field(default=Decimal("0"), ge=0)
    status: InvoiceStatus = Field(default=InvoiceStatus.draft)

    @model_validator(mode="after")
    def check_dates_and_payment(self) -> "Invoice":
        if self.due_date < self.issue_date:
            raise ValueError("due_date cannot be before issue_date.")
        if self.amount_paid > self.total:
            raise ValueError("amount_paid cannot exceed invoice total.")
        # R-I03 / R-I04: status must agree with the money actually received.
        # (Softer mismatches such as 'draft' with a payment are dataset warnings, see validators.py.)
        if self.status == InvoiceStatus.paid and self.balance_due != 0:
            raise ValueError("status 'paid' requires amount_paid to equal the invoice total.")
        if self.status == InvoiceStatus.partially_paid and not (0 < self.amount_paid < self.total):
            raise ValueError("status 'partially_paid' requires 0 < amount_paid < invoice total.")
        return self

    @property
    def total(self) -> Decimal:
        return sum((item.total for item in self.line_items), Decimal("0"))

    @property
    def balance_due(self) -> Decimal:
        return self.total - self.amount_paid


class Expense(CanonicalRecord):
    """A categorised business expense or vendor bill (accounts payable)."""
    expense_id: str = Field(..., min_length=1, description="Unique expense identifier.")
    vendor_id: Optional[str] = Field(default=None, description="Counterparty ID of the vendor.")
    expense_date: RecordDate = Field(...)
    due_date: Optional[date] = Field(default=None, description="Payment due date for unpaid bills.")
    amount: Money = Field(..., gt=0)
    currency: CurrencyCode = Field(default="INR", description="Expense currency (ISO 4217).")
    category: ExpenseCategory = Field(default=ExpenseCategory.other)
    description: str = Field(default="")
    is_paid: bool = Field(default=False)
    transaction_id: Optional[str] = Field(default=None, description="Transaction that paid this expense, if any.")

    @model_validator(mode="after")
    def check_dates_and_payment(self) -> "Expense":
        # R-E01
        if self.due_date is not None and self.due_date < self.expense_date:
            raise ValueError("due_date cannot be before expense_date.")
        # R-E02: a linked payment transaction implies the expense is paid.
        if self.transaction_id is not None and not self.is_paid:
            raise ValueError("transaction_id is set but is_paid is False.")
        return self


# Registry of canonical entities -> processed dataset file name (data/processed/<name>.parquet|csv)
CANONICAL_ENTITIES = {
    "businesses": Business,
    "accounts": Account,
    "counterparties": Counterparty,
    "transactions": Transaction,
    "invoices": Invoice,
    "expenses": Expense,
}
