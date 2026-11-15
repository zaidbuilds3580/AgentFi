# Day 6 Team Sync: API Contract Freeze (v1.0.0) & Security Announcement

**To**: Team AgentFi (M1 Frontend, M2 Backend, M3 Finance Engine, M4 LangGraph Agents)  
**From**: Member 2 (Backend & Database)  
**Date**: 2026-11-15  
**Subject**: API Contract Freeze v1.0.0, Standardized Tool Context, & Tenant Isolation Proofs

---

## 1. Executive Summary & Contract Freeze

As of today (Day 6), all public backend HTTP API contracts are **FROZEN at version `1.0.0`**.
- **Frozen Contract Schema**: `docs/openapi.frozen.json`
- **Full OpenAPI Schema**: `docs/openapi.json`
- **Route Inventory**: `docs/api-contract-inventory.md`

### What "Frozen" Means
1. **Zero Breaking Changes**: No endpoints, methods, response fields, or enum values will be removed or renamed. Existing field types and required fields will not be changed.
2. **Additive Only**: Future backend work before graduation is strictly additive (e.g., adding new optional query parameters or new optional response fields).
3. **Automated Guard in CI**: A contract test (`tests/contract/test_api_contract.py` + `backend/scripts/check_contract.py`) runs on every commit and PR. Any uncoordinated breaking change fails the build automatically.

---

## 2. STABLE Endpoints (Ready for Consumption)

| Endpoint | Method | Description |
|---|---|---|
| `/api/health` | `GET` | Health check returning status and `contract_version: "1.0.0"`. |
| `/api/health/db` | `GET` | Database liveness check (SELECT 1). |
| `/api/auth/register` | `POST` | Atomically registers a new SME business tenant and its initial owner user. |
| `/api/auth/login` | `POST` | Authenticates user credentials and issues a signed multi-tenant JWT. |
| `/api/auth/me` | `GET` | Returns authenticated user claims, profile, and business details. |
| `/api/imports` | `POST` | Authenticated CSV upload with schema validation, dedup, and error logging. |
| `/api/imports` | `GET` | Lists historical import batches for caller's business. |
| `/api/imports/{batch_id}` | `GET` | Detailed status and error counts for an import batch. |
| `/api/imports/{batch_id}/errors` | `GET` | Paginated row-level errors for an import batch. |
| `/api/transactions` | `GET` | Paginated, filterable, sortable list of transactions with totals. |
| `/api/transactions/{id}` | `GET` | Single transaction detail by UUID. |
| `/api/invoices` | `GET` | Enriched invoices with customer name and overdue status. |
| `/api/invoices/{id}` | `GET` | Single invoice detail by UUID. |
| `/api/expenses` | `GET` | Enriched expenses with vendor name, categories, and totals. |
| `/api/expenses/{id}` | `GET` | Single expense detail by UUID. |
| `/api/accounts` | `GET` | Dropdown list of active financial accounts for the business. |
| `/api/customers` | `GET` | Dropdown list of business customers with search. |
| `/api/vendors` | `GET` | Dropdown list of vendors with search. |
| `/api/categories` | `GET` | Distinct transaction and expense categories. |
| `/api/meta/data-range` | `GET` | Min/max dates and record counts for dashboard bounds. |
| `/api/financial-summary` | `GET` | Business financial KPIs and metrics backed by Member 3's engine. |

*(Note: `/api/businesses/{business_id}/...` routes remain available as internal aliases for backward compatibility but are hidden from the public schema).*

---

## 3. Standardized Error Envelope & Tracing

All API errors return a uniform shape with an `X-Request-ID` correlation header:
```json
{
  "detail": "Human-readable summary or field errors",
  "error": {
    "code": "ERROR_CODE",
    "message": "Human-readable summary",
    "request_id": "c138d6d5-...",
    "details": null
  },
  "request_id": "c138d6d5-..."
}
```
Security headers (`X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: strict-origin-when-cross-origin`) are injected on all responses.

---

## 4. Instructions for Member 1 (Next.js Frontend)

### Regenerating TypeScript Types
You can generate TypeScript interfaces directly from the frozen OpenAPI specification:
```bash
# Using openapi-typescript (from frontend directory)
npx openapi-typescript ../docs/openapi.json -o types/api.ts
```
Or if you prefer consuming raw JSON schemas, use `docs/openapi.json`.

---

## 5. Instructions for Members 3 & 4 (Engine & Agents)

### Tool Context Rule
> **RULE**: The LLM never selects the tenant. `business_id` is **never** an argument passed to or prompted by the LLM.

Instead, the server runtime creates a verified `ToolContext`:
```python
from backend.services.tool_context import build_tool_context

# Server builds context from authenticated token
ctx = build_tool_context(db=session, token_payload=user_token)

# Query database safely without HTTP roundtrips
from backend.services.read_api import fetch_transactions, fetch_invoices, fetch_expenses, fetch_accounts

transactions = fetch_transactions(db=session, business_id=ctx, start_date=start, end_date=end)
```

### Action Items for Discovered Mismatches
1. **Decimal Money Precision (Action for M4)**:
   - Backend models and schemas use `Decimal(18, 2)`.
   - Update `agents/*/schemas.py` to accept `Union[Decimal, float]` rather than strictly expecting `float`.
2. **FinancialSummary Shape (Action for M3 & M4)**:
   - Backend `GET /api/financial-summary` returns nested objects (`kpis`, `cash`, `receivables`, `expenses`, `comparison`).
   - M3's tool wrapper should map nested KPIs (e.g. `kpis.net_cash_flow`) when populating flat agent state.
3. **Overdue Invoice Field Names (Action for M4)**:
   - Backend invoice detail provides `outstanding_amount` (`amount_total - amount_paid`).
   - Map `InvoiceListItemRead.outstanding_amount` to `OverdueInvoice.amount`.
4. **Data Sourcing (Action for M3)**:
   - In production, call `backend.services.read_api` with `ToolContext` rather than reading raw CSV files from `data/processed/`.

---

## 6. Contracts to Confirm (Checklist for Day 7 Checkpoint)

Before our Day 7 performance and load checkpoint, please review and confirm:

- [ ] **M1 (Frontend)**: Confirm that the `FinancialSummaryResponse` KPI fields and nested sections (`kpis`, `cash`, `receivables`, `expenses`) match your metric cards and widgets.
- [ ] **M1 (Frontend)**: Confirm that the `Page[T]` envelope (`items`, `total`, `limit`, `offset`, `pages`) satisfies your table pagination requirements.
- [ ] **M3 (Finance Engine)**: Confirm that the `period` presets (`last_30_days`, `this_month`, `last_month`, `this_quarter`, `ytd`, `custom`) match your calculation engine's period resolver.
- [ ] **M4 (Agents)**: Confirm that your agent tool schemas accept `ToolContext` server injection and do not prompt users or the LLM for `business_id`.
- [ ] **M4 (Agents)**: Confirm that structured error objects (e.g. `{ "error": { "code": ..., "message": ... } }`) are caught cleanly in agent retry loops without raising unhandled exceptions.
