# AgentFi API Changelog & Contract Policy

This document tracks all versioned changes to the AgentFi external HTTP API and defines the team policy governing backward compatibility, deprecations, and schema evolution.

---

## Contract Governance Policy (Post-Day 6 Freeze)

As of **Day 6 (Contract Freeze v1.0.0)**, all routes marked `STABLE` in `docs/api-contract-inventory.md` are frozen against accidental breaking changes:

1. **Additive Changes Allowed (Non-Breaking)**:
   - Introducing new endpoints under `/api`.
   - Adding new optional query parameters.
   - Adding new optional fields to existing response DTOs.
   - Adding new enum values to response payloads.
   - Documenting new HTTP status codes (e.g. 400/403/404).

2. **Breaking Changes Prohibited Without Major Bump**:
   - Removing any existing endpoint or HTTP method.
   - Removing or renaming any existing response property.
   - Changing the type of an existing property (e.g. string to int, Decimal string to number).
   - Changing a response field from non-nullable to nullable.
   - Making an existing optional request field newly required.
   - Removing enum values from response properties.
   - Modifying success status codes (e.g. changing 200 to 201 or vice-versa).
   - Tightening authorization or RBAC role permissions without team consensus.

3. **Breaking Change Protocol**:
   If a breaking change is strictly necessary:
   1. Coordinate with Member 1 (Frontend) and Members 3 & 4 (Engine & Agents) in team chat.
   2. Bump `CONTRACT_VERSION` in `backend/core/version.py` (e.g. `1.0.0` -> `2.0.0`).
   3. Document the change, rationale, and teammate approvals in this changelog.
   4. Update the frozen contract baseline by running:
      ```bash
      python backend/scripts/check_contract.py --update
      ```
   5. Commit the updated `docs/openapi.frozen.json` and `docs/openapi.json`.

---

## Release History

### [1.0.0] - 2026-11-15 (Day 6 Contract Freeze)

#### Added
- **API Contract Freeze & Guard**: Introduced `docs/openapi.frozen.json` and automated guard `backend/scripts/check_contract.py` with CI enforcement in `tests/contract/test_api_contract.py`.
- **Contract Version Exposure**: Added `CONTRACT_VERSION = "1.0.0"` in `backend/core/version.py`, exposed in `app.version`, OpenAPI `info.version`, and `GET /api/health` response.
- **Correlation & Tracing (`X-Request-ID`)**: Added request ID middleware echoing incoming `X-Request-ID` or generating a UUID4, attached to all responses and error payloads.
- **Security Headers Middleware**: Enforced `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, and `Referrer-Policy: strict-origin-when-cross-origin`.
- **Standardized Error Envelope**: Global exception handlers for `StarletteHTTPException`, `RequestValidationError`, and unexpected exceptions ensuring `{ detail, error: { code, message, request_id, details }, request_id }`. Stack traces are strictly hidden from clients.
- **Contract Inventory**: Comprehensive documentation of all routes, auth levels, RBAC roles, tenant isolation mechanisms, and schemas in `docs/api-contract-inventory.md`.

#### Changed
- Excluded convenience alias endpoints (`/api/businesses/{business_id}/...`) from the public OpenAPI documentation via `include_in_schema=False`.
- Hardened token authentication: `get_current_user` now enforces user account activity (`is_active=True`) and tenant existence.

#### Endpoints Frozen in v1.0.0
- `GET /api/health`
- `GET /api/health/db`
- `POST /api/auth/register`
- `POST /api/auth/login`
- `GET /api/auth/me`
- `POST /api/imports`
- `GET /api/imports`
- `GET /api/imports/templates/{import_type}`
- `GET /api/imports/{batch_id}`
- `GET /api/imports/{batch_id}/errors`
- `GET /api/transactions`
- `GET /api/transactions/{id}`
- `GET /api/invoices`
- `GET /api/invoices/{id}`
- `GET /api/expenses`
- `GET /api/expenses/{id}`
- `GET /api/accounts`
- `GET /api/customers`
- `GET /api/vendors`
- `GET /api/categories`
- `GET /api/meta/data-range`
- `GET /api/financial-summary`
