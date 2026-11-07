"""
Unit and API tests for authentication endpoints and RBAC dependencies.
"""

import uuid
from fastapi.testclient import TestClient
from fastapi import Depends, HTTPException
import pytest

from backend.main import app
from backend.auth.security import create_access_token
from backend.api.deps import require_role

client = TestClient(app)
random_suffix = str(uuid.uuid4())[:8]


def test_auth_me_with_valid_token():
    """
    GET /api/auth/me returns decoded claims for a valid Bearer token.
    """
    token = create_access_token(
        subject="user-uuid-999",
        business_id="biz-uuid-111",
        role="owner",
    )

    response = client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["user_id"] == "user-uuid-999"
    assert data["business_id"] == "biz-uuid-111"
    assert data["role"] == "owner"
    assert "exp" in data
    assert "iat" in data


def test_auth_me_missing_token():
    """
    GET /api/auth/me returns 401 when Authorization header is absent.
    """
    response = client.get("/api/auth/me")
    assert response.status_code == 401
    assert response.json()["detail"] == "Not authenticated"


def test_auth_me_invalid_token():
    """
    GET /api/auth/me returns 401 when an invalid token string is provided.
    """
    response = client.get(
        "/api/auth/me",
        headers={"Authorization": "Bearer not-a-valid-jwt-token"},
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Could not validate credentials"


def test_auth_register_validation_short_password():
    """
    POST /api/auth/register rejects passwords shorter than 8 characters (422).
    """
    payload = {
        "email": "test@example.com",
        "password": "short",
        "full_name": "Test User",
        "business_name": "Acme SME Ltd",
        "role": "owner",
    }
    response = client.post("/api/auth/register", json=payload)
    assert response.status_code == 422


def test_auth_login_validation_invalid_email():
    """
    POST /api/auth/login validates email format (422).
    """
    payload = {
        "email": "not-an-email",
        "password": "ValidPassword123!",
    }
    response = client.post("/api/auth/login", json=payload)
    assert response.status_code == 422


@pytest.mark.integration
def test_auth_register_login_me_end_to_end_isolation():
    """
    Integration test: Register Biz 1 & Biz 2 -> Login -> verify /me and tenant isolation.
    """
    from backend.core.config import settings
    if "placeholder" in settings.DATABASE_URL.lower():
        pytest.skip("Supabase DATABASE_URL is not configured (contains placeholder)")

    # 1. Register Biz 1
    reg1 = {
        "email": f"owner1_{random_suffix}@company1.com",
        "password": "Password123!",
        "full_name": "Alice Owner",
        "business_name": "Alpha Corp",
    }
    res1 = client.post("/api/auth/register", json=reg1)
    assert res1.status_code == 201
    user1 = res1.json()
    assert user1["email"] == reg1["email"].lower()
    assert user1["role"] == "owner"

    # Duplicate registration returns 409
    dup_res = client.post("/api/auth/register", json=reg1)
    assert dup_res.status_code == 409

    # 2. Login Biz 1
    login1 = client.post("/api/auth/login", json={"email": reg1["email"], "password": reg1["password"]})
    assert login1.status_code == 200
    token1 = login1.json()["access_token"]

    # 3. Verify /me for Biz 1
    me1 = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token1}"})
    assert me1.status_code == 200
    assert me1.json()["business_name"] == "Alpha Corp"
    assert me1.json()["user_id"] == user1["id"]

    # 4. Register Biz 2
    reg2 = {
        "email": f"owner2_{random_suffix}@company2.com",
        "password": "Password123!",
        "full_name": "Bob Owner",
        "business_name": "Beta Corp",
    }
    res2 = client.post("/api/auth/register", json=reg2)
    assert res2.status_code == 201
    user2 = res2.json()

    # 5. Assert tenant isolation: business_id must differ
    assert user1["business_id"] != user2["business_id"]


def test_role_enforcement_rbac():
    """
    Test require_role dependency factory with authorized and unauthorized users.
    """
    # Create a temporary test route protected by require_role("owner", "admin")
    @app.get("/api/test-protected-role")
    def protected_route(user=Depends(require_role("owner", "admin"))):
        return {"authorized": True, "role": user.role}

    owner_token = create_access_token("u1", "b1", "owner")
    viewer_token = create_access_token("u2", "b1", "viewer")

    # Owner should succeed
    res_owner = client.get(
        "/api/test-protected-role",
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    assert res_owner.status_code == 200
    assert res_owner.json()["authorized"] is True

    # Viewer should be forbidden (403)
    res_viewer = client.get(
        "/api/test-protected-role",
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert res_viewer.status_code == 403
    assert "Operation forbidden" in res_viewer.json()["detail"]

