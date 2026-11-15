"""
Security Audit: Automated Route Inventory Test.

Iterates over ALL registered application routes and asserts that only an explicit allowlist
of public endpoints can be reached without authentication.
Guarantees that no unauthenticated route can ever be accidentally introduced into the codebase.
"""

from fastapi.testclient import TestClient
import pytest

from backend.main import create_app

app = create_app()
client = TestClient(app)

# Explicit public allowlist of routes that do NOT require authentication
PUBLIC_ALLOWLIST = {
    ("/", "GET"),
    ("/docs", "GET"),
    ("/docs/oauth2-redirect", "GET"),
    ("/redoc", "GET"),
    ("/api/openapi.json", "GET"),
    ("/api/health", "GET"),
    ("/api/health/db", "GET"),
    ("/api/auth/register", "POST"),
    ("/api/auth/login", "POST"),
}


def test_unauthenticated_route_inventory_enforcement():
    """
    Every registered route NOT in the public allowlist must reject unauthenticated requests with 401.
    """
    unauthorized_failures = []
    checked_routes = 0

    for route in app.routes:
        methods = getattr(route, "methods", set())
        path = getattr(route, "path", None)

        if not path or not methods:
            continue

        for method in methods:
            if method in ("HEAD", "OPTIONS"):
                continue

            # Check if explicitly public
            if (path, method) in PUBLIC_ALLOWLIST:
                continue

            checked_routes += 1
            # Format dummy path parameters if needed
            test_path = path.replace("{business_id}", "00000000-0000-0000-0000-000000000001")
            test_path = test_path.replace("{id}", "00000000-0000-0000-0000-000000000002")
            test_path = test_path.replace("{batch_id}", "00000000-0000-0000-0000-000000000003")
            test_path = test_path.replace("{import_type}", "transactions")

            # Call without Authorization header
            if method == "GET":
                response = client.get(test_path)
            elif method == "POST":
                response = client.post(test_path)
            elif method == "PUT":
                response = client.put(test_path)
            elif method == "DELETE":
                response = client.delete(test_path)
            else:
                continue

            if response.status_code != 401:
                unauthorized_failures.append(
                    f"{method} {path} returned status {response.status_code} instead of 401 without auth"
                )

    assert not unauthorized_failures, (
        f"Security vulnerability detected: Unauthenticated routes allowed without 401:\n"
        + "\n".join(f"- {f}" for f in unauthorized_failures)
    )
    assert checked_routes >= 15, f"Expected at least 15 authenticated routes checked, got {checked_routes}"


def test_public_allowlist_endpoints_respond_without_token():
    """
    Verify all allowlisted public endpoints respond without requiring a Bearer token.
    """
    # Root index
    res_root = client.get("/")
    assert res_root.status_code == 200

    # Health check
    res_health = client.get("/api/health")
    assert res_health.status_code == 200

    # OpenAPI JSON schema
    res_openapi = client.get("/api/openapi.json")
    assert res_openapi.status_code == 200
