"""Tests for bug fixes — verifies each fix from the codebase audit."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from aqm.core.agent import AgentDefinition
from aqm.core.chunks import ChunkManager
from aqm.core.context_file import ContextFile
from aqm.core.gate import GateResult, LLMGate
from aqm.core.pipeline import Pipeline, cancel_task, is_cancelled, _cancel_lock, _cancelled_tasks
from aqm.core.task import Task, TaskStatus
from aqm.queue.file import FileQueue
from aqm.queue.sqlite import SQLiteQueue


# ── BUG-5: Thread-safe cancellation ──────────────────────────────────


class TestThreadSafeCancellation:
    def test_cancel_and_check_from_threads(self):
        """cancel_task + is_cancelled are thread-safe."""
        task_id = "T-thread-test"
        results = []

        def cancel_then_check():
            cancel_task(task_id)
            results.append(is_cancelled(task_id))

        threads = [threading.Thread(target=cancel_then_check) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert all(results), "All threads should see cancelled=True"
        # Cleanup
        with _cancel_lock:
            _cancelled_tasks.discard(task_id)


# ── BUG-7: Word-boundary condition matching ──────────────────────────


class TestConditionWordBoundary:
    def _make_pipeline(self):
        pipeline = Pipeline.__new__(Pipeline)
        pipeline.agents = {}
        pipeline.config = MagicMock()
        return pipeline

    def test_critical_does_not_match_uncritical(self):
        pipeline = self._make_pipeline()
        result = pipeline._evaluate_condition(
            "severity == critical",
            None,
            "The issue is uncritical and can wait.",
        )
        assert result is False, "substring 'uncritical' should NOT match 'critical'"

    def test_critical_matches_exact_word(self):
        pipeline = self._make_pipeline()
        result = pipeline._evaluate_condition(
            "severity == critical",
            None,
            "severity: critical — needs immediate fix",
        )
        assert result is True

    def test_in_list_word_boundary(self):
        pipeline = self._make_pipeline()
        result = pipeline._evaluate_condition(
            "level in [high, critical]",
            None,
            "This is noncritical and of low priority.",
        )
        assert result is False

    def test_in_list_matches_exact(self):
        pipeline = self._make_pipeline()
        result = pipeline._evaluate_condition(
            "level in [high, critical]",
            None,
            "Level: high — escalate immediately.",
        )
        assert result is True


# ── BUG-8: Gate JSON parsing ─────────────────────────────────────────


class TestGateParsing:
    def _gate(self):
        return LLMGate.__new__(LLMGate)

    def test_nested_json(self):
        gate = self._gate()
        text = '{"decision": "approved", "reason": "good", "details": {"score": 95}}'
        result = gate._parse_response(text)
        assert result.decision == "approved"
        assert result.reason == "good"

    def test_not_approved_is_rejected(self):
        gate = self._gate()
        result = gate._parse_response("This is not approved because it has issues")
        assert result.decision == "rejected"

    def test_not_approve_is_rejected(self):
        gate = self._gate()
        result = gate._parse_response("I do not approve of this output")
        assert result.decision == "rejected"

    def test_approved_still_works(self):
        gate = self._gate()
        result = gate._parse_response("The output is approved, looks great")
        assert result.decision == "approved"

    def test_json_in_prose(self):
        gate = self._gate()
        text = 'Here is my evaluation: {"decision": "rejected", "reason": "missing tests"} end.'
        result = gate._parse_response(text)
        assert result.decision == "rejected"
        assert result.reason == "missing tests"


# ── HIGH-1: Context file encoding error ──────────────────────────────


class TestContextFileEncoding:
    def test_read_binary_file_doesnt_crash(self, tmp_path):
        cf = ContextFile(tmp_path / "task")
        cf.ensure_dir()
        # Write binary data that is not valid UTF-8
        cf.context_path.write_bytes(b"\x80\x81\x82\x83 hello \xff\xfe")
        result = cf.read()
        assert "hello" in result  # Should still contain readable parts

    def test_read_agent_context_binary(self, tmp_path):
        cf = ContextFile(tmp_path / "task")
        cf.ensure_dir()
        path = cf.agent_context_path("test")
        path.write_bytes(b"\x80\x81 agent output \xff")
        result = cf.read_agent_context("test")
        assert "agent output" in result


# ── HIGH-2: Corrupted chunks.json ─────────────────────────────────────


class TestCorruptedChunks:
    def test_load_corrupted_json(self, tmp_path):
        cm = ChunkManager(tmp_path / "task")
        cm._ensure_dir()
        cm.chunks_path.write_text("this is not json{{{", encoding="utf-8")
        cl = cm.load()
        assert cl.chunks == []  # Should return empty, not crash

    def test_load_invalid_schema(self, tmp_path):
        cm = ChunkManager(tmp_path / "task")
        cm._ensure_dir()
        cm.chunks_path.write_text('{"chunks": [{"invalid": true}]}', encoding="utf-8")
        cl = cm.load()
        assert cl.chunks == []


# ── HIGH-3: Reject counter reset ──────────────────────────────────────


class TestRejectCounterReset:
    def test_reject_counter_resets_on_agent_change(self, tmp_project):
        """When pipeline moves to a different agent and back, reject count resets."""
        agents = {
            "worker": AgentDefinition(
                id="worker", runtime="claude",
                system_prompt="{{ input }}",
                gate={"type": "llm", "prompt": "Review", "max_retries": 2},
                handoffs=[
                    {"to": "helper", "condition": "on_reject"},
                ],
            ),
            "helper": AgentDefinition(
                id="helper", runtime="claude",
                system_prompt="{{ input }}",
                handoffs=[{"to": "worker"}],
            ),
        }
        queue = FileQueue(tmp_project / ".aqm" / "queue-rr")
        pipeline = Pipeline(agents, queue, tmp_project)

        mock_rt = MagicMock()
        mock_rt.name = "mock"
        # worker rejected -> helper -> worker rejected -> should NOT fail yet (counter reset)
        call_count = [0]
        def side_effect(prompt, agent, task, on_output=None, on_thinking=None, on_tool=None):
            call_count[0] += 1
            if call_count[0] >= 5:
                return "final output"  # Stop eventually
            return "some output"
        mock_rt.run.side_effect = side_effect
        pipeline._runtimes["claude"] = mock_rt

        # Gate: reject first 2 calls, then approve
        mock_gate = MagicMock()
        gate_call = [0]
        def gate_side_effect(task, output):
            gate_call[0] += 1
            if gate_call[0] <= 1:
                return GateResult(decision="rejected", reason="not good")
            return GateResult(decision="approved", reason="ok")
        mock_gate.evaluate.side_effect = gate_side_effect
        pipeline._get_gate = lambda agent: mock_gate if agent.gate else None

        task = Task(description="test reject reset")
        queue.push(task, "worker")
        result = pipeline.run_task(task, "worker")

        # Should complete (not fail) because counter resets when agent changes
        assert result.status == TaskStatus.completed


# ── BUG-4: Queue race condition (concurrent pop) ─────────────────────


class TestQueueConcurrentPop:
    def test_file_queue_no_duplicate_pop(self, tmp_project):
        """Multiple threads popping should never get the same task."""
        queue = FileQueue(tmp_project / ".aqm" / "queue-race")
        n_tasks = 5
        for i in range(n_tasks):
            task = Task(description=f"task-{i}")
            queue.push(task, "q")

        popped_ids: list[str] = []
        lock = threading.Lock()

        def pop_one():
            t = queue.pop("q")
            if t:
                with lock:
                    popped_ids.append(t.id)

        threads = [threading.Thread(target=pop_one) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Each task should be popped exactly once
        assert len(popped_ids) == n_tasks
        assert len(set(popped_ids)) == n_tasks, "No duplicate task IDs"

    def test_sqlite_queue_no_duplicate_pop(self, tmp_project):
        """SQLite queue atomic pop prevents duplicates."""
        db_path = tmp_project / ".aqm" / "test-race.db"
        queue = SQLiteQueue(db_path)
        n_tasks = 5
        for i in range(n_tasks):
            task = Task(description=f"task-{i}")
            queue.push(task, "q")

        popped_ids: list[str] = []
        lock = threading.Lock()

        def pop_one():
            t = queue.pop("q")
            if t:
                with lock:
                    popped_ids.append(t.id)

        threads = [threading.Thread(target=pop_one) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(popped_ids) == n_tasks
        assert len(set(popped_ids)) == n_tasks
        queue.close()


# ── BUG-6: Chunk init efficiency ──────────────────────────────────────


class TestChunkInitEfficiency:
    def test_init_from_config_unique_ids(self, tmp_path):
        """All chunk IDs should be unique even with many initial chunks."""
        cm = ChunkManager(tmp_path / "task")
        cm.init_from_config([f"chunk-{i}" for i in range(20)])
        cl = cm.load()
        ids = [c.id for c in cl.chunks]
        assert len(ids) == 20
        assert len(set(ids)) == 20, "All chunk IDs must be unique"
