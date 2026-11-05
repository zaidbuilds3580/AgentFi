"""
Parsing helpers for messy real-world finance exports.

Handles the formats seen in Indian SME data: lakh-style thousands separators
("1,00,000.00"), currency symbols, Dr/Cr suffixes, accounting parentheses for
negatives, day-first dates and blank cells.
"""

from __future__ import annotations

import hashlib
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Iterable, Optional, Tuple

from finance_engine.schema.canonical import ExpenseCategory, InvoiceStatus

# Day-first formats are tried before ISO; Indian exports are day-first.
DEFAULT_DATE_FORMATS: Tuple[str, ...] = (
    "%d/%m/%y", "%d/%m/%Y", "%d-%m-%Y", "%d-%m-%y", "%d.%m.%Y",
    "%d %b %Y", "%d-%b-%Y", "%d-%b-%y", "%d %B %Y",
    "%Y-%m-%d",
)

_BLANK = {"", "-", "--", "na", "n/a", "nil", "null", "none"}


def is_blank(value: Optional[str]) -> bool:
    return value is None or str(value).strip().lower() in _BLANK


def parse_date(value: str, formats: Iterable[str] = DEFAULT_DATE_FORMATS) -> date:
    """Parse a date string using the first matching format. Raises ValueError if none match."""
    text = str(value).strip()
    for fmt in formats:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"unrecognised date '{value}'")


def parse_amount(value: Optional[str]) -> Optional[Tuple[Decimal, Optional[str]]]:
    """
    Parse a money cell into (absolute_amount, sign_hint).

    sign_hint is 'dr' / 'cr' when the cell carried a Dr/Cr suffix, '-' when it was
    negative (leading minus or parentheses), otherwise None.
    Returns None for blank cells. Raises ValueError for unparseable text.

    >>> parse_amount("1,00,000.50 Cr")
    (Decimal('100000.50'), 'cr')
    """
    if is_blank(value):
        return None
    text = str(value).strip().lower()
    hint: Optional[str] = None

    m = re.search(r"\b(dr|cr)\.?$", text)
    if m:
        hint = m.group(1)
        text = text[: m.start()].strip()
    if text.startswith("(") and text.endswith(")"):
        hint = hint or "-"
        text = text[1:-1]
    text = re.sub(r"(₹|rs\.?|inr|\$|,|\s)", "", text)
    if text.startswith("-"):
        hint = hint or "-"
        text = text[1:]
    try:
        amount = Decimal(text)
    except InvalidOperation:
        raise ValueError(f"unrecognised amount '{value}'") from None
    return amount.quantize(Decimal("0.01")), hint


def parse_bool(value: Optional[str]) -> bool:
    return str(value or "").strip().lower() in {"y", "yes", "true", "1", "paid"}


def parse_percent(value: Optional[str]) -> Decimal:
    """'18', '18%', '0.18' -> Decimal('0.18')."""
    if is_blank(value):
        return Decimal("0")
    text = str(value).strip().rstrip("%").strip()
    rate = Decimal(text)
    return rate / 100 if rate > 1 else rate


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def stable_id(prefix: str, *parts: object) -> str:
    """Deterministic ID so re-running ETL on the same file yields the same IDs."""
    digest = hashlib.sha1("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:12]
    return f"{prefix}_{digest}"


def normalise_invoice_id(invoice_number: str) -> str:
    """Canonical invoice_id derived from a human invoice number: 'INV 1001' -> 'inv-1001'."""
    return slugify(invoice_number)


INVOICE_NUMBER_RE = re.compile(r"\bINV[-/ ]?\d+\b", re.IGNORECASE)


def find_invoice_number(text: str) -> Optional[str]:
    """Find an invoice reference such as 'INV-1001' in bank narration."""
    m = INVOICE_NUMBER_RE.search(text or "")
    return m.group(0).upper().replace("/", "-").replace(" ", "-") if m else None


# Keyword rules for mapping free-text descriptions to ExpenseCategory.
# Order matters: first match wins.
CATEGORY_KEYWORDS: Tuple[Tuple[ExpenseCategory, Tuple[str, ...]], ...] = (
    (ExpenseCategory.payroll, ("salary", "salaries", "payroll", "wages", "stipend")),
    (ExpenseCategory.taxes, ("gst", "tds", "income tax", "advance tax", "cbdt", "professional tax")),
    (ExpenseCategory.loan_repayment, ("emi", "loan")),
    (ExpenseCategory.rent, ("rent", "lease")),
    (ExpenseCategory.utilities, ("electricity", "bescom", "msedcl", "water", "internet", "broadband",
                                 "airtel", "jio", "act fibernet", "phone")),
    (ExpenseCategory.marketing, ("google ads", "facebook", "meta ads", "linkedin ads", "advertis", "marketing")),
    (ExpenseCategory.software, ("aws", "amazon web services", "google workspace", "gsuite", "microsoft",
                                "zoho", "slack", "github", "notion", "software", "subscription", "saas")),
    (ExpenseCategory.travel, ("uber", "ola", "irctc", "makemytrip", "indigo", "air india", "hotel", "travel",
                              "cab", "flight")),
    (ExpenseCategory.professional_services, ("ca fees", "legal", "consult", "audit", "chartered accountant")),
    (ExpenseCategory.inventory, ("inventory", "stock", "raw material", "purchase of goods")),
    (ExpenseCategory.supplies, ("stationery", "office supplies", "supplies", "amazon", "flipkart", "printer")),
)


def categorize(*texts: Optional[str]) -> Optional[ExpenseCategory]:
    """Best-effort category from free text (sheet category column, narration, vendor name)."""
    haystack = " ".join(t for t in texts if t).lower()
    if not haystack.strip():
        return None
    for value in ExpenseCategory:  # exact enum value written in the sheet, e.g. "software"
        if haystack.strip() == value.value.replace("_", " ") or haystack.strip() == value.value:
            return value
    for category, keywords in CATEGORY_KEYWORDS:
        # keyword must start at a word boundary: 'emi' must not match 'premium', 'rent' not 'current'
        if any(re.search(r"\b" + re.escape(k), haystack) for k in keywords):
            return category
    return None


_STATUS_ALIASES = {
    "draft": InvoiceStatus.draft,
    "sent": InvoiceStatus.sent,
    "open": InvoiceStatus.sent,
    "unpaid": InvoiceStatus.sent,
    "issued": InvoiceStatus.sent,
    "partiallypaid": InvoiceStatus.partially_paid,
    "partial": InvoiceStatus.partially_paid,
    "paid": InvoiceStatus.paid,
    "closed": InvoiceStatus.paid,
    "overdue": InvoiceStatus.overdue,
    "void": InvoiceStatus.void,
    "cancelled": InvoiceStatus.void,
    "canceled": InvoiceStatus.void,
}


def parse_invoice_status(value: Optional[str]) -> Optional[InvoiceStatus]:
    """Map source-system status labels (Zoho, Tally, QuickBooks) to InvoiceStatus."""
    if is_blank(value):
        return None
    key = re.sub(r"[^a-z]", "", str(value).lower())
    return _STATUS_ALIASES.get(key)
