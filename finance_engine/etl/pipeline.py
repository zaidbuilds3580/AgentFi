"""
AgentFi ETL pipeline for one business.

    1. EXTRACT     run each source mapper listed in <folder>/manifest.json
    2. CLEAN       every CSV cell is cleaned on read (cleaning.clean_text)
    3. NORMALISE   parse dates/amounts/statuses, map categories, collapse counterparty
                   spelling variants into one ID (mappers + cleaning.party_key)
    4. DEDUPE      remove duplicates across files and against already-processed data (dedup.py)
    5. LINK        bank inflow -> invoice, expense -> bank payment
    6. VALIDATE    Pydantic (F/R rules) + dataset rules (X/W rules); records with errors are
                   moved to QUARANTINE and the dataset is re-checked until it is error-free
    7. LOAD        write processed CSVs (atomically), quarantine files, duplicate log and a
                   JSON run report to data/processed/<business_id>/

The manifest format:

{
  "business": {"business_id": "biz_001", "name": "...", "base_currency": "INR"},
  "accounts": [{"account_id": "acc_hdfc_current", "name": "...", "opening_balance": "100000.00"}],
  "sources": [
    {"type": "bank_statement", "path": "bank_csv/hdfc.csv", "account_id": "acc_hdfc_current", "layout": "hdfc_csv"},
    {"type": "invoice_export", "path": "invoices/zoho.csv"},
    {"type": "expense_sheet", "path": "expenses/expenses.csv"}
  ]
}

List sources oldest -> newest: for documents (invoices, expenses) the newest copy wins.
"""

from __future__ import annotations

import csv
import json
import os
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from pydantic import BaseModel, ValidationError

from finance_engine.etl.dedup import SOURCE_FILE, dedupe
from finance_engine.etl.mappers import (
    BANK_LAYOUTS,
    MappingResult,
    map_bank_statement,
    map_expense_sheet,
    map_invoice_export,
)
from finance_engine.schema.canonical import CANONICAL_ENTITIES, SCHEMA_VERSION, TransactionDirection
from finance_engine.schema.validators import (
    PRIMARY_KEYS,
    Severity,
    ValidationReport,
    validate_dataset,
)

Datasets = Dict[str, List[BaseModel]]
Rows = Dict[str, List[dict]]
# entity -> [(row as dict, [reasons])]
Quarantine = Dict[str, List[Tuple[dict, List[str]]]]

ENTITIES = list(CANONICAL_ENTITIES)
MAX_QUARANTINE_PASSES = 5


@dataclass
class PipelineResult:
    business_id: str
    datasets: Datasets
    report: ValidationReport
    quarantine: Quarantine = field(default_factory=dict)
    duplicates: Dict[str, List[Tuple[dict, str]]] = field(default_factory=dict)
    stats: Dict[str, Dict[str, int]] = field(default_factory=dict)

    @property
    def clean(self) -> bool:
        """True when the datasets that will be loaded contain no validation errors."""
        return self.stats.get("_meta", {}).get("remaining_errors", 0) == 0

    def quarantined_count(self) -> int:
        return sum(len(v) for v in self.quarantine.values())


def run_business(
    folder: Path,
    today: Optional[date] = None,
    processed_dir: Optional[Path] = None,
    incremental: bool = True,
) -> PipelineResult:
    """
    Run the full pipeline for the business described by <folder>/manifest.json.

    Args:
        folder: business folder containing manifest.json and the raw files.
        today: reference date for overdue / future checks (default: date.today()).
        processed_dir: where previously processed data lives. When `incremental` is True and the
            folder exists, its records are loaded first so re-runs and overlapping exports
            never create duplicates.
        incremental: set False for a full refresh (ignore previously processed data).
    """
    folder = Path(folder)
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    business = manifest["business"]
    biz_id = business["business_id"]

    report = ValidationReport()
    quarantine: Quarantine = defaultdict(list)
    stats: Dict[str, Dict[str, int]] = {}
    rows: Rows = {name: [] for name in ENTITIES}

    # -- previously processed data (incremental mode) ------------------------------------- #
    if incremental and processed_dir is not None and Path(processed_dir).exists():
        existing = load_processed(Path(processed_dir))
        for name in ENTITIES:
            rows[name].extend(existing.get(name, []))
        stats["existing"] = _counts(existing)

    # -- 1-3. extract, clean, normalise ---------------------------------------------------- #
    rows["businesses"].append({**business, SOURCE_FILE: "manifest.json"})
    accounts = {a["account_id"]: a for a in manifest["accounts"]}
    for acc in manifest["accounts"]:
        rows["accounts"].append({"business_id": biz_id, "source": "manifest", **acc, SOURCE_FILE: "manifest.json"})

    extracted: Rows = defaultdict(list)
    for src in manifest["sources"]:
        path = folder / src["path"]
        res = _extract(src, path, biz_id, accounts, report)
        if res is None:
            continue
        report.extend(res.report)
        for entity, raw_row, reason in res.rejected:
            quarantine[entity].append(({**raw_row, SOURCE_FILE: src["path"]}, [reason]))
        for entity, new_rows in res.rows.items():
            for r in new_rows:
                r[SOURCE_FILE] = src["path"]
            extracted[entity].extend(new_rows)
            rows[entity].extend(new_rows)
    stats["extracted"] = _counts(extracted)

    # -- 4. dedupe ------------------------------------------------------------------------- #
    dd = dedupe(rows)
    rows = dd.rows
    report.extend(dd.report)
    stats["duplicates_removed"] = dd.removed_count()

    # -- 5. link --------------------------------------------------------------------------- #
    _link(rows, report)

    # -- 6. validate + quarantine ---------------------------------------------------------- #
    datasets, remaining_errors = _validate_and_quarantine(rows, quarantine, report, today)
    stats["quarantined"] = {k: len(v) for k, v in quarantine.items() if v}
    stats["loaded"] = {k: len(v) for k, v in datasets.items()}
    stats["_meta"] = {"remaining_errors": remaining_errors}

    return PipelineResult(
        business_id=biz_id,
        datasets=datasets,
        report=report,
        quarantine=dict(quarantine),
        duplicates=dict(dd.removed),
        stats=stats,
    )


def _extract(src: dict, path: Path, biz_id: str, accounts: dict, report: ValidationReport) -> Optional[MappingResult]:
    kind = src["type"]
    if not path.exists():
        report.add(Severity.error, "M-00", "manifest", src["path"], "file not found.")
        return None
    if kind == "bank_statement":
        acc = accounts.get(src["account_id"])
        if acc is None:
            report.add(Severity.error, "M-00", "manifest", src["path"], f"unknown account_id '{src['account_id']}'.")
            return None
        # Each statement file is checked for internal running-balance consistency; the first
        # row of each file sets the baseline (files can start mid-period).
        return map_bank_statement(path, biz_id, src["account_id"],
                                  layout=BANK_LAYOUTS[src.get("layout", "hdfc_csv")],
                                  currency=acc.get("currency", "INR"))
    if kind == "invoice_export":
        return map_invoice_export(path, biz_id, source=src.get("source", "zoho_invoices"))
    if kind == "expense_sheet":
        return map_expense_sheet(path, biz_id, source=src.get("source", "expense_sheet"))
    report.add(Severity.error, "M-00", "manifest", src["path"], f"unknown source type '{kind}'.")
    return None


def _counts(rows: Rows) -> Dict[str, int]:
    return {k: len(v) for k, v in rows.items() if v}


# --------------------------------------------------------------------------- #
# Linking
# --------------------------------------------------------------------------- #

def _link(rows: Rows, report: ValidationReport) -> None:
    """
    Resolve cross-source links:
      - bank inflow narration mentions 'INV-1001' -> transaction.invoice_id (only if that invoice exists)
      - expense 'Payment Ref' == bank transaction source_ref -> expense.transaction_id
    Unresolved candidates are dropped with a W-11 warning instead of becoming broken references.
    """
    invoice_ids = {r["invoice_id"] for r in rows["invoices"]}
    for t in rows["transactions"]:
        inv = t.get("invoice_id")
        if inv and inv not in invoice_ids:
            report.add(Severity.warning, "W-11", "transactions", t["transaction_id"],
                       f"narration mentions invoice '{inv}' which is not in the invoice export; link dropped.")
            t.pop("invoice_id")

    outflows_by_ref = {
        t["source_ref"]: t for t in rows["transactions"]
        if t.get("source_ref") and t["direction"] == TransactionDirection.outflow
    }
    for e in rows["expenses"]:
        ref = e.pop("_payment_ref", None)
        if not ref:
            continue
        txn = outflows_by_ref.get(ref)
        if txn is None:
            report.add(Severity.warning, "W-11", "expenses", e["expense_id"],
                       f"payment ref '{ref}' not found among bank outflows; not linked.")
            continue
        e["transaction_id"] = txn["transaction_id"]
        e["is_paid"] = True
        # the bank line is now explained by a categorised expense
        if not txn.get("category"):
            txn["category"] = e["category"]


# --------------------------------------------------------------------------- #
# Validation + quarantine
# --------------------------------------------------------------------------- #

def _public(row: dict) -> dict:
    """Drop internal pipeline keys (prefixed with '_') before validation."""
    return {k: v for k, v in row.items() if not k.startswith("_")}


def _validate_and_quarantine(rows: Rows, quarantine: Quarantine, report: ValidationReport,
                             today: Optional[date]) -> Tuple[Datasets, int]:
    # Pydantic (field + single-record rules): bad rows go straight to quarantine
    datasets: Datasets = {}
    origin: Dict[Tuple[str, int], dict] = {}  # (entity, id(model)) -> source row
    for name in ENTITIES:
        model_cls = CANONICAL_ENTITIES[name]
        pk = PRIMARY_KEYS[name]
        datasets[name] = []
        for row in rows.get(name, []):
            try:
                m = model_cls.model_validate(_public(row))
            except ValidationError as exc:
                reasons = [f"F-00 {'.'.join(map(str, e['loc'])) or 'record'}: {e['msg']}" for e in exc.errors()]
                for r in reasons:
                    report.add(Severity.error, "F-00", name, str(row.get(pk) or "?"), r[5:])
                quarantine[name].append((row, reasons))
                continue
            datasets[name].append(m)
            origin[(name, id(m))] = row

    # Dataset rules: quarantine records with errors, then re-check (removing a record can
    # break references to it, e.g. an expense pointing at a quarantined transaction).
    final = ValidationReport()
    for _ in range(MAX_QUARANTINE_PASSES):
        final = validate_dataset(datasets, today=today)
        bad: Dict[str, Dict[str, List[str]]] = defaultdict(lambda: defaultdict(list))
        for issue in final.errors:
            if issue.record_id is not None and issue.entity in datasets:
                bad[issue.entity][issue.record_id].append(f"{issue.rule} {issue.message}")
        if not bad:
            break
        for issue in final.errors:
            report.issues.append(issue)
        for name, ids in bad.items():
            pk = PRIMARY_KEYS[name]
            kept, seen = [], set()
            for m in datasets[name]:
                rid = getattr(m, pk)
                # for X-01 (duplicate key) keep the first record, quarantine the rest
                only_dup = all(r.startswith("X-01") for r in ids.get(rid, []))
                if rid in ids and not (only_dup and rid not in seen):
                    quarantine[name].append((origin.get((name, id(m)), m.model_dump(mode="json")), ids[rid]))
                else:
                    kept.append(m)
                seen.add(rid)
            datasets[name] = kept
    else:
        final = validate_dataset(datasets, today=today)

    # keep warnings/infos from the final, error-free pass only (earlier passes repeat them)
    report.issues.extend(i for i in final.issues if i.severity != Severity.error)
    report.issues.extend(i for i in final.errors)  # anything that could not be quarantined
    return datasets, len(final.errors)


# --------------------------------------------------------------------------- #
# Load
# --------------------------------------------------------------------------- #

def _atomic_write_csv(path: Path, fieldnames: List[str], rows: List[dict]) -> None:
    """Write to a temp file then rename, so a crash never leaves a half-written dataset."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    os.replace(tmp, path)


def _jsonable(row: dict) -> dict:
    out = {}
    for k, v in row.items():
        if isinstance(v, (list, dict)):
            out[k] = json.dumps(v, default=str)
        elif hasattr(v, "value"):  # Enum
            out[k] = v.value
        else:
            out[k] = "" if v is None else str(v)
    return out


def write_processed(datasets: Datasets, out_dir: Path) -> List[Path]:
    """Write one CSV per entity: data/processed/<business_id>/<entity>.csv."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for name, models in datasets.items():
        fields = list(CANONICAL_ENTITIES[name].model_fields)
        rows = []
        for m in models:
            row = m.model_dump(mode="json")
            if "line_items" in row:
                row["line_items"] = json.dumps(row["line_items"])
            rows.append(row)
        path = out_dir / f"{name}.csv"
        _atomic_write_csv(path, fields, rows)
        written.append(path)
    return written


def write_run_outputs(result: PipelineResult, out_dir: Path) -> List[Path]:
    """
    Write everything a run produces:
        <entity>.csv                 clean, validated data (input for the finance engine / DB load)
        _quarantine/<entity>.csv     rejected rows + '_errors' column (fix the source and re-run)
        _duplicates.csv              every record removed by de-duplication and why
        _etl_report.json             stats per stage + all issues
    """
    out_dir = Path(out_dir)
    written = write_processed(result.datasets, out_dir)

    qdir = out_dir / "_quarantine"
    if qdir.exists():
        for old in qdir.glob("*.csv"):
            old.unlink()
    for name, items in result.quarantine.items():
        if not items:
            continue
        qdir.mkdir(parents=True, exist_ok=True)
        rows = [{**_jsonable(r), "_errors": " | ".join(reasons)} for r, reasons in items]
        fields = list(dict.fromkeys(k for r in rows for k in r))
        path = qdir / f"{name}.csv"
        _atomic_write_csv(path, fields, rows)
        written.append(path)

    dup_rows = [
        {"entity": name, "record_id": str(r.get(PRIMARY_KEYS.get(name, ""), "")),
         "source_file": r.get(SOURCE_FILE, ""), "reason": reason}
        for name, items in result.duplicates.items() for r, reason in items
    ]
    path = out_dir / "_duplicates.csv"
    _atomic_write_csv(path, ["entity", "record_id", "source_file", "reason"], dup_rows)
    written.append(path)

    report_path = out_dir / "_etl_report.json"
    report_path.write_text(json.dumps({
        "business_id": result.business_id,
        "schema_version": SCHEMA_VERSION,
        "run_at": datetime.now(timezone.utc).isoformat(),
        "clean": result.clean,
        "stats": {k: v for k, v in result.stats.items() if not k.startswith("_")},
        "issue_counts": {s.value: sum(1 for i in result.report.issues if i.severity == s) for s in Severity},
        "issues": [
            {"severity": i.severity.value, "rule": i.rule, "entity": i.entity,
             "record_id": i.record_id, "message": i.message}
            for i in result.report.issues
        ],
    }, indent=2), encoding="utf-8")
    written.append(report_path)
    return written


def load_processed(folder: Path) -> Rows:
    """Read processed CSVs back as raw rows (used for incremental runs)."""
    rows: Rows = {}
    for name in ENTITIES:
        path = Path(folder) / f"{name}.csv"
        if not path.exists():
            continue
        with open(path, newline="", encoding="utf-8") as fh:
            entity_rows = []
            for r in csv.DictReader(fh):
                row = {k: v for k, v in r.items() if v not in ("", None)}  # let model defaults apply
                if "line_items" in row:
                    row["line_items"] = json.loads(row["line_items"])
                row[SOURCE_FILE] = f"processed/{name}.csv"
                entity_rows.append(row)
        rows[name] = entity_rows
    return rows
