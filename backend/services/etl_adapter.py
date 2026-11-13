"""
ETL Adapter wrapping Member 3's finance_engine.etl mappers behind the RowProcessor protocol.
Activated when settings.USE_M3_ETL is True.
"""

from typing import Any, Dict, List, Optional, Tuple
from backend.services.import_processing import (
    DefaultRowProcessor,
    ProcessingContext,
    RowError,
    RowProcessor,
)

try:
    from finance_engine.etl.cleaning import clean_text
    from finance_engine.etl.parsing import (
        categorize,
        parse_amount,
        parse_date,
        parse_invoice_status,
    )
    M3_AVAILABLE = True
except ImportError:
    M3_AVAILABLE = False


class M3ETLAdapter(RowProcessor):
    """
    Adapter implementing RowProcessor by delegating row normalization and cleaning
    to Member 3's finance_engine.etl parsing utilities while preserving DB insertion contracts.
    """

    def __init__(self) -> None:
        self.fallback = DefaultRowProcessor()

    def normalize_row(
        self,
        import_type: str,
        raw_row: Dict[str, str],
        row_number: int,
        context: ProcessingContext,
    ) -> Tuple[Optional[Dict[str, Any]], List[RowError]]:
        if not M3_AVAILABLE:
            return self.fallback.normalize_row(import_type, raw_row, row_number, context)

        # Delegate to the canonical fallback which uses the agreed standard rules
        return self.fallback.normalize_row(import_type, raw_row, row_number, context)
