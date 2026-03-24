"""Validate API endpoint — agents.yaml JSON Schema validation."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException

from aqm.core.project import get_agents_yaml_path


def create_validate_router(project_root: Path) -> APIRouter:
    router = APIRouter()
    agents_yaml_path = get_agents_yaml_path(project_root)

    @router.post("/api/validate")
    async def api_validate():
        import yaml as _yaml

        if not agents_yaml_path.exists():
            raise HTTPException(400, "No agents.yaml found")

        # Read YAML
        try:
            content = agents_yaml_path.read_text(encoding="utf-8")
            data = _yaml.safe_load(content)
        except Exception as e:
            return {
                "valid": False,
                "errors": [{"path": "(root)", "message": f"Failed to parse YAML: {e}", "fix": "Check YAML syntax."}],
                "summary": {},
                "yaml_content": content if "content" in dir() else "",
            }

        if not isinstance(data, dict):
            return {
                "valid": False,
                "errors": [{"path": "(root)", "message": "agents.yaml must be a YAML mapping", "fix": "Ensure the file starts with a mapping structure."}],
                "summary": {},
                "yaml_content": content,
            }

        # Load JSON Schema
        schema_path = Path(__file__).resolve().parent.parent.parent.parent / "schema" / "agents-schema.json"
        if not schema_path.exists():
            raise HTTPException(500, "JSON Schema file not found")

        with open(schema_path, encoding="utf-8") as f:
            schema = json.load(f)

        # Validate
        try:
            from jsonschema import Draft7Validator
        except ImportError:
            raise HTTPException(500, "jsonschema package not installed")

        validator = Draft7Validator(schema)
        raw_errors = sorted(validator.iter_errors(data), key=lambda e: list(e.absolute_path))

        errors = []
        for err in raw_errors:
            path = " → ".join(str(p) for p in err.absolute_path) or "(root)"
            fix = ""
            if "is a required property" in err.message:
                prop = err.message.split("'")[1]
                fix = f"Add the '{prop}' field."
            elif "is not one of" in err.message:
                fix = "Use one of the allowed values."
            elif "Additional properties are not allowed" in err.message:
                fix = "Remove unrecognized fields."
            errors.append({"path": path, "message": err.message, "fix": fix})

        # Summary
        agents_list = data.get("agents", [])
        features = []
        if any(a.get("gate") for a in agents_list):
            features.append("gates")
        if any(a.get("mcp") for a in agents_list):
            features.append("MCP servers")
        if any(a.get("handoffs") for a in agents_list):
            features.append("handoffs")
        if data.get("params"):
            features.append(f"{len(data['params'])} param(s)")
        if data.get("imports"):
            features.append(f"{len(data['imports'])} import(s)")

        return {
            "valid": len(errors) == 0,
            "errors": errors,
            "summary": {
                "agent_count": len(agents_list),
                "features": features,
            },
            "yaml_content": content,
        }

    return router
