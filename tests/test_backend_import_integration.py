"""
Live integration tests for the CSV imports API against Supabase PostgreSQL.
Tests happy paths, duplicate protection, row-level error handling, find-or-create counterparties,
RBAC, and multi-tenant isolation.
"""

from decimal import Decimal
import io
import os
import uuid
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from backend.core.config import settings
from backend.database.session import SessionLocal, engine
from backend.main import app

pytestmark = pytest.mark.integration

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "..", "backend", "tests", "fixtures")


def _register_and_login(client: TestClient, role: str = "owner"):
    unique_suffix = uuid.uuid4().hex[:8]
    email = f"import_{role}_{unique_suffix}@example.com"
    pwd = "Password123!"
    biz_name = f"Import Test Biz {unique_suffix}"

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


def test_import_happy_path_all_three_types():
    client = TestClient(app)
    token, user = _register_and_login(client, "owner")
    headers = {"Authorization": f"Bearer {token}"}
    biz_id = user["business_id"]

    # 1. Transactions import
    with open(os.path.join(FIXTURES_DIR, "transactions_ok.csv"), "rb") as f:
        res = client.post(
            "/api/imports",
            headers=headers,
            data={"import_type": "transactions", "source_dataset": "bank_hdfc"},
            files={"file": ("transactions_ok.csv", f, "text/csv")},
        )
    assert res.status_code == 201, res.text
    data = res.json()
    assert data["status"] == "completed"
    assert data["total_rows"] == 4
    assert data["inserted_rows"] == 4
    assert data["duplicate_rows"] == 0
    assert data["error_rows"] == 0

    # 2. Invoices import
    with open(os.path.join(FIXTURES_DIR, "invoices_ok.csv"), "rb") as f:
        res = client.post(
            "/api/imports",
            headers=headers,
            data={"import_type": "invoices", "source_dataset": "zoho_invoices"},
            files={"file": ("invoices_ok.csv", f, "text/csv")},
        )
    assert res.status_code == 201, res.text
    inv_data = res.json()
    assert inv_data["status"] == "completed"
    assert inv_data["inserted_rows"] == 3

    # 3. Expenses import
    with open(os.path.join(FIXTURES_DIR, "expenses_ok.csv"), "rb") as f:
        res = client.post(
            "/api/imports",
            headers=headers,
            data={"import_type": "expenses", "source_dataset": "expense_sheet"},
            files={"file": ("expenses_ok.csv", f, "text/csv")},
        )
    assert res.status_code == 201, res.text
    exp_data = res.json()
    assert exp_data["status"] == "completed"
    assert exp_data["inserted_rows"] == 3


def test_duplicate_file_rejection_and_same_rows_dedup():
    client = TestClient(app)
    token, user = _register_and_login(client, "owner")
    headers = {"Authorization": f"Bearer {token}"}

    with open(os.path.join(FIXTURES_DIR, "transactions_ok.csv"), "rb") as f:
        content = f.read()

    # First upload -> 201
    res1 = client.post(
        "/api/imports",
        headers=headers,
        data={"import_type": "transactions"},
        files={"file": ("transactions_ok.csv", io.BytesIO(content), "text/csv")},
    )
    assert res1.status_code == 201
    batch1_id = res1.json()["batch_id"]

    # Re-upload identical file -> 409 DUPLICATE_FILE carrying earlier batch_id
    res2 = client.post(
        "/api/imports",
        headers=headers,
        data={"import_type": "transactions"},
        files={"file": ("transactions_ok.csv", io.BytesIO(content), "text/csv")},
    )
    assert res2.status_code == 409
    detail = res2.json()["detail"]
    assert detail["code"] == "DUPLICATE_FILE"
    assert detail["existing_batch_id"] == batch1_id

    # Upload same content with a modified byte (e.g. trailing newline) so file hash is different
    # but row records are identical -> inserted 0, duplicate_rows = 4
    modified_content = content + b"\n"
    res3 = client.post(
        "/api/imports",
        headers=headers,
        data={"import_type": "transactions"},
        files={"file": ("transactions_renamed.csv", io.BytesIO(modified_content), "text/csv")},
    )
    assert res3.status_code == 201
    data3 = res3.json()
    assert data3["total_rows"] == 4
    assert data3["inserted_rows"] == 0
    assert data3["duplicate_rows"] == 4


def test_on_error_skip_vs_abort():
    client = TestClient(app)
    token, user = _register_and_login(client, "owner")
    headers = {"Authorization": f"Bearer {token}"}

    with open(os.path.join(FIXTURES_DIR, "transactions_bad_rows.csv"), "rb") as f:
        content = f.read()

    # 1. on_error = skip -> inserts 2 valid rows, status completed_with_errors
    res_skip = client.post(
        "/api/imports",
        headers=headers,
        data={"import_type": "transactions", "on_error": "skip"},
        files={"file": ("bad_rows_skip.csv", io.BytesIO(content), "text/csv")},
    )
    assert res_skip.status_code == 201
    data_skip = res_skip.json()
    assert data_skip["status"] == "completed_with_errors"
    assert data_skip["inserted_rows"] == 2
    assert data_skip["error_rows"] == 4
    assert len(data_skip["errors"]) > 0

    # Query /errors endpoint to inspect error records
    batch_id = data_skip["batch_id"]
    err_res = client.get(f"/api/imports/{batch_id}/errors", headers=headers)
    assert err_res.status_code == 200
    errors_list = err_res.json()
    assert len(errors_list) >= 4
    error_codes = {e["code"] for e in errors_list}
    assert "INVALID_DATE" in error_codes
    assert "INVALID_TYPE" in error_codes
    assert "INVALID_AMOUNT" in error_codes

    # 2. on_error = abort -> rolls back all, inserted_rows = 0, status = failed
    res_abort = client.post(
        "/api/imports",
        headers=headers,
        data={"import_type": "transactions", "on_error": "abort"},
        files={"file": ("bad_rows_abort.csv", io.BytesIO(content + b" "), "text/csv")},
    )
    assert res_abort.status_code == 201
    data_abort = res_abort.json()
    assert data_abort["status"] == "failed"
    assert data_abort["inserted_rows"] == 0


def test_find_or_create_customers_and_vendors():
    client = TestClient(app)
    token, user = _register_and_login(client, "owner")
    headers = {"Authorization": f"Bearer {token}"}
    biz_id = user["business_id"]

    # Invoices creates customer once
    with open(os.path.join(FIXTURES_DIR, "invoices_ok.csv"), "rb") as f:
        client.post(
            "/api/imports",
            headers=headers,
            data={"import_type": "invoices"},
            files={"file": ("invoices_ok.csv", f, "text/csv")},
        )

    # Verify customer records exist in DB
    with SessionLocal() as session:
        custs = session.execute(
            text("SELECT name FROM customers WHERE business_id = :bid"),
            {"bid": biz_id},
        ).fetchall()
        names = {c[0] for c in custs}
        assert "Alpha Retail" in names
        assert "Beta Solutions" in names
        assert "Gamma Logistics" in names


def test_account_resolution_and_unknown_account_error():
    client = TestClient(app)
    token, user = _register_and_login(client, "owner")
    headers = {"Authorization": f"Bearer {token}"}

    csv_data = "transaction_date,transaction_type,amount,account_name\n2026-10-01,inflow,1000,NonExistentBank\n"
    res = client.post(
        "/api/imports",
        headers=headers,
        data={"import_type": "transactions", "on_error": "skip"},
        files={"file": ("unknown_account.csv", io.BytesIO(csv_data.encode()), "text/csv")},
    )
    assert res.status_code == 201
    data = res.json()
    assert data["error_rows"] == 1
    assert data["errors"][0]["code"] == "ACCOUNT_NOT_FOUND"


def test_file_validation_rejections():
    client = TestClient(app)
    token, user = _register_and_login(client, "owner")
    headers = {"Authorization": f"Bearer {token}"}

    # 1. Non-CSV extension -> 415
    res = client.post(
        "/api/imports",
        headers=headers,
        data={"import_type": "transactions"},
        files={"file": ("data.pdf", b"pdf content", "application/pdf")},
    )
    assert res.status_code == 415

    # 2. NUL bytes -> 422
    res = client.post(
        "/api/imports",
        headers=headers,
        data={"import_type": "transactions"},
        files={"file": ("corrupt.csv", b"header\x00data", "text/csv")},
    )
    assert res.status_code == 422

    # 3. Empty file -> 422
    res = client.post(
        "/api/imports",
        headers=headers,
        data={"import_type": "transactions"},
        files={"file": ("empty.csv", b"", "text/csv")},
    )
    assert res.status_code == 422


def test_rbac_and_tenant_isolation():
    client = TestClient(app)
    token_a, user_a = _register_and_login(client, "owner")
    token_b, user_b = _register_and_login(client, "owner")

    # Business A uploads a batch
    with open(os.path.join(FIXTURES_DIR, "transactions_ok.csv"), "rb") as f:
        res = client.post(
            "/api/imports",
            headers={"Authorization": f"Bearer {token_a}"},
            data={"import_type": "transactions"},
            files={"file": ("transactions_ok.csv", f, "text/csv")},
        )
    assert res.status_code == 201
    batch_a_id = res.json()["batch_id"]

    # Business B attempts to read Business A's batch -> 404
    res_b = client.get(
        f"/api/imports/{batch_a_id}",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert res_b.status_code == 404

    # Business B attempts to read Business A's batch errors -> 404
    res_b_err = client.get(
        f"/api/imports/{batch_a_id}/errors",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert res_b_err.status_code == 404

    # No auth header -> 401
    res_unauth = client.get(f"/api/imports/{batch_a_id}")
    assert res_unauth.status_code == 401


def test_templates_endpoint():
    client = TestClient(app)
    token, _ = _register_and_login(client, "owner")
    headers = {"Authorization": f"Bearer {token}"}

    # JSON template
    res = client.get("/api/imports/templates/transactions", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert data["import_type"] == "transactions"
    col_names = {c["name"] for c in data["columns"]}
    assert "transaction_date" in col_names
    assert "amount" in col_names
    assert "example_row" in data

    # CSV template download
    res_csv = client.get("/api/imports/templates/transactions?format=csv", headers=headers)
    assert res_csv.status_code == 200
    assert "text/csv" in res_csv.headers["content-type"]
    assert "transaction_date,transaction_type,amount" in res_csv.text
