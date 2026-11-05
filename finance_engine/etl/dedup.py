"""
De-duplication (ETL stage 4).

Runs on mapped rows (plain dicts) BEFORE Pydantic validation, across all source files of
a business and across previously processed data (incremental loads).

Rows carry an internal `_source_file` key set by the pipeline. Rows loaded from
data/processed/ come first, then new files in manifest order.

Two kinds of entities:

* Events (transactions): immutable once they happened → the FIRST copy wins.
    D-01  same (account, bank reference)                       → duplicate
    D-02  same (account, date, amount, direction, narration) seen in a DIFFERENT file
          (overlapping statement exports / re-running the same file) → duplicate.
          Identical rows inside ONE file are kept (two ₹250 Uber rides on one day are
          real); the validator flags them as W-03 for review.
          Matching is count-aware: if file A has 2 identical rows and file B has 3,
          one row from B is kept.

* Documents (businesses, accounts, counterparties, invoices, expenses): may be updated
  in later exports (an invoice becomes 'paid') → the NEWEST copy wins, and fields that
  are empty in the newer copy are filled from the older one.
    D-03  same invoice_id
    D-04  same expense (vendor + bill no., or vendor + date + amount + description)
    D-05  same counterparty_id (spelling variants already collapsed by party_key)
    D-06  same business / account
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Callable, Dict, Hashable, List, Optional, Tuple

from finance_engine.etl.cleaning import description_key, display_name
from finance_engine.schema.validators import Severity, ValidationReport

Rows = Dict[str, List[dict]]
SOURCE_FILE = "_source_file"


@dataclass
class DedupResult:
    rows: Rows
    removed: Dict[str, List[Tuple[dict, str]]] = field(default_factory=lambda: defaultdict(list))
    report: ValidationReport = field(default_factory=ValidationReport)

    def removed_count(self) -> Dict[str, int]:
        return {k: len(v) for k, v in self.removed.items() if v}


def dedupe(rows: Rows) -> DedupResult:
    result = DedupResult(rows={})
    result.rows["transactions"] = _dedupe_transactions(rows.get("transactions", []), result)

    doc_keys: Dict[str, Tuple[str, Callable[[dict], Hashable]]] = {
        "businesses": ("D-06", lambda r: r["business_id"]),
        "accounts": ("D-06", lambda r: (r["business_id"], r["account_id"])),
        "counterparties": ("D-05", lambda r: (r["business_id"], r["counterparty_id"])),
        "invoices": ("D-03", lambda r: (r["business_id"], r["invoice_id"])),
        "expenses": ("D-04", _expense_key),
    }
    for entity, (rule, key_fn) in doc_keys.items():
        result.rows[entity] = _dedupe_documents(entity, rows.get(entity, []), key_fn, rule, result)

    for entity, entity_rows in rows.items():  # anything else passes through untouched
        result.rows.setdefault(entity, entity_rows)
    return result


# ------------------------------- events -------------------------------------- #

def _txn_content_key(r: dict) -> tuple:
    return (r["business_id"], r["account_id"], str(r["transaction_date"]), str(r["amount"]),
            str(getattr(r["direction"], "value", r["direction"])), description_key(r.get("description")))


def _dedupe_transactions(txns: List[dict], result: DedupResult) -> List[dict]:
    kept: List[dict] = []
    by_ref: Dict[tuple, dict] = {}
    occurrences: Dict[tuple, Counter] = defaultdict(Counter)  # content key -> rows seen per file
    kept_per_file: Dict[tuple, Counter] = defaultdict(Counter)  # content key -> rows kept per file

    for r in txns:
        src = r.get(SOURCE_FILE, "?")
        ref = r.get("source_ref")
        ckey = _txn_content_key(r)
        occurrences[ckey][src] += 1

        if ref:
            ref_key = (r["business_id"], r["account_id"], ref)
            first = by_ref.get(ref_key)
            if first is not None:
                same_file = first.get(SOURCE_FILE) == src
                _drop(result, "transactions", r, "D-01",
                      Severity.warning if same_file else Severity.info,
                      f"bank ref '{ref}' already loaded from {first.get(SOURCE_FILE)}"
                      + (" (same file: exported twice?)" if same_file else ""))
                continue

        occ = occurrences[ckey][src]
        others = max((n for f, n in kept_per_file[ckey].items() if f != src), default=0)
        if occ <= others and not (ref and _all_kept_have_other_refs(kept, ckey, ref)):
            _drop(result, "transactions", r, "D-02", Severity.info,
                  f"same date/amount/narration already loaded from another file (overlapping export).")
            continue

        kept.append(r)
        kept_per_file[ckey][src] += 1
        if ref:
            by_ref[(r["business_id"], r["account_id"], ref)] = r
    return kept


def _all_kept_have_other_refs(kept: List[dict], ckey: tuple, ref: str) -> bool:
    """Two rows with DIFFERENT bank refs are different transactions even if everything else matches."""
    matches = [k for k in kept if _txn_content_key(k) == ckey]
    return bool(matches) and all(k.get("source_ref") and k["source_ref"] != ref for k in matches)


# ------------------------------ documents ------------------------------------ #

def _expense_key(r: dict) -> Hashable:
    if r.get("source_ref"):
        return (r["business_id"], r.get("vendor_id"), r["source_ref"])
    return (r["business_id"], r.get("vendor_id"), str(r["expense_date"]), str(r["amount"]),
            description_key(r.get("description")))


def _dedupe_documents(entity: str, docs: List[dict], key_fn, rule: str, result: DedupResult) -> List[dict]:
    merged: "Dict[Hashable, dict]" = {}
    order: List[Hashable] = []
    for r in docs:
        key = key_fn(r)
        old = merged.get(key)
        if old is None:
            merged[key] = r
            order.append(key)
            continue
        merged[key] = _merge_newer(entity, old, r)
        if old.get(SOURCE_FILE) == r.get(SOURCE_FILE) and entity in ("invoices", "expenses"):
            _drop(result, entity, old, rule, Severity.warning,
                  f"entered twice in {r.get(SOURCE_FILE)}; copies merged.")
            continue
        if _content(old) != _content(r):
            msg = f"updated by newer copy from {r.get(SOURCE_FILE)} (was {old.get(SOURCE_FILE)})."
        else:
            msg = f"identical copy in {r.get(SOURCE_FILE)} ignored."
        _drop(result, entity, old, rule, Severity.info, msg)
    return [merged[k] for k in order]


def _merge_newer(entity: str, old: dict, new: dict) -> dict:
    out = dict(new)
    for k, v in old.items():
        if out.get(k) in (None, "") and v not in (None, ""):
            out[k] = v
    if entity == "counterparties":
        out["name"] = display_name(old.get("name"), new.get("name"))
    # keep the original ingestion time and the original IDs other records may point to
    for k in ("ingested_at", "expense_id"):
        if old.get(k):
            out[k] = old[k]
    return out


def _content(r: dict) -> dict:
    return {k: str(v) for k, v in r.items() if not k.startswith("_") and k != "ingested_at"}


def _pk_of(entity: str, r: dict) -> Optional[str]:
    from finance_engine.schema.validators import PRIMARY_KEYS
    return r.get(PRIMARY_KEYS.get(entity, ""))


def _drop(result: DedupResult, entity: str, row: dict, rule: str, severity: Severity, msg: str) -> None:
    result.removed[entity].append((row, f"{rule}: {msg}"))
    result.report.add(severity, rule, entity, _pk_of(entity, row), msg)
