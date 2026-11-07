"""
Protocol and default implementation for CSV row parsing, alias normalization, and validation.
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import re
from typing import Any, Dict, List, Optional, Protocol, Tuple, runtime_checkable
import uuid
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.models.accounts import Account
from backend.models.constants import (
    ERR_ACCOUNT_NOT_FOUND,
    ERR_DUE_BEFORE_INVOICE,
    ERR_INVALID_AMOUNT,
    ERR_INVALID_CURRENCY,
    ERR_INVALID_DATE,
    ERR_INVALID_STATUS,
    ERR_INVALID_TYPE,
    ERR_MISSING_REQUIRED,
    ERR_PAID_EXCEEDS_TOTAL,
    INVOICE_STATUS_DRAFT,
    INVOICE_STATUS_OVERDUE,
    INVOICE_STATUS_PAID,
    INVOICE_STATUS_PARTIALLY_PAID,
    INVOICE_STATUS_SENT,
    INVOICE_STATUS_VOID,
    INVOICE_STATUSES,
    PAYMENT_METHOD_BANK_TRANSFER,
    PAYMENT_METHOD_CARD,
    PAYMENT_METHOD_CASH,
    PAYMENT_METHOD_CHEQUE,
    PAYMENT_METHOD_OTHER,
    PAYMENT_METHOD_UPI,
    PAYMENT_METHODS,
    TRANSACTION_TYPE_INFLOW,
    TRANSACTION_TYPE_OUTFLOW,
)
from backend.models.customers import Customer
from backend.models.vendors import Vendor


# Formula injection protection note:
# Any future CSV EXPORT must neutralize leading characters = + - @ by prefixing with a single quote or space.


@dataclass
class RowError:
    row_number: int
    field: Optional[str]
    code: str
    message: str


@dataclass
class ProcessingContext:
    business_id: uuid.UUID
    business_currency: str
    session: Session
    account_map: Dict[str, uuid.UUID] = field(default_factory=dict)
    customer_cache: Dict[str, uuid.UUID] = field(default_factory=dict)
    vendor_cache: Dict[str, uuid.UUID] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)


@runtime_checkable
class RowProcessor(Protocol):
    """Protocol for pluggable row normalization and validation."""
    def normalize_row(
        self,
        import_type: str,
        raw_row: Dict[str, str],
        row_number: int,
        context: ProcessingContext,
    ) -> Tuple[Optional[Dict[str, Any]], List[RowError]]:
        ...


class DefaultRowProcessor:
    """
    Standard row processor enforcing canonical validation rules:
    - strict date formats (YYYY-MM-DD, DD/MM/YYYY, DD-MM-YYYY)
    - positive Decimal amounts (no negative, zero, parentheses, scientific notation)
    - transaction direction aliases
    - invoice lifecycle and due date checks
    - account resolution (ACCOUNT_NOT_FOUND on mismatch)
    - find-or-create for customers and vendors
    """

    TRANSACTION_TYPE_MAP = {
        "inflow": TRANSACTION_TYPE_INFLOW,
        "credit": TRANSACTION_TYPE_INFLOW,
        "cr": TRANSACTION_TYPE_INFLOW,
        "income": TRANSACTION_TYPE_INFLOW,
        "deposit": TRANSACTION_TYPE_INFLOW,
        "received": TRANSACTION_TYPE_INFLOW,
        "outflow": TRANSACTION_TYPE_OUTFLOW,
        "debit": TRANSACTION_TYPE_OUTFLOW,
        "dr": TRANSACTION_TYPE_OUTFLOW,
        "expense": TRANSACTION_TYPE_OUTFLOW,
        "withdrawal": TRANSACTION_TYPE_OUTFLOW,
        "paid": TRANSACTION_TYPE_OUTFLOW,
    }

    def normalize_row(
        self,
        import_type: str,
        raw_row: Dict[str, str],
        row_number: int,
        context: ProcessingContext,
    ) -> Tuple[Optional[Dict[str, Any]], List[RowError]]:
        errors: List[RowError] = []

        if import_type == "transactions":
            return self._normalize_transaction(raw_row, row_number, context, errors)
        elif import_type == "invoices":
            return self._normalize_invoice(raw_row, row_number, context, errors)
        elif import_type == "expenses":
            return self._normalize_expense(raw_row, row_number, context, errors)
        else:
            errors.append(RowError(row_number, "import_type", ERR_INVALID_TYPE, f"Unsupported import type: {import_type}"))
            return None, errors

    def _clean_text(self, text_val: Optional[str], max_len: Optional[int] = None) -> Optional[str]:
        if not text_val:
            return None
        cleaned = re.sub(r"\s+", " ", text_val.strip())
        if not cleaned:
            return None
        if max_len:
            cleaned = cleaned[:max_len]
        return cleaned

    def _parse_date(self, date_str: Optional[str], field_name: str, row_number: int, errors: List[RowError]) -> Optional[date]:
        if not date_str or not date_str.strip():
            errors.append(RowError(row_number, field_name, ERR_MISSING_REQUIRED, f"{field_name} is required."))
            return None
        val = date_str.strip()
        parsed: Optional[date] = None

        # Format 1: YYYY-MM-DD
        if re.match(r"^\d{4}-\d{1,2}-\d{1,2}$", val):
            try:
                parsed = datetime.strptime(val, "%Y-%m-%d").date()
            except ValueError:
                pass
        # Format 2: DD/MM/YYYY
        elif re.match(r"^\d{1,2}/\d{1,2}/\d{4}$", val):
            try:
                parsed = datetime.strptime(val, "%d/%m/%Y").date()
            except ValueError:
                pass
        # Format 3: DD-MM-YYYY
        elif re.match(r"^\d{1,2}-\d{1,2}-\d{4}$", val):
            try:
                parsed = datetime.strptime(val, "%d-%m-%Y").date()
            except ValueError:
                pass

        if parsed is None:
            errors.append(RowError(row_number, field_name, ERR_INVALID_DATE, f"Invalid date '{val}'. Allowed: YYYY-MM-DD, DD/MM/YYYY, DD-MM-YYYY."))
            return None
        return parsed

    def _parse_amount(
        self,
        amount_str: Optional[str],
        field_name: str,
        row_number: int,
        context: ProcessingContext,
        errors: List[RowError],
        allow_zero: bool = False,
    ) -> Optional[Decimal]:
        if not amount_str or not amount_str.strip():
            errors.append(RowError(row_number, field_name, ERR_MISSING_REQUIRED, f"{field_name} is required."))
            return None
        raw = amount_str.strip()

        # Reject scientific notation or parentheses
        if re.search(r"[eE()]", raw):
            errors.append(RowError(row_number, field_name, ERR_INVALID_AMOUNT, f"Invalid amount format '{raw}'."))
            return None

        # Clean commas, currency symbols
        cleaned = re.sub(r"[₹$,]|Rs\.?|INR", "", raw, flags=re.IGNORECASE).strip()
        if not cleaned:
            errors.append(RowError(row_number, field_name, ERR_INVALID_AMOUNT, f"Amount cannot be blank."))
            return None

        try:
            val = Decimal(cleaned)
        except InvalidOperation:
            errors.append(RowError(row_number, field_name, ERR_INVALID_AMOUNT, f"Invalid numeric amount '{raw}'."))
            return None

        # Reject negative amounts
        if val < Decimal("0"):
            errors.append(RowError(row_number, field_name, ERR_INVALID_AMOUNT, f"Amount must be positive: {val}."))
            return None

        # Reject zero unless allowed (e.g. for amount_paid)
        if not allow_zero and val == Decimal("0"):
            errors.append(RowError(row_number, field_name, ERR_INVALID_AMOUNT, f"Amount cannot be zero."))
            return None

        # Check precision and round half-up if necessary
        exponent = val.as_tuple().exponent
        if isinstance(exponent, int) and exponent < -2:
            rounded = val.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            warning_msg = f"Row {row_number}: {field_name} {val} rounded to {rounded}"
            if warning_msg not in context.warnings:
                context.warnings.append(warning_msg)
            val = rounded
        else:
            val = val.quantize(Decimal("0.01"))

        return val

    def _resolve_account(
        self,
        account_name_str: Optional[str],
        row_number: int,
        context: ProcessingContext,
        errors: List[RowError],
    ) -> Optional[uuid.UUID]:
        cleaned = self._clean_text(account_name_str)
        if not cleaned:
            return None
        key = cleaned.lower()
        if key in context.account_map:
            return context.account_map[key]
        errors.append(RowError(
            row_number,
            "account_name",
            ERR_ACCOUNT_NOT_FOUND,
            f"Account '{cleaned}' not found for this business.",
        ))
        return None

    def _find_or_create_customer(self, name_str: Optional[str], context: ProcessingContext) -> Optional[uuid.UUID]:
        cleaned = self._clean_text(name_str)
        if not cleaned:
            return None
        key = cleaned.lower()
        if key in context.customer_cache:
            return context.customer_cache[key]

        stmt = select(Customer).where(
            Customer.business_id == context.business_id,
            func.lower(Customer.name) == key,
        )
        existing = context.session.scalars(stmt).first()
        if existing:
            context.customer_cache[key] = existing.id
            return existing.id

        new_cust = Customer(
            business_id=context.business_id,
            name=cleaned,
        )
        context.session.add(new_cust)
        context.session.flush()
        context.customer_cache[key] = new_cust.id
        return new_cust.id

    def _find_or_create_vendor(self, name_str: Optional[str], context: ProcessingContext) -> Optional[uuid.UUID]:
        cleaned = self._clean_text(name_str)
        if not cleaned:
            return None
        key = cleaned.lower()
        if key in context.vendor_cache:
            return context.vendor_cache[key]

        stmt = select(Vendor).where(
            Vendor.business_id == context.business_id,
            func.lower(Vendor.name) == key,
        )
        existing = context.session.scalars(stmt).first()
        if existing:
            context.vendor_cache[key] = existing.id
            return existing.id

        new_vendor = Vendor(
            business_id=context.business_id,
            name=cleaned,
        )
        context.session.add(new_vendor)
        context.session.flush()
        context.vendor_cache[key] = new_vendor.id
        return new_vendor.id

    def _normalize_transaction(
        self,
        row: Dict[str, str],
        row_number: int,
        context: ProcessingContext,
        errors: List[RowError],
    ) -> Tuple[Optional[Dict[str, Any]], List[RowError]]:
        d = self._parse_date(row.get("transaction_date"), "transaction_date", row_number, errors)

        raw_type = (row.get("transaction_type") or "").strip().lower()
        txn_type = self.TRANSACTION_TYPE_MAP.get(raw_type)
        if not txn_type:
            errors.append(RowError(row_number, "transaction_type", ERR_INVALID_TYPE, f"Invalid transaction_type '{raw_type}'."))

        amt = self._parse_amount(row.get("amount"), "amount", row_number, context, errors, allow_zero=False)

        acct_id = self._resolve_account(row.get("account_name"), row_number, context, errors)

        curr = (row.get("currency") or context.business_currency).strip().upper()
        if len(curr) != 3 or not curr.isalpha():
            errors.append(RowError(row_number, "currency", ERR_INVALID_CURRENCY, f"Currency must be 3 uppercase letters, got '{curr}'."))

        if errors:
            return None, errors

        result = {
            "business_id": context.business_id,
            "account_id": acct_id,
            "transaction_date": d,
            "transaction_type": txn_type,
            "amount": amt,
            "currency": curr,
            "description": self._clean_text(row.get("description"), max_len=500),
            "category": self._clean_text(row.get("category"), max_len=100),
            "counterparty": self._clean_text(row.get("counterparty"), max_len=255),
            "reference": self._clean_text(row.get("reference"), max_len=100),
            "source_record_id": self._clean_text(row.get("source_record_id")),
        }
        return result, []

    def _normalize_invoice(
        self,
        row: Dict[str, str],
        row_number: int,
        context: ProcessingContext,
        errors: List[RowError],
    ) -> Tuple[Optional[Dict[str, Any]], List[RowError]]:
        inv_num = self._clean_text(row.get("invoice_number"), max_len=100)
        if not inv_num:
            errors.append(RowError(row_number, "invoice_number", ERR_MISSING_REQUIRED, "invoice_number is required."))

        cust_name = self._clean_text(row.get("customer_name"), max_len=255)
        if not cust_name:
            errors.append(RowError(row_number, "customer_name", ERR_MISSING_REQUIRED, "customer_name is required."))

        inv_date = self._parse_date(row.get("invoice_date"), "invoice_date", row_number, errors)
        due_date = self._parse_date(row.get("due_date"), "due_date", row_number, errors)

        if inv_date and due_date and due_date < inv_date:
            errors.append(RowError(row_number, "due_date", ERR_DUE_BEFORE_INVOICE, f"due_date ({due_date}) cannot be before invoice_date ({inv_date})."))

        total = self._parse_amount(row.get("amount_total"), "amount_total", row_number, context, errors, allow_zero=False)

        paid_str = row.get("amount_paid")
        paid = Decimal("0.00")
        if paid_str and paid_str.strip():
            parsed_paid = self._parse_amount(paid_str, "amount_paid", row_number, context, errors, allow_zero=True)
            if parsed_paid is not None:
                paid = parsed_paid

        if total is not None and paid is not None and paid > total:
            errors.append(RowError(row_number, "amount_paid", ERR_PAID_EXCEEDS_TOTAL, f"amount_paid ({paid}) cannot exceed amount_total ({total})."))

        status = (row.get("status") or INVOICE_STATUS_SENT).strip().lower()
        if status not in INVOICE_STATUSES:
            errors.append(RowError(row_number, "status", ERR_INVALID_STATUS, f"Invalid invoice status '{status}'. Allowed: {INVOICE_STATUSES}."))

        pay_date = None
        if row.get("payment_date"):
            pay_date = self._parse_date(row.get("payment_date"), "payment_date", row_number, errors)

        curr = (row.get("currency") or context.business_currency).strip().upper()
        if len(curr) != 3 or not curr.isalpha():
            errors.append(RowError(row_number, "currency", ERR_INVALID_CURRENCY, f"Currency must be 3 uppercase letters, got '{curr}'."))

        if errors:
            return None, errors

        customer_id = self._find_or_create_customer(cust_name, context)

        result = {
            "business_id": context.business_id,
            "customer_id": customer_id,
            "invoice_number": inv_num,
            "invoice_date": inv_date,
            "due_date": due_date,
            "amount_total": total,
            "amount_paid": paid,
            "currency": curr,
            "status": status,
            "payment_date": pay_date,
            "notes": self._clean_text(row.get("notes"), max_len=1000),
            "source_record_id": self._clean_text(row.get("source_record_id")),
        }
        return result, []

    def _normalize_expense(
        self,
        row: Dict[str, str],
        row_number: int,
        context: ProcessingContext,
        errors: List[RowError],
    ) -> Tuple[Optional[Dict[str, Any]], List[RowError]]:
        exp_date = self._parse_date(row.get("expense_date"), "expense_date", row_number, errors)
        cat = self._clean_text(row.get("category"), max_len=100)
        if not cat:
            errors.append(RowError(row_number, "category", ERR_MISSING_REQUIRED, "category is required."))

        amt = self._parse_amount(row.get("amount"), "amount", row_number, context, errors, allow_zero=False)

        acct_id = self._resolve_account(row.get("account_name"), row_number, context, errors)

        raw_method = (row.get("payment_method") or "").strip().lower()
        if not raw_method:
            method = PAYMENT_METHOD_OTHER
        elif raw_method in PAYMENT_METHODS:
            method = raw_method
        else:
            method = PAYMENT_METHOD_OTHER
            warning_msg = f"Row {row_number}: payment_method '{raw_method}' mapped to 'other'."
            if warning_msg not in context.warnings:
                context.warnings.append(warning_msg)

        curr = (row.get("currency") or context.business_currency).strip().upper()
        if len(curr) != 3 or not curr.isalpha():
            errors.append(RowError(row_number, "currency", ERR_INVALID_CURRENCY, f"Currency must be 3 uppercase letters, got '{curr}'."))

        if errors:
            return None, errors

        vendor_name = row.get("vendor_name")
        vendor_id = self._find_or_create_vendor(vendor_name, context) if vendor_name else None

        result = {
            "business_id": context.business_id,
            "vendor_id": vendor_id,
            "account_id": acct_id,
            "expense_date": exp_date,
            "category": cat,
            "amount": amt,
            "currency": curr,
            "payment_method": method,
            "description": self._clean_text(row.get("description"), max_len=500),
            "source_record_id": self._clean_text(row.get("source_record_id")),
        }
        return result, []
