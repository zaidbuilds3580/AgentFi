"""
Script to inspect FastAPI application routes and generate docs/api-contract-inventory.md.
"""

import os
import sys

# Ensure repository root is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

import json
from backend.main import app

def generate_inventory():
    openapi = app.openapi()
    
    # Classify routes
    # Classification rules:
    # /api/health* = STABLE
    # /api/auth/* = STABLE
    # /api/imports* = STABLE
    # /api/transactions*, /api/invoices*, /api/expenses*, lookups, /api/meta/data-range = STABLE
    # /api/financial-summary = STABLE
    # /api/businesses/{business_id}/... = ALIAS
    # / = INTERNAL / ROOT

    stable_routes = []
    alias_routes = []
    internal_routes = []

    # Map routes from app.routes
    for route in app.routes:
        methods = getattr(route, "methods", set())
        path = getattr(route, "path", None)
        if not path or not methods:
            continue
        
        for m in sorted(methods):
            if m in ("HEAD", "OPTIONS"):
                continue
            
            # Determine classification
            if path in ("/api/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc", "/"):
                status_cat = "INTERNAL"
            elif "/businesses/{business_id}/" in path or path.endswith("/upload"):
                status_cat = "ALIAS"
            else:
                status_cat = "STABLE"

            # Determine auth & scoping
            if path in ("/", "/docs", "/redoc", "/api/openapi.json") or path.startswith("/api/health") or path in ("/api/auth/register", "/api/auth/login"):
                auth_req = "None (Public)"
                roles = "Any / Anonymous"
                scoping = "N/A"
            elif path.startswith("/api/imports") and m == "POST":
                auth_req = "Bearer JWT"
                roles = "owner, admin"
                scoping = "JWT business_id claim (server-injected)"
            elif "/businesses/{business_id}/" in path:
                auth_req = "Bearer JWT"
                roles = "owner, admin" if "upload" in path else "owner, admin, viewer"
                scoping = "Path business_id verified against JWT claim (403 on mismatch)"
            else:
                auth_req = "Bearer JWT"
                roles = "owner, admin, viewer"
                scoping = "JWT business_id claim (scoped() SQL filter)"

            # Schema info from OpenAPI
            path_item = openapi.get("paths", {}).get(path, {})
            op = path_item.get(m.lower(), {})
            summary = op.get("summary", getattr(route, "summary", ""))
            
            req_body = "N/A"
            if "requestBody" in op:
                content = op["requestBody"].get("content", {})
                req_types = list(content.keys())
                req_body = ", ".join(req_types)
                if "application/json" in content:
                    schema_ref = content["application/json"].get("schema", {}).get("$ref", "")
                    if schema_ref:
                        req_body = f"`{schema_ref.split('/')[-1]}`"
                elif "multipart/form-data" in content:
                    req_body = "`multipart/form-data` (CSV + metadata)"

            resp_schemas = []
            for code, resp in sorted(op.get("responses", {}).items()):
                c = resp.get("content", {}).get("application/json", {})
                schema_ref = c.get("schema", {}).get("$ref", "")
                name = schema_ref.split("/")[-1] if schema_ref else "JSON"
                resp_schemas.append(f"{code}: `{name}`")
            
            resp_str = ", ".join(resp_schemas) if resp_schemas else "200: `JSON`"

            error_codes = ["401", "422", "500"]
            if roles != "owner, admin, viewer" and roles != "Any / Anonymous":
                error_codes.append("403")
            if "{" in path:
                error_codes.append("404")
            if path in ("/api/auth/register", "/api/imports"):
                error_codes.append("409")
            if "/health/db" in path:
                error_codes = ["503"]

            entry = {
                "method": m,
                "path": path,
                "summary": summary,
                "auth": auth_req,
                "roles": roles,
                "scoping": scoping,
                "request_schema": req_body,
                "response_schema": resp_str,
                "error_codes": ", ".join(sorted(set(error_codes))),
                "status": status_cat,
            }

            if status_cat == "STABLE":
                stable_routes.append(entry)
            elif status_cat == "ALIAS":
                alias_routes.append(entry)
            else:
                internal_routes.append(entry)

    # Render Markdown
    md = []
    md.append("# AgentFi Backend API Contract Inventory")
    md.append("")
    md.append("This document is the authoritative route and contract inventory for AgentFi backend version `1.0.0` (Day 6 Freeze).")
    md.append("Every endpoint registered on the application is cataloged with its contract stability status, authentication requirements, Role-Based Access Control (RBAC), tenant-scoping mechanism, schemas, and supported HTTP status codes.")
    md.append("")
    md.append("## Stability Classification")
    md.append("- **STABLE**: Production endpoints consumed by Member 1 (Next.js Frontend) and Members 3/4. Frozen against breaking changes under contract v1.0.0.")
    md.append("- **ALIAS**: Convenience endpoints (e.g. `/api/businesses/{business_id}/...`) preserved for backward compatibility. Excluded from frozen public OpenAPI schema.")
    md.append("- **INTERNAL**: Service discovery, documentation, and root health check routes.")
    md.append("")
    md.append("## Canonical Error Envelope")
    md.append("All endpoints share a standardized, uniform error response format:")
    md.append("```json")
    md.append("{")
    md.append('  "detail": "Descriptive error message or field error array",')
    md.append('  "error": {')
    md.append('    "code": "ERROR_CODE",')
    md.append('    "message": "Human readable summary",')
    md.append('    "request_id": "c138d6d5-...",')
    md.append('    "details": null')
    md.append("  },")
    md.append('  "request_id": "c138d6d5-..."')
    md.append("}")
    md.append("```")
    md.append("Every response also includes the `X-Request-ID` correlation header and security headers (`X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: strict-origin-when-cross-origin`).")
    md.append("")
    md.append("---")
    md.append("")
    md.append("## 1. STABLE Endpoints (Contract Frozen v1.0.0)")
    md.append("")
    md.append("| Method | Path | Summary | Auth | Allowed Roles | Tenant Scoping | Request Schema | Response Schema | Error Codes | Status |")
    md.append("|---|---|---|---|---|---|---|---|---|---|")
    for r in stable_routes:
        md.append(f"| `{r['method']}` | `{r['path']}` | {r['summary']} | {r['auth']} | {r['roles']} | {r['scoping']} | {r['request_schema']} | {r['response_schema']} | {r['error_codes']} | **{r['status']}** |")

    md.append("")
    md.append("---")
    md.append("")
    md.append("## 2. ALIAS Endpoints (Preserved, Hidden from Public Schema)")
    md.append("")
    md.append("| Method | Path | Summary | Auth | Allowed Roles | Tenant Scoping | Request Schema | Response Schema | Error Codes | Status |")
    md.append("|---|---|---|---|---|---|---|---|---|---|")
    for r in alias_routes:
        md.append(f"| `{r['method']}` | `{r['path']}` | {r['summary']} | {r['auth']} | {r['roles']} | {r['scoping']} | {r['request_schema']} | {r['response_schema']} | {r['error_codes']} | **{r['status']}** |")

    md.append("")
    md.append("---")
    md.append("")
    md.append("## 3. INTERNAL & System Endpoints")
    md.append("")
    md.append("| Method | Path | Summary | Auth | Allowed Roles | Tenant Scoping | Request Schema | Response Schema | Error Codes | Status |")
    md.append("|---|---|---|---|---|---|---|---|---|---|")
    for r in internal_routes:
        md.append(f"| `{r['method']}` | `{r['path']}` | {r['summary']} | {r['auth']} | {r['roles']} | {r['scoping']} | {r['request_schema']} | {r['response_schema']} | {r['error_codes']} | **{r['status']}** |")

    content = "\n".join(md) + "\n"
    with open("docs/api-contract-inventory.md", "w", encoding="utf-8") as f:
        f.write(content)
    print("Generated docs/api-contract-inventory.md successfully!")

if __name__ == "__main__":
    generate_inventory()
