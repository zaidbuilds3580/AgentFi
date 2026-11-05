"""
ETL layer: maps real-world exports (bank statements, invoice exports, expense sheets)
onto the canonical schema, then cleans, normalises, de-duplicates, validates and loads them.

    python -m scripts.run_etl            # see docs/etl_pipeline.md
"""

from finance_engine.etl.cleaning import clean_text, description_key, party_key
from finance_engine.etl.dedup import DedupResult, dedupe
from finance_engine.etl.mappers import (
    BANK_LAYOUTS,
    BankLayout,
    MappingResult,
    map_bank_statement,
    map_expense_sheet,
    map_invoice_export,
)
from finance_engine.etl.pipeline import (
    PipelineResult,
    load_processed,
    run_business,
    write_processed,
    write_run_outputs,
)

__all__ = [
    "BANK_LAYOUTS",
    "BankLayout",
    "DedupResult",
    "MappingResult",
    "PipelineResult",
    "clean_text",
    "dedupe",
    "description_key",
    "load_processed",
    "map_bank_statement",
    "map_expense_sheet",
    "map_invoice_export",
    "party_key",
    "run_business",
    "write_processed",
    "write_run_outputs",
]
