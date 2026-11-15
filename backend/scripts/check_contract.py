"""
AgentFi API Contract Guard.

Compares the running FastAPI OpenAPI specification against docs/openapi.frozen.json.
Classifies all diffs into BREAKING (exit code 1) and NON-BREAKING (exit code 0).
Supports --update flag to deliberately update the frozen contract file.
"""

import argparse
import copy
import json
import os
import sys
from typing import Any, Dict, List, Set, Tuple

# Ensure repository root is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from backend.main import create_app

FROZEN_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../docs/openapi.frozen.json"))
OPENAPI_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../docs/openapi.json"))


def get_clean_schema() -> Dict[str, Any]:
    """
    Retrieves current OpenAPI schema and excludes internal non-API paths (e.g. root '/').
    """
    app = create_app()
    schema = copy.deepcopy(app.openapi())
    
    # Exclude internal non-/api routes
    paths = schema.get("paths", {})
    cleaned_paths = {p: item for p, item in paths.items() if p.startswith("/api/")}
    schema["paths"] = cleaned_paths
    return schema


def resolve_schema_ref(ref: str, full_schema: Dict[str, Any]) -> Dict[str, Any]:
    """
    Resolves a $ref pointer like #/components/schemas/TransactionRead.
    """
    if not ref or not ref.startswith("#/"):
        return {}
    parts = ref.lstrip("#/").split("/")
    curr = full_schema
    for p in parts:
        curr = curr.get(p, {})
    return curr


def compare_contracts(
    frozen: Dict[str, Any], live: Dict[str, Any]
) -> Tuple[List[str], List[str]]:
    """
    Compares frozen contract vs live contract.
    Returns (breaking_changes, non_breaking_changes).
    """
    breaking: List[str] = []
    non_breaking: List[str] = []

    frozen_paths = frozen.get("paths", {})
    live_paths = live.get("paths", {})

    # 1. Compare Paths
    for path, frozen_item in frozen_paths.items():
        if path not in live_paths:
            breaking.append(f"BREAKING: Removed path '{path}'")
            continue

        live_item = live_paths[path]
        for method, frozen_op in frozen_item.items():
            if method.lower() not in ("get", "post", "put", "patch", "delete"):
                continue

            if method not in live_item:
                breaking.append(f"BREAKING: Removed HTTP method '{method.upper()}' on path '{path}'")
                continue

            live_op = live_item[method]

            # Compare query/path parameters
            frozen_params = {p.get("name"): p for p in frozen_op.get("parameters", [])}
            live_params = {p.get("name"): p for p in live_op.get("parameters", [])}

            # Check removed parameters
            for pname, pdef in frozen_params.items():
                if pname not in live_params:
                    if pdef.get("in") == "path":
                        breaking.append(f"BREAKING: Removed path parameter '{pname}' on '{method.upper()} {path}'")
                    else:
                        non_breaking.append(f"NON-BREAKING: Removed query parameter '{pname}' on '{method.upper()} {path}'")

            # Check new or changed parameters
            for pname, pdef in live_params.items():
                if pname not in frozen_params:
                    if pdef.get("required", False):
                        breaking.append(f"BREAKING: Added new REQUIRED parameter '{pname}' to '{method.upper()} {path}'")
                    else:
                        non_breaking.append(f"NON-BREAKING: Added new optional parameter '{pname}' to '{method.upper()} {path}'")
                else:
                    # Parameter existed before; check if required changed from False to True
                    old_req = frozen_params[pname].get("required", False)
                    new_req = pdef.get("required", False)
                    if not old_req and new_req:
                        breaking.append(f"BREAKING: Parameter '{pname}' on '{method.upper()} {path}' became REQUIRED")

            # Compare Responses
            frozen_responses = frozen_op.get("responses", {})
            live_responses = live_op.get("responses", {})

            for code, frozen_resp in frozen_responses.items():
                # Success status code checks (2xx)
                if code.startswith("2") and code not in live_responses:
                    breaking.append(f"BREAKING: Success response status '{code}' removed on '{method.upper()} {path}'")
                elif code not in live_responses:
                    non_breaking.append(f"NON-BREAKING: Response status '{code}' removed on '{method.upper()} {path}'")

            for code, live_resp in live_responses.items():
                if code not in frozen_responses:
                    non_breaking.append(f"NON-BREAKING: Added new response status '{code}' to '{method.upper()} {path}'")

    # Check for newly added paths
    for path, live_item in live_paths.items():
        if path not in frozen_paths:
            non_breaking.append(f"NON-BREAKING: Added new path '{path}'")
            continue
        for method in live_item:
            if method.lower() not in ("get", "post", "put", "patch", "delete"):
                continue
            if method not in frozen_paths[path]:
                non_breaking.append(f"NON-BREAKING: Added new method '{method.upper()}' to existing path '{path}'")

    # 2. Compare Components / Schemas
    frozen_schemas = frozen.get("components", {}).get("schemas", {})
    live_schemas = live.get("components", {}).get("schemas", {})

    for sname, f_schema in frozen_schemas.items():
        if sname not in live_schemas:
            # Check if this schema is actually used by any live route
            breaking.append(f"BREAKING: Removed schema component '{sname}'")
            continue

        l_schema = live_schemas[sname]
        f_props = f_schema.get("properties", {})
        l_props = l_schema.get("properties", {})
        f_required = set(f_schema.get("required", []))
        l_required = set(l_schema.get("required", []))

        # Check removed or renamed properties
        for prop_name, prop_def in f_props.items():
            if prop_name not in l_props:
                breaking.append(f"BREAKING: Removed or renamed field '{sname}.{prop_name}'")
            else:
                # Compare property types
                f_type = prop_def.get("type") or prop_def.get("$ref")
                l_type = l_props[prop_name].get("type") or l_props[prop_name].get("$ref")
                if f_type != l_type and f_type is not None and l_type is not None:
                    breaking.append(
                        f"BREAKING: Changed type of field '{sname}.{prop_name}' from '{f_type}' to '{l_type}'"
                    )

        # Check added properties
        for prop_name in l_props:
            if prop_name not in f_props:
                if prop_name in l_required and prop_name not in f_required:
                    breaking.append(f"BREAKING: Added newly REQUIRED field '{sname}.{prop_name}'")
                else:
                    non_breaking.append(f"NON-BREAKING: Added optional field '{sname}.{prop_name}'")

        # Check enum changes
        if "enum" in f_schema and "enum" in l_schema:
            f_enums = set(f_schema["enum"])
            l_enums = set(l_schema["enum"])
            removed_enums = f_enums - l_enums
            added_enums = l_enums - f_enums
            if removed_enums:
                breaking.append(f"BREAKING: Removed enum values {removed_enums} from schema '{sname}'")
            if added_enums:
                non_breaking.append(f"NON-BREAKING: Added enum values {added_enums} to schema '{sname}'")

    return breaking, non_breaking


def main():
    parser = argparse.ArgumentParser(description="Check live FastAPI schema against frozen contract")
    parser.add_argument("--update", action="store_true", help="Update openapi.frozen.json and openapi.json")
    args = parser.parse_args()

    live_schema = get_clean_schema()

    if args.update or not os.path.exists(FROZEN_PATH):
        # Update frozen schema
        with open(FROZEN_PATH, "w", encoding="utf-8") as f:
            json.dump(live_schema, f, indent=2)
        # Update docs/openapi.json
        with open(OPENAPI_PATH, "w", encoding="utf-8") as f:
            json.dump(create_app().openapi(), f, indent=2)
        print(f"Contract frozen successfully: updated {FROZEN_PATH} and {OPENAPI_PATH}")
        sys.exit(0)

    with open(FROZEN_PATH, "r", encoding="utf-8") as f:
        frozen_schema = json.load(f)

    breaking, non_breaking = compare_contracts(frozen_schema, live_schema)

    print("=" * 60)
    print(f"API CONTRACT GUARD REPORT (Contract Version: {live_schema.get('info', {}).get('version', 'unknown')})")
    print("=" * 60)

    if non_breaking:
        print(f"\n[+] NON-BREAKING ADDITIONS ({len(non_breaking)}):")
        for nb in non_breaking:
            print(f"    - {nb}")

    if breaking:
        print(f"\n[!] BREAKING CHANGES DETECTED ({len(breaking)}):")
        for b in breaking:
            print(f"    * {b}")
        print("\nFAILURE: Contract guard failed. Breaking API changes are prohibited.")
        print("To deliberately commit a breaking change, bump CONTRACT_VERSION in backend/core/version.py,")
        print("document the approval in docs/CHANGELOG-API.md, and run:")
        print("    python backend/scripts/check_contract.py --update\n")
        sys.exit(1)

    print("\nSUCCESS: All API contracts match frozen specification. Zero breaking changes.")
    sys.exit(0)


if __name__ == "__main__":
    main()
