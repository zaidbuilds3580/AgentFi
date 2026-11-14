"""
Live integration tests for the Day 5 Financial Summary API against Supabase PostgreSQL.
Tests multi-tenant isolation, 403 cross-tenant guards, independent test oracle verification,
period presets, data coverage warnings, include trimming, and N+1 query prevention.
"""

from datetime import date
from decimal import Decimal
import os
import uuid
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event

from backend.database.session import SessionLocal, engine
from backend.main import app

pytestmark = pytest.mark.integration

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "..", "backend", "tests", "fixtures")


def _register_and_login(client: TestClient, role: str = "owner"):
    unique_suffix = uuid.uuid4().hex[:8]
    email = f"summary_{role}_{unique_suffix}@example.com"
    pwd = "Password123!"
    biz_name = f"Summary Test Biz {unique_suffix}"

    res = client.post(
        "/api/auth/register",
        json={
            "email": email,
            "password": pwd,
            "full_name": f"User {role}",
            "business_name": biz_name,
            "country": "IN",
            "currency": "INR",
        },
    )
    assert res.status_code == 201

    login_res = client.post("/api/auth/login", json={"email": email, "password": pwd})
    assert login_res.status_code == 200
    token = login_res.json()["access_token"]
    user_data = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).json()
    return token, user_data


def _import_fixture(client: TestClient, token: str, filename: str, import_type: str):
    file_path = os.path.join(FIXTURES_DIR, filename)
    with open(file_path, "rb") as f:
        res = client.post(
            "/api/imports",
            headers={"Authorization": f"Bearer {token}"},
            files={"file": (filename, f.read(), "text/csv")},
            data={"import_type": import_type},
        )
    assert res.status_code == 201, f"Failed import {filename}: {res.text}"
    return res.json()


def test_financial_summary_multi_tenant_isolation_and_403():
    client = TestClient(app)

    # 1. Register Business A and import data
    token_a, user_a = _register_and_login(client, "owner")
    headers_a = {"Authorization": f"Bearer {token_a}"}
    biz_a_id = user_a["business_id"]

    _import_fixture(client, token_a, "transactions_ok.csv", "transactions")
    _import_fixture(client, token_a, "invoices_ok.csv", "invoices")
    _import_fixture(client, token_a, "expenses_ok.csv", "expenses")

    # 2. Register Business B (clean business)
    token_b, user_b = _register_and_login(client, "owner")
    headers_b = {"Authorization": f"Bearer {token_b}"}
    biz_b_id = user_b["business_id"]

    # 3. Business A accesses their summary -> 200 OK
    res_a = client.get(
        f"/api/businesses/{biz_a_id}/financial-summary?period=custom&date_from=2026-09-01&date_to=2026-11-30&as_of=2026-11-15",
        headers=headers_a,
    )
    assert res_a.status_code == 200
    data_a = res_a.json()
    assert data_a["business"]["id"] == biz_a_id
    assert data_a["meta"]["computed_by"] == "finance_engine"
    assert data_a["kpis"] is not None

    # 4. Cross-tenant access: Business B token requesting Business A's summary -> 403 Forbidden
    res_cross = client.get(
        f"/api/businesses/{biz_a_id}/financial-summary",
        headers=headers_b,
    )
    assert res_cross.status_code == 403

    # 5. Anonymous access -> 401 Unauthorized
    res_anon = client.get(f"/api/businesses/{biz_a_id}/financial-summary")
    assert res_anon.status_code == 401

    # 6. Business B accesses own summary via alias -> 200 OK with no_data warning
    res_b = client.get("/api/financial-summary", headers=headers_b)
    assert res_b.status_code == 200
    data_b = res_b.json()
    assert data_b["business"]["id"] == biz_b_id
    assert "no_data" in data_b["meta"]["warnings"]
    assert Decimal(str(data_b["kpis"]["total_inflow"])) == Decimal("0.00")


def test_independent_test_oracle_verification():
    client = TestClient(app)
    token, user = _register_and_login(client, "owner")
    headers = {"Authorization": f"Bearer {token}"}
    biz_id = user["business_id"]

    _import_fixture(client, token, "transactions_ok.csv", "transactions")
    _import_fixture(client, token, "invoices_ok.csv", "invoices")
    _import_fixture(client, token, "expenses_ok.csv", "expenses")

    d_from = "2026-10-01"
    d_to = "2026-10-31"

    # Query all transactions in period via GET /api/transactions
    tx_res = client.get(
        f"/api/transactions?date_from={d_from}&date_to={d_to}&limit=200",
        headers=headers,
    )
    assert tx_res.status_code == 200
    txns = tx_res.json()["items"]

    # Independent Oracle Calculation (Python Decimals)
    oracle_inflow = Decimal("0.00")
    oracle_outflow = Decimal("0.00")
    for t in txns:
        amt = Decimal(str(t["amount"]))
        if t["transaction_type"] == "inflow":
            oracle_inflow += amt
        else:
            oracle_outflow += amt

    oracle_net = oracle_inflow - oracle_outflow

    # Fetch financial summary
    res = client.get(
        f"/api/businesses/{biz_id}/financial-summary?period=custom&date_from={d_from}&date_to={d_to}&as_of=2026-11-01",
        headers=headers,
    )
    assert res.status_code == 200
    summary = res.json()
    kpis = summary["kpis"]

    assert Decimal(str(kpis["total_inflow"])) == oracle_inflow
    assert Decimal(str(kpis["total_outflow"])) == oracle_outflow
    assert Decimal(str(kpis["net_cash_flow"])) == oracle_net


def test_data_coverage_warning_and_bad_parameters():
    client = TestClient(app)
    token, user = _register_and_login(client, "owner")
    headers = {"Authorization": f"Bearer {token}"}
    biz_id = user["business_id"]

    _import_fixture(client, token, "transactions_ok.csv", "transactions")

    # 1. Period in 2030 (outside data coverage)
    res_future = client.get(
        f"/api/businesses/{biz_id}/financial-summary?period=custom&date_from=2030-01-01&date_to=2030-01-31&as_of=2030-02-01",
        headers=headers,
    )
    assert res_future.status_code == 200
    data_future = res_future.json()
    assert "period_outside_data_coverage" in data_future["meta"]["warnings"]
    assert Decimal(str(data_future["kpis"]["total_inflow"])) == Decimal("0.00")

    # 2. Bad parameters: inverted dates
    res_bad_dates = client.get(
        f"/api/businesses/{biz_id}/financial-summary?period=custom&date_from=2026-10-31&date_to=2026-10-01",
        headers=headers,
    )
    assert res_bad_dates.status_code == 422

    # 3. Bad parameters: missing custom dates
    res_missing_dates = client.get(
        f"/api/businesses/{biz_id}/financial-summary?period=custom",
        headers=headers,
    )
    assert res_missing_dates.status_code == 422


def test_payload_trimming_via_include():
    client = TestClient(app)
    token, user = _register_and_login(client, "owner")
    headers = {"Authorization": f"Bearer {token}"}
    biz_id = user["business_id"]

    _import_fixture(client, token, "transactions_ok.csv", "transactions")

    res = client.get(
        f"/api/businesses/{biz_id}/financial-summary?include=kpis,cash",
        headers=headers,
    )
    assert res.status_code == 200
    data = res.json()
    assert data["kpis"] is not None
    assert data["cash"] is not None
    assert data["receivables"] is None
    assert data["expenses"] is None
    assert data["comparison"] is None


def test_n_plus_one_bounded_queries():
    client = TestClient(app)
    token, user = _register_and_login(client, "owner")
    headers = {"Authorization": f"Bearer {token}"}
    biz_id = user["business_id"]

    _import_fixture(client, token, "transactions_ok.csv", "transactions")
    _import_fixture(client, token, "invoices_ok.csv", "invoices")
    _import_fixture(client, token, "expenses_ok.csv", "expenses")

    queries = []

    def before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        queries.append(statement)

    event.listen(engine, "before_cursor_execute", before_cursor_execute)
    try:
        queries.clear()
        res = client.get(
            f"/api/businesses/{biz_id}/financial-summary?period=last_30_days",
            headers=headers,
        )
        assert res.status_code == 200
        # All data fetching is batch scoped; queries count should be bounded and constant
        select_queries = [q for q in queries if "SELECT" in q.upper()]
        assert len(select_queries) <= 12, f"Expected <= 12 SELECT queries, got {len(select_queries)}"
    finally:
        event.remove(engine, "before_cursor_execute", before_cursor_execute)
