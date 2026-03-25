"""Tests for entry_point auto-routing feature."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from aqm.core.agent import (
    AgentDefinition,
    get_entry_point,
    load_agents,
    resolve_start_agent,
)


# ── get_entry_point ───────────────────────────────────────────────────


class TestGetEntryPoint:
    def test_default_is_first(self, tmp_project):
        yaml_content = {
            "agents": [
                {"id": "a", "runtime": "claude", "system_prompt": "{{ input }}"},
            ]
        }
        yaml_path = tmp_project / ".aqm" / "agents.yaml"
        yaml_path.write_text(yaml.dump(yaml_content), encoding="utf-8")
        assert get_entry_point(yaml_path) == "first"

    def test_explicit_first(self, tmp_project):
        yaml_content = {
            "entry_point": "first",
            "agents": [
                {"id": "a", "runtime": "claude", "system_prompt": "{{ input }}"},
            ]
        }
        yaml_path = tmp_project / ".aqm" / "agents.yaml"
        yaml_path.write_text(yaml.dump(yaml_content), encoding="utf-8")
        assert get_entry_point(yaml_path) == "first"

    def test_auto(self, tmp_project):
        yaml_content = {
            "entry_point": "auto",
            "agents": [
                {"id": "a", "runtime": "claude", "system_prompt": "{{ input }}"},
            ]
        }
        yaml_path = tmp_project / ".aqm" / "agents.yaml"
        yaml_path.write_text(yaml.dump(yaml_content), encoding="utf-8")
        assert get_entry_point(yaml_path) == "auto"


# ── resolve_start_agent ──────────────────────────────────────────────


class TestResolveStartAgent:
    def _make_agents(self):
        return {
            "code_reviewer": AgentDefinition(
                id="code_reviewer", runtime="claude",
                system_prompt="Review code for bugs and style issues",
            ),
            "bug_fixer": AgentDefinition(
                id="bug_fixer", runtime="claude",
                system_prompt="Fix reported bugs in the codebase",
            ),
            "feature_planner": AgentDefinition(
                id="feature_planner", runtime="claude",
                system_prompt="Plan new feature implementations",
            ),
        }

    @patch("subprocess.run")
    @patch("shutil.which", return_value="/usr/local/bin/claude")
    def test_llm_selects_matching_agent(self, mock_which, mock_run):
        mock_run.return_value = MagicMock(
            stdout="bug_fixer", returncode=0,
        )
        agents = self._make_agents()
        result = resolve_start_agent("Fix the login crash", agents)
        assert result == "bug_fixer"

    @patch("subprocess.run")
    @patch("shutil.which", return_value="/usr/local/bin/claude")
    def test_llm_returns_invalid_falls_back(self, mock_which, mock_run):
        mock_run.return_value = MagicMock(
            stdout="nonexistent_agent", returncode=0,
        )
        agents = self._make_agents()
        result = resolve_start_agent("Some task", agents)
        assert result == "code_reviewer"  # first agent

    @patch("subprocess.run")
    @patch("shutil.which", return_value="/usr/local/bin/claude")
    def test_llm_returns_empty_falls_back(self, mock_which, mock_run):
        mock_run.return_value = MagicMock(stdout="", returncode=0)
        agents = self._make_agents()
        result = resolve_start_agent("Some task", agents)
        assert result == "code_reviewer"

    @patch("subprocess.run", side_effect=Exception("timeout"))
    @patch("shutil.which", return_value="/usr/local/bin/claude")
    def test_llm_error_falls_back(self, mock_which, mock_run):
        agents = self._make_agents()
        result = resolve_start_agent("Some task", agents)
        assert result == "code_reviewer"

    @patch("shutil.which", return_value=None)
    def test_no_claude_cli_falls_back(self, mock_which):
        agents = self._make_agents()
        result = resolve_start_agent("Some task", agents)
        assert result == "code_reviewer"

    @patch("subprocess.run")
    @patch("shutil.which", return_value="/usr/local/bin/claude")
    def test_llm_selects_with_extra_text(self, mock_which, mock_run):
        """LLM may include extra text; we take the first word."""
        mock_run.return_value = MagicMock(
            stdout="feature_planner is the best choice", returncode=0,
        )
        agents = self._make_agents()
        result = resolve_start_agent("Add dark mode", agents)
        assert result == "feature_planner"


# ── YAML loading with entry_point ─────────────────────────────────────


class TestEntryPointYAML:
    def test_load_agents_with_entry_point(self, tmp_project):
        """entry_point in YAML shouldn't break load_agents."""
        yaml_content = {
            "entry_point": "auto",
            "agents": [
                {"id": "a", "runtime": "claude", "system_prompt": "{{ input }}"},
                {"id": "b", "runtime": "claude", "system_prompt": "{{ input }}"},
            ]
        }
        yaml_path = tmp_project / ".aqm" / "agents.yaml"
        yaml_path.write_text(yaml.dump(yaml_content), encoding="utf-8")

        agents = load_agents(yaml_path)
        assert "a" in agents
        assert "b" in agents

    def test_backward_compatible_no_entry_point(self, tmp_project):
        yaml_content = {
            "agents": [
                {"id": "x", "runtime": "claude", "system_prompt": "{{ input }}"},
            ]
        }
        yaml_path = tmp_project / ".aqm" / "agents.yaml"
        yaml_path.write_text(yaml.dump(yaml_content), encoding="utf-8")

        agents = load_agents(yaml_path)
        assert "x" in agents
        assert get_entry_point(yaml_path) == "first"
