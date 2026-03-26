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


# ── P6: Callback error logging ──────────────────────────────────────


class TestCallbackErrorLogging:
    """Callback errors should be logged, not silently swallowed."""

    def test_callback_error_is_logged(self, caplog):
        """on_output callback errors should produce warning logs."""
        import io
        import logging
        from unittest.mock import patch, MagicMock

        from aqm.runtime.claude_code import ClaudeCodeRuntime

        rt = ClaudeCodeRuntime(Path("/tmp/project"), timeout=60)
        agent = AgentDefinition(
            id="test_agent", runtime="claude",
        )

        def failing_callback(text):
            if text:  # Don't fail on empty strings
                raise ConnectionError("SSE connection lost")

        # Mock Popen
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stderr = io.StringIO("")

        readline_count = [0]
        def mock_readline():
            readline_count[0] += 1
            if readline_count[0] == 1:
                return 'not-json-line\n'  # triggers non-JSON path
            return ""  # EOF

        mock_proc.stdout = MagicMock()
        mock_proc.stdout.readline = mock_readline
        mock_proc.wait.return_value = None

        with caplog.at_level(logging.WARNING, logger="aqm.runtime.claude_code"):
            with patch("subprocess.Popen", return_value=mock_proc):
                result = rt._run_stream_json(
                    ["claude", "--print"],
                    agent,
                    on_output=failing_callback,
                )

        # Verify the error was logged, not silently swallowed
        assert "callback error" in caplog.text.lower()
        assert "SSE connection lost" in caplog.text

    def test_callback_errors_suppressed_after_max(self, caplog):
        """After MAX_CALLBACK_ERRORS, further warnings are suppressed."""
        import io
        import logging
        from unittest.mock import patch, MagicMock

        from aqm.runtime.claude_code import ClaudeCodeRuntime

        rt = ClaudeCodeRuntime(Path("/tmp/project"), timeout=60)
        agent = AgentDefinition(
            id="test_agent", runtime="claude",
        )

        def always_fail(text):
            raise RuntimeError("always fails")

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stderr = io.StringIO("")

        # Generate 15 non-JSON lines to trigger 15 callback errors
        line_count = [0]
        def mock_readline():
            line_count[0] += 1
            if line_count[0] <= 15:
                return f'line-{line_count[0]}\n'
            return ""

        mock_proc.stdout = MagicMock()
        mock_proc.stdout.readline = mock_readline
        mock_proc.wait.return_value = None

        with caplog.at_level(logging.WARNING, logger="aqm.runtime.claude_code"):
            with patch("subprocess.Popen", return_value=mock_proc):
                rt._run_stream_json(
                    ["claude", "--print"],
                    agent,
                    on_output=always_fail,
                )

        # Should see "Too many callback errors" message
        assert "too many callback errors" in caplog.text.lower()
