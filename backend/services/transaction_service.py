"""
Service for transaction persistence and bulk ingestion.
"""

from typing import Any, Dict, List
import uuid
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from backend.models.transactions import Transaction


def bulk_insert_transactions(
    session: Session,
    business_id: uuid.UUID,
    rows: List[Dict[str, Any]],
    source_dataset: str,
    chunk_size: int = 1000,
) -> int:
    """
    Bulk inserts transactions with tenant enforcement and partial unique dedup.
    Returns the number of rows actually inserted.
    """
    if not rows:
        return 0

    inserted_total = 0
    for i in range(0, len(rows), chunk_size):
        chunk = rows[i : i + chunk_size]
        payload = []
        for r in chunk:
            item = dict(r)
            # Tenant isolation: business_id is ALWAYS enforced from caller
            item["business_id"] = business_id
            item["source_dataset"] = source_dataset
            payload.append(item)

        stmt = (
            insert(Transaction)
            .values(payload)
            .on_conflict_do_nothing(
                index_elements=["business_id", "source_dataset", "source_record_id"],
                index_where=text("(source_dataset IS NOT NULL) AND (source_record_id IS NOT NULL)"),
            )
            .returning(Transaction.id)
        )
        res = session.execute(stmt)
        inserted_total += len(res.fetchall())

    return inserted_total
