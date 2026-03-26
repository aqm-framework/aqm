"""QA tests — edge cases, validation, error recovery, integration."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from aqm.core.agent import (
    AgentDefinition,
    ChunksConfig,
    ConsensusConfig,
    load_agents,
)
from aqm.core.chunks import ChunkManager, parse_chunk_directives
from aqm.core.context_file import ContextFile
from aqm.core.pipeline import Pipeline
from aqm.core.task import Task, TaskStatus
from aqm.queue.file import FileQueue


# ── P1: Validation ────────────────────────────────────────────────────


class TestValidation:
    def test_require_chunks_done_without_chunks_raises(self, tmp_project):
        yaml_content = {
            "agents": [
                {"id": "a", "runtime": "claude", "system_prompt": "{{ input }}"},
                {
                    "id": "session",
                    "type": "session",
                    "participants": ["a"],
                    "consensus": {
                        "method": "vote",
                        "require_chunks_done": True,
                    },
                    # No chunks config!
                },
            ]
        }
        yaml_path = tmp_project / ".aqm" / "agents.yaml"
        yaml_path.write_text(yaml.dump(yaml_content), encoding="utf-8")

        with pytest.raises(ValueError, match="require_chunks_done.*chunks"):
            load_agents(yaml_path)

    def test_summary_agent_must_be_participant(self, tmp_project):
        yaml_content = {
            "agents": [
                {"id": "a", "runtime": "claude", "system_prompt": "{{ input }}"},
                {"id": "b", "runtime": "claude", "system_prompt": "{{ input }}"},
                {
                    "id": "session",
                    "type": "session",
                    "participants": ["a"],
                    "summary_agent": "b",  # b is NOT a participant
                },
            ]
        }
        yaml_path = tmp_project / ".aqm" / "agents.yaml"
        yaml_path.write_text(yaml.dump(yaml_content), encoding="utf-8")

        with pytest.raises(ValueError, match="summary_agent.*participant"):
            load_agents(yaml_path)

    def test_chunks_on_non_session_warns(self, tmp_project, caplog):
        """Chunks config on regular agent should log a warning."""
        import logging

        yaml_content = {
            "agents": [
                {
                    "id": "regular",
                    "runtime": "claude",
                    "system_prompt": "{{ input }}",
                    "chunks": {"initial": ["Task 1"]},
                },
            ]
        }
        yaml_path = tmp_project / ".aqm" / "agents.yaml"
        yaml_path.write_text(yaml.dump(yaml_content), encoding="utf-8")

        with caplog.at_level(logging.WARNING):
            agents = load_agents(yaml_path)

        assert "regular" in agents
        assert "chunks only apply" in caplog.text.lower() or "not a session" in caplog.text.lower()


# ── P2: Chunk edge cases ─────────────────────────────────────────────


class TestChunkEdgeCases:
    def test_pipe_in_description_escaped(self, tmp_path):
        mgr = ChunkManager(tmp_path / "task-1")
        mgr.add("Implement OAuth | Bearer tokens")
        summary = mgr.summary()
        # The pipe should be escaped so markdown table isn't broken
        assert "\\|" in summary
        # Should still have proper table structure
        assert summary.count("\n") >= 3

    def test_chunk_done_nonexistent_warns(self, tmp_path, caplog):
        import logging

        mgr = ChunkManager(tmp_path / "task-1")
        mgr.add("Real task")

        with caplog.at_level(logging.WARNING):
            actions = parse_chunk_directives(
                "CHUNK_DONE: C-999", mgr, "agent",
            )

        assert len(actions) == 0
        assert "C-999" in caplog.text

    def test_chunk_remove_nonexistent_warns(self, tmp_path, caplog):
        import logging

        mgr = ChunkManager(tmp_path / "task-1")

        with caplog.at_level(logging.WARNING):
            actions = parse_chunk_directives(
                "CHUNK_REMOVE: C-999", mgr, "agent",
            )

        assert len(actions) == 0
        assert "C-999" in caplog.text

    def test_empty_chunk_add_ignored(self, tmp_path):
        mgr = ChunkManager(tmp_path / "task-1")
        actions = parse_chunk_directives(
            "CHUNK_ADD:   \n", mgr, "agent",
        )
        assert len(actions) == 0
        assert len(mgr.load().chunks) == 0

    def test_chunk_id_reuses_gaps(self, tmp_path):
        """Removed chunk IDs get reused by next add."""
        mgr = ChunkManager(tmp_path / "task-1")
        mgr.add("First")   # C-001
        mgr.add("Second")  # C-002
        mgr.add("Third")   # C-003
        mgr.remove("C-002")
        c4 = mgr.add("Fourth")  # Reuses C-002 (first available)
        assert c4.id == "C-002"


# ── P3: Session error recovery ────────────────────────────────────────


class TestSessionErrorRecovery:
    def test_agent_crash_mid_session_continues(self, tmp_project):
        """If one agent crashes, session records error and continues."""
        agents = {
            "a": AgentDefinition(
                id="a", runtime="claude", system_prompt="{{ input }}",
            ),
            "b": AgentDefinition(
                id="b", runtime="claude", system_prompt="{{ input }}",
            ),
            "session": AgentDefinition(
                id="session",
                type="session",
                participants=["a", "b"],
                max_rounds=2,
                consensus=ConsensusConfig(method="vote", require="all"),
            ),
        }
        queue = FileQueue(tmp_project / ".aqm" / "file-queue")
        pipeline = Pipeline(agents, queue, tmp_project)

        mock_rt = MagicMock()
        mock_rt.name = "mock"
        mock_rt.run.side_effect = [
            "Agent A is fine. VOTE: AGREE",
            RuntimeError("Agent B crashed!"),  # Agent B fails
            "Agent A round 2. VOTE: AGREE",
            "Agent B recovered. VOTE: AGREE",
        ]
        pipeline._runtimes["claude"] = mock_rt

        task = Task(description="Crash test")
        queue.push(task, "session")
        result = pipeline.run_task(task, "session")

        # Session should record error but continue
        error_stages = [s for s in result.stages if "ERROR" in s.output_text]
        assert len(error_stages) >= 1
        assert "crashed" in error_stages[0].output_text.lower()

    def test_cancel_during_session(self, tmp_project):
        """Cancellation mid-session should stop early."""
        from aqm.core.pipeline import cancel_task, _cancelled_tasks

        agents = {
            "a": AgentDefinition(
                id="a", runtime="claude", system_prompt="{{ input }}",
            ),
            "b": AgentDefinition(
                id="b", runtime="claude", system_prompt="{{ input }}",
            ),
            "session": AgentDefinition(
                id="session",
                type="session",
                participants=["a", "b"],
                max_rounds=10,
                consensus=ConsensusConfig(method="vote"),
            ),
        }
        queue = FileQueue(tmp_project / ".aqm" / "file-queue")
        pipeline = Pipeline(agents, queue, tmp_project)

        call_count = 0

        def mock_run(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                cancel_task(task.id)
            return "Still discussing..."

        mock_rt = MagicMock()
        mock_rt.name = "mock"
        mock_rt.run.side_effect = mock_run
        pipeline._runtimes["claude"] = mock_rt

        task = Task(description="Cancel test")
        queue.push(task, "session")
        result = pipeline.run_task(task, "session")

        # Cancel is detected at next round/turn check —
        # session exits early, then run_task detects cancellation
        assert result.status == TaskStatus.cancelled
        # Cleanup
        _cancelled_tasks.discard(task.id)


# ── P4: Integration — batch → session(chunks) → batch ────────────────


class TestIntegrationPipeline:
    def test_batch_session_chunks_batch_chain(self, tmp_project):
        """Full chain: planner → session(with chunks) → implementer."""
        agents = {
            "planner": AgentDefinition(
                id="planner", runtime="claude",
                system_prompt="Plan: {{ input }}",
                handoffs=[{"to": "review"}],
            ),
            "arch": AgentDefinition(
                id="arch", runtime="claude",
                context_strategy="own",
                system_prompt="{{ input }} {{ transcript }} {{ chunks }}",
            ),
            "sec": AgentDefinition(
                id="sec", runtime="claude",
                context_strategy="shared",
                system_prompt="{{ input }} {{ transcript }} {{ chunks }}",
            ),
            "review": AgentDefinition(
                id="review",
                type="session",
                participants=["arch", "sec"],
                max_rounds=3,
                consensus=ConsensusConfig(
                    method="vote", require="all",
                    require_chunks_done=True,
                ),
                chunks=ChunksConfig(initial=["Design API", "Security review"]),
                handoffs=[{"to": "implementer"}],
            ),
            "implementer": AgentDefinition(
                id="implementer", runtime="claude",
                system_prompt="Implement: {{ input }}",
            ),
        }
        queue = FileQueue(tmp_project / ".aqm" / "file-queue")
        pipeline = Pipeline(agents, queue, tmp_project)

        mock_rt = MagicMock()
        mock_rt.name = "mock"
        mock_rt.run.side_effect = [
            "Here is the plan.",                              # planner
            "CHUNK_DONE: C-001\nVOTE: AGREE",                # arch in session
            "CHUNK_DONE: C-002\nVOTE: AGREE",                # sec in session
            "Implementation complete.",                        # implementer
        ]
        pipeline._runtimes["claude"] = mock_rt

        task = Task(description="Build API")
        queue.push(task, "planner")
        result = pipeline.run_task(task, "planner")

        assert result.status == TaskStatus.completed
        assert result.metadata.get("session_consensus") is True
        assert result.metadata.get("chunks_done") == 2
        # Verify all stages exist
        agent_ids = [s.agent_id for s in result.stages]
        assert "planner" in agent_ids
        assert "review" in agent_ids
        assert "implementer" in agent_ids

    def test_mixed_context_strategy_in_session(self, tmp_project):
        """Agents with different strategies see different context."""
        agents = {
            "shared_agent": AgentDefinition(
                id="shared_agent", runtime="claude",
                context_strategy="shared",
                system_prompt="{{ context }}",
            ),
            "own_agent": AgentDefinition(
                id="own_agent", runtime="claude",
                context_strategy="own",
                system_prompt="{{ context }}",
            ),
            "session": AgentDefinition(
                id="session",
                type="session",
                participants=["shared_agent", "own_agent"],
                max_rounds=2,
                consensus=ConsensusConfig(method="vote", require="all"),
            ),
        }
        queue = FileQueue(tmp_project / ".aqm" / "file-queue")
        pipeline = Pipeline(agents, queue, tmp_project)

        mock_rt = MagicMock()
        mock_rt.name = "mock"
        mock_rt.run.side_effect = [
            "Shared agent round 1",
            "Own agent round 1",
            "Shared agent round 2. VOTE: AGREE",
            "Own agent round 2. VOTE: AGREE",
        ]
        pipeline._runtimes["claude"] = mock_rt

        task = Task(description="Mixed strategy")
        queue.push(task, "session")
        result = pipeline.run_task(task, "session")

        # Round 2 prompts: shared_agent should see all context,
        # own_agent should only see its own
        # call_args_list: [shared_r1, own_r1, shared_r2, own_r2]
        own_r2_prompt = mock_rt.run.call_args_list[3][0][0]
        shared_r2_prompt = mock_rt.run.call_args_list[2][0][0]

        # own_agent should NOT see shared_agent's output in {{ context }}
        assert "Shared agent round 1" not in own_r2_prompt
        # But it DOES see it in transcript ({{ transcript }} is always full)

        assert result.metadata.get("session_consensus") is True


# ── P5: Context file edge cases ───────────────────────────────────────


class TestContextFileEdgeCases:
    def test_read_nonexistent_task_dir(self, tmp_path):
        cf = ContextFile(tmp_path / "nonexistent-task")
        assert cf.read() == ""
        assert cf.read_transcript() == ""
        assert cf.read_agent_context("any") == ""
        assert cf.read_for_strategy("any", "own") == ""
        assert cf.read_for_strategy("any", "shared") == ""
        assert cf.read_for_strategy("any", "both") == ""

    def test_unicode_in_agent_context(self, tmp_path):
        cf = ContextFile(tmp_path / "task-unicode")
        cf.append_agent_context(
            agent_id="dev",
            stage_number=1,
            input_text="한글 입력",
            output_text="日本語の出力 🎉",
        )
        content = cf.read_agent_context("dev")
        # agent context stores only output now
        assert "日本語の出力" in content


# ── P6: Timeout config propagation ──────────────────────────────────


class TestTimeoutConfigPropagation:
    """Regression: config timeout must reach the runtime, not be hardcoded."""

    def test_custom_timeout_reaches_runtime(self, tmp_project):
        """Pipeline should pass config timeout to ClaudeCodeRuntime."""
        from aqm.core.config import ProjectConfig, RuntimeTimeouts
        from aqm.core.pipeline import Pipeline

        agents = {
            "a": AgentDefinition(
                id="a", runtime="claude", system_prompt="{{ input }}",
            ),
        }
        queue = FileQueue(tmp_project / ".aqm" / "file-queue")
        config = ProjectConfig(timeouts=RuntimeTimeouts(claude=900))
        pipeline = Pipeline(agents, queue, tmp_project, config=config)

        rt = pipeline._get_runtime(agents["a"])
        assert rt._timeout == 900

    def test_default_timeout_is_600(self, tmp_project):
        from aqm.core.pipeline import Pipeline

        agents = {
            "a": AgentDefinition(
                id="a", runtime="claude", system_prompt="{{ input }}",
            ),
        }
        queue = FileQueue(tmp_project / ".aqm" / "file-queue")
        pipeline = Pipeline(agents, queue, tmp_project)

        rt = pipeline._get_runtime(agents["a"])
        assert rt._timeout == 600


# ── P7: ClaudeCodeRuntime command build ──────────────────────────────


class TestClaudeCodeRuntimeCmdBuild:
    """Test that ClaudeCodeRuntime builds the command correctly."""

    def test_print_mode_auto_adds_skip_permissions(self):
        """--print mode should auto-add --dangerously-skip-permissions."""
        from unittest.mock import patch

        from aqm.runtime.claude import ClaudeCodeRuntime

        rt = ClaudeCodeRuntime(Path("/tmp/project"))
        agent = AgentDefinition(
            id="test_agent",
            runtime="claude",
            cli_flags=["--allowedTools", "Edit,Read"],
        )
        task = Task(description="test")

        # Capture the command by patching subprocess.run
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout="ok", stderr=""
            )
            with patch(
                "aqm.runtime.claude._check_claude_cli_available"
            ):
                rt.run("test prompt", agent, task)

        cmd = mock_run.call_args[0][0]
        assert "--dangerously-skip-permissions" in cmd
        assert "--print" in cmd

    def test_skip_permissions_not_duplicated(self):
        """If user already set --dangerously-skip-permissions, don't duplicate."""
        from unittest.mock import patch

        from aqm.runtime.claude import ClaudeCodeRuntime

        rt = ClaudeCodeRuntime(Path("/tmp/project"))
        agent = AgentDefinition(
            id="test_agent",
            runtime="claude",
            cli_flags=["--dangerously-skip-permissions"],
        )
        task = Task(description="test")

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout="ok", stderr=""
            )
            with patch(
                "aqm.runtime.claude._check_claude_cli_available"
            ):
                rt.run("test prompt", agent, task)

        cmd = mock_run.call_args[0][0]
        count = cmd.count("--dangerously-skip-permissions")
        assert count == 1


# ── P8: Streaming heartbeat ─────────────────────────────────────────


class TestStreamingHeartbeat:
    """Test that streaming uses selectors for non-blocking reads."""

    def test_heartbeat_sent_on_idle(self):
        """When no output for HEARTBEAT_INTERVAL, empty string callback fires."""
        import io
        import selectors
        from unittest.mock import patch, MagicMock

        from aqm.runtime.claude import ClaudeCodeRuntime

        rt = ClaudeCodeRuntime(Path("/tmp/project"), timeout=60)
        agent = AgentDefinition(
            id="test_agent", runtime="claude",
        )

        output_calls = []

        def track_output(text):
            output_calls.append(text)

        # Mock Popen to return a process with controlled stdout
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stderr = io.StringIO("")

        # Simulate: selector times out once (heartbeat), then gets data, then EOF
        mock_selector = MagicMock()
        call_count = [0]

        def mock_select(timeout=None):
            call_count[0] += 1
            if call_count[0] == 1:
                return []  # timeout → heartbeat
            return [(None, None)]  # data ready

        mock_selector.select = mock_select

        readline_calls = [0]
        def mock_readline():
            readline_calls[0] += 1
            if readline_calls[0] == 1:
                return '{"type": "result", "result": "done"}\n'
            return ""  # EOF

        mock_proc.stdout = MagicMock()
        mock_proc.stdout.readline = mock_readline
        mock_proc.poll.return_value = None  # Still running during heartbeat
        mock_proc.wait.return_value = None

        with patch("subprocess.Popen", return_value=mock_proc):
            with patch("selectors.DefaultSelector", return_value=mock_selector):
                result = rt._run_stream_json(
                    ["claude", "--print"],
                    agent,
                    on_output=track_output,
                )

        # First call should be empty heartbeat
        assert "" in output_calls
        assert result == "done"


# ── P9: Callback error logging ──────────────────────────────────────


class TestCallbackErrorLogging:
    """Callback errors should be logged, not silently swallowed."""

    def test_callback_error_is_logged(self, caplog):
        import io
        import logging
        from unittest.mock import patch, MagicMock
        from aqm.runtime.claude import ClaudeCodeRuntime

        rt = ClaudeCodeRuntime(Path("/tmp/project"), timeout=60)
        agent = AgentDefinition(id="test_agent", runtime="claude")

        def failing_callback(text):
            if text:
                raise ConnectionError("SSE connection lost")

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stderr = io.StringIO("")
        readline_count = [0]
        def mock_readline():
            readline_count[0] += 1
            if readline_count[0] == 1:
                return 'not-json-line\n'
            return ""
        mock_proc.stdout = MagicMock()
        mock_proc.stdout.readline = mock_readline
        mock_proc.poll.return_value = None
        mock_proc.wait.return_value = None

        mock_sel = MagicMock()
        sel_count = [0]
        def mock_select(timeout=None):
            sel_count[0] += 1
            return [(None, None)]  # always data ready
        mock_sel.select = mock_select

        with caplog.at_level(logging.WARNING, logger="aqm.runtime.claude"):
            with patch("subprocess.Popen", return_value=mock_proc):
                with patch("selectors.DefaultSelector", return_value=mock_sel):
                    rt._run_stream_json(["claude", "--print"], agent, on_output=failing_callback)

        assert "callback error" in caplog.text.lower()
        assert "SSE connection lost" in caplog.text


# ── P10: Tool streaming ─────────────────────────────────────────────


class TestToolStreaming:
    """Test that tool use events are parsed and forwarded."""

    def test_claude_tool_start_from_content_block_start(self):
        import io, json
        from unittest.mock import patch, MagicMock
        from aqm.runtime.claude import ClaudeCodeRuntime

        rt = ClaudeCodeRuntime(Path("/tmp/project"), timeout=60)
        agent = AgentDefinition(id="test", runtime="claude")
        tool_events = []

        events = [
            json.dumps({"type": "stream_event", "event": {
                "type": "content_block_start",
                "content_block": {"type": "tool_use", "id": "tu_123", "name": "Read"}
            }}),
            json.dumps({"type": "stream_event", "event": {
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": "done"}
            }}),
            json.dumps({"type": "result", "result": "done"}),
        ]

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stderr = io.StringIO("")
        call_idx = [0]
        def mock_readline():
            if call_idx[0] < len(events):
                line = events[call_idx[0]] + "\n"
                call_idx[0] += 1
                return line
            return ""
        mock_proc.stdout = MagicMock()
        mock_proc.stdout.readline = mock_readline
        mock_proc.poll.return_value = None
        mock_proc.wait.return_value = None
        mock_sel = MagicMock()
        mock_sel.select = lambda timeout=None: [(None, None)]

        with patch("subprocess.Popen", return_value=mock_proc):
            with patch("selectors.DefaultSelector", return_value=mock_sel):
                rt._run_stream_json(
                    ["claude", "--print"], agent,
                    on_output=lambda x: None,
                    on_tool=lambda et, d: tool_events.append((et, d)),
                )

        assert len(tool_events) >= 1
        assert tool_events[0][0] == "tool_start"
        assert tool_events[0][1]["tool"] == "Read"

    def test_tool_callback_type_exported(self):
        from aqm.runtime.base import ToolCallback
        assert ToolCallback is not None
