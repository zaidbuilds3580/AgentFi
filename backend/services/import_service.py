"""
Import orchestration service for validating, mapping, deduplicating, and persisting CSV ingest batches.
"""

from collections import Counter
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
from typing import Any, Dict, List, Optional, Sequence, Tuple
import uuid

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.core.config import settings
from backend.models.accounts import Account
from backend.models.businesses import Business
from backend.models.constants import (
    DEFAULT_SOURCE_DATASET,
    ERR_DUPLICATE_FILE,
    ERR_DUPLICATE_HEADERS,
    ERR_EMPTY_FILE,
    ERR_HEADER_ONLY,
    ERR_INVALID_TYPE,
    ERR_MISSING_REQUIRED,
    IMPORT_STATUS_COMPLETED,
    IMPORT_STATUS_COMPLETED_WITH_ERRORS,
    IMPORT_STATUS_FAILED,
    IMPORT_STATUS_PROCESSING,
    IMPORT_TYPES,
    ON_ERROR_MODE_ABORT,
    ON_ERROR_MODE_SKIP,
)
from backend.models.customers import Customer
from backend.models.import_batch import ImportBatch
from backend.models.import_row_error import ImportRowError
from backend.models.vendors import Vendor
from backend.schemas.imports import ImportResult, RowErrorRead
from backend.services.etl_adapter import M3ETLAdapter
from backend.services.expense_service import bulk_insert_expenses
from backend.services.import_processing import (
    DefaultRowProcessor,
    ProcessingContext,
    RowError,
    RowProcessor,
)
from backend.services.invoice_service import bulk_insert_invoices
from backend.services.transaction_service import bulk_insert_transactions


# Column specification and alias maps
TRANSACTION_REQUIRED_HEADERS = {"transaction_date", "transaction_type", "amount"}
TRANSACTION_OPTIONAL_HEADERS = {
    "description",
    "account_name",
    "category",
    "counterparty",
    "reference",
    "currency",
    "source_record_id",
}
TRANSACTION_ALIASES = {
    "date": "transaction_date",
    "txn_date": "transaction_date",
    "type": "transaction_type",
    "direction": "transaction_type",
    "ref": "reference",
    "account": "account_name",
}

INVOICE_REQUIRED_HEADERS = {
    "invoice_number",
    "customer_name",
    "invoice_date",
    "due_date",
    "amount_total",
}
INVOICE_OPTIONAL_HEADERS = {
    "amount_paid",
    "status",
    "payment_date",
    "currency",
    "notes",
    "source_record_id",
}
INVOICE_ALIASES = {
    "date": "invoice_date",
    "total": "amount_total",
    "invoice_amount": "amount_total",
    "amount": "amount_total",
    "customer": "customer_name",
    "ref": "source_record_id",
}

EXPENSE_REQUIRED_HEADERS = {"expense_date", "category", "amount"}
EXPENSE_OPTIONAL_HEADERS = {
    "vendor_name",
    "description",
    "payment_method",
    "account_name",
    "currency",
    "source_record_id",
}
EXPENSE_ALIASES = {
    "date": "expense_date",
    "vendor": "vendor_name",
    "account": "account_name",
    "ref": "source_record_id",
}


class DuplicateFileError(Exception):
    def __init__(self, existing_batch_id: uuid.UUID):
        self.existing_batch_id = existing_batch_id
        super().__init__(f"Duplicate file: already imported in batch {existing_batch_id}")


class ImportService:
    def __init__(self, db: Session):
        self.db = db
        if settings.USE_M3_ETL:
            self.processor: RowProcessor = M3ETLAdapter()
        else:
            self.processor: RowProcessor = DefaultRowProcessor()

    def check_duplicate_file(
        self,
        business_id: uuid.UUID,
        import_type: str,
        file_sha256: str,
    ) -> Optional[uuid.UUID]:
        stmt = (
            select(ImportBatch.id)
            .where(
                ImportBatch.business_id == business_id,
                ImportBatch.import_type == import_type,
                ImportBatch.file_sha256 == file_sha256,
                ImportBatch.status.in_([IMPORT_STATUS_COMPLETED, IMPORT_STATUS_COMPLETED_WITH_ERRORS]),
            )
            .limit(1)
        )
        return self.db.scalars(stmt).first()

    def process_import(
        self,
        business_id: uuid.UUID,
        user_id: Optional[uuid.UUID],
        import_type: str,
        file_bytes: bytes,
        original_filename: str,
        source_dataset: str = DEFAULT_SOURCE_DATASET,
        on_error_mode: str = ON_ERROR_MODE_SKIP,
    ) -> ImportResult:
        if import_type not in IMPORT_TYPES:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"code": ERR_INVALID_TYPE, "message": f"Unsupported import_type: {import_type}"},
            )

        # 1. Sanitize file content and compute hash
        if b"\x00" in file_bytes:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"code": "NUL_BYTE_DETECTED", "message": "File contains binary NUL bytes."},
            )

        file_sha256 = hashlib.sha256(file_bytes).hexdigest()
        file_size = len(file_bytes)

        # Check duplicate file
        existing_batch_id = self.check_duplicate_file(business_id, import_type, file_sha256)
        if existing_batch_id:
            raise DuplicateFileError(existing_batch_id)

        # Decode CSV content
        csv_text = self._decode_csv(file_bytes)

        # 2. Parse headers and rows
        header_mapping, raw_rows, warnings = self._parse_and_validate_csv(csv_text, import_type)

        # 3. Create initial ImportBatch record in 'processing' status and commit immediately
        batch = ImportBatch(
            business_id=business_id,
            created_by=user_id,
            import_type=import_type,
            source_dataset=source_dataset,
            original_filename=original_filename,
            file_size_bytes=file_size,
            file_sha256=file_sha256,
            status=IMPORT_STATUS_PROCESSING,
            on_error_mode=on_error_mode,
            started_at=datetime.now(timezone.utc),
        )
        self.db.add(batch)
        self.db.commit()
        self.db.refresh(batch)

        # 4. Prepare ProcessingContext
        business = self.db.get(Business, business_id)
        business_currency = business.currency if business else "INR"

        accounts = self.db.scalars(select(Account).where(Account.business_id == business_id)).all()
        account_map = {acc.name.lower(): acc.id for acc in accounts}

        customers = self.db.scalars(select(Customer).where(Customer.business_id == business_id)).all()
        customer_cache = {c.name.lower(): c.id for c in customers}

        vendors = self.db.scalars(select(Vendor).where(Vendor.business_id == business_id)).all()
        vendor_cache = {v.name.lower(): v.id for v in vendors}

        context = ProcessingContext(
            business_id=business_id,
            business_currency=business_currency,
            session=self.db,
            account_map=account_map,
            customer_cache=customer_cache,
            vendor_cache=vendor_cache,
            warnings=warnings,
        )

        valid_rows: List[Dict[str, Any]] = []
        row_errors: List[RowError] = []
        raw_error_dicts: List[Dict[str, Any]] = []

        occurrence_tracker: Counter = Counter()

        # 5. Row-by-row normalization and validation
        for row_idx, raw_dict in enumerate(raw_rows, start=2):  # row 1 is header
            mapped_dict = self._apply_alias_mapping(raw_dict, header_mapping)
            normalized_row, errors = self.processor.normalize_row(import_type, mapped_dict, row_idx, context)

            if errors:
                for err in errors:
                    row_errors.append(err)
                    raw_snippet = json.dumps(mapped_dict)[:2048]
                    raw_error_dicts.append({
                        "batch_id": batch.id,
                        "business_id": business_id,
                        "row_number": err.row_number,
                        "field": err.field,
                        "code": err.code,
                        "message": err.message,
                        "raw_row": mapped_dict,
                    })
            elif normalized_row:
                # Deduplication key generation
                if not normalized_row.get("source_record_id"):
                    key_repr = self._make_dedup_key(import_type, normalized_row)
                    occurrence_tracker[key_repr] += 1
                    occ = occurrence_tracker[key_repr]
                    hash_input = f"{import_type}:{key_repr}:{occ}".encode("utf-8")
                    normalized_row["source_record_id"] = hashlib.sha1(hash_input).hexdigest()

                valid_rows.append(normalized_row)

        total_rows = len(raw_rows)
        error_rows_count = len({e.row_number for e in row_errors})
        inserted_rows_count = 0
        duplicate_rows_count = 0

        # 6. Bulk persistence transaction
        try:
            if on_error_mode == ON_ERROR_MODE_ABORT and row_errors:
                # Abort mode: rollback any created entities and do not insert data rows
                self.db.rollback()
                batch_status = IMPORT_STATUS_FAILED
            else:
                # Insert valid rows
                if valid_rows:
                    if import_type == "transactions":
                        inserted_rows_count = bulk_insert_transactions(
                            self.db, business_id, valid_rows, source_dataset
                        )
                    elif import_type == "invoices":
                        inserted_rows_count = bulk_insert_invoices(
                            self.db, business_id, valid_rows, source_dataset
                        )
                    elif import_type == "expenses":
                        inserted_rows_count = bulk_insert_expenses(
                            self.db, business_id, valid_rows, source_dataset
                        )

                    duplicate_rows_count = len(valid_rows) - inserted_rows_count

                self.db.commit()

                if error_rows_count == 0:
                    batch_status = IMPORT_STATUS_COMPLETED
                elif inserted_rows_count > 0:
                    batch_status = IMPORT_STATUS_COMPLETED_WITH_ERRORS
                else:
                    batch_status = IMPORT_STATUS_FAILED

        except Exception as e:
            self.db.rollback()
            batch_status = IMPORT_STATUS_FAILED
            context.warnings.append(f"Persistence error: {str(e)}")

        # 7. Finalize batch and persist row errors in a dedicated short transaction
        try:
            # Refresh batch instance attached to session
            batch = self.db.get(ImportBatch, batch.id)
            batch.status = batch_status
            batch.total_rows = total_rows
            batch.inserted_rows = inserted_rows_count
            batch.duplicate_rows = duplicate_rows_count
            batch.error_rows = error_rows_count
            batch.completed_at = datetime.now(timezone.utc)

            error_summary = None
            if len(row_errors) > 1000:
                error_summary = {
                    "total_error_records": len(row_errors),
                    "persisted_error_records": 1000,
                    "truncated": True,
                }
            if batch_status == IMPORT_STATUS_FAILED and on_error_mode == ON_ERROR_MODE_ABORT:
                error_summary = error_summary or {}
                error_summary["abort_reason"] = f"Aborted due to {error_rows_count} row errors with on_error=abort"
            batch.error_summary = error_summary

            # Persist up to 1000 row errors
            to_persist = raw_error_dicts[:1000]
            for err_dict in to_persist:
                err_obj = ImportRowError(
                    batch_id=batch.id,
                    business_id=business_id,
                    row_number=err_dict["row_number"],
                    field=err_dict["field"],
                    code=err_dict["code"],
                    message=err_dict["message"],
                    raw_row=err_dict["raw_row"],
                )
                self.db.add(err_obj)

            self.db.commit()
            self.db.refresh(batch)

        except Exception as fin_exc:
            self.db.rollback()
            raise fin_exc

        errors_preview = [
            RowErrorRead(
                row_number=e.row_number,
                field=e.field,
                code=e.code,
                message=e.message,
            )
            for e in row_errors[:50]
        ]

        return ImportResult(
            batch_id=batch.id,
            import_type=batch.import_type,
            status=batch.status,
            source_dataset=batch.source_dataset,
            original_filename=batch.original_filename,
            total_rows=batch.total_rows,
            inserted_rows=batch.inserted_rows,
            duplicate_rows=batch.duplicate_rows,
            error_rows=batch.error_rows,
            warnings=context.warnings,
            errors=errors_preview,
            started_at=batch.started_at,
            completed_at=batch.completed_at,
        )

    def _decode_csv(self, file_bytes: bytes) -> str:
        for enc in settings.CSV_ENCODINGS:
            try:
                return file_bytes.decode(enc)
            except UnicodeDecodeError:
                continue
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "INVALID_ENCODING", "message": "Failed to decode CSV with utf-8 or cp1252."},
        )

    def _parse_and_validate_csv(
        self,
        csv_text: str,
        import_type: str,
    ) -> Tuple[Dict[str, str], List[Dict[str, str]], List[str]]:
        warnings: List[str] = []
        lines = [line for line in csv_text.splitlines() if line.strip()]
        if not lines:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"code": ERR_EMPTY_FILE, "message": "CSV file is empty."},
            )

        # Sniff delimiter
        sample = "\n".join(lines[:10])
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",\t;")
            delimiter = dialect.delimiter
        except Exception:
            delimiter = ","

        reader = csv.reader(io.StringIO(csv_text), delimiter=delimiter)
        raw_header = next(reader, None)
        if not raw_header:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"code": ERR_EMPTY_FILE, "message": "CSV file contains no headers."},
            )

        cleaned_headers = [h.strip().lower() for h in raw_header]
        if not any(cleaned_headers):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"code": ERR_EMPTY_FILE, "message": "CSV headers are blank."},
            )

        # Check for duplicate headers
        if len(cleaned_headers) != len(set(cleaned_headers)):
            duplicates = [h for h, count in Counter(cleaned_headers).items() if count > 1]
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"code": ERR_DUPLICATE_HEADERS, "message": f"Duplicate CSV headers: {duplicates}"},
            )

        # Check required headers and map aliases
        required_set, optional_set, alias_map = self._get_header_spec(import_type)
        header_mapping: Dict[str, str] = {}
        canonical_present: set = set()
        unknown_headers: List[str] = []

        for h in cleaned_headers:
            canonical = alias_map.get(h, h)
            header_mapping[h] = canonical
            if canonical in required_set or canonical in optional_set:
                canonical_present.add(canonical)
            else:
                unknown_headers.append(h)

        if unknown_headers:
            warnings.append(f"Ignored unknown columns: {sorted(unknown_headers)}")

        missing_required = required_set - canonical_present
        if missing_required:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": ERR_MISSING_REQUIRED,
                    "message": f"Missing required headers: {sorted(missing_required)}",
                    "missing": sorted(missing_required),
                },
            )

        rows = []
        for row in reader:
            if not any(c.strip() for c in row):
                continue
            row_dict = {
                cleaned_headers[i]: row[i].strip() if i < len(row) else ""
                for i in range(len(cleaned_headers))
            }
            rows.append(row_dict)

        if not rows:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"code": ERR_HEADER_ONLY, "message": "CSV file contains headers only, no data rows."},
            )

        if len(rows) > settings.MAX_IMPORT_ROWS:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail={"code": "ROW_LIMIT_EXCEEDED", "message": f"File exceeds maximum of {settings.MAX_IMPORT_ROWS} rows."},
            )

        return header_mapping, rows, warnings

    def _get_header_spec(self, import_type: str) -> Tuple[set, set, Dict[str, str]]:
        if import_type == "transactions":
            return TRANSACTION_REQUIRED_HEADERS, TRANSACTION_OPTIONAL_HEADERS, TRANSACTION_ALIASES
        elif import_type == "invoices":
            return INVOICE_REQUIRED_HEADERS, INVOICE_OPTIONAL_HEADERS, INVOICE_ALIASES
        elif import_type == "expenses":
            return EXPENSE_REQUIRED_HEADERS, EXPENSE_OPTIONAL_HEADERS, EXPENSE_ALIASES
        return set(), set(), {}

    def _apply_alias_mapping(self, raw_row: Dict[str, str], header_mapping: Dict[str, str]) -> Dict[str, str]:
        mapped = {}
        for k, v in raw_row.items():
            canonical_key = header_mapping.get(k, k)
            mapped[canonical_key] = v
        return mapped

    def _make_dedup_key(self, import_type: str, row: Dict[str, Any]) -> str:
        if import_type == "transactions":
            return f"{row.get('transaction_date')}:{row.get('transaction_type')}:{row.get('amount')}:{row.get('reference') or ''}"
        elif import_type == "invoices":
            return f"{row.get('invoice_number')}"
        elif import_type == "expenses":
            return f"{row.get('expense_date')}:{row.get('category')}:{row.get('amount')}:{row.get('vendor_id') or ''}"
        return str(row)
