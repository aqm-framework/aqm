"""Tests for YAML reusability features: params, extends, imports."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from aqm.core.agent import (
    AgentDefinition,
    ParamDefinition,
    load_agents,
    resolve_params,
    substitute_params,
)


# ── Parameterization ──────────────────────────────────────────────────


class TestParamDefinition:
    def test_string_param_with_default(self):
        p = ParamDefinition(type="string", default="hello")
        assert p.default == "hello"
        assert p.required is False

    def test_number_param(self):
        p = ParamDefinition(type="number", default=3)
        assert p.default == 3

    def test_boolean_param(self):
        p = ParamDefinition(type="boolean", default=True)
        assert p.default is True

    def test_required_param(self):
        p = ParamDefinition(type="string", required=True, description="Must set")
        assert p.required is True
        assert p.default is None


class TestResolveParams:
    def test_defaults_used(self):
        defs = {
            "model": ParamDefinition(type="string", default="sonnet"),
            "retries": ParamDefinition(type="number", default=3),
        }
        result = resolve_params({}, defs)
        assert result == {"model": "sonnet", "retries": 3}

    def test_cli_overrides_default(self):
        defs = {"model": ParamDefinition(type="string", default="sonnet")}
        result = resolve_params({}, defs, cli_overrides={"model": "opus"})
        assert result["model"] == "opus"

    def test_file_overrides_default(self, tmp_path):
        overrides_file = tmp_path / "params.yaml"
        overrides_file.write_text("model: haiku\n", encoding="utf-8")

        defs = {"model": ParamDefinition(type="string", default="sonnet")}
        result = resolve_params({}, defs, overrides_file=overrides_file)
        assert result["model"] == "haiku"

    def test_cli_overrides_file(self, tmp_path):
        overrides_file = tmp_path / "params.yaml"
        overrides_file.write_text("model: haiku\n", encoding="utf-8")

        defs = {"model": ParamDefinition(type="string", default="sonnet")}
        result = resolve_params(
            {}, defs,
            cli_overrides={"model": "opus"},
            overrides_file=overrides_file,
        )
        assert result["model"] == "opus"

    def test_required_missing_raises(self):
        defs = {
            "project_path": ParamDefinition(
                type="string", required=True, description="Path to project"
            )
        }
        with pytest.raises(ValueError, match="Required parameter 'project_path'"):
            resolve_params({}, defs)

    def test_number_coercion_from_cli(self):
        defs = {"retries": ParamDefinition(type="number", default=3)}
        result = resolve_params({}, defs, cli_overrides={"retries": "5"})
        assert result["retries"] == 5

    def test_boolean_coercion_from_cli(self):
        defs = {"verbose": ParamDefinition(type="boolean", default=False)}
        result = resolve_params({}, defs, cli_overrides={"verbose": "true"})
        assert result["verbose"] is True


class TestSubstituteParams:
    def test_simple_substitution(self):
        raw = {"agents": [{"id": "a", "model": "${{ params.model }}"}]}
        result = substitute_params(raw, {"model": "opus"})
        assert result["agents"][0]["model"] == "opus"

    def test_nested_substitution(self):
        raw = {
            "agents": [
                {
                    "id": "a",
                    "mcp": [
                        {"server": "fs", "args": ["${{ params.path }}"]}
                    ],
                }
            ]
        }
        result = substitute_params(raw, {"path": "/my/project"})
        assert result["agents"][0]["mcp"][0]["args"][0] == "/my/project"

    def test_multiple_params_in_one_string(self):
        raw = {"text": "${{ params.a }}-${{ params.b }}"}
        result = substitute_params(raw, {"a": "hello", "b": "world"})
        assert result["text"] == "hello-world"

    def test_unknown_param_raises(self):
        raw = {"x": "${{ params.unknown }}"}
        with pytest.raises(ValueError, match="Unknown parameter reference"):
            substitute_params(raw, {"model": "opus"})

    def test_non_string_values_unchanged(self):
        raw = {"count": 42, "flag": True}
        result = substitute_params(raw, {})
        assert result == {"count": 42, "flag": True}


class TestParamsIntegration:
    def test_full_param_pipeline(self, tmp_path):
        """End-to-end: params declared, substituted, agents loaded."""
        root = tmp_path
        aq_dir = root / ".aqm"
        aq_dir.mkdir(parents=True)
        (aq_dir / "tasks").mkdir()

        yaml_content = {
            "params": {
                "model": {
                    "type": "string",
                    "default": "claude-sonnet-4-20250514",
                    "description": "Model for all agents",
                },
                "project_path": {
                    "type": "string",
                    "required": True,
                    "description": "Path to the project root",
                },
            },
            "agents": [
                {
                    "id": "dev",
                    "name": "Developer",
                    "runtime": "api",
                    "model": "${{ params.model }}",
                    "system_prompt": "Work on ${{ params.project_path }}",
                    "mcp": [
                        {"server": "filesystem", "args": ["${{ params.project_path }}"]}
                    ],
                }
            ],
        }
        yaml_path = aq_dir / "agents.yaml"
        yaml_path.write_text(yaml.dump(yaml_content), encoding="utf-8")

        agents = load_agents(
            yaml_path,
            cli_params={"project_path": "/my/project"},
        )
        dev = agents["dev"]
        assert dev.model == "claude-sonnet-4-20250514"
        assert "/my/project" in dev.system_prompt
        assert dev.mcp[0].args == ["/my/project"]

    def test_params_with_overrides_file(self, tmp_path):
        root = tmp_path
        aq_dir = root / ".aqm"
        aq_dir.mkdir(parents=True)
        (aq_dir / "tasks").mkdir()

        yaml_content = {
            "params": {
                "model": {"type": "string", "default": "sonnet"},
            },
            "agents": [
                {
                    "id": "a",
                    "name": "A",
                    "runtime": "api",
                    "model": "${{ params.model }}",
                }
            ],
        }
        yaml_path = aq_dir / "agents.yaml"
        yaml_path.write_text(yaml.dump(yaml_content), encoding="utf-8")

        # Create params.yaml in the same directory as agents.yaml
        params_file = aq_dir / "params.yaml"
        params_file.write_text("model: opus\n", encoding="utf-8")

        agents = load_agents(yaml_path)
        assert agents["a"].model == "opus"


# ── Extends / Abstract ─────────────────────────────────────────────────


class TestExtends:
    def test_basic_extends(self, tmp_path):
        aq_dir = tmp_path / ".aqm"
        aq_dir.mkdir(parents=True)
        (aq_dir / "tasks").mkdir()

        yaml_content = {
            "agents": [
                {
                    "id": "base_reviewer",
                    "abstract": True,
                    "runtime": "api",
                    "system_prompt": "Review: {{ input }}",
                    "gate": {"type": "llm"},
                },
                {
                    "id": "code_reviewer",
                    "extends": "base_reviewer",
                    "system_prompt": "Review CODE: {{ input }}",
                },
            ]
        }
        yaml_path = aq_dir / "agents.yaml"
        yaml_path.write_text(yaml.dump(yaml_content), encoding="utf-8")

        agents = load_agents(yaml_path)

        # Abstract agent should NOT be in the output
        assert "base_reviewer" not in agents

        # Child should exist and inherit runtime + gate from parent
        assert "code_reviewer" in agents
        cr = agents["code_reviewer"]
        assert cr.runtime == "api"
        assert cr.gate is not None
        assert cr.gate.type == "llm"
        # Child's own system_prompt overrides parent's
        assert "Review CODE" in cr.system_prompt

    def test_extends_nonexistent_raises(self, tmp_path):
        aq_dir = tmp_path / ".aqm"
        aq_dir.mkdir(parents=True)
        (aq_dir / "tasks").mkdir()

        yaml_content = {
            "agents": [
                {
                    "id": "child",
                    "extends": "nonexistent_parent",
                    "runtime": "api",
                }
            ]
        }
        yaml_path = aq_dir / "agents.yaml"
        yaml_path.write_text(yaml.dump(yaml_content), encoding="utf-8")

        with pytest.raises(ValueError, match="extends 'nonexistent_parent'"):
            load_agents(yaml_path)

    def test_abstract_without_extends(self, tmp_path):
        """Abstract agents without children are just excluded."""
        aq_dir = tmp_path / ".aqm"
        aq_dir.mkdir(parents=True)
        (aq_dir / "tasks").mkdir()

        yaml_content = {
            "agents": [
                {
                    "id": "abstract_only",
                    "abstract": True,
                    "runtime": "api",
                },
                {
                    "id": "concrete",
                    "name": "Concrete",
                    "runtime": "api",
                },
            ]
        }
        yaml_path = aq_dir / "agents.yaml"
        yaml_path.write_text(yaml.dump(yaml_content), encoding="utf-8")

        agents = load_agents(yaml_path)
        assert "abstract_only" not in agents
        assert "concrete" in agents

    def test_extends_non_abstract_parent(self, tmp_path):
        """Extends works even if parent is not abstract."""
        aq_dir = tmp_path / ".aqm"
        aq_dir.mkdir(parents=True)
        (aq_dir / "tasks").mkdir()

        yaml_content = {
            "agents": [
                {
                    "id": "parent",
                    "name": "Parent",
                    "runtime": "api",
                    "model": "sonnet",
                },
                {
                    "id": "child",
                    "extends": "parent",
                    "model": "opus",
                },
            ]
        }
        yaml_path = aq_dir / "agents.yaml"
        yaml_path.write_text(yaml.dump(yaml_content), encoding="utf-8")

        agents = load_agents(yaml_path)
        assert "parent" in agents  # Not abstract, so still present
        assert "child" in agents
        assert agents["child"].model == "opus"
        assert agents["child"].runtime == "api"  # Inherited


# ── Imports ────────────────────────────────────────────────────────────


class TestImports:
    def test_basic_import(self, tmp_path):
        aq_dir = tmp_path / ".aqm"
        aq_dir.mkdir(parents=True)
        (aq_dir / "tasks").mkdir()

        # Create the shared file
        shared_dir = aq_dir / "shared"
        shared_dir.mkdir()

        shared_content = {
            "agents": [
                {
                    "id": "reviewer",
                    "name": "Shared Reviewer",
                    "runtime": "api",
                    "system_prompt": "Review: {{ input }}",
                }
            ]
        }
        shared_path = shared_dir / "reviewer.yaml"
        shared_path.write_text(yaml.dump(shared_content), encoding="utf-8")

        # Main agents.yaml that imports the shared file
        main_content = {
            "imports": [
                {"from": "./shared/reviewer.yaml", "agents": ["reviewer"]}
            ],
            "agents": [
                {
                    "id": "developer",
                    "name": "Developer",
                    "runtime": "api",
                    "handoffs": [
                        {"to": "reviewer", "condition": "always"}
                    ],
                }
            ],
        }
        yaml_path = aq_dir / "agents.yaml"
        yaml_path.write_text(yaml.dump(main_content), encoding="utf-8")

        agents = load_agents(yaml_path)
        assert "reviewer" in agents
        assert "developer" in agents
        assert agents["reviewer"].system_prompt == "Review: {{ input }}"

    def test_import_selective(self, tmp_path):
        """Only import specified agent IDs."""
        aq_dir = tmp_path / ".aqm"
        aq_dir.mkdir(parents=True)
        (aq_dir / "tasks").mkdir()

        shared_content = {
            "agents": [
                {"id": "wanted", "name": "Wanted", "runtime": "api"},
                {"id": "not_wanted", "name": "Not Wanted", "runtime": "api"},
            ]
        }
        shared_path = aq_dir / "shared.yaml"
        shared_path.write_text(yaml.dump(shared_content), encoding="utf-8")

        main_content = {
            "imports": [{"from": "./shared.yaml", "agents": ["wanted"]}],
            "agents": [
                {"id": "main_agent", "name": "Main", "runtime": "api"},
            ],
        }
        yaml_path = aq_dir / "agents.yaml"
        yaml_path.write_text(yaml.dump(main_content), encoding="utf-8")

        agents = load_agents(yaml_path)
        assert "wanted" in agents
        assert "not_wanted" not in agents

    def test_import_file_not_found(self, tmp_path):
        aq_dir = tmp_path / ".aqm"
        aq_dir.mkdir(parents=True)
        (aq_dir / "tasks").mkdir()

        main_content = {
            "imports": [{"from": "./nonexistent.yaml", "agents": []}],
            "agents": [
                {"id": "a", "name": "A", "runtime": "api"},
            ],
        }
        yaml_path = aq_dir / "agents.yaml"
        yaml_path.write_text(yaml.dump(main_content), encoding="utf-8")

        with pytest.raises(FileNotFoundError, match="nonexistent.yaml"):
            load_agents(yaml_path)

    def test_import_with_extends(self, tmp_path):
        """Import a base agent and extend it locally."""
        aq_dir = tmp_path / ".aqm"
        aq_dir.mkdir(parents=True)
        (aq_dir / "tasks").mkdir()

        shared_content = {
            "agents": [
                {
                    "id": "base_agent",
                    "abstract": True,
                    "runtime": "api",
                    "gate": {"type": "llm"},
                    "system_prompt": "Base prompt: {{ input }}",
                }
            ]
        }
        shared_path = aq_dir / "base.yaml"
        shared_path.write_text(yaml.dump(shared_content), encoding="utf-8")

        main_content = {
            "imports": [{"from": "./base.yaml", "agents": ["base_agent"]}],
            "agents": [
                {
                    "id": "my_agent",
                    "extends": "base_agent",
                    "system_prompt": "Custom prompt: {{ input }}",
                }
            ],
        }
        yaml_path = aq_dir / "agents.yaml"
        yaml_path.write_text(yaml.dump(main_content), encoding="utf-8")

        agents = load_agents(yaml_path)
        assert "base_agent" not in agents  # abstract
        assert "my_agent" in agents
        assert agents["my_agent"].gate is not None
        assert "Custom prompt" in agents["my_agent"].system_prompt


# ── Combined features ──────────────────────────────────────────────────


class TestCombinedFeatures:
    def test_params_with_extends(self, tmp_path):
        """Params + extends together."""
        aq_dir = tmp_path / ".aqm"
        aq_dir.mkdir(parents=True)
        (aq_dir / "tasks").mkdir()

        yaml_content = {
            "params": {
                "model": {"type": "string", "default": "sonnet"},
            },
            "agents": [
                {
                    "id": "base",
                    "abstract": True,
                    "runtime": "api",
                    "model": "${{ params.model }}",
                },
                {
                    "id": "worker",
                    "extends": "base",
                    "system_prompt": "Work: {{ input }}",
                },
            ],
        }
        yaml_path = aq_dir / "agents.yaml"
        yaml_path.write_text(yaml.dump(yaml_content), encoding="utf-8")

        agents = load_agents(yaml_path)
        assert agents["worker"].model == "sonnet"

    def test_params_with_imports(self, tmp_path):
        """Params substituted in imported file paths work after import."""
        aq_dir = tmp_path / ".aqm"
        aq_dir.mkdir(parents=True)
        (aq_dir / "tasks").mkdir()

        shared_content = {
            "agents": [
                {
                    "id": "imported",
                    "name": "Imported",
                    "runtime": "api",
                }
            ]
        }
        shared_path = aq_dir / "shared.yaml"
        shared_path.write_text(yaml.dump(shared_content), encoding="utf-8")

        main_content = {
            "imports": [{"from": "./shared.yaml"}],
            "agents": [
                {"id": "local", "name": "Local", "runtime": "api"},
            ],
        }
        yaml_path = aq_dir / "agents.yaml"
        yaml_path.write_text(yaml.dump(main_content), encoding="utf-8")

        agents = load_agents(yaml_path)
        assert "imported" in agents
        assert "local" in agents

    def test_auto_name_from_id(self, tmp_path):
        """Agent name auto-filled from id when not provided."""
        aq_dir = tmp_path / ".aqm"
        aq_dir.mkdir(parents=True)
        (aq_dir / "tasks").mkdir()

        yaml_content = {
            "agents": [
                {"id": "code_reviewer", "runtime": "api"},
            ]
        }
        yaml_path = aq_dir / "agents.yaml"
        yaml_path.write_text(yaml.dump(yaml_content), encoding="utf-8")

        agents = load_agents(yaml_path)
        assert agents["code_reviewer"].name == "Code Reviewer"
