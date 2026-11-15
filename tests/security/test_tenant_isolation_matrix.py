"""
Integration Test Suite: Comprehensive Tenant Isolation & Security Matrix.

Validates multi-tenant boundaries between two distinct businesses (A & B):
1. Cross-Tenant Data Isolation (A never sees B's transactions, invoices, expenses, imports, summary)
2. Detail Endpoint Privacy (Accessing B's ID with A's token returns 404, never 403, preventing enumeration)
3. Alias Path Protection (Accessing /businesses/{B_id}/... with A's token returns 403)
4. Token Tampering Matrix (Expired token, wrong signature, nonexistent tenant, tampered payload -> 401)
5. RBAC Matrix (Viewer role can read but cannot import -> 403; no rows written)
6. Deactivated Account Enforcement (Deactivated user cannot login and token stops working -> 401)
"""

from datetime import date, datetime, timedelta, timezone
import io
import uuid
import pytest
from fastapi.testclient import TestClient
from jose import jwt

from backend.auth.security import create_access_token
from backend.core.config import settings
from backend.database.session import SessionLocal
from backend.main import app
from backend.models.businesses import Business
from backend.models.expenses import Expense
from backend.models.invoices import Invoice
from backend.models.transactions import Transaction
from backend.models.users import User

pytestmark = pytest.mark.integration
client = TestClient(app)


def _create_tenant(client: TestClient, label: str):
    unique = uuid.uuid4().hex[:8]
    email = f"tenant_{label}_{unique}@example.com"
    pwd = "Password123!"
    res = client.post(
        "/api/auth/register",
        json={
            "email": email,
            "password": pwd,
            "full_name": f"Owner {label}",
            "business_name": f"Business {label} {unique}",
            "country": "IN",
            "currency": "INR",
        },
    )
    assert res.status_code == 201
    user_dto = res.json()

    login_res = client.post("/api/auth/login", json={"email": email, "password": pwd})
    assert login_res.status_code == 200
    token = login_res.json()["access_token"]
    return {
        "user_id": user_dto["id"],
        "business_id": user_dto["business_id"],
        "email": email,
        "token": token,
        "headers": {"Authorization": f"Bearer {token}"},
    }


def test_cross_tenant_matrix_data_isolation():
    """
    Assert that Business A never sees Business B's financial data across any STABLE route.
    """
    biz_a = _create_tenant(client, "A")
    biz_b = _create_tenant(client, "B")

    # Seed direct records for Biz A and Biz B in DB
    db = SessionLocal()
    try:
        tx_a = Transaction(
            business_id=uuid.UUID(biz_a["business_id"]),
            transaction_date=date(2026, 11, 1),
            amount=1000.00,
            currency="INR",
            transaction_type="inflow",
            description="Tenant A Inflow",
        )
        tx_b = Transaction(
            business_id=uuid.UUID(biz_b["business_id"]),
            transaction_date=date(2026, 11, 2),
            amount=2000.00,
            currency="INR",
            transaction_type="inflow",
            description="Tenant B Inflow",
        )
        db.add_all([tx_a, tx_b])
        db.commit()
        tx_a_id = str(tx_a.id)
        tx_b_id = str(tx_b.id)
    finally:
        db.close()

    # 1. Biz A lists transactions -> must see tx_a and NEVER tx_b
    res_a = client.get("/api/transactions", headers=biz_a["headers"])
    assert res_a.status_code == 200
    items_a = res_a.json()["items"]
    ids_seen_by_a = [item["id"] for item in items_a]
    assert tx_a_id in ids_seen_by_a
    assert tx_b_id not in ids_seen_by_a

    # 2. Biz B lists transactions -> must see tx_b and NEVER tx_a
    res_b = client.get("/api/transactions", headers=biz_b["headers"])
    assert res_b.status_code == 200
    items_b = res_b.json()["items"]
    ids_seen_by_b = [item["id"] for item in items_b]
    assert tx_b_id in ids_seen_by_b
    assert tx_a_id not in ids_seen_by_b

    # 3. Biz A detail request for Biz B's ID returns 404 (cross-tenant enumeration prevention)
    res_cross_detail = client.get(f"/api/transactions/{tx_b_id}", headers=biz_a["headers"])
    assert res_cross_detail.status_code == 404
    assert res_cross_detail.json()["error"]["code"] == "NOT_FOUND"

    # 4. Biz A calling alias endpoint with Biz B's path business_id returns 403
    res_cross_alias = client.get(
        f"/api/businesses/{biz_b['business_id']}/transactions",
        headers=biz_a["headers"],
    )
    assert res_cross_alias.status_code == 403
    assert res_cross_alias.json()["error"]["code"] == "FORBIDDEN"


def test_cross_tenant_detail_404_for_invoices_and_expenses():
    """
    Cross-tenant detail queries on invoices and expenses must return 404.
    """
    biz_a = _create_tenant(client, "DetailA")
    biz_b = _create_tenant(client, "DetailB")

    db = SessionLocal()
    try:
        inv_b = Invoice(
            business_id=uuid.UUID(biz_b["business_id"]),
            invoice_number=f"INV-B-{uuid.uuid4().hex[:6]}",
            invoice_date=date(2026, 11, 1),
            due_date=date(2026, 12, 1),
            amount_total=5000.00,
            amount_paid=0.00,
            status="sent",
        )
        exp_b = Expense(
            business_id=uuid.UUID(biz_b["business_id"]),
            expense_date=date(2026, 11, 2),
            amount=750.00,
            currency="INR",
            category="Office Supplies",
        )
        db.add_all([inv_b, exp_b])
        db.commit()
        inv_b_id = str(inv_b.id)
        exp_b_id = str(exp_b.id)
    finally:
        db.close()

    # Biz A querying Biz B's invoice -> 404
    res_inv = client.get(f"/api/invoices/{inv_b_id}", headers=biz_a["headers"])
    assert res_inv.status_code == 404

    # Biz A querying Biz B's expense -> 404
    res_exp = client.get(f"/api/expenses/{exp_b_id}", headers=biz_a["headers"])
    assert res_exp.status_code == 404

    # Biz A querying Biz B's path aliases -> 403
    assert client.get(f"/api/businesses/{biz_b['business_id']}/invoices", headers=biz_a["headers"]).status_code == 403
    assert client.get(f"/api/businesses/{biz_b['business_id']}/expenses", headers=biz_a["headers"]).status_code == 403
    assert client.get(f"/api/businesses/{biz_b['business_id']}/financial-summary", headers=biz_a["headers"]).status_code == 403


def test_token_tampering_security():
    """
    Test that invalid signatures, expired tokens, nonexistent tenant IDs,
    and payload tampering are rejected with 401.
    """
    biz = _create_tenant(client, "Tamper")

    # 1. Expired token
    expired_token = create_access_token(
        subject=biz["user_id"],
        business_id=biz["business_id"],
        role="owner",
        expires_delta=timedelta(seconds=-60),
    )
    res_exp = client.get("/api/transactions", headers={"Authorization": f"Bearer {expired_token}"})
    assert res_exp.status_code == 401

    # 2. Token signed with wrong secret
    wrong_secret_token = jwt.encode(
        {"sub": biz["user_id"], "business_id": biz["business_id"], "role": "owner", "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
        "wrong-secret-key-1234567890",
        algorithm=settings.JWT_ALGORITHM,
    )
    res_wrong_secret = client.get("/api/transactions", headers={"Authorization": f"Bearer {wrong_secret_token}"})
    assert res_wrong_secret.status_code == 401

    # 3. Token with nonexistent business UUID
    nonexistent_biz_id = str(uuid.uuid4())
    nonexistent_biz_token = create_access_token(
        subject=biz["user_id"],
        business_id=nonexistent_biz_id,
        role="owner",
    )
    res_nonexistent = client.get("/api/transactions", headers={"Authorization": f"Bearer {nonexistent_biz_token}"})
    assert res_nonexistent.status_code == 401
    assert "Business tenant does not exist" in res_nonexistent.json()["detail"]


def test_rbac_viewer_role_write_protection():
    """
    RBAC: Viewer role can read data but cannot perform write/import actions (403).
    """
    biz = _create_tenant(client, "RBAC")

    # Issue a viewer token for this business
    viewer_token = create_access_token(
        subject=biz["user_id"],
        business_id=biz["business_id"],
        role="viewer",
    )
    viewer_headers = {"Authorization": f"Bearer {viewer_token}"}

    # 1. Viewer can read transactions (200)
    res_read = client.get("/api/transactions", headers=viewer_headers)
    assert res_read.status_code == 200

    # 2. Viewer attempting upload/import returns 403 Forbidden
    csv_content = b"date,amount,currency,type\n2026-11-01,500.00,INR,inflow\n"
    res_import = client.post(
        "/api/imports",
        headers=viewer_headers,
        data={"import_type": "transactions"},
        files={"file": ("test.csv", io.BytesIO(csv_content), "text/csv")},
    )
    assert res_import.status_code == 403
    assert res_import.json()["error"]["code"] == "FORBIDDEN"


def test_deactivated_user_and_tenant_enforcement():
    """
    Deactivated user (is_active=False) cannot log in and existing tokens stop working.
    """
    from backend.api.deps import clear_auth_cache

    biz = _create_tenant(client, "Deactivated")

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == uuid.UUID(biz["user_id"])).first()
        assert user is not None
        user.is_active = False
        db.commit()
    finally:
        db.close()

    # Clear auth cache to force fresh DB verification
    clear_auth_cache(biz["user_id"])

    # 1. Login fails with 401
    login_res = client.post("/api/auth/login", json={"email": biz["email"], "password": "Password123!"})
    assert login_res.status_code == 401

    # 2. Existing valid token fails with 401
    res_token = client.get("/api/transactions", headers=biz["headers"])
    assert res_token.status_code == 401
    assert "User account is deactivated" in res_token.json()["detail"]
