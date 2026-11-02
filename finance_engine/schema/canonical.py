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
"""

from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Annotated, List, Optional
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator


SCHEMA_VERSION = "1.0.0"


def _validate_currency(v: str) -> str:
    """Normalise and validate an ISO 4217 currency code."""
    v = v.upper()
    if len(v) != 3 or not v.isalpha():
        raise ValueError("currency must be a 3-letter ISO 4217 code (e.g., 'INR', 'USD').")
    return v


# ISO 4217 currency code, upper-cased on input
CurrencyCode = Annotated[str, AfterValidator(_validate_currency)]


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
    email: Optional[str] = Field(default=None, description="Contact email, used for reminders.")
    payment_terms_days: Optional[int] = Field(
        default=None, ge=0,
        description="Agreed payment terms in days (e.g., 30 for Net-30)."
    )


class Transaction(CanonicalRecord):
    """A single money movement in or out of an account."""
    transaction_id: str = Field(..., min_length=1, description="Unique transaction identifier.")
    account_id: str = Field(..., min_length=1, description="Account the transaction belongs to.")
    transaction_date: date = Field(..., description="Date the transaction was posted.")
    amount: Decimal = Field(..., gt=0, description="Absolute transaction amount (always positive).")
    currency: CurrencyCode = Field(default="INR", description="Transaction currency (ISO 4217).")
    direction: TransactionDirection = Field(..., description="Inflow or outflow.")
    description: str = Field(default="", description="Bank narration or memo text.")
    counterparty_id: Optional[str] = Field(default=None, description="Linked counterparty, if resolved.")
    category: Optional[ExpenseCategory] = Field(
        default=None,
        description="Expense category for outflows, if classified."
    )
    invoice_id: Optional[str] = Field(default=None, description="Invoice this transaction settles, if any.")


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
        return self.subtotal + self.tax_amount


class Invoice(CanonicalRecord):
    """A customer invoice (accounts receivable)."""
    invoice_id: str = Field(..., min_length=1, description="Unique invoice identifier.")
    invoice_number: str = Field(..., min_length=1, description="Human-facing invoice number.")
    customer_id: str = Field(..., min_length=1, description="Counterparty ID of the customer.")
    issue_date: date = Field(...)
    due_date: date = Field(...)
    currency: CurrencyCode = Field(default="INR", description="Invoice currency (ISO 4217).")
    line_items: List[InvoiceLineItem] = Field(..., min_length=1)
    amount_paid: Decimal = Field(default=Decimal("0"), ge=0)
    status: InvoiceStatus = Field(default=InvoiceStatus.draft)

    @model_validator(mode="after")
    def check_dates_and_payment(self) -> "Invoice":
        if self.due_date < self.issue_date:
            raise ValueError("due_date cannot be before issue_date.")
        if self.amount_paid > self.total:
            raise ValueError("amount_paid cannot exceed invoice total.")
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
    expense_date: date = Field(...)
    due_date: Optional[date] = Field(default=None, description="Payment due date for unpaid bills.")
    amount: Decimal = Field(..., gt=0)
    currency: CurrencyCode = Field(default="INR", description="Expense currency (ISO 4217).")
    category: ExpenseCategory = Field(default=ExpenseCategory.other)
    description: str = Field(default="")
    is_paid: bool = Field(default=False)
    transaction_id: Optional[str] = Field(default=None, description="Transaction that paid this expense, if any.")


# Registry of canonical entities -> processed dataset file name (data/processed/<name>.parquet|csv)
CANONICAL_ENTITIES = {
    "businesses": Business,
    "accounts": Account,
    "counterparties": Counterparty,
    "transactions": Transaction,
    "invoices": Invoice,
    "expenses": Expense,
}
