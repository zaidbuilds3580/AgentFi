"""
Read-only access to one business's canonical data for the finance engine.

The engine never reads raw files: it works on the validated output of the ETL
(data/processed/<business_id>/<entity>.csv) or on in-memory canonical models (tests,
future DB repository). Every record is filtered by business_id (tenant isolation) and
sorted deterministically, so the same data always gives the same results.
"""

from __future__ import annotations

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


class BusinessNotFoundError(LookupError):
    """No processed data exists for the requested business."""


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
            raise ValueError("business_id must not be empty.")
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

        business_id = (business_id or "").strip()
        if not business_id:
            raise ValueError("business_id must not be empty.")
        root = Path(processed_dir) if processed_dir else Path(settings.PROCESSED_DATA_DIR)
        if not root.is_absolute():
            root = Path(__file__).resolve().parents[1] / root
        folder = root / business_id
        if not (folder / "businesses.csv").exists():
            raise BusinessNotFoundError(
                f"no processed data for business '{business_id}' in {folder}. Run: python -m scripts.run_etl"
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
