# AgentFi Backend API Contract Inventory

This document is the authoritative route and contract inventory for AgentFi backend version `1.0.0` (Day 6 Freeze).
Every endpoint registered on the application is cataloged with its contract stability status, authentication requirements, Role-Based Access Control (RBAC), tenant-scoping mechanism, schemas, and supported HTTP status codes.

## Stability Classification
- **STABLE**: Production endpoints consumed by Member 1 (Next.js Frontend) and Members 3/4. Frozen against breaking changes under contract v1.0.0.
- **ALIAS**: Convenience endpoints (e.g. `/api/businesses/{business_id}/...`) preserved for backward compatibility. Excluded from frozen public OpenAPI schema.
- **INTERNAL**: Service discovery, documentation, and root health check routes.

## Canonical Error Envelope
All endpoints share a standardized, uniform error response format:
```json
{
  "detail": "Descriptive error message or field error array",
  "error": {
    "code": "ERROR_CODE",
    "message": "Human readable summary",
    "request_id": "c138d6d5-...",
    "details": null
  },
  "request_id": "c138d6d5-..."
}
```
Every response also includes the `X-Request-ID` correlation header and security headers (`X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: strict-origin-when-cross-origin`).

---

## 1. STABLE Endpoints (Contract Frozen v1.0.0)

| Method | Path | Summary | Auth | Allowed Roles | Tenant Scoping | Request Schema | Response Schema | Error Codes | Status |
|---|---|---|---|---|---|---|---|---|---|
| `GET` | `/api/health` | Application Health Check | None (Public) | Any / Anonymous | N/A | N/A | 200: `JSON` | 401, 422, 500 | **STABLE** |
| `GET` | `/api/health/db` | Database Connectivity Check | None (Public) | Any / Anonymous | N/A | N/A | 200: `JSON` | 503 | **STABLE** |
| `POST` | `/api/auth/register` | Register new SME business tenant & owner user | None (Public) | Any / Anonymous | N/A | `RegisterRequest` | 201: `UserPublic`, 422: `HTTPValidationError` | 401, 409, 422, 500 | **STABLE** |
| `POST` | `/api/auth/login` | Login user & issue signed JWT | None (Public) | Any / Anonymous | N/A | `LoginRequest` | 200: `Token`, 422: `HTTPValidationError` | 401, 422, 500 | **STABLE** |
| `GET` | `/api/auth/me` | Get current user claims & profile from JWT | Bearer JWT | owner, admin, viewer | JWT business_id claim (scoped() SQL filter) | N/A | 200: `JSON` | 401, 422, 500 | **STABLE** |
| `POST` | `/api/imports` | Upload and ingest CSV data (transactions, invoices, or expenses) | Bearer JWT | owner, admin | JWT business_id claim (server-injected) | `multipart/form-data` (CSV + metadata) | 201: `ImportResult`, 422: `HTTPValidationError` | 401, 403, 409, 422, 500 | **STABLE** |
| `GET` | `/api/imports` | List historical import batches for caller's business | Bearer JWT | owner, admin, viewer | JWT business_id claim (scoped() SQL filter) | N/A | 200: `ImportBatchListResponse`, 422: `HTTPValidationError` | 401, 409, 422, 500 | **STABLE** |
| `GET` | `/api/imports/templates/{import_type}` | Get CSV template specification or download blank CSV template | Bearer JWT | owner, admin, viewer | JWT business_id claim (scoped() SQL filter) | N/A | 200: `JSON`, 422: `HTTPValidationError` | 401, 404, 422, 500 | **STABLE** |
| `GET` | `/api/imports/{batch_id}` | Get detailed status and summary of an import batch | Bearer JWT | owner, admin, viewer | JWT business_id claim (scoped() SQL filter) | N/A | 200: `ImportResult`, 422: `HTTPValidationError` | 401, 404, 422, 500 | **STABLE** |
| `GET` | `/api/imports/{batch_id}/errors` | Get paginated list of row-level errors for an import batch | Bearer JWT | owner, admin, viewer | JWT business_id claim (scoped() SQL filter) | N/A | 200: `JSON`, 422: `HTTPValidationError` | 401, 404, 422, 500 | **STABLE** |
| `GET` | `/api/transactions` | List transactions with filtering, sorting, pagination, and optional totals | Bearer JWT | owner, admin, viewer | JWT business_id claim (scoped() SQL filter) | N/A | 200: `TransactionPage_TransactionRead_`, 422: `HTTPValidationError` | 401, 422, 500 | **STABLE** |
| `GET` | `/api/transactions/{id}` | Get single transaction detail by UUID | Bearer JWT | owner, admin, viewer | JWT business_id claim (scoped() SQL filter) | N/A | 200: `TransactionRead`, 422: `HTTPValidationError` | 401, 404, 422, 500 | **STABLE** |
| `GET` | `/api/invoices` | List invoices with customer enrichment, status filters, overdue checks, and sorting | Bearer JWT | owner, admin, viewer | JWT business_id claim (scoped() SQL filter) | N/A | 200: `Page_InvoiceListItemRead_`, 422: `HTTPValidationError` | 401, 422, 500 | **STABLE** |
| `GET` | `/api/invoices/{id}` | Get single invoice detail by UUID | Bearer JWT | owner, admin, viewer | JWT business_id claim (scoped() SQL filter) | N/A | 200: `InvoiceListItemRead`, 422: `HTTPValidationError` | 401, 404, 422, 500 | **STABLE** |
| `GET` | `/api/expenses` | List expenses with vendor enrichment, category/method filters, and optional totals | Bearer JWT | owner, admin, viewer | JWT business_id claim (scoped() SQL filter) | N/A | 200: `ExpensePage_ExpenseListItemRead_`, 422: `HTTPValidationError` | 401, 422, 500 | **STABLE** |
| `GET` | `/api/expenses/{id}` | Get single expense detail by UUID | Bearer JWT | owner, admin, viewer | JWT business_id claim (scoped() SQL filter) | N/A | 200: `ExpenseListItemRead`, 422: `HTTPValidationError` | 401, 404, 422, 500 | **STABLE** |
| `GET` | `/api/accounts` | List active financial accounts for the business | Bearer JWT | owner, admin, viewer | JWT business_id claim (scoped() SQL filter) | N/A | 200: `JSON` | 401, 422, 500 | **STABLE** |
| `GET` | `/api/customers` | List customers for dropdown selection with optional search | Bearer JWT | owner, admin, viewer | JWT business_id claim (scoped() SQL filter) | N/A | 200: `JSON`, 422: `HTTPValidationError` | 401, 422, 500 | **STABLE** |
| `GET` | `/api/vendors` | List vendors for dropdown selection with optional search | Bearer JWT | owner, admin, viewer | JWT business_id claim (scoped() SQL filter) | N/A | 200: `JSON`, 422: `HTTPValidationError` | 401, 422, 500 | **STABLE** |
| `GET` | `/api/categories` | List distinct categories populated for transactions or expenses | Bearer JWT | owner, admin, viewer | JWT business_id claim (scoped() SQL filter) | N/A | 200: `JSON`, 422: `HTTPValidationError` | 401, 422, 500 | **STABLE** |
| `GET` | `/api/meta/data-range` | Get min date, max date, and count across transactions, invoices, and expenses | Bearer JWT | owner, admin, viewer | JWT business_id claim (scoped() SQL filter) | N/A | 200: `DataRangeResponse` | 401, 422, 500 | **STABLE** |
| `GET` | `/api/financial-summary` | Get financial summary for the authenticated user's business (convenience alias) | Bearer JWT | owner, admin, viewer | JWT business_id claim (scoped() SQL filter) | N/A | 200: `FinancialSummaryResponse`, 422: `HTTPValidationError` | 401, 422, 500 | **STABLE** |

---

## 2. ALIAS Endpoints (Preserved, Hidden from Public Schema)

| Method | Path | Summary | Auth | Allowed Roles | Tenant Scoping | Request Schema | Response Schema | Error Codes | Status |
|---|---|---|---|---|---|---|---|---|---|
| `POST` | `/api/businesses/{business_id}/upload` | Upload CSV data (alias endpoint with explicit business_id verification) | Bearer JWT | owner, admin | Path business_id verified against JWT claim (403 on mismatch) | N/A | 200: `JSON` | 401, 403, 404, 422, 500 | **ALIAS** |
| `GET` | `/api/businesses/{business_id}/transactions` | None | Bearer JWT | owner, admin, viewer | Path business_id verified against JWT claim (403 on mismatch) | N/A | 200: `JSON` | 401, 404, 422, 500 | **ALIAS** |
| `GET` | `/api/businesses/{business_id}/invoices` | None | Bearer JWT | owner, admin, viewer | Path business_id verified against JWT claim (403 on mismatch) | N/A | 200: `JSON` | 401, 404, 422, 500 | **ALIAS** |
| `GET` | `/api/businesses/{business_id}/expenses` | None | Bearer JWT | owner, admin, viewer | Path business_id verified against JWT claim (403 on mismatch) | N/A | 200: `JSON` | 401, 404, 422, 500 | **ALIAS** |
| `GET` | `/api/businesses/{business_id}/financial-summary` | Get business financial summary and KPIs backed by the deterministic Finance Engine | Bearer JWT | owner, admin, viewer | Path business_id verified against JWT claim (403 on mismatch) | N/A | 200: `JSON` | 401, 404, 422, 500 | **ALIAS** |

---

## 3. INTERNAL & System Endpoints

| Method | Path | Summary | Auth | Allowed Roles | Tenant Scoping | Request Schema | Response Schema | Error Codes | Status |
|---|---|---|---|---|---|---|---|---|---|
| `GET` | `/api/openapi.json` |  | None (Public) | Any / Anonymous | N/A | N/A | 200: `JSON` | 401, 422, 500 | **INTERNAL** |
| `GET` | `/docs` |  | None (Public) | Any / Anonymous | N/A | N/A | 200: `JSON` | 401, 422, 500 | **INTERNAL** |
| `GET` | `/docs/oauth2-redirect` |  | Bearer JWT | owner, admin, viewer | JWT business_id claim (scoped() SQL filter) | N/A | 200: `JSON` | 401, 422, 500 | **INTERNAL** |
| `GET` | `/redoc` |  | None (Public) | Any / Anonymous | N/A | N/A | 200: `JSON` | 401, 422, 500 | **INTERNAL** |
| `GET` | `/` | Root index | None (Public) | Any / Anonymous | N/A | N/A | 200: `JSON` | 401, 422, 500 | **INTERNAL** |
