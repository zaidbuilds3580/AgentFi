"""
File ingestion and CSV import endpoints for transactions, invoices, and expenses.
"""

from datetime import datetime
import io
import os
from typing import Any, Dict, List, Optional, Tuple
import uuid

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.api.deps import get_current_user, get_db, require_role
from backend.auth.schemas import TokenPayload
from backend.core.config import settings
from backend.models.constants import (
    DEFAULT_SOURCE_DATASET,
    ERR_DUPLICATE_FILE,
    IMPORT_TYPES,
    ON_ERROR_MODE_SKIP,
    USER_ROLE_ADMIN,
    USER_ROLE_OWNER,
)
from backend.models.import_batch import ImportBatch
from backend.models.import_row_error import ImportRowError
from backend.schemas.imports import (
    ImportBatchListResponse,
    ImportResult,
    ImportTemplateResponse,
    RowErrorRead,
    TemplateColumn,
)
from backend.services.import_service import DuplicateFileError, ImportService

router = APIRouter(prefix="", tags=["Imports"])


def _sanitize_filename(filename: Optional[str]) -> str:
    if not filename:
        return "upload.csv"
    basename = os.path.basename(filename)
    # Strip dangerous directory traversal characters
    clean = os.path.splitdrive(basename)[1]
    clean = clean.replace("/", "").replace("\\", "").strip()
    return clean or "upload.csv"


async def _read_upload_file(file: UploadFile) -> Tuple[bytes, str]:
    filename = _sanitize_filename(file.filename)
    ext = os.path.splitext(filename)[1].lower()
    if ext not in settings.ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail={"code": "UNSUPPORTED_MEDIA_TYPE", "message": f"Only .csv files are supported. Received '{ext}'"},
        )

    max_bytes = settings.MAX_UPLOAD_MB * 1024 * 1024
    content = bytearray()
    chunk_size = 1024 * 1024  # 1 MB chunk

    while True:
        chunk = await file.read(chunk_size)
        if not chunk:
            break
        content.extend(chunk)
        if len(content) > max_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail={"code": "FILE_TOO_LARGE", "message": f"File exceeds maximum allowed size of {settings.MAX_UPLOAD_MB} MB."},
            )

    return bytes(content), filename


@router.post(
    "/imports",
    response_model=ImportResult,
    status_code=status.HTTP_201_CREATED,
    summary="Upload and ingest CSV data (transactions, invoices, or expenses)",
)
async def upload_import(
    file: UploadFile = File(...),
    import_type: str = Form(...),
    source_dataset: str = Form(DEFAULT_SOURCE_DATASET),
    on_error: str = Form(ON_ERROR_MODE_SKIP),
    current_user: TokenPayload = Depends(require_role(USER_ROLE_OWNER, USER_ROLE_ADMIN)),
    db: Session = Depends(get_db),
) -> ImportResult:
    business_id = uuid.UUID(current_user.business_id)
    user_id = uuid.UUID(current_user.sub)

    file_bytes, safe_filename = await _read_upload_file(file)

    service = ImportService(db)
    try:
        result = service.process_import(
            business_id=business_id,
            user_id=user_id,
            import_type=import_type.strip().lower(),
            file_bytes=file_bytes,
            original_filename=safe_filename,
            source_dataset=source_dataset.strip(),
            on_error_mode=on_error.strip().lower(),
        )
        return result
    except DuplicateFileError as dup_err:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": ERR_DUPLICATE_FILE,
                "message": "An identical file has already been imported for this entity.",
                "existing_batch_id": str(dup_err.existing_batch_id),
            },
        )


@router.post(
    "/businesses/{business_id}/upload",
    response_model=ImportResult,
    status_code=status.HTTP_201_CREATED,
    summary="Upload CSV data (alias endpoint with explicit business_id verification)",
)
async def upload_import_alias(
    business_id: uuid.UUID,
    file: UploadFile = File(...),
    import_type: str = Form(...),
    source_dataset: str = Form(DEFAULT_SOURCE_DATASET),
    on_error: str = Form(ON_ERROR_MODE_SKIP),
    current_user: TokenPayload = Depends(require_role(USER_ROLE_OWNER, USER_ROLE_ADMIN)),
    db: Session = Depends(get_db),
) -> ImportResult:
    if str(business_id) != str(current_user.business_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot upload data for another business.",
        )
    return await upload_import(
        file=file,
        import_type=import_type,
        source_dataset=source_dataset,
        on_error=on_error,
        current_user=current_user,
        db=db,
    )


@router.get(
    "/imports",
    response_model=ImportBatchListResponse,
    summary="List historical import batches for caller's business",
)
def list_imports(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: TokenPayload = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ImportBatchListResponse:
    business_id = uuid.UUID(current_user.business_id)

    total_stmt = select(func.count()).select_from(ImportBatch).where(ImportBatch.business_id == business_id)
    total = db.scalar(total_stmt) or 0

    stmt = (
        select(ImportBatch)
        .where(ImportBatch.business_id == business_id)
        .order_by(ImportBatch.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    batches = db.scalars(stmt).all()

    items = [
        ImportResult(
            batch_id=b.id,
            import_type=b.import_type,
            status=b.status,
            source_dataset=b.source_dataset,
            original_filename=b.original_filename,
            total_rows=b.total_rows,
            inserted_rows=b.inserted_rows,
            duplicate_rows=b.duplicate_rows,
            error_rows=b.error_rows,
            warnings=[],
            errors=[],
            started_at=b.started_at,
            completed_at=b.completed_at,
        )
        for b in batches
    ]

    return ImportBatchListResponse(items=items, total=total, limit=limit, offset=offset)


@router.get(
    "/imports/templates/{import_type}",
    summary="Get CSV template specification or download blank CSV template",
)
def get_template(
    import_type: str,
    format: Optional[str] = Query(None, description="Set format=csv to download empty template file"),
    current_user: TokenPayload = Depends(get_current_user),
) -> Any:
    it = import_type.strip().lower()
    if it not in IMPORT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "INVALID_TYPE", "message": f"Supported types: {list(IMPORT_TYPES)}"},
        )

    if it == "transactions":
        columns = [
            TemplateColumn(name="transaction_date", required=True, type="date", description="Date in YYYY-MM-DD, DD/MM/YYYY, or DD-MM-YYYY format", aliases=["date", "txn_date"]),
            TemplateColumn(name="transaction_type", required=True, type="string", description="Direction: inflow (credit, income) or outflow (debit, expense)", aliases=["type", "direction"]),
            TemplateColumn(name="amount", required=True, type="numeric", description="Positive numeric amount (e.g. 1500.50)"),
            TemplateColumn(name="description", required=False, type="string", description="Transaction narration (max 500 chars)"),
            TemplateColumn(name="account_name", required=False, type="string", description="Matching business account name (case-insensitive)", aliases=["account"]),
            TemplateColumn(name="category", required=False, type="string", description="Classification category (max 100 chars)"),
            TemplateColumn(name="counterparty", required=False, type="string", description="Payer or payee party name"),
            TemplateColumn(name="reference", required=False, type="string", description="Bank reference / cheque number", aliases=["ref"]),
            TemplateColumn(name="currency", required=False, type="string", description="3-letter currency code (default business currency)"),
            TemplateColumn(name="source_record_id", required=False, type="string", description="Unique source id for provenance and deduplication"),
        ]
        example = {
            "transaction_date": "2026-10-01",
            "transaction_type": "inflow",
            "amount": "25000.00",
            "account_name": "Primary Current Account",
            "description": "Client invoice payment milestone 1",
            "category": "Sales Revenue",
            "counterparty": "Acme Corp",
            "reference": "TXN-88291",
            "currency": "INR",
        }
    elif it == "invoices":
        columns = [
            TemplateColumn(name="invoice_number", required=True, type="string", description="Unique invoice identifier (max 100 chars)"),
            TemplateColumn(name="customer_name", required=True, type="string", description="Customer party name (find-or-creates customer)", aliases=["customer"]),
            TemplateColumn(name="invoice_date", required=True, type="date", description="Issue date in YYYY-MM-DD, DD/MM/YYYY, or DD-MM-YYYY", aliases=["date"]),
            TemplateColumn(name="due_date", required=True, type="date", description="Due date (must be on or after invoice_date)"),
            TemplateColumn(name="amount_total", required=True, type="numeric", description="Total invoice amount (strictly positive)", aliases=["total", "invoice_amount", "amount"]),
            TemplateColumn(name="amount_paid", required=False, type="numeric", description="Amount already settled (<= amount_total, default 0)"),
            TemplateColumn(name="status", required=False, type="string", description="Lifecycle: draft, sent, partially_paid, paid, overdue, void (default sent)"),
            TemplateColumn(name="payment_date", required=False, type="date", description="Date settled if paid"),
            TemplateColumn(name="currency", required=False, type="string", description="3-letter currency code (default business currency)"),
            TemplateColumn(name="notes", required=False, type="string", description="Customer notes or terms (max 1000 chars)"),
            TemplateColumn(name="source_record_id", required=False, type="string", description="External record id for dedup", aliases=["ref"]),
        ]
        example = {
            "invoice_number": "INV-2026-001",
            "customer_name": "Zenith Retail Ltd",
            "invoice_date": "2026-10-05",
            "due_date": "2026-10-25",
            "amount_total": "75000.00",
            "amount_paid": "0.00",
            "status": "sent",
            "currency": "INR",
            "notes": "Payment terms 20 days",
        }
    else:  # expenses
        columns = [
            TemplateColumn(name="expense_date", required=True, type="date", description="Expense date in YYYY-MM-DD, DD/MM/YYYY, or DD-MM-YYYY", aliases=["date"]),
            TemplateColumn(name="category", required=True, type="string", description="Expense classification (e.g. Office Supplies, Travel, Software)"),
            TemplateColumn(name="amount", required=True, type="numeric", description="Expense amount (strictly positive)"),
            TemplateColumn(name="vendor_name", required=False, type="string", description="Vendor / supplier name (find-or-creates vendor)", aliases=["vendor"]),
            TemplateColumn(name="description", required=False, type="string", description="Expense description (max 500 chars)"),
            TemplateColumn(name="payment_method", required=False, type="string", description="Method: cash, bank_transfer, card, upi, cheque, other (default other)"),
            TemplateColumn(name="account_name", required=False, type="string", description="Account funding the expense", aliases=["account"]),
            TemplateColumn(name="currency", required=False, type="string", description="3-letter currency code (default business currency)"),
            TemplateColumn(name="source_record_id", required=False, type="string", description="External record id for dedup", aliases=["ref"]),
        ]
        example = {
            "expense_date": "2026-10-10",
            "category": "Cloud Infrastructure",
            "amount": "4500.00",
            "vendor_name": "AWS India",
            "description": "Production compute servers",
            "payment_method": "card",
            "currency": "INR",
        }

    if format and format.lower() == "csv":
        header_line = ",".join(c.name for c in columns) + "\n"
        return Response(
            content=header_line,
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{it}_template.csv"'},
        )

    return ImportTemplateResponse(
        import_type=it,
        columns=columns,
        example_row=example,
    )


@router.get(
    "/imports/{batch_id}",
    response_model=ImportResult,
    summary="Get detailed status and summary of an import batch",
)
def get_import_batch(
    batch_id: uuid.UUID,
    current_user: TokenPayload = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ImportResult:
    business_id = uuid.UUID(current_user.business_id)

    stmt = select(ImportBatch).where(
        ImportBatch.id == batch_id,
        ImportBatch.business_id == business_id,
    )
    batch = db.scalars(stmt).first()
    if not batch:
        # Cross-tenant access or missing batch returns 404 (tenant isolation)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Import batch {batch_id} not found.",
        )

    errors_stmt = (
        select(ImportRowError)
        .where(ImportRowError.batch_id == batch_id, ImportRowError.business_id == business_id)
        .order_by(ImportRowError.row_number)
        .limit(50)
    )
    errors = db.scalars(errors_stmt).all()

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
        warnings=[],
        errors=[
            RowErrorRead(
                row_number=e.row_number,
                field=e.field,
                code=e.code,
                message=e.message,
            )
            for e in errors
        ],
        started_at=batch.started_at,
        completed_at=batch.completed_at,
    )


@router.get(
    "/imports/{batch_id}/errors",
    response_model=List[RowErrorRead],
    summary="Get paginated list of row-level errors for an import batch",
)
def get_import_errors(
    batch_id: uuid.UUID,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    current_user: TokenPayload = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> List[RowErrorRead]:
    business_id = uuid.UUID(current_user.business_id)

    # Verify batch belongs to caller tenant
    batch_stmt = select(ImportBatch.id).where(
        ImportBatch.id == batch_id,
        ImportBatch.business_id == business_id,
    )
    if not db.scalars(batch_stmt).first():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Import batch {batch_id} not found.",
        )

    errors_stmt = (
        select(ImportRowError)
        .where(
            ImportRowError.batch_id == batch_id,
            ImportRowError.business_id == business_id,
        )
        .order_by(ImportRowError.row_number)
        .offset(offset)
        .limit(limit)
    )
    errors = db.scalars(errors_stmt).all()
    return [
        RowErrorRead(
            row_number=e.row_number,
            field=e.field,
            code=e.code,
            message=e.message,
        )
        for e in errors
    ]
