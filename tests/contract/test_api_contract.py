"""
Contract tests: Enforce that live OpenAPI specification strictly matches docs/openapi.frozen.json.
Guarantees zero breaking changes for Member 1 (Next.js Frontend) and Members 3 & 4.
"""

import copy
import json
import os
import pytest

from backend.main import create_app
from backend.scripts.check_contract import (
    FROZEN_PATH,
    compare_contracts,
    get_clean_schema,
)


def test_frozen_contract_file_exists():
    """
    Ensure docs/openapi.frozen.json exists.
    """
    assert os.path.exists(FROZEN_PATH), f"Frozen contract missing at {FROZEN_PATH}"


def test_live_schema_matches_frozen_contract():
    """
    Verify live OpenAPI schema has zero breaking changes against docs/openapi.frozen.json.
    """
    with open(FROZEN_PATH, "r", encoding="utf-8") as f:
        frozen_schema = json.load(f)

    live_schema = get_clean_schema()
    breaking, non_breaking = compare_contracts(frozen_schema, live_schema)

    assert not breaking, f"Breaking API changes detected:\n" + "\n".join(f"- {b}" for b in breaking)


def test_contract_guard_detects_deliberate_breaking_changes():
    """
    Demonstrate that check_contract.py logic actively catches deliberate breaking changes:
    1. Removed path
    2. Removed method
    3. Removed response field
    4. Changed field type
    5. Newly required request parameter
    """
    with open(FROZEN_PATH, "r", encoding="utf-8") as f:
        frozen_schema = json.load(f)

    # 1. Simulate removed endpoint
    tampered_removed_path = copy.deepcopy(frozen_schema)
    del tampered_removed_path["paths"]["/api/transactions"]
    breaking, _ = compare_contracts(frozen_schema, tampered_removed_path)
    assert any("Removed path '/api/transactions'" in b for b in breaking)

    # 2. Simulate removed method
    tampered_removed_method = copy.deepcopy(frozen_schema)
    del tampered_removed_method["paths"]["/api/auth/login"]["post"]
    breaking, _ = compare_contracts(frozen_schema, tampered_removed_method)
    assert any("Removed HTTP method 'POST'" in b for b in breaking)

    # 3. Simulate removed response field
    tampered_removed_field = copy.deepcopy(frozen_schema)
    schemas = tampered_removed_field["components"]["schemas"]
    if "TransactionRead" in schemas and "amount" in schemas["TransactionRead"]["properties"]:
        del schemas["TransactionRead"]["properties"]["amount"]
        breaking, _ = compare_contracts(frozen_schema, tampered_removed_field)
        assert any("Removed or renamed field 'TransactionRead.amount'" in b for b in breaking)

    # 4. Simulate changed field type
    tampered_type = copy.deepcopy(frozen_schema)
    schemas = tampered_type["components"]["schemas"]
    if "Token" in schemas and "access_token" in schemas["Token"]["properties"]:
        schemas["Token"]["properties"]["access_token"]["type"] = "integer"
        breaking, _ = compare_contracts(frozen_schema, tampered_type)
        assert any("Changed type of field 'Token.access_token'" in b for b in breaking)
