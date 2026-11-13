"""
Export the application's OpenAPI 3.1 JSON schema to docs/openapi.json.
Useful for frontend code generation, API documentation, and contract validation.
"""

import json
import os
import sys

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from backend.main import create_app


def export_openapi():
    app = create_app()
    openapi_schema = app.openapi()
    
    docs_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "docs"))
    os.makedirs(docs_dir, exist_ok=True)
    
    output_path = os.path.join(docs_dir, "openapi.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(openapi_schema, f, indent=2)
    
    print(f"Successfully exported OpenAPI schema to {output_path}")


if __name__ == "__main__":
    export_openapi()
