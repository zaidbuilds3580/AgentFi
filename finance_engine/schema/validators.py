"""
Dataset-level validation for AgentFi canonical records.

Pydantic models in canonical.py enforce field and single-record rules: a record that
breaks them cannot even be constructed. This module checks rules that need the
*whole dataset* (references between entities, duplicates, tenant isolation) plus
"soft" data-quality rules that should be flagged but not block ingestion.

Every rule has a stable ID that matches docs/validation_rules.md.

Usage:
    report = validate_dataset({"accounts": [...], "transactions": [...], ...})
    if not report.ok:
        print(report.summary())
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple, Type

from pydantic import BaseModel, ValidationError

from finance_engine.schema.canonical import (
    CANONICAL_ENTITIES,
    CounterpartyType,
    InvoiceStatus,
    TransactionDirection,
)


class Severity(str, Enum):
    error = "error"      # record must be rejected / fixed before use
    warning = "warning"  # record is usable but should be reviewed
    info = "info"        # expected ETL action worth logging (e.g. duplicate from overlapping export removed)


@dataclass(frozen=True)
class Issue:
    """One validation finding."""
    severity: Severity
    rule: str
    entity: str
    record_id: Optional[str]
    message: str

    def __str__(self) -> str:
        rid = f"[{self.record_id}]" if self.record_id else ""
        return f"{self.severity.value.upper():7} {self.rule:6} {self.entity}{rid}: {self.message}"


@dataclass
class ValidationReport:
    """Collection of issues found while validating a dataset."""
    issues: List[Issue] = field(default_factory=list)

    def add(self, severity: Severity, rule: str, entity: str, record_id: Optional[str], message: str) -> None:
        self.issues.append(Issue(severity, rule, entity, record_id, message))

    def extend(self, other: "ValidationReport") -> None:
        self.issues.extend(other.issues)

    @property
    def errors(self) -> List[Issue]:
        return [i for i in self.issues if i.severity == Severity.error]

    @property
    def warnings(self) -> List[Issue]:
        return [i for i in self.issues if i.severity == Severity.warning]

    @property
    def infos(self) -> List[Issue]:
        return [i for i in self.issues if i.severity == Severity.info]

    @property
    def ok(self) -> bool:
        """True when there are no errors (warnings are allowed)."""
        return not self.errors

    def rules_hit(self) -> set:
        return {i.rule for i in self.issues}

    def summary(self) -> str:
        lines = [f"{len(self.errors)} error(s), {len(self.warnings)} warning(s), {len(self.infos)} info"]
        lines += [str(i) for i in self.issues]
        return "\n".join(lines)


# Primary-key field of each canonical entity
PRIMARY_KEYS: Dict[str, str] = {
    "businesses": "business_id",
    "accounts": "account_id",
    "counterparties": "counterparty_id",
    "transactions": "transaction_id",
    "invoices": "invoice_id",
    "expenses": "expense_id",
}


# --------------------------------------------------------------------------- #
# Row-level: raw dicts -> canonical models
# --------------------------------------------------------------------------- #

def validate_rows(
    entity: str, rows: Iterable[Mapping[str, Any]]
) -> Tuple[List[BaseModel], ValidationReport]:
    """
    Build canonical models from dict rows (e.g. output of an ETL mapper).

    Rows that fail Pydantic validation are reported as errors (rule F-00) and skipped.
    """
    model: Type[BaseModel] = CANONICAL_ENTITIES[entity]
    pk = PRIMARY_KEYS[entity]
    report = ValidationReport()
    valid: List[BaseModel] = []
    for idx, row in enumerate(rows):
        try:
            valid.append(model.model_validate(row))
        except ValidationError as exc:
            rid = row.get(pk) or f"row {idx}"
            for err in exc.errors():
                loc = ".".join(str(p) for p in err["loc"]) or "record"
                report.add(Severity.error, "F-00", entity, str(rid), f"{loc}: {err['msg']}")
    return valid, report


# --------------------------------------------------------------------------- #
# Dataset-level
# --------------------------------------------------------------------------- #

def validate_dataset(
    datasets: Mapping[str, Sequence[BaseModel]],
    today: Optional[date] = None,
) -> ValidationReport:
    """
    Run all cross-record rules over a set of canonical records.

    Args:
        datasets: entity name (key of CANONICAL_ENTITIES) -> list of model instances.
                  Missing entities are treated as empty.
        today: reference date for overdue / future-date checks (defaults to date.today()).
    """
    today = today or date.today()
    report = ValidationReport()
    data = {name: list(datasets.get(name, [])) for name in CANONICAL_ENTITIES}

    unknown = set(datasets) - set(CANONICAL_ENTITIES)
    for name in unknown:
        report.add(Severity.error, "X-00", name, None, "unknown entity name; expected one of CANONICAL_ENTITIES.")

    index = {name: _index(name, recs, report) for name, recs in data.items()}

    _check_tenancy(data, index, report)
    _check_source_refs(data, report)
    _check_references(data, index, report)
    _check_transactions(data, index, report, today)
    _check_invoices(data, index, report, today)
    _check_expenses(data, index, report, today)
    return report


def _pk(entity: str, rec: BaseModel) -> str:
    return getattr(rec, PRIMARY_KEYS[entity])


def _index(entity: str, records: List[BaseModel], report: ValidationReport) -> Dict[str, BaseModel]:
    """X-01: primary keys must be unique within (business_id, entity)."""
    idx: Dict[str, BaseModel] = {}
    seen: set = set()
    for rec in records:
        key = (rec.business_id, _pk(entity, rec))
        if key in seen:
            report.add(Severity.error, "X-01", entity, _pk(entity, rec), "duplicate primary key.")
            continue
        seen.add(key)
        idx[_pk(entity, rec)] = rec
    return idx


def _check_tenancy(data, index, report: ValidationReport) -> None:
    """X-02: every record must belong to a known business (when businesses are supplied)."""
    known = set(index["businesses"])
    if not known:
        return
    for name, records in data.items():
        if name == "businesses":
            continue
        for rec in records:
            if rec.business_id not in known:
                report.add(Severity.error, "X-02", name, _pk(name, rec),
                           f"business_id '{rec.business_id}' is not a known business.")


def _check_source_refs(data, report: ValidationReport) -> None:
    """X-03: (business_id, source, source_ref) must be unique -> no double ingestion."""
    for name, records in data.items():
        if name == "businesses":
            continue
        seen: Dict[tuple, str] = {}
        for rec in records:
            if not rec.source_ref:
                continue
            key = (rec.business_id, rec.source, rec.source_ref)
            if key in seen and seen[key] == _pk(name, rec):
                continue  # identical primary key: already reported as X-01
            if key in seen:
                report.add(Severity.error, "X-03", name, _pk(name, rec),
                           f"source_ref '{rec.source_ref}' from '{rec.source}' already ingested as {seen[key]}.")
            else:
                seen[key] = _pk(name, rec)


def _ref(report, index, entity, rec, fk_value, target, rule, label) -> Optional[BaseModel]:
    """Resolve a foreign key; report an error if it points nowhere or crosses tenants."""
    if fk_value is None:
        return None
    target_rec = index[target].get(fk_value)
    if target_rec is None:
        report.add(Severity.error, rule, entity, _pk(entity, rec),
                   f"{label} '{fk_value}' does not exist in {target}.")
        return None
    if target_rec.business_id != rec.business_id:
        report.add(Severity.error, "X-02", entity, _pk(entity, rec),
                   f"{label} '{fk_value}' belongs to another business ({target_rec.business_id}).")
        return None
    return target_rec


def _check_references(data, index, report: ValidationReport) -> None:
    """X-04 .. X-06: referential integrity and counterparty roles."""
    for t in data["transactions"]:
        _ref(report, index, "transactions", t, t.account_id, "accounts", "X-04", "account_id")
        _ref(report, index, "transactions", t, t.counterparty_id, "counterparties", "X-04", "counterparty_id")
        _ref(report, index, "transactions", t, t.invoice_id, "invoices", "X-04", "invoice_id")

    for inv in data["invoices"]:
        cust = _ref(report, index, "invoices", inv, inv.customer_id, "counterparties", "X-04", "customer_id")
        if cust is not None and cust.counterparty_type != CounterpartyType.customer:
            report.add(Severity.warning, "X-05", "invoices", inv.invoice_id,
                       f"customer '{cust.counterparty_id}' has counterparty_type '{cust.counterparty_type.value}', expected 'customer'.")

    for exp in data["expenses"]:
        vendor = _ref(report, index, "expenses", exp, exp.vendor_id, "counterparties", "X-04", "vendor_id")
        if vendor is not None and vendor.counterparty_type == CounterpartyType.customer:
            report.add(Severity.warning, "X-06", "expenses", exp.expense_id,
                       f"vendor '{vendor.counterparty_id}' is marked as a customer.")
        _ref(report, index, "expenses", exp, exp.transaction_id, "transactions", "X-04", "transaction_id")


def _check_transactions(data, index, report: ValidationReport, today: date) -> None:
    dup_keys: Dict[tuple, str] = {}
    for t in data["transactions"]:
        acc = index["accounts"].get(t.account_id)
        # X-07: transaction currency must match its account currency
        if acc is not None and acc.currency != t.currency:
            report.add(Severity.error, "X-07", "transactions", t.transaction_id,
                       f"currency {t.currency} differs from account {acc.account_id} currency {acc.currency}.")
        # W-01: future-dated transaction
        if t.transaction_date > today:
            report.add(Severity.warning, "W-01", "transactions", t.transaction_id,
                       f"transaction_date {t.transaction_date} is in the future.")
        # W-02: uncategorised outflow
        if t.direction == TransactionDirection.outflow and t.category is None and t.invoice_id is None:
            report.add(Severity.warning, "W-02", "transactions", t.transaction_id,
                       "outflow has no expense category.")
        # W-03: probable duplicate (same account/date/amount/direction/description)
        key = (t.business_id, t.account_id, t.transaction_date, t.amount, t.direction,
               " ".join(t.description.lower().split()))
        if key in dup_keys and dup_keys[key] != t.transaction_id:
            report.add(Severity.warning, "W-03", "transactions", t.transaction_id,
                       f"looks like a duplicate of {dup_keys[key]}.")
        else:
            dup_keys[key] = t.transaction_id


def _check_invoices(data, index, report: ValidationReport, today: date) -> None:
    received: Dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    for t in data["transactions"]:
        if t.invoice_id:
            received[t.invoice_id] += t.amount

    for inv in data["invoices"]:
        rid = inv.invoice_id
        balance = inv.balance_due
        # W-04: status should reflect due date
        if inv.status in (InvoiceStatus.sent, InvoiceStatus.partially_paid) and inv.due_date < today and balance > 0:
            report.add(Severity.warning, "W-04", "invoices", rid,
                       f"due {inv.due_date} with balance {balance} outstanding; status should be 'overdue'.")
        if inv.status == InvoiceStatus.overdue and inv.due_date >= today:
            report.add(Severity.warning, "W-04", "invoices", rid,
                       f"marked 'overdue' but due_date {inv.due_date} has not passed.")
        # W-05: fully paid but status says otherwise / draft with money received
        if balance == 0 and inv.status not in (InvoiceStatus.paid, InvoiceStatus.void):
            report.add(Severity.warning, "W-05", "invoices", rid,
                       f"fully paid but status is '{inv.status.value}'.")
        if inv.status == InvoiceStatus.draft and inv.amount_paid > 0:
            report.add(Severity.warning, "W-05", "invoices", rid, "draft invoice already has payments.")
        # W-06: payments recorded on the invoice should match linked bank inflows
        if rid in received and received[rid] != inv.amount_paid:
            report.add(Severity.warning, "W-06", "invoices", rid,
                       f"amount_paid {inv.amount_paid} != sum of linked inflows {received[rid]}.")
        # W-07: invoice currency vs business base currency
        biz = index["businesses"].get(inv.business_id)
        if biz is not None and biz.base_currency != inv.currency:
            report.add(Severity.warning, "W-07", "invoices", rid,
                       f"currency {inv.currency} differs from base currency {biz.base_currency}; FX conversion needed.")


def _check_expenses(data, index, report: ValidationReport, today: date) -> None:
    for exp in data["expenses"]:
        rid = exp.expense_id
        if exp.expense_date > today:
            report.add(Severity.warning, "W-01", "expenses", rid, f"expense_date {exp.expense_date} is in the future.")
        # W-08: paid without a linked transaction (cash or not yet reconciled)
        if exp.is_paid and exp.transaction_id is None:
            report.add(Severity.warning, "W-08", "expenses", rid, "marked paid but not linked to a transaction.")
        txn = index["transactions"].get(exp.transaction_id) if exp.transaction_id else None
        if txn is not None:
            # X-08: an expense can only be paid by an outflow
            if txn.direction != TransactionDirection.outflow:
                report.add(Severity.error, "X-08", "expenses", rid,
                           f"linked transaction {txn.transaction_id} is an inflow.")
            # W-09: amounts should match
            elif txn.amount != exp.amount:
                report.add(Severity.warning, "W-09", "expenses", rid,
                           f"amount {exp.amount} != linked transaction amount {txn.amount}.")
        # W-10: unpaid bill past due
        if not exp.is_paid and exp.due_date is not None and exp.due_date < today:
            report.add(Severity.warning, "W-10", "expenses", rid, f"unpaid bill was due {exp.due_date}.")
