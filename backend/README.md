# AgentFi Backend Service (Member 2)

Welcome to the backend core of **AgentFi**, an AI-powered multi-agent finance operations platform for SMEs. This service is built with **FastAPI**, **SQLAlchemy 2.0**, **Alembic**, and connects to a managed **Supabase PostgreSQL** database.

---

## Architecture Principles

1. **Deterministic Financial Truth**: Financial operations, calculations, invoices, and accounting balances are managed deterministically by backend services and database constraints—never hallucinated by an LLM.
2. **Mandatory Tenant Isolation**: Multi-tenancy is strictly enforced. Every business domain record references `business_id`, and every query in the service layer filters by `business_id` extracted from the cryptographically verified JWT.
3. **Database Privacy**: Only this backend service accesses PostgreSQL directly. The Next.js frontend (Member 1), Finance Engine (Member 3), and LangGraph Agents (Member 4) interact exclusively through authenticated REST APIs or structured Pydantic DTOs.
4. **Supabase Client Key Isolation**: The backend uses standard PostgreSQL connection strings via `psycopg` (v3). No client `anon` or `service_role` keys are ever used or committed.

---

## Folder Map

```
backend/
├── main.py                  # FastAPI application entrypoint, CORS, and lifecycle
├── api/
│   ├── router.py            # Aggregates sub-routers under /api
│   ├── health.py            # Liveness (/health) and Supabase connectivity (/health/db)
│   ├── auth.py              # Auth endpoints (/auth/register, /auth/login, /auth/me)
│   └── deps.py              # Dependencies: get_db, get_current_user, get_current_business_id, require_role
├── auth/
│   ├── security.py          # Bcrypt hashing and JWT encode/decode routines
│   └── schemas.py           # Pydantic schemas (Token, TokenPayload, LoginRequest, RegisterRequest)
├── core/
│   ├── config.py            # Environment settings and production security guards
│   └── logging.py           # Structured logging configuration
├── database/
│   └── session.py           # SQLAlchemy 2.0 engine, SessionLocal, Base, get_db generator
├── models/                  # SQLAlchemy ORM models (empty skeleton, implemented on Day 2)
├── schemas/                 # Domain Pydantic schemas (Day 2)
├── services/                # Business logic and tenant isolation layer (Day 2)
├── integrations/            # External mock banking and email adapters (Day 3+)
├── alembic/                 # Migration environment wired to Base.metadata and settings.DATABASE_URL
├── alembic.ini              # Alembic migration configuration
├── requirements.txt         # Pinned backend dependencies
└── README.md                # This setup guide
```

---

## 1. Prerequisites

- Python 3.12 (Recommended standard for AgentFi)
- A [Supabase](https://supabase.com) account (Free Tier is fully supported)
- Git

---

## 2. Virtual Environment & Dependencies Setup

From the repository root:

```bash
# Create a virtual environment with Python 3.12 inside backend/ (git-ignored)
py -3.12 -m venv backend/.venv
# (or: python -m venv backend/.venv if 3.12 is default)

# Activate the virtual environment
# Windows (PowerShell):
backend\.venv\Scripts\Activate.ps1
# Windows (CMD):
backend\.venv\Scripts\activate.bat
# macOS / Linux:
source backend/.venv/bin/activate

# Upgrade pip and install pinned backend dependencies
pip install --upgrade pip
pip install -r backend/requirements.txt
```

---

## 3. Supabase Database Setup

### Step A: Obtain Session Pooler Connection String
1. Log in to your [Supabase Dashboard](https://supabase.com/dashboard) and open your AgentFi project.
2. Click the **Connect** button in the top navigation bar (or navigate to **Project Settings** -> **Database**).
3. Select the **Connection string** tab, then choose **URI**.
4. Choose the **Session pooler** mode (Port `5432`).
   - *Why?* The session pooler is IPv4-compatible (direct connections on `db.<ref>.supabase.co:5432` are IPv6-only and may fail on standard ISP networks) and fully supports prepared statements and Alembic migrations.

### Step B: URL-Encode Your Database Password
If your Supabase database password contains special characters (e.g. `@`, `#`, `$`, `%`, `+`, `/`), you **MUST** URL-encode it before placing it in the connection string:
- In Python:
  ```bash
  python -c "import urllib.parse; print(urllib.parse.quote_plus('YourP@ssw0rd!'))"
  ```
- Example: `P@ss#123` becomes `P%40ss%23123`.

### Step C: Configure `.env`
Copy the template if you haven't already:
```bash
cp .env.example .env
```
Update `DATABASE_URL` in `.env`:
```env
DATABASE_URL=postgresql+psycopg://postgres.<your-project-ref>:<URL_ENCODED_PASSWORD>@<region>.pooler.supabase.com:5432/postgres
USE_TRANSACTION_POOLER=false
JWT_SECRET_KEY=generate_a_random_64_character_hex_or_alphanumeric_secret_here
ENVIRONMENT=local
DEBUG=true
```

To generate a secure JWT secret:
```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

---

## 4. Running the Development Server

From the project root:

```bash
uvicorn backend.main:app --reload --port 8000
```

- **Interactive API Documentation (Swagger UI)**: [http://localhost:8000/docs](http://localhost:8000/docs)
- **Alternative ReDoc UI**: [http://localhost:8000/redoc](http://localhost:8000/redoc)
- **Root Health Check**: [http://localhost:8000/api/health](http://localhost:8000/api/health)
- **Supabase DB Health Check**: [http://localhost:8000/api/health/db](http://localhost:8000/api/health/db)

---

## 5. Database Migrations (Alembic)

Alembic manages all schema changes as code. Never make manual table modifications in the Supabase Table Editor.

```bash
# Apply migrations to your Supabase PostgreSQL database
alembic -c backend/alembic.ini upgrade head

# Rollback migrations (e.g. to base or specific revision)
alembic -c backend/alembic.ini downgrade base

# Check current revision status against database
alembic -c backend/alembic.ini current

# Inspect latest available heads
alembic -c backend/alembic.ini heads

# Generate a new autogenerated migration after updating backend/models/
alembic -c backend/alembic.ini revision --autogenerate -m "describe_changes"
```

---

## 6. Demo Seed Script (Smoke-Testing Only)

To populate an idempotent demo dataset (1 business, 1 owner user, 2 accounts, 3 customers, 3 vendors, 10 transactions, 5 invoices, 8 expenses):

```bash
# Optional: Set custom password for demo user (default: DemoSecurePassword123!)
export DEMO_USER_PASSWORD="YourSecurePassword123!"

# Run seed script
python backend/scripts/seed_demo.py
```

*Note: Real SME financial datasets are ingested via Member 3's ETL pipeline.*

---

## 7. Running Tests

Run the test suite from the repository root:

```bash
# Run all unit tests (does not require a live Supabase database)
python -m pytest tests/test_backend_health.py tests/test_backend_security.py tests/test_backend_auth.py tests/test_backend_schema.py tests/test_backend_import_unit.py tests/test_backend_query_unit.py

# Run live database integration tests against Supabase (requires valid DATABASE_URL in .env)
python -m pytest tests/test_backend_auth.py tests/test_backend_schema.py tests/test_backend_import_integration.py tests/test_backend_read_integration.py -m integration
```

---

## 8. CSV Ingestion & Import API (Day 3)

The backend provides authenticated CSV ingestion with automatic header alias resolution, strict type validation, row-level error audit logs, and deduplication against the canonical schema.

### Step A: Authenticate & Obtain JWT
```bash
# Login as an owner or admin
TOKEN=$(curl -s -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "demo.owner@sampletraders.com", "password": "DemoSecurePassword123!"}' \
  | jq -r .access_token)
```

### Step B: Download CSV Template
```bash
# Download template CSV with standard headers
curl -s -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/imports/templates/transactions?format=csv \
  -o transactions_template.csv
```

### Step C: Upload & Ingest CSV File
```bash
# Upload a bank transactions export
curl -X POST http://localhost:8000/api/imports \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@transactions_template.csv" \
  -F "import_type=transactions" \
  -F "source_dataset=bank_hdfc" \
  -F "on_error=skip"
```

### Step D: Inspect Status & Row Errors
```bash
# Inspect batch summary
curl -s -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/imports/<batch_id> | jq .

# Inspect paginated row-level errors
curl -s -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/imports/<batch_id>/errors | jq .
```

---

## 9. Core Financial Read APIs & Lookups (Day 4)

Exposes paginated, filterable, sortable, tenant-scoped read endpoints for transactions, invoices, expenses, UI dropdowns, and an internal programmatic data access layer for Members 3 & 4.

### REST Endpoints
- `GET /api/transactions`: Filter by date range, type (`inflow`/`outflow`), category, amount, counterparty, search `q`, dataset. Includes optional aggregate totals (`include_totals=true`).
- `GET /api/transactions/{id}`: Tenant-scoped detail lookup (404 on cross-tenant access).
- `GET /api/invoices`: Enriched with `customer_name` and `outstanding_amount`. Filter by status, customer, date range, overdue, unpaid, search `q`.
- `GET /api/invoices/{id}`: Tenant-scoped detail lookup.
- `GET /api/expenses`: Enriched with `vendor_name`. Filter by date, category, vendor, payment method, recurring flag, amount, search `q`. Optional totals.
- `GET /api/expenses/{id}`: Tenant-scoped detail lookup.
- `GET /api/accounts`: Active account dropdown lookup.
- `GET /api/customers?q=`: Customer dropdown lookup with search.
- `GET /api/vendors?q=`: Vendor dropdown lookup with search.
- `GET /api/categories?kind=transaction|expense`: Distinct populated categories.
- `GET /api/meta/data-range`: Min/max dates and record counts for dashboard bounds.

### Internal Read Layer (for Finance Engine & Agents)
Members 3 & 4 can query the database directly in Python without HTTP calls using:
```python
from backend.services.read_api import fetch_transactions, fetch_invoices, fetch_expenses

# Scoped to business_id, type-safe, returns canonical Pydantic models (max 100k rows)
txns = fetch_transactions(db=session, business_id=biz_uuid, start_date=d1, end_date=d2)
```
See [docs/data-access-for-finance-tools.md](../docs/data-access-for-finance-tools.md) for full details.

### Export OpenAPI Schema
```bash
python backend/scripts/export_openapi.py
# Schema written to docs/openapi.json
```

---

## 10. Financial Summary API & Engine Adapter (Day 5)

Exposes the business financial summary endpoint backed by Member 3's deterministic Finance Engine via an extensible provider adapter.

### REST Endpoints
- `GET /api/businesses/{business_id}/financial-summary`: Primary endpoint. Enforces tenant matching against JWT (403 if mismatch). Returns headline KPIs (`total_inflow`, `total_outflow`, `net_cash_flow`, `total_expenses`), cash balances, receivables aging, top 5 expense categories, and preceding period comparisons.
- `GET /api/financial-summary`: Convenience alias using the token's `business_id`.

### Configuration Settings
- `FINANCE_ENGINE_MODE`: `auto` (default), `engine` (enforces Member 3's engine), or `interim` (fallback backend SQL aggregates).
- `FINANCE_ENGINE_TIMEOUT_SECONDS`: Request timeout guard (default `20.0`).

### Running Day 5 Tests
```bash
# Run unit tests (no DB required)
python -m pytest tests/test_backend_summary_unit.py

# Run live integration tests against Supabase
python -m pytest tests/test_backend_summary_integration.py -m integration
```




