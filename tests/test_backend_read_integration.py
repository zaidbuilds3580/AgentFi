"""
Live integration tests for the Day 4 Read APIs against Supabase PostgreSQL.
Tests multi-tenant isolation, pagination, filtering, display totals, lookup endpoints,
internal read API (read_api.py), 404 cross-tenant privacy, and N+1 prevention.
"""

from datetime import date
from decimal import Decimal
import io
import os
import uuid
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, text

from backend.database.session import SessionLocal, engine
from backend.main import app
from backend.services.read_api import (
    MAX_INTERNAL_ROWS,
    fetch_expenses,
    fetch_invoices,
    fetch_transactions,
)

pytestmark = pytest.mark.integration

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "..", "backend", "tests", "fixtures")


def _register_and_login(client: TestClient, role: str = "owner"):
    unique_suffix = uuid.uuid4().hex[:8]
    email = f"read_{role}_{unique_suffix}@example.com"
    pwd = "Password123!"
    biz_name = f"Read Test Biz {unique_suffix}"

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


def test_multi_tenant_isolation_and_404_privacy():
    client = TestClient(app)
    # Register Business A
    token_a, user_a = _register_and_login(client, "owner")
    headers_a = {"Authorization": f"Bearer {token_a}"}
    biz_a_id = user_a["business_id"]

    # Register Business B
    token_b, user_b = _register_and_login(client, "owner")
    headers_b = {"Authorization": f"Bearer {token_b}"}
    biz_b_id = user_b["business_id"]

    # Import fixtures into Business A
    _import_fixture(client, token_a, "transactions_ok.csv", "transactions")
    _import_fixture(client, token_a, "invoices_ok.csv", "invoices")
    _import_fixture(client, token_a, "expenses_ok.csv", "expenses")

    # 1. Business A sees their data
    res_tx_a = client.get("/api/transactions", headers=headers_a)
    assert res_tx_a.status_code == 200
    tx_items_a = res_tx_a.json()["items"]
    assert len(tx_items_a) > 0
    sample_tx_id = tx_items_a[0]["id"]

    res_inv_a = client.get("/api/invoices", headers=headers_a)
    assert res_inv_a.status_code == 200
    inv_items_a = res_inv_a.json()["items"]
    assert len(inv_items_a) > 0
    sample_inv_id = inv_items_a[0]["id"]

    res_exp_a = client.get("/api/expenses", headers=headers_a)
    assert res_exp_a.status_code == 200
    exp_items_a = res_exp_a.json()["items"]
    assert len(exp_items_a) > 0
    sample_exp_id = exp_items_a[0]["id"]

    # 2. Business B list endpoints return 0 items (strict isolation)
    res_tx_b = client.get("/api/transactions", headers=headers_b)
    assert res_tx_b.status_code == 200
    assert res_tx_b.json()["total"] == 0
    assert len(res_tx_b.json()["items"]) == 0

    res_inv_b = client.get("/api/invoices", headers=headers_b)
    assert res_inv_b.status_code == 200
    assert res_inv_b.json()["total"] == 0

    res_exp_b = client.get("/api/expenses", headers=headers_b)
    assert res_exp_b.status_code == 200
    assert res_exp_b.json()["total"] == 0

    # 3. Cross-tenant ID access returns 404 (NOT 403, to prevent enumeration)
    assert client.get(f"/api/transactions/{sample_tx_id}", headers=headers_b).status_code == 404
    assert client.get(f"/api/invoices/{sample_inv_id}", headers=headers_b).status_code == 404
    assert client.get(f"/api/expenses/{sample_exp_id}", headers=headers_b).status_code == 404

    # 4. Thin alias endpoint with mismatched business_id returns 403
    alias_res = client.get(f"/api/businesses/{biz_a_id}/transactions", headers=headers_b)
    assert alias_res.status_code == 403


def test_transactions_pagination_filtering_and_totals():
    client = TestClient(app)
    token, user = _register_and_login(client, "owner")
    headers = {"Authorization": f"Bearer {token}"}

    _import_fixture(client, token, "transactions_ok.csv", "transactions")

    # 1. Pagination check
    res = client.get("/api/transactions?limit=2&offset=0", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert len(data["items"]) == 2
    assert data["limit"] == 2
    assert data["offset"] == 0
    assert data["has_more"] is True
    first_id = data["items"][0]["id"]

    # Next page
    res_next = client.get("/api/transactions?limit=2&offset=2", headers=headers)
    assert res_next.status_code == 200
    data_next = res_next.json()
    assert len(data_next["items"]) >= 1
    assert data_next["items"][0]["id"] != first_id

    # 2. Type filter
    inflow_res = client.get("/api/transactions?transaction_type=inflow", headers=headers)
    assert inflow_res.status_code == 200
    for item in inflow_res.json()["items"]:
        assert item["transaction_type"] == "inflow"

    outflow_res = client.get("/api/transactions?transaction_type=outflow", headers=headers)
    assert outflow_res.status_code == 200
    for item in outflow_res.json()["items"]:
        assert item["transaction_type"] == "outflow"

    # 3. Totals check
    totals_res = client.get("/api/transactions?include_totals=true", headers=headers)
    assert totals_res.status_code == 200
    totals_data = totals_res.json()
    totals = totals_data["totals"]
    assert totals is not None
    assert totals["count"] == totals_data["total"]
    inflow_d = Decimal(str(totals["inflow_total"]))
    outflow_d = Decimal(str(totals["outflow_total"]))
    net_d = Decimal(str(totals["net"]))
    assert net_d == inflow_d - outflow_d

    # 4. Search q
    q_res = client.get("/api/transactions?q=Consulting", headers=headers)
    assert q_res.status_code == 200
    for item in q_res.json()["items"]:
        text_dump = f"{item['description']} {item['counterparty']} {item['reference']}".lower()
        assert "consulting" in text_dump


def test_invoices_enrichment_and_filters():
    client = TestClient(app)
    token, user = _register_and_login(client, "owner")
    headers = {"Authorization": f"Bearer {token}"}

    _import_fixture(client, token, "invoices_ok.csv", "invoices")

    # Fetch list
    res = client.get("/api/invoices", headers=headers)
    assert res.status_code == 200
    items = res.json()["items"]
    assert len(items) > 0

    # Verify customer_name joined and outstanding_amount computed
    for item in items:
        assert "customer_name" in item
        assert "outstanding_amount" in item
        tot = Decimal(str(item["amount_total"]))
        paid = Decimal(str(item["amount_paid"]))
        out = Decimal(str(item["outstanding_amount"]))
        assert out == tot - paid

    # Detail endpoint
    inv_id = items[0]["id"]
    detail_res = client.get(f"/api/invoices/{inv_id}", headers=headers)
    assert detail_res.status_code == 200
    assert detail_res.json()["id"] == inv_id

    # Unpaid filter
    unpaid_res = client.get("/api/invoices?unpaid=true", headers=headers)
    assert unpaid_res.status_code == 200
    for item in unpaid_res.json()["items"]:
        assert item["status"] not in ("paid", "void")
        assert Decimal(str(item["amount_paid"])) < Decimal(str(item["amount_total"]))


def test_expenses_totals_and_enrichment():
    client = TestClient(app)
    token, user = _register_and_login(client, "owner")
    headers = {"Authorization": f"Bearer {token}"}

    _import_fixture(client, token, "expenses_ok.csv", "expenses")

    res = client.get("/api/expenses?include_totals=true", headers=headers)
    assert res.status_code == 200
    data = res.json()
    items = data["items"]
    assert len(items) > 0

    for item in items:
        assert "vendor_name" in item

    totals = data["totals"]
    assert totals is not None
    assert totals["count"] == data["total"]
    assert Decimal(str(totals["total_amount"])) > Decimal("0.00")

    # Detail endpoint
    exp_id = items[0]["id"]
    detail_res = client.get(f"/api/expenses/{exp_id}", headers=headers)
    assert detail_res.status_code == 200
    assert detail_res.json()["id"] == exp_id


def test_lookups_endpoints():
    client = TestClient(app)
    token, user = _register_and_login(client, "owner")
    headers = {"Authorization": f"Bearer {token}"}

    # Import fixtures
    _import_fixture(client, token, "transactions_ok.csv", "transactions")
    _import_fixture(client, token, "invoices_ok.csv", "invoices")
    _import_fixture(client, token, "expenses_ok.csv", "expenses")

    # Accounts lookup
    acc_res = client.get("/api/accounts", headers=headers)
    assert acc_res.status_code == 200
    accounts = acc_res.json()
    assert isinstance(accounts, list)

    # Customers lookup
    cust_res = client.get("/api/customers", headers=headers)
    assert cust_res.status_code == 200
    customers = cust_res.json()
    assert isinstance(customers, list)
    assert len(customers) > 0

    # Vendors lookup
    vend_res = client.get("/api/vendors", headers=headers)
    assert vend_res.status_code == 200
    vendors = vend_res.json()
    assert isinstance(vendors, list)
    assert len(vendors) > 0

    # Categories lookup (transactions & expenses)
    cat_tx_res = client.get("/api/categories?kind=transaction", headers=headers)
    assert cat_tx_res.status_code == 200
    assert isinstance(cat_tx_res.json(), list)

    cat_exp_res = client.get("/api/categories?kind=expense", headers=headers)
    assert cat_exp_res.status_code == 200
    assert isinstance(cat_exp_res.json(), list)

    # Invalid kind returns 422
    bad_cat_res = client.get("/api/categories?kind=invalid", headers=headers)
    assert bad_cat_res.status_code == 422

    # Data range metadata
    meta_res = client.get("/api/meta/data-range", headers=headers)
    assert meta_res.status_code == 200
    meta = meta_res.json()
    assert "transactions" in meta
    assert "invoices" in meta
    assert "expenses" in meta
    assert meta["transactions"]["count"] > 0
    assert meta["invoices"]["count"] > 0
    assert meta["expenses"]["count"] > 0


def test_internal_read_api():
    client = TestClient(app)
    token, user = _register_and_login(client, "owner")
    biz_id = uuid.UUID(user["business_id"])

    _import_fixture(client, token, "transactions_ok.csv", "transactions")
    _import_fixture(client, token, "invoices_ok.csv", "invoices")
    _import_fixture(client, token, "expenses_ok.csv", "expenses")

    with SessionLocal() as db:
        # 1. Fetch transactions
        txns = fetch_transactions(db, biz_id)
        assert len(txns) > 0
        assert all(t.business_id == biz_id for t in txns)

        # 2. Fetch invoices
        invs = fetch_invoices(db, biz_id)
        assert len(invs) > 0
        assert all(i.business_id == biz_id for i in invs)

        # 3. Fetch expenses
        exps = fetch_expenses(db, biz_id)
        assert len(exps) > 0
        assert all(e.business_id == biz_id for e in exps)

        # 4. Limit exceeding MAX_INTERNAL_ROWS raises ValueError
        with pytest.raises(ValueError):
            fetch_transactions(db, biz_id, limit=MAX_INTERNAL_ROWS + 1)


def test_n_plus_one_prevention_on_invoices_and_expenses():
    client = TestClient(app)
    token, user = _register_and_login(client, "owner")
    headers = {"Authorization": f"Bearer {token}"}

    _import_fixture(client, token, "invoices_ok.csv", "invoices")
    _import_fixture(client, token, "expenses_ok.csv", "expenses")

    # Verify that listing invoices issues bounded queries (count + single joined select)
    queries = []

    def before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        queries.append(statement)

    event.listen(engine, "before_cursor_execute", before_cursor_execute)
    try:
        queries.clear()
        res_inv = client.get("/api/invoices?limit=50", headers=headers)
        assert res_inv.status_code == 200
        # Exactly 2 queries: one COUNT, one SELECT with JOIN
        # (Exclude any background internal transaction/savepoint statements if any)
        select_queries = [q for q in queries if "SELECT" in q.upper()]
        assert len(select_queries) <= 2, f"Expected <= 2 SELECT queries, got {len(select_queries)}"

        queries.clear()
        res_exp = client.get("/api/expenses?limit=50", headers=headers)
        assert res_exp.status_code == 200
        select_queries_exp = [q for q in queries if "SELECT" in q.upper()]
        assert len(select_queries_exp) <= 2, f"Expected <= 2 SELECT queries, got {len(select_queries_exp)}"
    finally:
        event.remove(engine, "before_cursor_execute", before_cursor_execute)
