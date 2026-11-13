# AgentFi

**AgentFi** – An AI‑Powered Multi‑Agent Finance Operations Assistant for Small and Medium Enterprises (SMEs).

## Architecture Overview
- **backend/** – FastAPI REST API service, Supabase PostgreSQL, Alembic, and JWT auth
- **agents/** – Individual finance agents (cashflow, invoice, expense, intelligence, reporting)
- **graph/** – State graph, router, and workflow orchestration
- **finance_engine/** – Core finance tools and deterministic algorithms
- **config/** – Configuration files and environment settings
- **scripts/** – Utility and automation scripts

## Python Version
The team uses Python 3.12. To set up your local development environment:

```bash
python3.12 -m venv .venv312
source .venv312/bin/activate
pip install -r requirements.txt
pip install -r backend/requirements.txt
```

## Backend & Database (Member 2)
The backend service powers AgentFi's deterministic financial operations and tenant-isolated data layer:
- **Framework**: FastAPI with Pydantic v2
- **Database**: Supabase PostgreSQL via SQLAlchemy 2.0 and `psycopg` (v3) session pooling
- **Migrations**: Alembic as code (no manual UI edits)
- **Security**: Custom JWT issuing with RBAC (`owner`, `admin`, `viewer`) and tenant isolation by `business_id`

For setup, database configuration, and running the server, see the [Backend Documentation](backend/README.md).

This repository contains the initial modular layout for the project.
