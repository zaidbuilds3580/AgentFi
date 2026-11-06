"""
Unit and API tests for authentication endpoints and RBAC dependencies.
"""

from fastapi.testclient import TestClient
from fastapi import Depends, HTTPException
import pytest

from backend.main import app
from backend.auth.security import create_access_token
from backend.api.deps import require_role

client = TestClient(app)


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


def test_auth_register_not_implemented():
    """
    POST /api/auth/register returns 501 Not Implemented (skeleton for Day 2).
    """
    payload = {
        "email": "test@example.com",
        "password": "StrongPassword123!",
        "full_name": "Test User",
        "business_name": "Acme SME Ltd",
        "role": "owner",
    }
    response = client.post("/api/auth/register", json=payload)
    assert response.status_code == 501
    assert "Day 2" in response.json()["detail"]


def test_auth_login_not_implemented():
    """
    POST /api/auth/login returns 501 Not Implemented (skeleton for Day 2).
    """
    payload = {
        "email": "test@example.com",
        "password": "StrongPassword123!",
    }
    response = client.post("/api/auth/login", json=payload)
    assert response.status_code == 501
    assert "Day 2" in response.json()["detail"]


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

