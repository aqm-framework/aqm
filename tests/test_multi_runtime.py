"""Tests for Gemini CLI runtime, Codex CLI runtime, and multi-runtime pipelines."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from aqm.core.agent import AgentDefinition, load_agents
from aqm.core.pipeline import Pipeline
from aqm.core.task import Task, TaskStatus
from aqm.queue.file import FileQueue
from aqm.runtime.gemini import (
    GeminiCLIRuntime,
    _DEFAULT_GEMINI_MODEL,
    _TEMP_FILES_TO_CLEANUP,
    _check_gemini_cli_available,
    _cleanup_temp_files,
    _write_temp_file,
)
from aqm.runtime.codex import (
    CodexCLIRuntime,
    _check_codex_cli_available,
)


# ── CLI availability checks ────────────────────────────────────────────


class TestCLIAvailabilityChecks:
    def test_gemini_cli_not_found(self):
        with patch("shutil.which", return_value=None):
            with pytest.raises(FileNotFoundError, match="gemini"):
                _check_gemini_cli_available()

    def test_gemini_cli_found(self):
        with patch("shutil.which", return_value="/usr/local/bin/gemini"):
            _check_gemini_cli_available()

    def test_codex_cli_not_found(self):
        with patch("shutil.which", return_value=None):
            with pytest.raises(FileNotFoundError, match="codex"):
                _check_codex_cli_available()

    def test_codex_cli_found(self):
        with patch("shutil.which", return_value="/usr/local/bin/codex"):
            _check_codex_cli_available()


# ── GeminiCLIRuntime ───────────────────────────────────────────────────


class TestGeminiCLIRuntime:
    def _make_agent(self, **overrides) -> AgentDefinition:
        defaults = {
            "id": "test_agent",
            "runtime": "gemini",
            "system_prompt": "You are a helpful assistant. {{ input }}",
        }
        defaults.update(overrides)
        return AgentDefinition(**defaults)

    def test_name(self):
        runtime = GeminiCLIRuntime()
        assert runtime.name == "gemini_cli"

    @patch("aqm.runtime.gemini.shutil.which", return_value="/usr/local/bin/gemini")
    @patch("subprocess.run")
    def test_run_basic(self, mock_run, mock_which):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="Hello from Gemini!",
            stderr="",
        )

        runtime = GeminiCLIRuntime()
        agent = self._make_agent(system_prompt="")
        task = Task(description="test")

        result = runtime.run("test prompt", agent, task)
        assert result == "Hello from Gemini!"

        call_args = mock_run.call_args
        cmd = call_args[0][0]
        assert "gemini" in cmd
        assert "-p" in cmd
        assert "test prompt" in cmd

    @patch("aqm.runtime.gemini.shutil.which", return_value="/usr/local/bin/gemini")
    @patch("subprocess.run")
    def test_run_with_model(self, mock_run, mock_which):
        mock_run.return_value = MagicMock(
            returncode=0, stdout="response", stderr="",
        )

        runtime = GeminiCLIRuntime()
        agent = self._make_agent(model="gemini-2.5-flash", system_prompt="")
        task = Task(description="test")

        runtime.run("prompt", agent, task)

        cmd = mock_run.call_args[0][0]
        assert "-m" in cmd
        assert "gemini-2.5-flash" in cmd

    @patch("aqm.runtime.gemini.shutil.which", return_value="/usr/local/bin/gemini")
    @patch("subprocess.run")
    def test_system_prompt_via_env_var(self, mock_run, mock_which):
        """System prompt should be passed via GEMINI_SYSTEM_MD env var."""
        mock_run.return_value = MagicMock(
            returncode=0, stdout="response", stderr="",
        )

        runtime = GeminiCLIRuntime()
        agent = self._make_agent(system_prompt="Be helpful")
        task = Task(description="test")

        runtime.run("prompt", agent, task)

        cmd = mock_run.call_args[0][0]
        assert "--system-prompt" not in cmd

        call_kwargs = mock_run.call_args[1]
        env = call_kwargs.get("env", {})
        assert "GEMINI_SYSTEM_MD" in env

    @patch("aqm.runtime.gemini.shutil.which", return_value="/usr/local/bin/gemini")
    @patch("subprocess.run")
    def test_run_failure(self, mock_run, mock_which):
        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr="Error: invalid model",
        )

        runtime = GeminiCLIRuntime()
        agent = self._make_agent(system_prompt="")
        task = Task(description="test")

        with pytest.raises(RuntimeError, match="Gemini CLI execution failed"):
            runtime.run("prompt", agent, task)

    @patch("aqm.runtime.gemini.shutil.which", return_value="/usr/local/bin/gemini")
    def test_run_streaming(self, mock_which):
        mock_proc = MagicMock()
        mock_proc.stdout.readline.side_effect = ["line 1\n", "line 2\n", ""]
        mock_proc.returncode = 0
        mock_proc.wait.return_value = None

        lines_received: list[str] = []

        with patch("subprocess.Popen", return_value=mock_proc):
            runtime = GeminiCLIRuntime()
            agent = self._make_agent(system_prompt="")
            task = Task(description="test")

            result = runtime.run(
                "prompt", agent, task,
                on_output=lambda line: lines_received.append(line),
            )

        assert result == "line 1\nline 2"
        assert lines_received == ["line 1", "line 2"]


# ── CodexCLIRuntime ───────────────────────────────────────────────────


class TestCodexCLIRuntime:
    def _make_agent(self, **overrides) -> AgentDefinition:
        defaults = {
            "id": "test_agent",
            "runtime": "codex",
            "system_prompt": "You are a helpful assistant. {{ input }}",
        }
        defaults.update(overrides)
        return AgentDefinition(**defaults)

    def test_name(self):
        runtime = CodexCLIRuntime()
        assert runtime.name == "codex_cli"

    @patch("aqm.runtime.codex.shutil.which", return_value="/usr/local/bin/codex")
    @patch("subprocess.run")
    def test_run_basic(self, mock_run, mock_which):
        mock_run.return_value = MagicMock(
            returncode=0, stdout="Code generated!", stderr="",
        )

        runtime = CodexCLIRuntime()
        agent = self._make_agent(system_prompt="")
        task = Task(description="test")

        result = runtime.run("write a function", agent, task)
        assert result == "Code generated!"

        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "codex"
        assert cmd[1] == "exec"
        assert "--full-auto" in cmd

    @patch("aqm.runtime.codex.shutil.which", return_value="/usr/local/bin/codex")
    @patch("subprocess.run")
    def test_run_with_model(self, mock_run, mock_which):
        mock_run.return_value = MagicMock(
            returncode=0, stdout="response", stderr="",
        )

        runtime = CodexCLIRuntime()
        agent = self._make_agent(model="o4-mini", system_prompt="")
        task = Task(description="test")

        runtime.run("prompt", agent, task)

        cmd = mock_run.call_args[0][0]
        assert "-m" in cmd
        assert "o4-mini" in cmd

    @patch("aqm.runtime.codex.shutil.which", return_value="/usr/local/bin/codex")
    @patch("subprocess.run")
    def test_system_prompt_prepended(self, mock_run, mock_which):
        """System prompt should be prepended to the user prompt."""
        mock_run.return_value = MagicMock(
            returncode=0, stdout="response", stderr="",
        )

        runtime = CodexCLIRuntime()
        agent = self._make_agent(system_prompt="Be a code reviewer")
        task = Task(description="test")

        runtime.run("review this code", agent, task)

        cmd = mock_run.call_args[0][0]
        full_prompt = cmd[2]
        assert "Be a code reviewer" in full_prompt
        assert "review this code" in full_prompt

    @patch("aqm.runtime.codex.shutil.which", return_value="/usr/local/bin/codex")
    @patch("subprocess.run")
    def test_run_failure(self, mock_run, mock_which):
        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr="Error: auth failed",
        )

        runtime = CodexCLIRuntime()
        agent = self._make_agent(system_prompt="")
        task = Task(description="test")

        with pytest.raises(RuntimeError, match="Codex CLI execution failed"):
            runtime.run("prompt", agent, task)

    @patch("aqm.runtime.codex.shutil.which", return_value="/usr/local/bin/codex")
    def test_run_streaming(self, mock_which):
        mock_proc = MagicMock()
        mock_proc.stdout.readline.side_effect = ["output 1\n", "output 2\n", ""]
        mock_proc.returncode = 0
        mock_proc.wait.return_value = None

        lines_received: list[str] = []

        with patch("subprocess.Popen", return_value=mock_proc):
            runtime = CodexCLIRuntime()
            agent = self._make_agent(system_prompt="")
            task = Task(description="test")

            result = runtime.run(
                "prompt", agent, task,
                on_output=lambda line: lines_received.append(line),
            )

        assert result == "output 1\noutput 2"
        assert lines_received == ["output 1", "output 2"]


# ── YAML loading with all runtimes ────────────────────────────────────


class TestRuntimeYAMLLoading:
    def test_load_gemini_agent(self, tmp_project):
        yaml_content = {
            "agents": [
                {
                    "id": "planner",
                    "runtime": "gemini",
                    "model": "gemini-2.5-flash",
                    "system_prompt": "Plan: {{ input }}",
                }
            ]
        }
        yaml_path = tmp_project / ".aqm" / "agents.yaml"
        yaml_path.write_text(yaml.dump(yaml_content), encoding="utf-8")

        agents = load_agents(yaml_path)
        assert agents["planner"].runtime == "gemini"
        assert agents["planner"].model == "gemini-2.5-flash"

    def test_load_codex_agent(self, tmp_project):
        yaml_content = {
            "agents": [
                {
                    "id": "coder",
                    "runtime": "codex",
                    "model": "o4-mini",
                    "system_prompt": "Code: {{ input }}",
                }
            ]
        }
        yaml_path = tmp_project / ".aqm" / "agents.yaml"
        yaml_path.write_text(yaml.dump(yaml_content), encoding="utf-8")

        agents = load_agents(yaml_path)
        assert agents["coder"].runtime == "codex"
        assert agents["coder"].model == "o4-mini"

    def test_load_claude_agent(self, tmp_project):
        yaml_content = {
            "agents": [
                {
                    "id": "reviewer",
                    "runtime": "claude",
                    "system_prompt": "Review: {{ input }}",
                }
            ]
        }
        yaml_path = tmp_project / ".aqm" / "agents.yaml"
        yaml_path.write_text(yaml.dump(yaml_content), encoding="utf-8")

        agents = load_agents(yaml_path)
        assert agents["reviewer"].runtime == "claude"

    def test_mixed_runtime_pipeline(self, tmp_project):
        """Test a pipeline mixing all three providers."""
        yaml_content = {
            "agents": [
                {
                    "id": "planner",
                    "runtime": "gemini",
                    "system_prompt": "Plan: {{ input }}",
                    "handoffs": [{"to": "executor", "condition": "always"}],
                },
                {
                    "id": "executor",
                    "runtime": "claude",
                    "system_prompt": "Execute: {{ input }}",
                    "mcp": [{"server": "github"}],
                    "handoffs": [{"to": "reviewer", "condition": "always"}],
                },
                {
                    "id": "reviewer",
                    "runtime": "codex",
                    "system_prompt": "Review: {{ input }}",
                },
            ]
        }
        yaml_path = tmp_project / ".aqm" / "agents.yaml"
        yaml_path.write_text(yaml.dump(yaml_content), encoding="utf-8")

        agents = load_agents(yaml_path)
        assert agents["planner"].runtime == "gemini"
        assert agents["executor"].runtime == "claude"
        assert agents["reviewer"].runtime == "codex"

    def test_runtime_is_required(self, tmp_project):
        """Missing runtime should fail validation."""
        yaml_content = {
            "agents": [
                {
                    "id": "bad",
                    "system_prompt": "{{ input }}",
                }
            ]
        }
        yaml_path = tmp_project / ".aqm" / "agents.yaml"
        yaml_path.write_text(yaml.dump(yaml_content), encoding="utf-8")

        with pytest.raises(Exception):
            load_agents(yaml_path)

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

        with pytest.raises(Exception):
            load_agents(yaml_path)


# ── Pipeline runtime resolution ───────────────────────────────────────


class TestPipelineRuntimeResolution:
    def test_get_gemini_runtime(self, tmp_project):
        agents = {
            "test": AgentDefinition(
                id="test", runtime="gemini", system_prompt="{{ input }}",
            )
        }
        queue = FileQueue(tmp_project / ".aqm" / "file-queue")
        pipeline = Pipeline(agents, queue, tmp_project)

        runtime = pipeline._get_runtime(agents["test"])
        assert isinstance(runtime, GeminiCLIRuntime)

    def test_get_codex_runtime(self, tmp_project):
        agents = {
            "test": AgentDefinition(
                id="test", runtime="codex", system_prompt="{{ input }}",
            )
        }
        queue = FileQueue(tmp_project / ".aqm" / "file-queue")
        pipeline = Pipeline(agents, queue, tmp_project)

        runtime = pipeline._get_runtime(agents["test"])
        assert isinstance(runtime, CodexCLIRuntime)

    def test_claude_always_uses_claude_code(self, tmp_project):
        """Claude runtime always uses ClaudeCodeRuntime."""
        from aqm.runtime.claude import ClaudeCodeRuntime

        agents = {
            "plain": AgentDefinition(
                id="plain", runtime="claude", system_prompt="{{ input }}",
            ),
            "with_mcp": AgentDefinition(
                id="with_mcp", runtime="claude", system_prompt="{{ input }}",
                mcp=[{"server": "github"}],
            ),
            "with_flags": AgentDefinition(
                id="with_flags", runtime="claude", system_prompt="{{ input }}",
                cli_flags=["--allowedTools", "Edit,Read"],
            ),
        }
        queue = FileQueue(tmp_project / ".aqm" / "file-queue")
        pipeline = Pipeline(agents, queue, tmp_project)

        for agent in agents.values():
            runtime = pipeline._get_runtime(agent)
            assert isinstance(runtime, ClaudeCodeRuntime)

    def test_runtime_caching(self, tmp_project):
        agents = {
            "a": AgentDefinition(id="a", runtime="gemini", system_prompt="{{ input }}"),
            "b": AgentDefinition(id="b", runtime="gemini", system_prompt="{{ input }}"),
        }
        queue = FileQueue(tmp_project / ".aqm" / "file-queue")
        pipeline = Pipeline(agents, queue, tmp_project)

        rt1 = pipeline._get_runtime(agents["a"])
        rt2 = pipeline._get_runtime(agents["b"])
        assert rt1 is rt2

    def test_unknown_runtime_raises(self, tmp_project):
        agent = AgentDefinition.__new__(AgentDefinition)
        object.__setattr__(agent, "runtime", "nonexistent")
        object.__setattr__(agent, "id", "bad")
        object.__setattr__(agent, "mcp", [])
        object.__setattr__(agent, "cli_flags", None)

        queue = FileQueue(tmp_project / ".aqm" / "file-queue")
        pipeline = Pipeline({"bad": agent}, queue, tmp_project)

        with pytest.raises(ValueError, match="Unknown runtime"):
            pipeline._get_runtime(agent)

    def test_full_pipeline_with_gemini(self, tmp_project):
        agents = {
            "planner": AgentDefinition(
                id="planner", runtime="gemini",
                system_prompt="Plan: {{ input }}",
            ),
        }
        queue = FileQueue(tmp_project / ".aqm" / "file-queue")
        pipeline = Pipeline(agents, queue, tmp_project)

        mock_runtime = MagicMock()
        mock_runtime.name = "gemini_cli"
        mock_runtime.run.return_value = "Plan completed."
        pipeline._runtimes["gemini"] = mock_runtime

        task = Task(description="Build a login page")
        queue.push(task, "planner")

        result = pipeline.run_task(task, "planner")
        assert result.status == TaskStatus.completed
        assert result.stages[0].output_text == "Plan completed."

    def test_claude_runtime_cached_single_instance(self, tmp_project):
        """All claude agents share the same ClaudeCodeRuntime instance."""
        from aqm.runtime.claude import ClaudeCodeRuntime

        agents = {
            "a": AgentDefinition(id="a", runtime="claude", system_prompt="{{ input }}"),
            "b": AgentDefinition(id="b", runtime="claude", system_prompt="{{ input }}", mcp=[{"server": "github"}]),
        }
        queue = FileQueue(tmp_project / ".aqm" / "file-queue")
        pipeline = Pipeline(agents, queue, tmp_project)

        rt_a = pipeline._get_runtime(agents["a"])
        rt_b = pipeline._get_runtime(agents["b"])
        assert isinstance(rt_a, ClaudeCodeRuntime)
        assert rt_a is rt_b  # Same cached instance

    def test_full_pipeline_with_codex(self, tmp_project):
        agents = {
            "coder": AgentDefinition(
                id="coder", runtime="codex",
                system_prompt="Code: {{ input }}",
            ),
        }
        queue = FileQueue(tmp_project / ".aqm" / "file-queue")
        pipeline = Pipeline(agents, queue, tmp_project)

        mock_runtime = MagicMock()
        mock_runtime.name = "codex_cli"
        mock_runtime.run.return_value = "Code written."
        pipeline._runtimes["codex"] = mock_runtime

        task = Task(description="Write unit tests")
        queue.push(task, "coder")

        result = pipeline.run_task(task, "coder")
        assert result.status == TaskStatus.completed
        assert result.stages[0].output_text == "Code written."


# ── Temp file helpers ─────────────────────────────────────────────────


class TestTempFileHelpers:
    def test_write_temp_file_creates_file(self):
        path = _write_temp_file("hello world", prefix="aqm_test_", suffix=".md")
        try:
            assert path.exists()
            assert path.read_text(encoding="utf-8") == "hello world"
            assert path in _TEMP_FILES_TO_CLEANUP
        finally:
            path.unlink(missing_ok=True)
            if path in _TEMP_FILES_TO_CLEANUP:
                _TEMP_FILES_TO_CLEANUP.remove(path)

    def test_write_temp_file_unicode(self):
        path = _write_temp_file("한글 테스트 🎉", prefix="aqm_uni_", suffix=".md")
        try:
            assert path.read_text(encoding="utf-8") == "한글 테스트 🎉"
        finally:
            path.unlink(missing_ok=True)
            if path in _TEMP_FILES_TO_CLEANUP:
                _TEMP_FILES_TO_CLEANUP.remove(path)

    def test_cleanup_temp_files(self, tmp_path):
        # Create a real temp file and register it
        test_file = tmp_path / "cleanup_test.md"
        test_file.write_text("to be cleaned")
        _TEMP_FILES_TO_CLEANUP.append(test_file)

        _cleanup_temp_files()

        assert not test_file.exists()
        # Cleanup the list entry
        if test_file in _TEMP_FILES_TO_CLEANUP:
            _TEMP_FILES_TO_CLEANUP.remove(test_file)

    def test_cleanup_missing_file_no_error(self):
        """Cleanup should not raise if file already deleted."""
        fake_path = Path("/tmp/aqm_nonexistent_cleanup_test.md")
        _TEMP_FILES_TO_CLEANUP.append(fake_path)

        _cleanup_temp_files()  # Should not raise

        if fake_path in _TEMP_FILES_TO_CLEANUP:
            _TEMP_FILES_TO_CLEANUP.remove(fake_path)


# ── Runtime init ──────────────────────────────────────────────────────


class TestRuntimeInit:
    def test_gemini_init_with_project_root(self, tmp_path):
        runtime = GeminiCLIRuntime(project_root=tmp_path)
        assert runtime._project_root == tmp_path

    def test_gemini_init_without_project_root(self):
        runtime = GeminiCLIRuntime()
        assert runtime._project_root is None

    def test_codex_init_with_project_root(self, tmp_path):
        runtime = CodexCLIRuntime(project_root=tmp_path)
        assert runtime._project_root == tmp_path

    def test_codex_init_without_project_root_uses_cwd(self):
        runtime = CodexCLIRuntime()
        assert runtime._project_root == Path.cwd()


# ── Backward compatibility: claude_code_flags → cli_flags ─────────────


class TestClaudeCodeFlagsBackwardCompat:
    """Test deprecated claude_code_flags is migrated to cli_flags."""

    def test_claude_code_flags_migrated_to_cli_flags(self):
        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            agent = AgentDefinition(
                id="x",
                runtime="claude",
                claude_code_flags=["--allowedTools", "Edit,Read"],
            )

        assert agent.cli_flags == ["--allowedTools", "Edit,Read"]

    def test_claude_code_flags_emits_deprecation_warning(self):
        with pytest.warns(DeprecationWarning, match="claude_code_flags.*deprecated"):
            AgentDefinition(
                id="x",
                runtime="claude",
                claude_code_flags=["--allowedTools", "Edit,Read"],
            )

    def test_cli_flags_takes_precedence_over_claude_code_flags(self):
        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            agent = AgentDefinition(
                id="x",
                runtime="claude",
                claude_code_flags=["--old-flag"],
                cli_flags=["--new-flag"],
            )

        assert agent.cli_flags == ["--new-flag"]


# ── Gemini cli_flags in command build ─────────────────────────────────


class TestGeminiCLIFlagsInCmd:
    """Test that cli_flags appear in the Gemini CLI command."""

    @patch("aqm.runtime.gemini.shutil.which", return_value="/usr/local/bin/gemini")
    @patch("subprocess.run")
    def test_cli_flags_in_gemini_cmd(self, mock_run, mock_which):
        mock_run.return_value = MagicMock(
            returncode=0, stdout="Gemini response", stderr="",
        )

        runtime = GeminiCLIRuntime()
        agent = AgentDefinition(
            id="test_agent",
            runtime="gemini",
            system_prompt="",
            cli_flags=["--sandbox", "strict"],
        )
        task = Task(description="test")

        runtime.run("test prompt", agent, task)

        cmd = mock_run.call_args[0][0]
        assert "--sandbox" in cmd
        assert "strict" in cmd


# ── Codex cli_flags in command build ──────────────────────────────────


class TestCodexCLIFlagsInCmd:
    """Test that cli_flags appear in the Codex CLI command."""

    @patch("aqm.runtime.codex.shutil.which", return_value="/usr/local/bin/codex")
    @patch("subprocess.run")
    def test_cli_flags_in_codex_cmd(self, mock_run, mock_which):
        mock_run.return_value = MagicMock(
            returncode=0, stdout="Codex response", stderr="",
        )

        runtime = CodexCLIRuntime()
        agent = AgentDefinition(
            id="test_agent",
            runtime="codex",
            system_prompt="",
            cli_flags=["--sandbox", "read-only"],
        )
        task = Task(description="test")

        runtime.run("write code", agent, task)

        cmd = mock_run.call_args[0][0]
        assert "--sandbox" in cmd
        assert "read-only" in cmd
