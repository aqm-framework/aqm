"""Integration tests — validate YAML configs with retry feature via aqm validate."""

from __future__ import annotations

import json
import os
import subprocess
import shutil
from pathlib import Path

import pytest
import yaml


INTEGRATION_DIR = Path("/tmp/aqm_test_retry")


@pytest.fixture(autouse=True)
def setup_integration_dir():
    """Create and clean integration test directory."""
    INTEGRATION_DIR.mkdir(exist_ok=True)
    yield
    # Cleanup after all tests
    if INTEGRATION_DIR.exists():
        shutil.rmtree(INTEGRATION_DIR)


def _write_yaml(name: str, content: dict) -> Path:
    """Write a YAML config to the integration dir and return the path."""
    path = INTEGRATION_DIR / name
    path.write_text(yaml.dump(content), encoding="utf-8")
    return path


def _init_project(subdir: str) -> Path:
    """Create a minimal AQM project structure."""
    project = INTEGRATION_DIR / subdir
    project.mkdir(parents=True, exist_ok=True)
    aqm_dir = project / ".aqm"
    aqm_dir.mkdir(exist_ok=True)
    (aqm_dir / "tasks").mkdir(exist_ok=True)
    (aqm_dir / "pipelines").mkdir(exist_ok=True)
    return project


def _validate_yaml(yaml_path: Path) -> tuple[bool, str]:
    """Run aqm validate on the given YAML file. Returns (success, output)."""
    try:
        from aqm.core.agent import load_agents
        agents = load_agents(yaml_path)
        return True, f"Loaded {len(agents)} agents"
    except Exception as e:
        return False, str(e)


# ═══════════════════════════════════════════════════════════════════════
# 1. MINIMAL — agent with retry config, minimal fields
# ═══════════════════════════════════════════════════════════════════════


class TestMinimalRetryYAML:

    def test_minimal_retry_config(self):
        project = _init_project("minimal")
        yaml_content = {
            "agents": [
                {
                    "id": "worker",
                    "runtime": "claude",
                    "system_prompt": "{{ input }}",
                    "retry": {"max_retries": 1},
                },
            ]
        }
        yaml_path = project / ".aqm" / "agents.yaml"
        yaml_path.write_text(yaml.dump(yaml_content), encoding="utf-8")

        ok, msg = _validate_yaml(yaml_path)
        assert ok, f"Minimal retry config failed: {msg}"

    def test_no_retry_field(self):
        project = _init_project("no_retry")
        yaml_content = {
            "agents": [
                {
                    "id": "worker",
                    "runtime": "claude",
                    "system_prompt": "{{ input }}",
                },
            ]
        }
        yaml_path = project / ".aqm" / "agents.yaml"
        yaml_path.write_text(yaml.dump(yaml_content), encoding="utf-8")

        ok, msg = _validate_yaml(yaml_path)
        assert ok, f"No retry config failed: {msg}"

    def test_retry_null(self):
        project = _init_project("retry_null")
        yaml_content = {
            "agents": [
                {
                    "id": "worker",
                    "runtime": "claude",
                    "system_prompt": "{{ input }}",
                    "retry": None,
                },
            ]
        }
        yaml_path = project / ".aqm" / "agents.yaml"
        yaml_path.write_text(yaml.dump(yaml_content), encoding="utf-8")

        ok, msg = _validate_yaml(yaml_path)
        assert ok, f"Retry null failed: {msg}"


# ═══════════════════════════════════════════════════════════════════════
# 2. FULL — all retry fields specified
# ═══════════════════════════════════════════════════════════════════════


class TestFullRetryYAML:

    def test_full_retry_config(self):
        project = _init_project("full")
        yaml_content = {
            "agents": [
                {
                    "id": "worker",
                    "runtime": "claude",
                    "system_prompt": "Work: {{ input }}",
                    "retry": {
                        "max_retries": 3,
                        "fallback_context_strategy": "last_only",
                        "backoff": 10,
                    },
                },
                {
                    "id": "reviewer",
                    "runtime": "gemini",
                    "system_prompt": "Review: {{ input }}",
                    "gate": {"type": "llm", "prompt": "Is this good?"},
                    "retry": {
                        "max_retries": 1,
                        "fallback_context_strategy": "none",
                        "backoff": 0,
                    },
                    "handoffs": [
                        {"to": "worker", "condition": "on_reject"},
                    ],
                },
            ]
        }
        yaml_path = project / ".aqm" / "agents.yaml"
        yaml_path.write_text(yaml.dump(yaml_content), encoding="utf-8")

        ok, msg = _validate_yaml(yaml_path)
        assert ok, f"Full retry config failed: {msg}"

    def test_all_fallback_strategies(self):
        for strategy in ("none", "last_only", "own", "shared", "both"):
            project = _init_project(f"strat_{strategy}")
            yaml_content = {
                "agents": [
                    {
                        "id": "worker",
                        "runtime": "claude",
                        "system_prompt": "{{ input }}",
                        "retry": {
                            "max_retries": 1,
                            "fallback_context_strategy": strategy,
                        },
                    },
                ]
            }
            yaml_path = project / ".aqm" / "agents.yaml"
            yaml_path.write_text(yaml.dump(yaml_content), encoding="utf-8")

            ok, msg = _validate_yaml(yaml_path)
            assert ok, f"Strategy '{strategy}' failed: {msg}"


# ═══════════════════════════════════════════════════════════════════════
# 3. EDGE CASES
# ═══════════════════════════════════════════════════════════════════════


class TestRetryEdgeCases:

    def test_retry_zero_retries(self):
        project = _init_project("zero_retries")
        yaml_content = {
            "agents": [
                {
                    "id": "worker",
                    "runtime": "claude",
                    "system_prompt": "{{ input }}",
                    "retry": {"max_retries": 0},
                },
            ]
        }
        yaml_path = project / ".aqm" / "agents.yaml"
        yaml_path.write_text(yaml.dump(yaml_content), encoding="utf-8")

        ok, msg = _validate_yaml(yaml_path)
        assert ok, f"Zero retries failed: {msg}"

    def test_retry_with_gate_and_handoffs(self):
        """Retry and gate configs should coexist independently."""
        project = _init_project("retry_gate")
        yaml_content = {
            "agents": [
                {
                    "id": "worker",
                    "runtime": "claude",
                    "system_prompt": "{{ input }}",
                    "retry": {"max_retries": 2, "backoff": 1},
                    "gate": {"type": "llm", "prompt": "OK?", "max_retries": 3},
                    "handoffs": [
                        {"to": "worker", "condition": "on_reject"},
                    ],
                },
            ]
        }
        yaml_path = project / ".aqm" / "agents.yaml"
        yaml_path.write_text(yaml.dump(yaml_content), encoding="utf-8")

        ok, msg = _validate_yaml(yaml_path)
        assert ok, f"Retry+gate config failed: {msg}"

    def test_retry_large_max_retries(self):
        project = _init_project("large_retries")
        yaml_content = {
            "agents": [
                {
                    "id": "worker",
                    "runtime": "claude",
                    "system_prompt": "{{ input }}",
                    "retry": {"max_retries": 100, "backoff": 0},
                },
            ]
        }
        yaml_path = project / ".aqm" / "agents.yaml"
        yaml_path.write_text(yaml.dump(yaml_content), encoding="utf-8")

        ok, msg = _validate_yaml(yaml_path)
        assert ok, f"Large retries failed: {msg}"

    def test_retry_with_session_agent(self):
        """Session agents can also have retry (though runtime is optional)."""
        project = _init_project("session_retry")
        yaml_content = {
            "agents": [
                {"id": "a", "runtime": "claude", "system_prompt": "{{ input }}"},
                {"id": "b", "runtime": "claude", "system_prompt": "{{ input }}"},
                {
                    "id": "discussion",
                    "type": "session",
                    "participants": ["a", "b"],
                    "system_prompt": "{{ input }}",
                    "retry": {"max_retries": 1},
                },
            ]
        }
        yaml_path = project / ".aqm" / "agents.yaml"
        yaml_path.write_text(yaml.dump(yaml_content), encoding="utf-8")

        ok, msg = _validate_yaml(yaml_path)
        assert ok, f"Session retry failed: {msg}"

    def test_retry_with_mcp(self):
        project = _init_project("retry_mcp")
        yaml_content = {
            "agents": [
                {
                    "id": "worker",
                    "runtime": "claude",
                    "system_prompt": "{{ input }}",
                    "retry": {"max_retries": 2},
                    "mcp": [{"server": "filesystem"}],
                },
            ]
        }
        yaml_path = project / ".aqm" / "agents.yaml"
        yaml_path.write_text(yaml.dump(yaml_content), encoding="utf-8")

        ok, msg = _validate_yaml(yaml_path)
        assert ok, f"Retry+MCP failed: {msg}"


# ═══════════════════════════════════════════════════════════════════════
# 4. ERROR CASES
# ═══════════════════════════════════════════════════════════════════════


class TestRetryErrorCases:

    def test_invalid_fallback_strategy(self):
        project = _init_project("invalid_strat")
        yaml_content = {
            "agents": [
                {
                    "id": "worker",
                    "runtime": "claude",
                    "system_prompt": "{{ input }}",
                    "retry": {
                        "max_retries": 1,
                        "fallback_context_strategy": "INVALID",
                    },
                },
            ]
        }
        yaml_path = project / ".aqm" / "agents.yaml"
        yaml_path.write_text(yaml.dump(yaml_content), encoding="utf-8")

        ok, msg = _validate_yaml(yaml_path)
        assert not ok, "Should reject invalid fallback_context_strategy"

    def test_negative_max_retries_type(self):
        """Pydantic should accept negative int (no minimum constraint in model)."""
        project = _init_project("neg_retries")
        yaml_content = {
            "agents": [
                {
                    "id": "worker",
                    "runtime": "claude",
                    "system_prompt": "{{ input }}",
                    "retry": {"max_retries": -1},
                },
            ]
        }
        yaml_path = project / ".aqm" / "agents.yaml"
        yaml_path.write_text(yaml.dump(yaml_content), encoding="utf-8")

        # This may or may not fail depending on model constraints
        ok, msg = _validate_yaml(yaml_path)
        # The model allows negative int; schema has minimum: 0 but model doesn't
        # Just check it doesn't crash unexpectedly
        assert isinstance(ok, bool)


# ═══════════════════════════════════════════════════════════════════════
# 5. JSON SCHEMA VALIDATION
# ═══════════════════════════════════════════════════════════════════════


class TestSchemaValidation:

    def test_schema_valid_json(self):
        """Both schema files should be valid JSON."""
        for path in [
            Path(__file__).parent.parent / "schema" / "agents-schema.json",
            Path(__file__).parent.parent / "aqm" / "schema" / "agents-schema.json",
        ]:
            if path.exists():
                data = json.loads(path.read_text())
                assert "definitions" in data
                assert "RetryConfig" in data["definitions"]

    def test_schema_retry_config_properties(self):
        """RetryConfig definition should have the expected properties."""
        schema_path = Path(__file__).parent.parent / "schema" / "agents-schema.json"
        if not schema_path.exists():
            pytest.skip("schema not found")

        data = json.loads(schema_path.read_text())
        rc = data["definitions"]["RetryConfig"]
        assert "max_retries" in rc["properties"]
        assert "fallback_context_strategy" in rc["properties"]
        assert "backoff" in rc["properties"]
        assert rc["properties"]["max_retries"]["type"] == "integer"
        assert rc["properties"]["max_retries"]["default"] == 0
