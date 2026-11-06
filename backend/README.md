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

- Python 3.11, 3.12, 3.13, or 3.14
- A [Supabase](https://supabase.com) account (Free Tier is fully supported)
- Git

---

## 2. Virtual Environment & Dependencies Setup

From the repository root:

```bash
# Create a virtual environment inside backend/ (git-ignored)
python -m venv backend/.venv

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

Verify that Alembic detects your database connection and metadata (no migrations are created on Day 1):

```bash
# Check current migration revision
alembic -c backend/alembic.ini current

# Check migration heads
alembic -c backend/alembic.ini heads
```

---

## 6. Running Tests

Run the test suite from the repository root:

```bash
# Run all unit tests (does not require a live database)
pytest tests/test_backend_health.py tests/test_backend_security.py tests/test_backend_auth.py

# Run live database integration tests (requires valid DATABASE_URL in .env)
pytest tests/test_backend_health.py -m integration
```

