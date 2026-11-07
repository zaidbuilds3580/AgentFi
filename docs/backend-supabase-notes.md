# Supabase Security & Database Architecture Notes

This document specifies the database access architecture, tenant isolation model, and security posture for AgentFi. These patterns are designed for **Day 1** and strictly enforced on **Day 2 (Schema & Models)** and beyond.

---

## 1. Access Topology & System Boundaries

```
[ Frontend (Next.js) ]      [ LangGraph Agents (M4) ]      [ Finance Engine (M3) ]
           \                           |                           /
            \                          |                          /
             \-->  REST API / Authenticated Tool Services  <-----/
                                       |
                                       v
                           [ FastAPI Backend (M2) ]
                                       |
                      SQLAlchemy 2.0 (psycopg v3, Port 5432)
                                       v
                       [ Supabase PostgreSQL Database ]
```

- **Exclusive Database Access**: **Only the FastAPI backend has direct access to PostgreSQL.**
- **No Client Keys in Codebase**: Neither `SUPABASE_ANON_KEY` nor `SUPABASE_SERVICE_ROLE_KEY` are used by the backend. FastAPI connects using standard PostgreSQL connection strings as the server-side role (`postgres`).
- **No Direct Database Access for Frontend or Agents**:
  - The Next.js frontend (Member 1) **must never** communicate with Supabase directly, query the database, or embed Supabase client libraries/keys. All communication flows through `/api/*` endpoints authenticated via FastAPI-issued JWTs.
  - The LangGraph agents (Member 4) and Finance Engine (Member 3) consume data through structured Pydantic DTOs and backend services/APIs.

---

## 2. Row Level Security (RLS) Defense-in-Depth

Supabase exposes an auto-generated PostgREST HTTP API by default for any table in the `public` schema. If an anonymous key (`anon_key`) is ever exposed publicly or inspected in network traffic, unprotected tables could be read or modified.

To neutralize this risk:
1. **RLS Enabled on Every Table**:
   Every table created via Alembic migrations on Day 2 must immediately enable RLS:
   ```sql
   ALTER TABLE <table_name> ENABLE ROW LEVEL SECURITY;
   ```
2. **Zero Public Policies**:
   No permissive `CREATE POLICY` statements will be granted to the `anon` or `authenticated` Supabase roles.
3. **Server-Side Bypass**:
   The FastAPI backend connects as the table owner / PostgreSQL superuser role (`postgres.<project-ref>`), which naturally bypasses RLS policies in PostgreSQL.
4. **Result**:
   Even if the Supabase project URL and anon key are leaked publicly, the Supabase PostgREST API returns `401 Unauthorized` / empty datasets for all tables.

---

## 3. Mandatory Tenant Isolation via `business_id`

AgentFi is a multi-tenant platform serving multiple SME businesses. Cross-tenant data leakage is catastrophic in financial operations.

- **Mandatory Foreign Key**: Every business domain table (`users`, `accounts`, `invoices`, `expenses`, `transactions`, `audit_logs`, etc.) must have a non-nullable `business_id UUID REFERENCES businesses(id) ON DELETE CASCADE`.
- **JWT-Driven Enforcement**:
  - The authenticated user's `business_id` is cryptographically signed inside the JWT payload issued by FastAPI.
  - The backend dependency `get_current_business_id` extracts this claim.
  - **Every database query in the service layer must filter by `business_id`**:
    ```python
    stmt = select(Invoice).where(
        Invoice.business_id == current_business_id,
        Invoice.id == invoice_id
    )
    ```
- **No Client-Supplied Tenant IDs**: Service endpoints never trust tenant IDs from request query parameters or request bodies; the tenant ID is always bound to the authenticated JWT.

---

## 4. Single Source of Truth for Migrations (Alembic Only)

To prevent schema drift across team members' environments:
- **Zero Supabase UI Schema Mutations**: Teammates must **never** create tables, add columns, or alter types using the Supabase Web UI Table Editor.
- **Alembic as Code**: All schema definitions exist solely in `backend/models/` and are versioned through revision files inside `backend/alembic/versions/`.
- **Reproducibility**: Running `alembic upgrade head` on any clean Supabase instance or test database reconstructs the identical database state.

---

## 5. Connection Pooling & Resource Quotas

- **Supabase Free-Tier Protection**: Supabase free-tier PostgreSQL instances have strict max connection limits (~60 pooler connections).
- **Session Pooler (Port 5432)**:
  - Used for local development and Alembic migrations.
  - Keeps connection pool small: `pool_size=5`, `max_overflow=5`, `pool_recycle=1800`.
- **Transaction Pooler (Port 6543)**:
  - If used for high-concurrency serverless deployments, `USE_TRANSACTION_POOLER=True` ensures `prepare_threshold=None` in `psycopg` to prevent prepared-statement errors across transaction boundaries.

