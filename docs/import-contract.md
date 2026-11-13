# AgentFi CSV Import & Ingestion Contract (Day 3)

This document specifies the REST API contract, CSV schemas, validation rules, deduplication semantics, and integration protocols for the AgentFi ingestion pipeline.

---

## 1. Endpoints Overview

| Method | Endpoint | Allowed Roles | Description |
| :--- | :--- | :--- | :--- |
| `POST` | `/api/imports` | `owner`, `admin` | Ingest a CSV file (`multipart/form-data`) into `transactions`, `invoices`, or `expenses`. |
| `POST` | `/api/businesses/{business_id}/upload` | `owner`, `admin` | Thin alias matching legacy path specification; validates `business_id == token.business_id`. |
| `GET` | `/api/imports` | All (`viewer`+) | Paginated list of import batches for caller's business (`limit`, `offset`, newest first). |
| `GET` | `/api/imports/{batch_id}` | All (`viewer`+) | Detailed status, counts, and top 50 error preview for a single batch. |
| `GET` | `/api/imports/{batch_id}/errors` | All (`viewer`+) | Paginated row-level error audit log for a batch. |
| `GET` | `/api/imports/templates/{import_type}` | All (`viewer`+) | Schema hints, required/optional column metadata, and example payload (JSON or `?format=csv`). |

---

## 2. Ingest Request & Response Formats

### Request: `POST /api/imports`
* **Content-Type:** `multipart/form-data`
* **Headers:** `Authorization: Bearer <jwt_access_token>`
* **Form Parameters:**
  * `file`: Binary file content (must have `.csv` extension, max 10 MB, no binary NUL bytes).
  * `import_type`: Enum string (`transactions` | `invoices` | `expenses`).
  * `source_dataset`: Optional label for data provenance (default: `csv_upload`).
  * `on_error`: Error tolerance policy (`skip` | `abort`, default: `skip`).

### Response: `201 Created`
```json
{
  "batch_id": "86294b35-ed46-435d-aab8-1a177c4f7684",
  "import_type": "transactions",
  "status": "completed",
  "source_dataset": "csv_upload",
  "original_filename": "hdfc_oct_statement.csv",
  "total_rows": 150,
  "inserted_rows": 148,
  "duplicate_rows": 2,
  "error_rows": 0,
  "warnings": [],
  "errors": [],
  "started_at": "2026-11-07T14:29:11.091306Z",
  "completed_at": "2026-11-07T14:29:11.597490Z"
}
```

### Error Response: `409 Conflict (DUPLICATE_FILE)`
Returned when an identical file (`sha256` hash and `import_type`) was previously completed for this business.
```json
{
  "detail": {
    "code": "DUPLICATE_FILE",
    "message": "An identical file has already been imported for this entity.",
    "existing_batch_id": "0959cbb1-0e66-4a9d-a5dc-d4dce7e79811"
  }
}
```

---

## 3. CSV Schemas & Header Specifications

All headers are case-insensitive and whitespace-trimmed. Unknown columns are ignored and surfaced as warnings.

### A. Transactions (`import_type=transactions`)
* **Required Headers:** `transaction_date`, `transaction_type`, `amount`
* **Optional Headers:** `description`, `account_name`, `category`, `counterparty`, `reference`, `currency`, `source_record_id`
* **Header Aliases:**
  * `date`, `txn_date` &rarr; `transaction_date`
  * `type`, `direction` &rarr; `transaction_type`
  * `ref` &rarr; `reference`
  * `account` &rarr; `account_name`

### B. Invoices (`import_type=invoices`)
* **Required Headers:** `invoice_number`, `customer_name`, `invoice_date`, `due_date`, `amount_total`
* **Optional Headers:** `amount_paid`, `status`, `payment_date`, `currency`, `notes`, `source_record_id`
* **Header Aliases:**
  * `date` &rarr; `invoice_date`
  * `total`, `invoice_amount`, `amount` &rarr; `amount_total`
  * `customer` &rarr; `customer_name`
  * `ref` &rarr; `source_record_id`

### C. Expenses (`import_type=expenses`)
* **Required Headers:** `expense_date`, `category`, `amount`
* **Optional Headers:** `vendor_name`, `description`, `payment_method`, `account_name`, `currency`, `source_record_id`
* **Header Aliases:**
  * `date` &rarr; `expense_date`
  * `vendor` &rarr; `vendor_name`
  * `account` &rarr; `account_name`
  * `ref` &rarr; `source_record_id`

---

## 4. Normalization & Validation Rules

* **Date Formats:**
  * Accepted: `YYYY-MM-DD`, `DD/MM/YYYY`, `DD-MM-YYYY` (day-first, Indian standard convention).
  * Anything else is rejected with `INVALID_DATE`.
* **Money & Decimal Amounts:**
  * Parsed via `Decimal` (never floating-point).
  * Automatically strips commas, whitespace, and currency symbols (`₹`, `$`, `Rs.`, `INR`).
  * Amounts stored strictly positive. Negative amounts, parentheses `(100)`, scientific notation `1e3`, and zero (for transactions/expenses) are rejected with `INVALID_AMOUNT`.
  * Precision is rounded half-up to 2 decimal places with an audit warning if >2 decimal places are provided.
* **Transaction Types:**
  * `inflow` aliases: `inflow`, `credit`, `cr`, `income`, `deposit`, `received`
  * `outflow` aliases: `outflow`, `debit`, `dr`, `expense`, `withdrawal`, `paid`
  * Anything else is rejected with `INVALID_TYPE`.
* **Invoice Lifecycles:**
  * Default: `sent`. Allowed: `draft`, `sent`, `partially_paid`, `paid`, `overdue`, `void`.
  * Constraints: `due_date >= invoice_date` (`DUE_BEFORE_INVOICE`) and `amount_paid <= amount_total` (`PAID_EXCEEDS_TOTAL`).
* **Expense Payment Methods:**
  * Allowed: `cash`, `bank_transfer`, `card`, `upi`, `cheque`, `other`. Unknown values default to `other` with a warning.
* **Counterparties (Customers & Vendors):**
  * Find-or-created automatically inside the database transaction by `(business_id, lower(name))`.
* **Account Resolution:**
  * `account_name` must match an existing account of the business. Unknown names trigger `ACCOUNT_NOT_FOUND`. Never silently creates accounts.

---

## 5. Deduplication & Provenance Architecture

1. **Exact File Deduplication:**
   A partial unique index on `import_batches(business_id, import_type, file_sha256)` blocks re-importing the identical file once completed.
2. **Row-Level Provenance & Safe Re-Ingest:**
   When `source_record_id` is omitted in the CSV, the system computes a deterministic hash:
   $$\text{sha1}(\text{import\_type} + \text{normalized\_key\_fields} + \text{occurrence\_index})$$
   * Preserves duplicate legitimate transactions occurring on the same day in a single file.
   * Prevents duplicates if a file is re-uploaded under a different filename or re-ingested via background pipelines using PostgreSQL `ON CONFLICT (business_id, source_dataset, source_record_id) DO NOTHING`.

---

## 6. RowProcessor Protocol for Extensibility

Pluggable row processing is implemented via `RowProcessor` in `backend/services/import_processing.py`:

```python
class RowProcessor(Protocol):
    def normalize_row(
        self,
        import_type: str,
        raw_row: Dict[str, str],
        row_number: int,
        context: ProcessingContext,
    ) -> Tuple[Optional[Dict[str, Any]], List[RowError]]:
        ...
```

* `DefaultRowProcessor`: Production-grade canonical validation rules.
* `M3ETLAdapter` (`backend/services/etl_adapter.py`): Pluggable adapter wrapping Member 3's `finance_engine.etl` routines when `USE_M3_ETL=true`.

---

## 7. Error Codes Reference

| Error Code | HTTP Status | Description |
| :--- | :--- | :--- |
| `INVALID_DATE` | 201 (row error) | Date does not match YYYY-MM-DD, DD/MM/YYYY, or DD-MM-YYYY. |
| `INVALID_AMOUNT` | 201 (row error) | Non-numeric, negative, scientific notation, or invalid characters. |
| `INVALID_TYPE` | 201 (row error) | Transaction type is neither inflow nor outflow alias. |
| `MISSING_REQUIRED` | 422 / 201 | Required header or required field is missing. |
| `ACCOUNT_NOT_FOUND` | 201 (row error) | Account name does not exist for this business tenant. |
| `DUE_BEFORE_INVOICE` | 201 (row error) | Invoice due date occurs before the issue date. |
| `PAID_EXCEEDS_TOTAL` | 201 (row error) | Invoice amount paid is greater than amount total. |
| `DUPLICATE_FILE` | 409 Conflict | Identical file hash already successfully imported. |
| `EMPTY_FILE` | 422 Unprocessable | File is 0 bytes or has no valid rows. |
| `HEADER_ONLY` | 422 Unprocessable | File has headers but zero data records. |
| `DUPLICATE_HEADERS` | 422 Unprocessable | Two or more headers have the same name. |

---

## 8. Contracts to Confirm

1. **Accepted Date Formats:** Standardized on `YYYY-MM-DD`, `DD/MM/YYYY`, `DD-MM-YYYY`. Please confirm if any other bank statement format (e.g. `DD Mon YYYY` like `05 Oct 2026`) should be parsed by `DefaultRowProcessor` directly.
2. **Transaction Direction Aliases:** Mapped `inflow` (credit, cr, income, deposit, received) and `outflow` (debit, dr, expense, withdrawal, paid).
3. **Endpoint Routing:** Primary is `POST /api/imports`. The legacy `/api/businesses/{id}/upload` is implemented as an alias.
4. **Default Error Mode:** Defaults to `on_error=skip` (best user experience for SME batch uploads with warnings and partial success).
5. **Member 3 ETL Pipeline:** Runs offline (`finance_engine/etl/pipeline.py`), or optionally dynamically via `USE_M3_ETL=true` through `M3ETLAdapter`.

---

### Notes for Teammates

* **For Member 1 (Frontend):**
  * Call `GET /api/imports/templates/{import_type}` to dynamically display required columns and tooltips in your file upload UI.
  * Use `POST /api/imports` with `multipart/form-data`.
  * If response returns status `completed_with_errors`, inspect the `errors` array in `ImportResult` to highlight invalid row numbers directly in your UI.
* **For Member 3 (Finance Engine & ETL):**
  * Ingested records in Supabase always carry `source_dataset` and `source_record_id` for traceability back to raw CSV exports.
  * You can adapt custom ETL mappers directly into `backend/services/etl_adapter.py`.
