"""
Tests for health check endpoints (/api/health and /api/health/db).
"""

import pytest
from fastapi.testclient import TestClient
from unittest.mock import MagicMock

from backend.main import app
from backend.core.config import settings
from backend.api.deps import get_db

client = TestClient(app)


def test_health_endpoint():
    """
    GET /api/health must return 200 with status ok and current environment.
    """
    response = client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["environment"] == settings.ENVIRONMENT


def test_root_endpoint():
    """
    GET / must return 200 with app status and docs link.
    """
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "running"
    assert data["docs"] == "/docs"


def test_health_db_disconnected_mock():
    """
    GET /api/health/db returns 503 Service Unavailable when the database fails,
    providing helpful hints without leaking connection strings.
    """
    mock_db = MagicMock()
    mock_db.execute.side_effect = Exception("Connection refused to database host")

    app.dependency_overrides[get_db] = lambda: mock_db
    try:
        response = client.get("/api/health/db")
        assert response.status_code == 503
        data = response.json()
        assert data["detail"]["database"] == "disconnected"
        assert "troubleshooting_hints" in data["detail"]
        # Ensure no credentials or connection strings are leaked
        assert "postgresql" not in str(data)
        assert "password" not in str(data["detail"]["message"])
    finally:
        app.dependency_overrides.clear()


@pytest.mark.integration
def test_health_db_live_integration():
    """
    Integration test against live Supabase database.
    Skipped automatically if DATABASE_URL contains placeholder or is not configured.
    """
    if "placeholder" in settings.DATABASE_URL.lower():
        pytest.skip("Supabase DATABASE_URL is not configured (contains placeholder)")

    response = client.get("/api/health/db")
    assert response.status_code == 200
    data = response.json()
    assert data["database"] == "connected"

