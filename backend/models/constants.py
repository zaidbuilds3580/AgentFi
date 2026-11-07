"""
Domain constants and allowed enumeration values for PostgreSQL CHECK constraints and Pydantic validation.
"""

from typing import Tuple

# User roles for RBAC
USER_ROLE_OWNER = "owner"
USER_ROLE_ADMIN = "admin"
USER_ROLE_VIEWER = "viewer"
USER_ROLES: Tuple[str, ...] = (USER_ROLE_OWNER, USER_ROLE_ADMIN, USER_ROLE_VIEWER)

# Financial Account types
ACCOUNT_TYPE_BANK = "bank"
ACCOUNT_TYPE_CASH = "cash"
ACCOUNT_TYPE_CREDIT_CARD = "credit_card"
ACCOUNT_TYPE_WALLET = "wallet"
ACCOUNT_TYPES: Tuple[str, ...] = (
    ACCOUNT_TYPE_BANK,
    ACCOUNT_TYPE_CASH,
    ACCOUNT_TYPE_CREDIT_CARD,
    ACCOUNT_TYPE_WALLET,
)

# Transaction types (amounts stored strictly positive; direction determined by type)
TRANSACTION_TYPE_INFLOW = "inflow"
TRANSACTION_TYPE_OUTFLOW = "outflow"
TRANSACTION_TYPES: Tuple[str, ...] = (
    TRANSACTION_TYPE_INFLOW,
    TRANSACTION_TYPE_OUTFLOW,
)

# Invoice lifecycles
INVOICE_STATUS_DRAFT = "draft"
INVOICE_STATUS_SENT = "sent"
INVOICE_STATUS_PARTIALLY_PAID = "partially_paid"
INVOICE_STATUS_PAID = "paid"
INVOICE_STATUS_OVERDUE = "overdue"
INVOICE_STATUS_VOID = "void"
INVOICE_STATUSES: Tuple[str, ...] = (
    INVOICE_STATUS_DRAFT,
    INVOICE_STATUS_SENT,
    INVOICE_STATUS_PARTIALLY_PAID,
    INVOICE_STATUS_PAID,
    INVOICE_STATUS_OVERDUE,
    INVOICE_STATUS_VOID,
)

# Payment methods
PAYMENT_METHOD_CASH = "cash"
PAYMENT_METHOD_BANK_TRANSFER = "bank_transfer"
PAYMENT_METHOD_CARD = "card"
PAYMENT_METHOD_UPI = "upi"
PAYMENT_METHOD_CHEQUE = "cheque"
PAYMENT_METHOD_OTHER = "other"
PAYMENT_METHODS: Tuple[str, ...] = (
    PAYMENT_METHOD_CASH,
    PAYMENT_METHOD_BANK_TRANSFER,
    PAYMENT_METHOD_CARD,
    PAYMENT_METHOD_UPI,
    PAYMENT_METHOD_CHEQUE,
    PAYMENT_METHOD_OTHER,
)

# Geographic and localization defaults
DEFAULT_CURRENCY = "INR"
DEFAULT_COUNTRY = "IN"
DEFAULT_TIMEZONE = "Asia/Kolkata"
DEFAULT_FISCAL_YEAR_START_MONTH = 4

