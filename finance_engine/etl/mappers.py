"""
Source -> canonical mappers.

Each mapper reads one kind of real-world export and returns plain dict rows keyed by
canonical entity name. Rows are NOT validated here; pipeline.py turns them into
Pydantic models via validators.validate_rows() and then runs dataset rules.

Field-by-field mappings are documented in docs/data_mapping.md.

Supported sources:
    bank statement CSV  -> transactions        (layouts: HDFC, SBI; add more via BankLayout)
    invoice export CSV  -> invoices, counterparties (Zoho Books-style, one row per line item)
    expense sheet CSV   -> expenses, counterparties
"""

from __future__ import annotations

import csv
import re
from collections import OrderedDict
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from finance_engine.etl.cleaning import clean_text, display_name, party_key
from finance_engine.etl.parsing import (
    DEFAULT_DATE_FORMATS,
    categorize,
    find_invoice_number,
    is_blank,
    normalise_invoice_id,
    parse_amount,
    parse_bool,
    parse_date,
    parse_invoice_status,
    parse_percent,
    slugify,
    stable_id,
)
from finance_engine.schema.canonical import CounterpartyType, InvoiceStatus, TransactionDirection
from finance_engine.schema.validators import Severity, ValidationReport

Rows = Dict[str, List[dict]]


@dataclass
class MappingResult:
    """Output of a mapper: raw canonical rows per entity + issues found while parsing."""
    rows: Rows = field(default_factory=dict)
    report: ValidationReport = field(default_factory=ValidationReport)
    # rows that could not be mapped at all: (entity, raw row as {header: value}, reason) -> quarantine
    rejected: List[Tuple[str, dict, str]] = field(default_factory=list)

    def add_row(self, entity: str, row: dict) -> None:
        self.rows.setdefault(entity, []).append(row)


def _read_csv(path: Path) -> List[List[str]]:
    """Read a CSV and clean every cell (stage 2: cleaning). Falls back to cp1252 for old Excel files."""
    try:
        # utf-8-sig strips the BOM that Excel adds when saving CSVs
        with open(path, newline="", encoding="utf-8-sig") as fh:
            return [[clean_text(c) for c in row] for row in csv.reader(fh)]
    except UnicodeDecodeError:
        with open(path, newline="", encoding="cp1252") as fh:
            return [[clean_text(c) for c in row] for row in csv.reader(fh)]


def party_id(prefix: str, name: str) -> str:
    """Counterparty ID from the *normalised* name, so spelling variants collapse to one party."""
    return f"{prefix}-{slugify(party_key(name))}"


def _norm(header: Optional[str]) -> str:
    """Compare headers on letters/digits/% only: 'Chq./Ref.No.' == 'chq/ref no' == 'chqrefno'."""
    return re.sub(r"[^a-z0-9%]", "", (header or "").lower())


# --------------------------------------------------------------------------- #
# Bank statements
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class BankLayout:
    """
    Column layout of a bank statement export. Header names are compared on letters,
    digits and '%' only, so 'Withdrawal Amt.' == 'withdrawal amt'.

    Use either (debit_col + credit_col) or (amount_col [+ drcr_col]).
    """
    name: str
    date_col: str
    description_col: str
    ref_col: Optional[str] = None
    debit_col: Optional[str] = None
    credit_col: Optional[str] = None
    amount_col: Optional[str] = None
    drcr_col: Optional[str] = None
    balance_col: Optional[str] = None
    date_formats: Tuple[str, ...] = DEFAULT_DATE_FORMATS


HDFC_LAYOUT = BankLayout(
    name="hdfc_csv",
    date_col="date",
    description_col="narration",
    ref_col="chq/ref no",
    debit_col="withdrawal amt",
    credit_col="deposit amt",
    balance_col="closing balance",
    date_formats=("%d/%m/%y", "%d/%m/%Y"),
)

SBI_LAYOUT = BankLayout(
    name="sbi_csv",
    date_col="txn date",
    description_col="description",
    ref_col="ref no/cheque no",
    debit_col="debit",
    credit_col="credit",
    balance_col="balance",
    date_formats=("%d %b %Y", "%d-%b-%Y", "%d/%m/%Y"),
)

GENERIC_SIGNED_LAYOUT = BankLayout(
    name="generic_bank_csv",
    date_col="date",
    description_col="description",
    ref_col="reference",
    amount_col="amount",
    drcr_col="type",
    balance_col="balance",
)

BANK_LAYOUTS = {l.name: l for l in (HDFC_LAYOUT, SBI_LAYOUT, GENERIC_SIGNED_LAYOUT)}


def map_bank_statement(
    path: Path,
    business_id: str,
    account_id: str,
    layout: BankLayout = HDFC_LAYOUT,
    currency: str = "INR",
    opening_balance: Optional[Decimal] = None,
) -> MappingResult:
    """
    Map a bank statement export to canonical transaction rows.

    - Skips bank preamble lines until the header row is found.
    - Skips separator / footer rows ('*****', 'Statement Summary', blank lines).
    - Verifies the running balance when a balance column exists (W-12).
    - Extracts invoice references ('INV-1001') from inflow narrations as a link candidate.
    """
    result = MappingResult()
    entity = "transactions"
    raw = _read_csv(path)

    header_idx = next(
        (i for i, r in enumerate(raw)
         if _norm_set(r) >= {_norm(layout.date_col), _norm(layout.description_col)}),
        None,
    )
    if header_idx is None:
        result.report.add(Severity.error, "M-01", entity, None,
                          f"{path.name}: header row with '{layout.date_col}' and '{layout.description_col}' not found.")
        return result

    cols = {_norm(h): i for i, h in enumerate(raw[header_idx])}
    missing = [c for c in (layout.date_col, layout.description_col, layout.debit_col, layout.credit_col,
                           layout.amount_col, layout.ref_col, layout.balance_col, layout.drcr_col)
               if c and _norm(c) not in cols]
    if missing:
        result.report.add(Severity.error, "M-01", entity, None, f"{path.name}: missing columns {missing}.")
        return result

    def cell(row: List[str], col: Optional[str]) -> Optional[str]:
        if col is None:
            return None
        i = cols[_norm(col)]
        return row[i].strip() if i < len(row) else None

    prev_balance = opening_balance
    started = False
    for line_no, row in enumerate(raw[header_idx + 1:], start=header_idx + 2):
        loc = f"{path.name}:{line_no}"
        if not any(c.strip() for c in row):
            continue
        if set("".join(row).strip()) <= set("*-= "):
            if started:
                break  # separator after the data block -> statement summary footer follows
            continue
        if _norm(row[0]).startswith(("statementsummary", "openingbalance", "closingbalance")):
            break
        started = True
        date_txt = cell(row, layout.date_col)
        try:
            txn_date = parse_date(date_txt, layout.date_formats)
        except ValueError:
            # footer / summary lines have no date; anything else with content is a real problem
            if any(not is_blank(cell(row, c)) for c in (layout.debit_col, layout.credit_col, layout.amount_col) if c):
                result.report.add(Severity.error, "M-02", entity, loc, f"unparseable date '{date_txt}'.")
                result.rejected.append((entity, _raw(raw[header_idx], row, line_no),
                                        f"M-02 unparseable date '{date_txt}'"))
            continue

        try:
            amount, direction = _bank_amount(row, cell, layout)
        except ValueError as exc:
            result.report.add(Severity.error, "M-03", entity, loc, str(exc))
            result.rejected.append((entity, _raw(raw[header_idx], row, line_no), f"M-03 {exc}"))
            continue
        if amount is None:
            result.report.add(Severity.warning, "M-04", entity, loc, "row has no debit/credit amount; skipped.")
            continue

        description = " ".join((cell(row, layout.description_col) or "").split())
        ref = cell(row, layout.ref_col)
        ref = None if is_blank(ref) else ref.lstrip("0") or ref

        # W-12: running balance check
        balance_txt = cell(row, layout.balance_col)
        if balance_txt and not is_blank(balance_txt):
            try:
                bal, hint = parse_amount(balance_txt)
                bal = -bal if hint in ("dr", "-") else bal
                if prev_balance is not None:
                    expected = prev_balance + (amount if direction == TransactionDirection.inflow else -amount)
                    if expected != bal:
                        result.report.add(Severity.warning, "W-12", entity, loc,
                                          f"running balance {bal} != expected {expected}; rows missing or reordered?")
                prev_balance = bal
            except ValueError:
                result.report.add(Severity.warning, "M-03", entity, loc, f"unparseable balance '{balance_txt}'.")

        txn = {
            "business_id": business_id,
            "transaction_id": stable_id("txn", business_id, account_id, txn_date, amount, direction.value,
                                        ref or description, line_no if not ref else ""),
            "account_id": account_id,
            "transaction_date": txn_date,
            "amount": amount,
            "currency": currency,
            "direction": direction,
            "description": description,
            "source": layout.name,
            "source_ref": ref,
        }
        if direction == TransactionDirection.outflow:
            txn["category"] = categorize(description)
        else:
            inv_no = find_invoice_number(description)
            if inv_no:
                txn["invoice_id"] = normalise_invoice_id(inv_no)  # resolved/verified in pipeline
        result.add_row(entity, txn)
    return result


def _raw(header: Sequence[str], row: Sequence[str], line_no: int) -> dict:
    """Raw row as {original header: value} for the quarantine file."""
    out = {h or f"col{i}": (row[i] if i < len(row) else "") for i, h in enumerate(header)}
    out["_line"] = line_no
    return out


def _norm_set(row: Sequence[str]) -> set:
    return {_norm(c) for c in row}


def _bank_amount(row, cell, layout: BankLayout):
    """Return (amount, direction) or (None, None) when the row carries no money."""
    if layout.amount_col:
        parsed = parse_amount(cell(row, layout.amount_col))
        if parsed is None:
            return None, None
        amount, hint = parsed
        drcr = (cell(row, layout.drcr_col) or "").lower() if layout.drcr_col else ""
        is_debit = drcr.startswith("d") or hint in ("dr", "-")
        if amount == 0:
            return None, None
        return amount, TransactionDirection.outflow if is_debit else TransactionDirection.inflow

    debit = parse_amount(cell(row, layout.debit_col))
    credit = parse_amount(cell(row, layout.credit_col))
    debit_amt = debit[0] if debit else Decimal("0")
    credit_amt = credit[0] if credit else Decimal("0")
    if debit_amt and credit_amt:
        raise ValueError("row has both a debit and a credit amount.")
    if debit_amt:
        return debit_amt, TransactionDirection.outflow
    if credit_amt:
        return credit_amt, TransactionDirection.inflow
    return None, None


# --------------------------------------------------------------------------- #
# Invoices (Zoho Books-style export: one row per line item)
# --------------------------------------------------------------------------- #

INVOICE_COLUMNS = {
    "invoice number": "invoice_number",
    "invoice date": "issue_date",
    "due date": "due_date",
    "customer name": "customer_name",
    "customer email": "customer_email",
    "payment terms": "payment_terms",
    "item name": "item",
    "quantity": "quantity",
    "item price": "unit_price",
    "tax %": "tax_rate",
    "amount paid": "amount_paid",
    "invoice status": "status",
    "currency code": "currency",
}
REQUIRED_INVOICE_COLUMNS = {"invoice_number", "issue_date", "due_date", "customer_name", "item", "unit_price"}


def map_invoice_export(path: Path, business_id: str, source: str = "zoho_invoices") -> MappingResult:
    """Group line-item rows by invoice number; create customer counterparties by name."""
    result = MappingResult()
    raw = _read_csv(path)
    if not raw:
        return result
    lookup = {_norm(k): v for k, v in INVOICE_COLUMNS.items()}
    header = [lookup.get(_norm(h)) for h in raw[0]]
    missing = REQUIRED_INVOICE_COLUMNS - set(header)
    if missing:
        result.report.add(Severity.error, "M-01", "invoices", None, f"{path.name}: missing columns {sorted(missing)}.")
        return result

    invoices: "OrderedDict[str, dict]" = OrderedDict()
    customers: "OrderedDict[str, dict]" = OrderedDict()

    for line_no, values in enumerate(raw[1:], start=2):
        if not any(v.strip() for v in values):
            continue
        r = {k: v.strip() for k, v in zip(header, values) if k}
        loc = f"{path.name}:{line_no}"
        number = r["invoice_number"]
        try:
            customer_id = _customer(customers, business_id, source, r)
            inv = invoices.get(number)
            if inv is None:
                status_txt = r.get("status")
                status = parse_invoice_status(status_txt)
                if status_txt and status is None:
                    result.report.add(Severity.warning, "M-05", "invoices", number,
                                      f"unknown status '{status_txt}', defaulting to 'sent'.")
                paid = parse_amount(r.get("amount_paid"))
                inv = invoices[number] = {
                    "business_id": business_id,
                    "invoice_id": normalise_invoice_id(number),
                    "invoice_number": number,
                    "customer_id": customer_id,
                    "issue_date": parse_date(r["issue_date"]),
                    "due_date": parse_date(r["due_date"]),
                    "currency": r.get("currency") or "INR",
                    "line_items": [],
                    "amount_paid": paid[0] if paid else Decimal("0"),
                    "status": status or InvoiceStatus.sent,
                    "source": source,
                    "source_ref": number,
                }
            else:
                # Header-level fields repeat on every line; they must agree.
                paid = parse_amount(r.get("amount_paid"))
                if paid and paid[0] != inv["amount_paid"]:
                    result.report.add(Severity.warning, "M-06", "invoices", number,
                                      f"line {line_no}: amount_paid differs from first line; using first value.")
            qty = parse_amount(r.get("quantity"))
            price = parse_amount(r["unit_price"])
            inv["line_items"].append({
                "description": r["item"],
                "quantity": qty[0] if qty else Decimal("1"),
                "unit_price": price[0] if price else Decimal("0"),
                "tax_rate": parse_percent(r.get("tax_rate")),
            })
        except (ValueError, KeyError) as exc:
            result.report.add(Severity.error, "M-02", "invoices", loc, str(exc))
            result.rejected.append(("invoices", _raw(raw[0], values, line_no), f"M-02 {exc}"))

    for row in customers.values():
        result.add_row("counterparties", row)
    for inv in invoices.values():
        result.add_row("invoices", inv)
    return result


def _customer(customers: dict, business_id: str, source: str, r: dict) -> str:
    name = r["customer_name"]
    cid = party_id("cust", name)
    if cid in customers:
        customers[cid]["name"] = display_name(customers[cid]["name"], name)
        if not customers[cid].get("email") and r.get("customer_email"):
            customers[cid]["email"] = r["customer_email"]
    else:
        terms = r.get("payment_terms") or ""
        digits = "".join(ch for ch in terms if ch.isdigit())
        customers[cid] = {
            "business_id": business_id,
            "counterparty_id": cid,
            "name": name,
            "counterparty_type": CounterpartyType.customer,
            "email": r.get("customer_email") or None,
            "payment_terms_days": int(digits) if digits else None,
            "source": source,
        }
    return cid


# --------------------------------------------------------------------------- #
# Expenses / vendor bills (simple spreadsheet kept by the business)
# --------------------------------------------------------------------------- #

EXPENSE_COLUMNS = {
    "date": "expense_date",
    "bill no": "bill_no",
    "vendor": "vendor",
    "category": "category",
    "description": "description",
    "amount": "amount",
    "currency": "currency",
    "paid": "paid",
    "due date": "due_date",
    "payment ref": "payment_ref",
}
REQUIRED_EXPENSE_COLUMNS = {"expense_date", "amount"}


def map_expense_sheet(path: Path, business_id: str, source: str = "expense_sheet") -> MappingResult:
    """Map an expense sheet. 'Payment Ref' is kept for linking to bank transactions in the pipeline."""
    result = MappingResult()
    raw = _read_csv(path)
    if not raw:
        return result
    lookup = {_norm(k): v for k, v in EXPENSE_COLUMNS.items()}
    header = [lookup.get(_norm(h)) for h in raw[0]]
    missing = REQUIRED_EXPENSE_COLUMNS - set(header)
    if missing:
        result.report.add(Severity.error, "M-01", "expenses", None, f"{path.name}: missing columns {sorted(missing)}.")
        return result

    vendors: "OrderedDict[str, dict]" = OrderedDict()
    for line_no, values in enumerate(raw[1:], start=2):
        if not any(v.strip() for v in values):
            continue
        r = {k: v.strip() for k, v in zip(header, values) if k}
        loc = f"{path.name}:{line_no}"
        try:
            amount = parse_amount(r.get("amount"))
            if amount is None:
                result.report.add(Severity.warning, "M-04", "expenses", loc, "no amount; skipped.")
                continue
            expense_date = parse_date(r["expense_date"])
            vendor_id = None
            vendor_name = r.get("vendor") or ""
            if vendor_name:
                vendor_id = party_id("vend", vendor_name)
                if vendor_id in vendors:
                    vendors[vendor_id]["name"] = display_name(vendors[vendor_id]["name"], vendor_name)
                vendors.setdefault(vendor_id, {
                    "business_id": business_id,
                    "counterparty_id": vendor_id,
                    "name": vendor_name,
                    "counterparty_type": CounterpartyType.vendor,
                    "source": source,
                })
            category = categorize(r.get("category")) or categorize(r.get("description"), vendor_name)
            if category is None:
                result.report.add(Severity.warning, "M-07", "expenses", loc,
                                  f"could not map category '{r.get('category', '')}'; using 'other'.")
            bill_no = r.get("bill_no") or None
            row = {
                "business_id": business_id,
                "expense_id": stable_id("exp", business_id, bill_no or line_no, vendor_id, expense_date),
                "vendor_id": vendor_id,
                "expense_date": expense_date,
                "due_date": None if is_blank(r.get("due_date")) else parse_date(r["due_date"]),
                "amount": amount[0],
                "currency": r.get("currency") or "INR",
                "category": category or "other",
                "description": r.get("description", ""),
                "is_paid": parse_bool(r.get("paid")),
                "source": source,
                "source_ref": bill_no,
                # helper key consumed (and removed) by the pipeline
                "_payment_ref": None if is_blank(r.get("payment_ref")) else r["payment_ref"].lstrip("0"),
            }
            result.add_row("expenses", row)
        except ValueError as exc:
            result.report.add(Severity.error, "M-02", "expenses", loc, str(exc))
            result.rejected.append(("expenses", _raw(raw[0], values, line_no), f"M-02 {exc}"))

    for row in vendors.values():
        result.add_row("counterparties", row)
    return result
