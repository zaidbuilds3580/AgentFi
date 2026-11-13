"""
Read-only access to one business's canonical data for the finance engine.

The engine never reads raw files: it works on the validated output of the ETL
(data/processed/<business_id>/<entity>.csv) or on in-memory canonical models (tests,
future DB repository). Every record is filtered by business_id (tenant isolation) and
sorted deterministically, so the same data always gives the same results.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Dict, Iterable, Mapping, Optional, Sequence, Tuple

from pydantic import BaseModel

from finance_engine.schema.canonical import (
    Account,
    Business,
    Counterparty,
    Expense,
    Invoice,
    Transaction,
)


from finance_engine.errors import BusinessNotFoundError, InvalidInputError  # noqa: E402  (re-exported)


@dataclass(frozen=True)
class FinanceData:
    business: Business
    accounts: Tuple[Account, ...] = ()
    counterparties: Tuple[Counterparty, ...] = ()
    transactions: Tuple[Transaction, ...] = ()
    invoices: Tuple[Invoice, ...] = ()
    expenses: Tuple[Expense, ...] = ()

    # ------------------------------------------------------------------ builders

    @classmethod
    def from_datasets(cls, datasets: Mapping[str, Sequence[BaseModel]], business_id: str) -> "FinanceData":
        """Build from canonical models (e.g. PipelineResult.datasets), keeping only one tenant."""
        business_id = (business_id or "").strip()
        if not business_id:
            raise InvalidInputError("business_id must not be empty.")
        biz = next((b for b in datasets.get("businesses", []) if b.business_id == business_id), None)
        if biz is None:
            raise BusinessNotFoundError(f"business '{business_id}' not found.")

        def mine(name: str, sort_key) -> tuple:
            return tuple(sorted((r for r in datasets.get(name, []) if r.business_id == business_id), key=sort_key))

        return cls(
            business=biz,
            accounts=mine("accounts", lambda a: a.account_id),
            counterparties=mine("counterparties", lambda c: c.counterparty_id),
            transactions=mine("transactions", lambda t: (t.transaction_date, t.transaction_id)),
            invoices=mine("invoices", lambda i: (i.issue_date, i.invoice_id)),
            expenses=mine("expenses", lambda e: (e.expense_date, e.expense_id)),
        )

    @classmethod
    def from_processed(cls, business_id: str, processed_dir: Optional[Path] = None) -> "FinanceData":
        """Load data/processed/<business_id>/ written by the ETL (scripts/run_etl.py)."""
        from config.settings import settings
        from finance_engine.etl.pipeline import load_processed
        from finance_engine.schema.canonical import CANONICAL_ENTITIES

        business_id = validate_business_id(business_id)
        folder = _resolve_dir(processed_dir or settings.PROCESSED_DATA_DIR) / business_id
        if not (folder / "businesses.csv").exists():
            raise BusinessNotFoundError(
                f"no processed data for business '{business_id}'. Run: python -m scripts.run_etl"
            )
        rows = load_processed(folder)
        datasets: Dict[str, list] = {
            name: [CANONICAL_ENTITIES[name].model_validate(_public(r)) for r in rows.get(name, [])]
            for name in CANONICAL_ENTITIES
        }
        return cls.from_datasets(datasets, business_id)

    # ------------------------------------------------------------------ helpers

    @property
    def business_id(self) -> str:
        return self.business.business_id

    @property
    def base_currency(self) -> str:
        return self.business.base_currency

    def counterparty_name(self, counterparty_id: Optional[str]) -> str:
        if not counterparty_id:
            return "Unknown"
        for c in self.counterparties:
            if c.counterparty_id == counterparty_id:
                return c.name
        return counterparty_id

    def bank_data_range(self, accounts: Optional[Iterable[Account]] = None) -> Optional[Tuple[date, date]]:
        """First and last transaction date (None when there are no transactions)."""
        ids = {a.account_id for a in accounts} if accounts is not None else None
        dates = [t.transaction_date for t in self.transactions if ids is None or t.account_id in ids]
        return (min(dates), max(dates)) if dates else None


def _public(row: dict) -> dict:
    return {k: v for k, v in row.items() if not k.startswith("_")}


# ============================================================================= #
# Loading for tools: validation, auto-build and caching
# ============================================================================= #

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SAMPLES_DIR = PROJECT_ROOT / "data" / "samples"
_BUSINESS_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-]{0,63}$")
_CACHE: Dict[tuple, Tuple[tuple, "FinanceData"]] = {}
_CACHE_LOCK = threading.Lock()


def validate_business_id(business_id: object) -> str:
    """
    Strip and validate a business_id. IDs are used as folder names, so only letters, digits,
    '_' and '-' are allowed (max 64 characters). This blocks path traversal such as '../x'.
    """
    value = str(business_id or "").strip()
    if not value:
        raise InvalidInputError("business_id must not be empty.")
    if not _BUSINESS_ID_RE.match(value):
        raise InvalidInputError("business_id may only contain letters, digits, '_' and '-' (max 64 characters).")
    return value


def _resolve_dir(path) -> Path:
    p = Path(path)
    return p if p.is_absolute() else PROJECT_ROOT / p


def _signature(paths) -> tuple:
    """Cheap change detector: name, size and modification time of every file involved."""
    sig = []
    for p in sorted(paths):
        try:
            st = p.stat()
            sig.append((str(p), st.st_size, st.st_mtime_ns))
        except FileNotFoundError:
            sig.append((str(p), -1, -1))
    return tuple(sig)


def _source_folder(business_id: str) -> Optional[Path]:
    """A raw (data/raw/<id>) or sample (data/samples/<id>) folder with a manifest.json."""
    from config.settings import settings
    for root in (_resolve_dir(settings.RAW_DATA_DIR), SAMPLES_DIR):
        folder = root / business_id
        if (folder / "manifest.json").exists():
            return folder
    return None


def load_business(business_id: str, processed_dir: Optional[Path] = None, auto_build: bool = True) -> "FinanceData":
    """
    Load one business for the finance tools.

    1. data/processed/<id>/ (output of scripts/run_etl.py), if it exists
    2. otherwise, when `auto_build` is on, run the ETL IN MEMORY on data/raw/<id>/ or
       data/samples/<id>/ (whichever has a manifest.json). Nothing is written to disk.
    3. otherwise BusinessNotFoundError

    Results are cached per folder and re-loaded automatically when any file changes,
    so repeated tool calls in one query don't re-read the CSVs. Thread-safe.
    """
    from config.settings import settings

    business_id = validate_business_id(business_id)
    processed = _resolve_dir(processed_dir or settings.PROCESSED_DATA_DIR) / business_id

    if (processed / "businesses.csv").exists():
        key = ("processed", str(processed))
        sig = _signature(processed.glob("*.csv"))
        loader = lambda: FinanceData.from_processed(business_id, processed.parent)  # noqa: E731
    else:
        source = _source_folder(business_id) if auto_build else None
        if source is None:
            # message is user-facing (agents, API): no absolute server paths
            raise BusinessNotFoundError(
                f"no data for business '{business_id}': no processed data and no manifest.json in "
                f"data/raw/{business_id} or data/samples/{business_id}."
            )
        key = ("source", str(source))
        sig = _signature([p for p in source.rglob("*") if p.is_file()])

        def loader():
            from finance_engine.etl.pipeline import run_business
            result = run_business(source, incremental=False)
            return FinanceData.from_datasets(result.datasets, business_id)

    with _CACHE_LOCK:
        hit = _CACHE.get(key)
        if hit is not None and hit[0] == sig:
            return hit[1]
    data = loader()
    with _CACHE_LOCK:
        _CACHE[key] = (sig, data)
    return data


def clear_cache() -> None:
    """Forget all cached business data (e.g. after replacing processed files in tests)."""
    with _CACHE_LOCK:
        _CACHE.clear()
