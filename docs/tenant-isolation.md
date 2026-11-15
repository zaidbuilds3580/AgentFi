# AgentFi Tenant Isolation Architecture & Security Specification

This document details the multi-tenant isolation model, defense-in-depth mechanisms, testing strategy, and operational considerations for the AgentFi platform.

---

## 1. Core Isolation Rules

1. **Server-Derived Tenant Identity**:
   - The tenant identity (`business_id`) is strictly extracted from the cryptographically verified JWT access token issued at login.
   - Client requests can NEVER supply, override, or choose a `business_id` for data operations.
   - When a `business_id` appears in a URL path (e.g., `/api/businesses/{business_id}/...`), the server asserts that `path.business_id == token.business_id`. If there is any mismatch, the server immediately halts execution with **HTTP 403 Forbidden**.

2. **Cross-Tenant Privacy (404 Not Found Policy)**:
   - When a tenant queries a specific resource by ID (transaction, invoice, expense, import batch) that belongs to another business tenant, the backend returns **HTTP 404 Not Found** (identical to a nonexistent resource).
   - This prevents ID enumeration, timing attacks, and information disclosure about other SME tenants.

3. **LLM Boundary Protection**:
   - Neither the LLM nor agent runtimes (Members 3 and 4) ever choose the tenant.
   - All tool calls and data access functions receive tenant context strictly from the server via `ToolContext`.

---

## 2. Multi-Layer Defense in Depth

The backend enforces tenant boundaries across five distinct architectural layers:

```
+-------------------------------------------------------------+
| Layer 1: Cryptographic Authentication (JWT Verification)     |
|   - Verifies HMAC-SHA256 signature and token expiration     |
|   - Validates user.is_active and business.is_active in DB   |
+-------------------------------------------------------------+
                              |
+-------------------------------------------------------------+
| Layer 2: Route & RBAC Layer (FastAPI Dependencies)          |
|   - get_current_user / get_current_business_id dependencies |
|   - require_role("owner", "admin") restricts write actions  |
|   - Path mismatch checks return 403 Forbidden               |
+-------------------------------------------------------------+
                              |
+-------------------------------------------------------------+
| Layer 3: Service Layer (scoped() Query Helper)              |
|   - scoped(Model, business_id) prepends WHERE business_id   |
|   - AST static analysis guard enforces usage in CI          |
+-------------------------------------------------------------+
                              |
+-------------------------------------------------------------+
| Layer 4: Write Path Normalization                           |
|   - Imports and bulk inserts force business_id = caller_biz |
|   - Partial unique indexes prevent cross-tenant dedup clash |
+-------------------------------------------------------------+
                              |
+-------------------------------------------------------------+
| Layer 5: Database Layer (PostgreSQL Row-Level Security)     |
|   - relrowsecurity = true on all public tables              |
|   - NOT NULL constraints and B-Tree indexes on business_id  |
+-------------------------------------------------------------+
```

---

## 3. Mechanisms Detailed

### 3.1 JWT Claim Validation & Active Checks
- Decoded via python-jose using `settings.JWT_SECRET_KEY`.
- `backend/api/deps.py` actively checks the database to verify that both the user account and the business tenant exist and have `is_active = True`. If deactivated, immediate 401 Unauthorized is returned.

### 3.2 The `scoped(model, business_id)` Query Helper
All database read services construct queries using:
```python
from backend.services.query_filters import scoped

stmt = scoped(Transaction, business_id).where(...)
results = db.execute(stmt).scalars().all()
```
This guarantees that no query can execute against business-owned models without a `WHERE business_id = :business_id` clause.

### 3.3 Static AST Scoping Guard (`test_scoping_guard.py`)
In automated CI runs, the abstract syntax tree (AST) of `backend/services/` and `backend/api/` is scanned. Any `db.query(Model)` or `select(Model)` targeting a business-owned table without `scoped()` or `business_id` immediately fails the build.

---

## 4. Automated Test Matrix

The isolation architecture is backed by an automated test suite:

| Test File | Scope | Covered Threats |
|---|---|---|
| `tests/security/test_route_inventory.py` | Route-Level Audit | Ensures every route requires authentication unless explicitly allowlisted (401 enforcement). |
| `tests/security/test_scoping_guard.py` | Query-Level Static AST | Scans code for unscoped queries on business-owned tables. |
| `tests/security/test_tenant_isolation_matrix.py` | Cross-Tenant Integration | Verifies Tenant A never sees Tenant B data across all routes; 404 on cross-tenant IDs; 403 on path mismatch. |
| `tests/security/test_tenant_isolation_matrix.py` | Token Tampering | Verifies expired tokens, wrong signatures, forged tenant UUIDs, and payload modifications return 401. |
| `tests/security/test_tenant_isolation_matrix.py` | RBAC & Writes | Verifies viewer role cannot perform imports/writes (403). |
| `tests/security/test_tenant_isolation_matrix.py` | Account Deactivation | Verifies deactivated user cannot login and token is invalidated (401). |
| `tests/security/test_database_rls_and_indexes.py` | Database Catalogs | Queries `pg_class` (RLS enabled), `information_schema` (NOT NULL columns), and `pg_indexes` (indexed `business_id`). |

---

## 5. Known Limitations & Architecture Notes

- **Shared Database / Shared Schema**:
  AgentFi uses a multi-tenant shared database with shared tables and column-level discriminator (`business_id`). This is optimal for SME university-scale architecture.
- **Backend Service Connection Role**:
  The backend connects to PostgreSQL via Supabase's connection pooler using the `postgres` / `service_role` superuser credentials. Because PostgreSQL superusers bypass RLS by design, **the primary enforcement barrier is Layer 2 & Layer 3 (FastAPI & Service Layer Scoping)**, with RLS acting as a defense-in-depth barrier against accidental direct SQL access via Supabase client SDKs.
- **Supabase Anon Role**:
  RLS is enabled on all tables, and there are no permissive policies granting `anon` access. Unauthenticated direct access via Supabase REST API yields 0 rows.
