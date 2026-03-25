"""Tests for Gemini runtime (CLI and API modes)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest
import yaml

from aqm.core.agent import AgentDefinition, load_agents
from aqm.core.pipeline import Pipeline
from aqm.core.task import Task, TaskStatus
from aqm.queue.file import FileQueue
from aqm.runtime.gemini import (
    GeminiAPIRuntime,
    GeminiCLIRuntime,
    _DEFAULT_GEMINI_MODEL,
    _check_gemini_cli_available,
)


# ── CLI availability check ─────────────────────────────────────────────


class TestGeminiCLICheck:
    def test_gemini_cli_not_found(self):
        with patch("shutil.which", return_value=None):
            with pytest.raises(FileNotFoundError, match="gemini"):
                _check_gemini_cli_available()

    def test_gemini_cli_found(self):
        with patch("shutil.which", return_value="/usr/local/bin/gemini"):
            _check_gemini_cli_available()  # Should not raise


# ── GeminiCLIRuntime ───────────────────────────────────────────────────


class TestGeminiCLIRuntime:
    def _make_agent(self, **overrides) -> AgentDefinition:
        defaults = {
            "id": "test_agent",
            "runtime": "gemini_cli",
            "system_prompt": "You are a helpful assistant. {{ input }}",
        }
        defaults.update(overrides)
        return AgentDefinition(**defaults)

    def test_name(self):
        runtime = GeminiCLIRuntime()
        assert runtime.name == "gemini_cli"

    @patch("shutil.which", return_value="/usr/local/bin/gemini")
    @patch("subprocess.run")
    def test_run_basic(self, mock_run, mock_which):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="Hello from Gemini!",
            stderr="",
        )

        runtime = GeminiCLIRuntime()
        agent = self._make_agent()
        task = Task(description="test")

        result = runtime.run("test prompt", agent, task)
        assert result == "Hello from Gemini!"

        # Verify command includes prompt
        call_args = mock_run.call_args
        cmd = call_args[0][0]
        assert "gemini" in cmd
        assert "-p" in cmd
        assert "test prompt" in cmd

    @patch("shutil.which", return_value="/usr/local/bin/gemini")
    @patch("subprocess.run")
    def test_run_with_model(self, mock_run, mock_which):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="response",
            stderr="",
        )

        runtime = GeminiCLIRuntime()
        agent = self._make_agent(model="gemini-2.0-flash")
        task = Task(description="test")

        runtime.run("prompt", agent, task)

        cmd = mock_run.call_args[0][0]
        assert "--model" in cmd
        assert "gemini-2.0-flash" in cmd

    @patch("shutil.which", return_value="/usr/local/bin/gemini")
    @patch("subprocess.run")
    def test_run_with_system_prompt(self, mock_run, mock_which):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="response",
            stderr="",
        )

        runtime = GeminiCLIRuntime()
        agent = self._make_agent(system_prompt="Be helpful")
        task = Task(description="test")

        runtime.run("prompt", agent, task)

        cmd = mock_run.call_args[0][0]
        assert "--system-prompt" in cmd
        assert "Be helpful" in cmd

    @patch("shutil.which", return_value="/usr/local/bin/gemini")
    @patch("subprocess.run")
    def test_run_failure(self, mock_run, mock_which):
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="Error: invalid model",
        )

        runtime = GeminiCLIRuntime()
        agent = self._make_agent()
        task = Task(description="test")

        with pytest.raises(RuntimeError, match="Gemini CLI execution failed"):
            runtime.run("prompt", agent, task)

    @patch("shutil.which", return_value="/usr/local/bin/gemini")
    def test_run_streaming(self, mock_which):
        mock_proc = MagicMock()
        mock_proc.stdout.readline.side_effect = [
            "line 1\n",
            "line 2\n",
            "",  # EOF
        ]
        mock_proc.returncode = 0
        mock_proc.wait.return_value = None

        lines_received: list[str] = []

        with patch("subprocess.Popen", return_value=mock_proc):
            runtime = GeminiCLIRuntime()
            agent = self._make_agent()
            task = Task(description="test")

            result = runtime.run(
                "prompt", agent, task,
                on_output=lambda line: lines_received.append(line),
            )

        assert result == "line 1\nline 2"
        assert lines_received == ["line 1", "line 2"]


# ── GeminiAPIRuntime ──────────────────────────────────────────────────


class TestGeminiAPIRuntime:
    def _make_agent(self, **overrides) -> AgentDefinition:
        defaults = {
            "id": "test_agent",
            "runtime": "gemini_api",
            "system_prompt": "You are a helpful assistant. {{ input }}",
        }
        defaults.update(overrides)
        return AgentDefinition(**defaults)

    def test_name(self):
        runtime = GeminiAPIRuntime()
        assert runtime.name == "gemini_api"

    def test_missing_api_key(self):
        runtime = GeminiAPIRuntime()
        with patch.dict("os.environ", {}, clear=True):
            # Remove GEMINI_API_KEY if it exists
            import os
            env = os.environ.copy()
            env.pop("GEMINI_API_KEY", None)
            with patch.dict("os.environ", env, clear=True):
                with pytest.raises(ValueError, match="GEMINI_API_KEY"):
                    runtime._get_client()

    @patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"})
    def test_missing_sdk(self):
        runtime = GeminiAPIRuntime()
        with patch.dict("sys.modules", {"google": None, "google.genai": None}):
            with pytest.raises(ImportError, match="google-genai"):
                runtime._get_client()

    def test_run_basic(self):
        runtime = GeminiAPIRuntime()
        agent = self._make_agent(system_prompt="")
        task = Task(description="test")

        mock_response = MagicMock()
        mock_response.text = "Hello from Gemini API!"

        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_response
        runtime._client = mock_client

        result = runtime.run("test prompt", agent, task)
        assert result == "Hello from Gemini API!"

        mock_client.models.generate_content.assert_called_once()
        call_kwargs = mock_client.models.generate_content.call_args
        assert call_kwargs[1]["model"] == _DEFAULT_GEMINI_MODEL
        assert call_kwargs[1]["contents"] == "test prompt"

    def test_run_with_custom_model(self):
        runtime = GeminiAPIRuntime()
        agent = self._make_agent(model="gemini-1.5-pro", system_prompt="")
        task = Task(description="test")

        mock_response = MagicMock()
        mock_response.text = "response"

        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_response
        runtime._client = mock_client

        runtime.run("prompt", agent, task)

        call_kwargs = mock_client.models.generate_content.call_args
        assert call_kwargs[1]["model"] == "gemini-1.5-pro"

    def test_run_streaming(self):
        runtime = GeminiAPIRuntime()
        agent = self._make_agent(system_prompt="")
        task = Task(description="test")

        chunk1 = MagicMock()
        chunk1.text = "Hello "
        chunk2 = MagicMock()
        chunk2.text = "World!"

        mock_client = MagicMock()
        mock_client.models.generate_content_stream.return_value = [chunk1, chunk2]
        runtime._client = mock_client

        lines_received: list[str] = []
        result = runtime.run(
            "prompt", agent, task,
            on_output=lambda line: lines_received.append(line),
        )

        assert result == "Hello World!"
        assert "Hello " in lines_received
        assert "World!" in lines_received

    def test_run_empty_response(self):
        runtime = GeminiAPIRuntime()
        agent = self._make_agent(system_prompt="")
        task = Task(description="test")

        mock_response = MagicMock()
        mock_response.text = None

        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_response
        runtime._client = mock_client

        result = runtime.run("prompt", agent, task)
        assert result == ""


# ── YAML loading with Gemini runtimes ─────────────────────────────────


class TestGeminiYAMLLoading:
    def test_load_gemini_cli_agent(self, tmp_project):
        yaml_content = {
            "agents": [
                {
                    "id": "planner",
                    "runtime": "gemini_cli",
                    "model": "gemini-2.0-flash",
                    "system_prompt": "Plan: {{ input }}",
                }
            ]
        }
        yaml_path = tmp_project / ".aqm" / "agents.yaml"
        yaml_path.write_text(yaml.dump(yaml_content), encoding="utf-8")

        agents = load_agents(yaml_path)
        assert "planner" in agents
        assert agents["planner"].runtime == "gemini_cli"
        assert agents["planner"].model == "gemini-2.0-flash"

    def test_load_gemini_api_agent(self, tmp_project):
        yaml_content = {
            "agents": [
                {
                    "id": "reviewer",
                    "runtime": "gemini_api",
                    "model": "gemini-1.5-pro",
                    "system_prompt": "Review: {{ input }}",
                }
            ]
        }
        yaml_path = tmp_project / ".aqm" / "agents.yaml"
        yaml_path.write_text(yaml.dump(yaml_content), encoding="utf-8")

        agents = load_agents(yaml_path)
        assert "reviewer" in agents
        assert agents["reviewer"].runtime == "gemini_api"

    def test_mixed_runtime_pipeline(self, tmp_project):
        """Test a pipeline mixing Claude and Gemini runtimes."""
        yaml_content = {
            "agents": [
                {
                    "id": "planner",
                    "runtime": "gemini_api",
                    "system_prompt": "Plan: {{ input }}",
                    "handoffs": [{"to": "executor", "condition": "always"}],
                },
                {
                    "id": "executor",
                    "runtime": "claude_code",
                    "system_prompt": "Execute: {{ input }}",
                    "handoffs": [{"to": "reviewer", "condition": "always"}],
                },
                {
                    "id": "reviewer",
                    "runtime": "gemini_cli",
                    "system_prompt": "Review: {{ input }}",
                },
            ]
        }
        yaml_path = tmp_project / ".aqm" / "agents.yaml"
        yaml_path.write_text(yaml.dump(yaml_content), encoding="utf-8")

        agents = load_agents(yaml_path)
        assert agents["planner"].runtime == "gemini_api"
        assert agents["executor"].runtime == "claude_code"
        assert agents["reviewer"].runtime == "gemini_cli"

    def test_invalid_runtime_rejected(self, tmp_project):
        """Invalid runtime value should fail validation."""
        yaml_content = {
            "agents": [
                {
                    "id": "bad",
                    "runtime": "openai",
                    "system_prompt": "{{ input }}",
                }
            ]
        }
        yaml_path = tmp_project / ".aqm" / "agents.yaml"
        yaml_path.write_text(yaml.dump(yaml_content), encoding="utf-8")

        with pytest.raises(Exception):  # Pydantic validation error
            load_agents(yaml_path)


# ── Pipeline runtime resolution ───────────────────────────────────────


class TestPipelineGeminiRuntime:
    def test_get_gemini_cli_runtime(self, tmp_project):
        agents = {
            "test": AgentDefinition(
                id="test",
                runtime="gemini_cli",
                system_prompt="{{ input }}",
            )
        }
        queue = FileQueue(tmp_project / ".aqm" / "file-queue")
        pipeline = Pipeline(agents, queue, tmp_project)

        runtime = pipeline._get_runtime(agents["test"])
        assert isinstance(runtime, GeminiCLIRuntime)
        assert runtime.name == "gemini_cli"

    def test_get_gemini_api_runtime(self, tmp_project):
        agents = {
            "test": AgentDefinition(
                id="test",
                runtime="gemini_api",
                system_prompt="{{ input }}",
            )
        }
        queue = FileQueue(tmp_project / ".aqm" / "file-queue")
        pipeline = Pipeline(agents, queue, tmp_project)

        runtime = pipeline._get_runtime(agents["test"])
        assert isinstance(runtime, GeminiAPIRuntime)
        assert runtime.name == "gemini_api"

    def test_runtime_caching(self, tmp_project):
        """Same runtime type should be reused across calls."""
        agents = {
            "a": AgentDefinition(id="a", runtime="gemini_cli", system_prompt="{{ input }}"),
            "b": AgentDefinition(id="b", runtime="gemini_cli", system_prompt="{{ input }}"),
        }
        queue = FileQueue(tmp_project / ".aqm" / "file-queue")
        pipeline = Pipeline(agents, queue, tmp_project)

        rt1 = pipeline._get_runtime(agents["a"])
        rt2 = pipeline._get_runtime(agents["b"])
        assert rt1 is rt2

    def test_unknown_runtime_raises(self, tmp_project):
        """Unknown runtime should raise ValueError."""
        agent = AgentDefinition.__new__(AgentDefinition)
        object.__setattr__(agent, "runtime", "nonexistent")
        object.__setattr__(agent, "id", "bad")

        queue = FileQueue(tmp_project / ".aqm" / "file-queue")
        pipeline = Pipeline({"bad": agent}, queue, tmp_project)

        with pytest.raises(ValueError, match="Unknown runtime"):
            pipeline._get_runtime(agent)

    def test_full_pipeline_with_gemini_api(self, tmp_project):
        """End-to-end pipeline run with mocked GeminiAPIRuntime."""
        agents = {
            "planner": AgentDefinition(
                id="planner",
                runtime="gemini_api",
                system_prompt="Plan: {{ input }}",
            ),
        }
        queue = FileQueue(tmp_project / ".aqm" / "file-queue")
        pipeline = Pipeline(agents, queue, tmp_project)

        # Mock the runtime
        mock_runtime = MagicMock()
        mock_runtime.name = "gemini_api"
        mock_runtime.run.return_value = "Plan completed successfully."
        pipeline._runtimes["gemini_api"] = mock_runtime

        task = Task(description="Build a login page")
        queue.push(task, "planner")

        result = pipeline.run_task(task, "planner")
        assert result.status == TaskStatus.completed
        assert len(result.stages) == 1
        assert result.stages[0].output_text == "Plan completed successfully."
