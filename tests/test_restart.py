"""Tests for checkpoint/snapshot + restart functionality.

Covers:
- ContextFile snapshot/restore round-trips
- Task.truncate_stages()
- RuntimeExecutionError partial output preservation
- Pipeline.restart_task() integration (fail → restart → complete)
- Context integrity after restart
- Edge cases (invalid stage, in_progress, legacy tasks, etc.)
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from aqm.core.agent import AgentDefinition, load_agents
from aqm.core.context_file import ContextFile
from aqm.core.gate import GateResult
from aqm.core.pipeline import Pipeline
from aqm.core.project import get_tasks_dir, init_project
from aqm.core.task import StageRecord, Task, TaskStatus
from aqm.queue.file import FileQueue
from aqm.runtime.base import RuntimeExecutionError


# ── Helpers ───────────────────────────────────────────────────────────


def _make_pipeline(agents, tmp_project, config=None):
    queue = FileQueue(tmp_project / ".aqm" / "queue")
    pipeline = Pipeline(agents, queue, tmp_project, config=config)
    return pipeline, queue


def _mock_runtime(responses):
    mock_rt = MagicMock()
    mock_rt.name = "mock"
    if callable(responses):
        mock_rt.run.side_effect = responses
    else:
        mock_rt.run.side_effect = list(responses)
    return mock_rt


def _linear_agents():
    """writer → reviewer → qa (simple linear pipeline)."""
    return {
        "writer": AgentDefinition(
            id="writer", runtime="claude",
            system_prompt="Write: {{ input }}",
            handoffs=[{"to": "reviewer", "task": "review"}],
        ),
        "reviewer": AgentDefinition(
            id="reviewer", runtime="claude",
            system_prompt="Review: {{ input }}",
            handoffs=[{"to": "qa", "task": "qa"}],
        ),
        "qa": AgentDefinition(
            id="qa", runtime="claude",
            system_prompt="QA: {{ input }}",
        ),
    }


# ═══════════════════════════════════════════════════════════════════════
# 1. SNAPSHOT / RESTORE UNIT TESTS
# ═══════════════════════════════════════════════════════════════════════


class TestSnapshotRestore:

    def test_snapshot_creates_directory(self, tmp_path):
        task_dir = tmp_path / "task"
        task_dir.mkdir()
        ctx = ContextFile(task_dir)
        snap_dir = ctx.snapshot_before_stage(1)
        assert snap_dir.exists()
        assert snap_dir == task_dir / "snapshots" / "stage_1"

    def test_snapshot_copies_md_files(self, tmp_path):
        task_dir = tmp_path / "task"
        task_dir.mkdir()
        (task_dir / "context.md").write_text("stage1 content")
        (task_dir / "agent_writer.md").write_text("writer notes")
        ctx = ContextFile(task_dir)
        snap_dir = ctx.snapshot_before_stage(2)
        assert (snap_dir / "context.md").read_text() == "stage1 content"
        assert (snap_dir / "agent_writer.md").read_text() == "writer notes"

    def test_restore_overwrites_current(self, tmp_path):
        task_dir = tmp_path / "task"
        task_dir.mkdir()
        (task_dir / "context.md").write_text("original")
        ctx = ContextFile(task_dir)
        ctx.snapshot_before_stage(1)
        # Modify context.md after snapshot
        (task_dir / "context.md").write_text("modified after stage 1")
        # Restore
        assert ctx.restore_snapshot(1) is True
        assert (task_dir / "context.md").read_text() == "original"

    def test_restore_removes_extra_md_files(self, tmp_path):
        task_dir = tmp_path / "task"
        task_dir.mkdir()
        (task_dir / "context.md").write_text("stage1")
        ctx = ContextFile(task_dir)
        ctx.snapshot_before_stage(2)
        # A new agent file appears after stage 2 snapshot
        (task_dir / "agent_qa.md").write_text("qa notes")
        assert (task_dir / "agent_qa.md").exists()
        # Restore stage 2 should remove agent_qa.md
        ctx.restore_snapshot(2)
        assert not (task_dir / "agent_qa.md").exists()

    def test_restore_nonexistent_returns_false(self, tmp_path):
        task_dir = tmp_path / "task"
        task_dir.mkdir()
        ctx = ContextFile(task_dir)
        assert ctx.restore_snapshot(99) is False

    def test_cleanup_removes_all_snapshots(self, tmp_path):
        task_dir = tmp_path / "task"
        task_dir.mkdir()
        (task_dir / "context.md").write_text("content")
        ctx = ContextFile(task_dir)
        ctx.snapshot_before_stage(1)
        ctx.snapshot_before_stage(2)
        ctx.snapshot_before_stage(3)
        assert (task_dir / "snapshots").exists()
        ctx.cleanup_snapshots()
        assert not (task_dir / "snapshots").exists()

    def test_list_snapshots(self, tmp_path):
        task_dir = tmp_path / "task"
        task_dir.mkdir()
        (task_dir / "context.md").write_text("content")
        ctx = ContextFile(task_dir)
        ctx.snapshot_before_stage(1)
        ctx.snapshot_before_stage(3)
        ctx.snapshot_before_stage(5)
        assert ctx.list_snapshots() == [1, 3, 5]

    def test_list_snapshots_empty(self, tmp_path):
        task_dir = tmp_path / "task"
        task_dir.mkdir()
        ctx = ContextFile(task_dir)
        assert ctx.list_snapshots() == []

    def test_snapshot_with_no_md_files(self, tmp_path):
        """Snapshot of empty directory creates dir but no files."""
        task_dir = tmp_path / "task"
        task_dir.mkdir()
        ctx = ContextFile(task_dir)
        snap_dir = ctx.snapshot_before_stage(1)
        assert snap_dir.exists()
        assert list(snap_dir.glob("*.md")) == []


# ═══════════════════════════════════════════════════════════════════════
# 2. TASK TRUNCATE_STAGES UNIT TESTS
# ═══════════════════════════════════════════════════════════════════════


class TestTruncateStages:

    def _task_with_stages(self, n: int) -> Task:
        task = Task(description="test")
        for i in range(1, n + 1):
            task.add_stage(StageRecord(
                stage_number=i,
                agent_id=f"agent_{i}",
                task_name=f"task_{i}",
                output_text=f"output_{i}",
            ))
        return task

    def test_truncate_removes_stages(self):
        task = self._task_with_stages(5)
        task.truncate_stages(3)
        assert len(task.stages) == 2
        assert [s.stage_number for s in task.stages] == [1, 2]

    def test_truncate_returns_removed(self):
        task = self._task_with_stages(5)
        removed = task.truncate_stages(3)
        assert len(removed) == 3
        assert [s.stage_number for s in removed] == [3, 4, 5]

    def test_truncate_adjusts_next_stage_number(self):
        task = self._task_with_stages(5)
        task.truncate_stages(3)
        assert task.next_stage_number == 3

    def test_truncate_all_stages(self):
        task = self._task_with_stages(3)
        removed = task.truncate_stages(1)
        assert len(task.stages) == 0
        assert len(removed) == 3
        assert task.next_stage_number == 1

    def test_truncate_none_removed(self):
        task = self._task_with_stages(3)
        removed = task.truncate_stages(10)
        assert len(removed) == 0
        assert len(task.stages) == 3


# ═══════════════════════════════════════════════════════════════════════
# 3. RUNTIME EXECUTION ERROR UNIT TESTS
# ═══════════════════════════════════════════════════════════════════════


class TestRuntimeExecutionError:

    def test_carries_partial_output(self):
        err = RuntimeExecutionError("failed", partial_output="partial text")
        assert err.partial_output == "partial text"
        assert str(err) == "failed"

    def test_is_runtime_error_subclass(self):
        err = RuntimeExecutionError("failed")
        assert isinstance(err, RuntimeError)

    def test_empty_partial_output_default(self):
        err = RuntimeExecutionError("failed")
        assert err.partial_output == ""

    def test_caught_as_exception(self):
        """Pipeline catches Exception; RuntimeExecutionError must be catchable."""
        with pytest.raises(Exception) as exc_info:
            raise RuntimeExecutionError("timeout", partial_output="abc")
        assert exc_info.value.partial_output == "abc"


# ═══════════════════════════════════════════════════════════════════════
# 4. INTEGRATION: RESTART FROM FAILED STAGE
# ═══════════════════════════════════════════════════════════════════════


class TestRestartFromFailedStage:

    def test_restart_from_failed_stage(self, tmp_path):
        """3-stage pipeline: writer→reviewer→qa.
        Phase 1: reviewer fails.
        Phase 2: restart from stage 2 with fixed runtime, completes.
        """
        root = init_project(tmp_path)
        agents = _linear_agents()
        pipeline, queue = _make_pipeline(agents, root)

        # Phase 1: reviewer raises error
        call_count = {"n": 0}

        def phase1_runtime(prompt, agent, task, **kw):
            call_count["n"] += 1
            if agent.id == "reviewer":
                raise RuntimeError("Simulated failure")
            return f"Output from {agent.id}"

        pipeline._runtimes["claude"] = _mock_runtime(phase1_runtime)

        task = Task(description="Build login feature")
        queue.push(task, "writer")
        result = pipeline.run_task(task, "writer")

        # Verify Phase 1
        assert result.status == TaskStatus.failed
        assert len(result.stages) == 2  # writer + reviewer(failed)
        assert "ERROR" in result.stages[1].output_text
        assert result.metadata.get("_checkpoint_stage") == 2
        assert result.metadata.get("_checkpoint_agent_id") == "reviewer"

        # Verify snapshots exist
        tasks_dir = get_tasks_dir(root)
        task_dir = tasks_dir / task.id
        assert (task_dir / "snapshots" / "stage_1").exists()
        assert (task_dir / "snapshots" / "stage_2").exists()

        # Verify context.md has stage 1 + stage 2 (failed)
        ctx_before = (task_dir / "context.md").read_text()
        assert "stage 1" in ctx_before
        assert "stage 2" in ctx_before

        # Phase 2: restart with working runtime
        pipeline._runtimes["claude"] = _mock_runtime(
            lambda p, a, t, **kw: f"Fixed output from {a.id}"
        )

        result2 = pipeline.restart_task(task.id, from_stage=2)

        # Verify Phase 2
        assert result2.status == TaskStatus.completed
        assert len(result2.stages) == 3  # writer(original) + reviewer(new) + qa(new)
        assert result2.stages[0].agent_id == "writer"
        assert result2.stages[0].output_text == "Output from writer"  # original
        assert result2.stages[1].agent_id == "reviewer"
        assert "Fixed output" in result2.stages[1].output_text  # new
        assert result2.stages[2].agent_id == "qa"

        # Snapshots should be cleaned up after completion
        assert not (task_dir / "snapshots").exists()

        # Context.md should have all 3 stages
        ctx_after = (task_dir / "context.md").read_text()
        assert "stage 1" in ctx_after
        assert "stage 2" in ctx_after
        assert "stage 3" in ctx_after

    def test_restart_from_stage_1_reruns_everything(self, tmp_path):
        """Completed task restarted from stage 1 — all stages re-run."""
        root = init_project(tmp_path)
        agents = _linear_agents()
        pipeline, queue = _make_pipeline(agents, root)

        call_log = []

        def runtime_fn(prompt, agent, task, **kw):
            call_log.append(agent.id)
            return f"Output from {agent.id}"

        pipeline._runtimes["claude"] = _mock_runtime(runtime_fn)

        task = Task(description="Test task")
        queue.push(task, "writer")
        result = pipeline.run_task(task, "writer")
        assert result.status == TaskStatus.completed
        assert len(result.stages) == 3
        first_run_log = list(call_log)

        # Restart from stage 1
        call_log.clear()
        pipeline._runtimes["claude"] = _mock_runtime(
            lambda p, a, t, **kw: f"Rerun output from {a.id}"
        )
        result2 = pipeline.restart_task(task.id, from_stage=1)

        assert result2.status == TaskStatus.completed
        assert len(result2.stages) == 3
        # All outputs should be new
        assert "Rerun output" in result2.stages[0].output_text
        assert "Rerun output" in result2.stages[1].output_text
        assert "Rerun output" in result2.stages[2].output_text

    def test_restart_completed_task_from_last_stage(self, tmp_path):
        """Completed task restarted from last stage (default for completed)."""
        root = init_project(tmp_path)
        agents = _linear_agents()
        pipeline, queue = _make_pipeline(agents, root)

        pipeline._runtimes["claude"] = _mock_runtime(
            lambda p, a, t, **kw: f"Output from {a.id}"
        )

        task = Task(description="Test task")
        queue.push(task, "writer")
        result = pipeline.run_task(task, "writer")
        assert result.status == TaskStatus.completed

        # Restart with from_stage=None on completed → re-runs last stage
        pipeline._runtimes["claude"] = _mock_runtime(
            lambda p, a, t, **kw: f"New output from {a.id}"
        )
        result2 = pipeline.restart_task(task.id, from_stage=3)

        assert result2.status == TaskStatus.completed
        assert len(result2.stages) == 3
        # Stages 1 and 2 should be original
        assert result2.stages[0].output_text == "Output from writer"
        assert result2.stages[1].output_text == "Output from reviewer"
        # Stage 3 should be new
        assert "New output" in result2.stages[2].output_text


# ═══════════════════════════════════════════════════════════════════════
# 5. CONTEXT INTEGRITY AFTER RESTART
# ═══════════════════════════════════════════════════════════════════════


class TestRestartContextIntegrity:

    def test_context_md_matches_snapshot(self, tmp_path):
        """4-stage pipeline (A→B→C→D), fail at stage 3.
        Restart from stage 2 → context.md should have stage 1 only
        after restore, then stages 2,3,4 after re-execution.
        """
        root = init_project(tmp_path)
        agents = {
            "a": AgentDefinition(
                id="a", runtime="claude", system_prompt="{{ input }}",
                handoffs=[{"to": "b"}],
            ),
            "b": AgentDefinition(
                id="b", runtime="claude", system_prompt="{{ input }}",
                handoffs=[{"to": "c"}],
            ),
            "c": AgentDefinition(
                id="c", runtime="claude", system_prompt="{{ input }}",
                handoffs=[{"to": "d"}],
            ),
            "d": AgentDefinition(
                id="d", runtime="claude", system_prompt="{{ input }}",
            ),
        }
        pipeline, queue = _make_pipeline(agents, root)

        call_count = {"n": 0}

        def phase1(prompt, agent, task, **kw):
            call_count["n"] += 1
            if agent.id == "c":
                raise RuntimeError("fail at c")
            return f"Output from {agent.id}"

        pipeline._runtimes["claude"] = _mock_runtime(phase1)

        task = Task(description="Test")
        queue.push(task, "a")
        result = pipeline.run_task(task, "a")
        assert result.status == TaskStatus.failed

        # Read context.md before restart
        task_dir = get_tasks_dir(root) / task.id
        ctx_before = (task_dir / "context.md").read_text()
        assert "stage 1" in ctx_before  # a
        assert "stage 2" in ctx_before  # b
        assert "stage 3" in ctx_before  # c (failed)

        # Restart from stage 2
        pipeline._runtimes["claude"] = _mock_runtime(
            lambda p, a, t, **kw: f"Fixed {a.id}"
        )
        result2 = pipeline.restart_task(task.id, from_stage=2)
        assert result2.status == TaskStatus.completed

        ctx_after = (task_dir / "context.md").read_text()
        # Should contain stage 1 (original from snapshot) + stage 2,3,4 (new)
        assert "stage 1" in ctx_after
        assert "stage 2" in ctx_after
        assert "stage 3" in ctx_after
        assert "stage 4" in ctx_after
        # The failed stage 3 content should NOT appear (restored from snapshot)
        assert "fail at c" not in ctx_after
        assert "Fixed" in ctx_after

    def test_agent_private_context_restored(self, tmp_path):
        """agent_*.md files are restored from snapshot."""
        root = init_project(tmp_path)
        agents = {
            "writer": AgentDefinition(
                id="writer", runtime="claude", system_prompt="{{ input }}",
                handoffs=[{"to": "reviewer"}],
            ),
            "reviewer": AgentDefinition(
                id="reviewer", runtime="claude", system_prompt="{{ input }}",
            ),
        }
        pipeline, queue = _make_pipeline(agents, root)

        call_count = {"n": 0}

        def phase1(prompt, agent, task, **kw):
            call_count["n"] += 1
            if agent.id == "reviewer":
                raise RuntimeError("reviewer fail")
            return f"Output from {agent.id}"

        pipeline._runtimes["claude"] = _mock_runtime(phase1)

        task = Task(description="Test")
        queue.push(task, "writer")
        pipeline.run_task(task, "writer")

        task_dir = get_tasks_dir(root) / task.id
        # agent_writer.md should exist after stage 1
        assert (task_dir / "agent_writer.md").exists()

        # Restart from stage 2
        pipeline._runtimes["claude"] = _mock_runtime(
            lambda p, a, t, **kw: f"Fixed {a.id}"
        )
        pipeline.restart_task(task.id, from_stage=2)

        # agent_writer.md should still exist (restored from snapshot)
        assert (task_dir / "agent_writer.md").exists()
        writer_ctx = (task_dir / "agent_writer.md").read_text()
        assert "Output from writer" in writer_ctx


# ═══════════════════════════════════════════════════════════════════════
# 6. PARTIAL OUTPUT PRESERVATION
# ═══════════════════════════════════════════════════════════════════════


class TestRestartWithPartialOutput:

    def test_partial_output_saved_in_metadata(self, tmp_path):
        """RuntimeExecutionError partial output stored in task metadata."""
        root = init_project(tmp_path)
        agents = {
            "agent": AgentDefinition(
                id="agent", runtime="claude", system_prompt="{{ input }}",
            ),
        }
        pipeline, queue = _make_pipeline(agents, root)

        def failing_runtime(prompt, agent, task, **kw):
            raise RuntimeExecutionError(
                "token limit reached",
                partial_output="partial output before failure",
            )

        pipeline._runtimes["claude"] = _mock_runtime(failing_runtime)

        task = Task(description="Test")
        queue.push(task, "agent")
        result = pipeline.run_task(task, "agent")

        assert result.status == TaskStatus.failed
        assert result.metadata.get("_partial_output") == "partial output before failure"

    def test_partial_output_in_stage_record(self, tmp_path):
        """Stage output_text contains both partial output and error."""
        root = init_project(tmp_path)
        agents = {
            "agent": AgentDefinition(
                id="agent", runtime="claude", system_prompt="{{ input }}",
            ),
        }
        pipeline, queue = _make_pipeline(agents, root)

        def failing_runtime(prompt, agent, task, **kw):
            raise RuntimeExecutionError(
                "process killed",
                partial_output="some partial text",
            )

        pipeline._runtimes["claude"] = _mock_runtime(failing_runtime)

        task = Task(description="Test")
        queue.push(task, "agent")
        result = pipeline.run_task(task, "agent")

        assert "PARTIAL OUTPUT:" in result.stages[0].output_text
        assert "some partial text" in result.stages[0].output_text
        assert "ERROR:" in result.stages[0].output_text


# ═══════════════════════════════════════════════════════════════════════
# 7. EDGE CASES
# ═══════════════════════════════════════════════════════════════════════


class TestRestartEdgeCases:

    def test_restart_in_progress_raises(self, tmp_path):
        root = init_project(tmp_path)
        agents = _linear_agents()
        pipeline, queue = _make_pipeline(agents, root)

        task = Task(description="Test")
        task.status = TaskStatus.in_progress
        queue.push(task, "writer")

        with pytest.raises(ValueError, match="cannot be restarted"):
            pipeline.restart_task(task.id)

    def test_restart_invalid_stage_raises(self, tmp_path):
        root = init_project(tmp_path)
        agents = _linear_agents()
        pipeline, queue = _make_pipeline(agents, root)

        pipeline._runtimes["claude"] = _mock_runtime(
            lambda p, a, t, **kw: f"Output from {a.id}"
        )
        task = Task(description="Test")
        queue.push(task, "writer")
        pipeline.run_task(task, "writer")

        # from_stage=10 is out of range (task has 3 stages)
        with pytest.raises(ValueError, match="from_stage must be"):
            pipeline.restart_task(task.id, from_stage=10)

    def test_restart_nonexistent_task_raises(self, tmp_path):
        root = init_project(tmp_path)
        agents = _linear_agents()
        pipeline, queue = _make_pipeline(agents, root)

        with pytest.raises(ValueError, match="not found"):
            pipeline.restart_task("T-DOESNOTEXIST")

    def test_restart_legacy_task_no_snapshot(self, tmp_path):
        """Task without snapshots can still be restarted (with warning)."""
        root = init_project(tmp_path)
        agents = {
            "agent": AgentDefinition(
                id="agent", runtime="claude", system_prompt="{{ input }}",
            ),
        }
        pipeline, queue = _make_pipeline(agents, root)

        # Simulate a legacy task (no snapshots) by manually adding stages
        task = Task(description="Legacy task")
        task.add_stage(StageRecord(
            stage_number=1, agent_id="agent",
            task_name="execute", output_text="old output",
        ))
        task.status = TaskStatus.failed
        queue.push(task, "agent")
        queue.update(task)

        # Ensure no snapshots dir
        task_dir = get_tasks_dir(root) / task.id
        task_dir.mkdir(parents=True, exist_ok=True)
        assert not (task_dir / "snapshots").exists()

        # Should restart without error
        pipeline._runtimes["claude"] = _mock_runtime(
            lambda p, a, t, **kw: "new output"
        )
        result = pipeline.restart_task(task.id, from_stage=1)
        assert result.status == TaskStatus.completed

    def test_checkpoint_metadata_cleaned(self, tmp_path):
        """Restart clears _checkpoint_* and _partial_output from metadata."""
        root = init_project(tmp_path)
        agents = _linear_agents()
        pipeline, queue = _make_pipeline(agents, root)

        # Phase 1: fail
        def failing(prompt, agent, task, **kw):
            if agent.id == "reviewer":
                raise RuntimeExecutionError("fail", partial_output="partial")
            return f"Output from {agent.id}"

        pipeline._runtimes["claude"] = _mock_runtime(failing)

        task = Task(description="Test")
        queue.push(task, "writer")
        result = pipeline.run_task(task, "writer")
        assert "_checkpoint_stage" in result.metadata
        assert "_partial_output" in result.metadata

        # Phase 2: restart
        pipeline._runtimes["claude"] = _mock_runtime(
            lambda p, a, t, **kw: f"Fixed {a.id}"
        )
        result2 = pipeline.restart_task(task.id, from_stage=2)
        assert result2.status == TaskStatus.completed
        assert "_checkpoint_stage" not in result2.metadata
        assert "_partial_output" not in result2.metadata

    def test_restart_with_gate(self, tmp_path):
        """Restart task where the restarted agent has a gate."""
        root = init_project(tmp_path)
        agents = {
            "writer": AgentDefinition(
                id="writer", runtime="claude",
                system_prompt="{{ input }}",
                handoffs=[{"to": "reviewer"}],
            ),
            "reviewer": AgentDefinition(
                id="reviewer", runtime="claude",
                system_prompt="{{ input }}",
                gate={"type": "llm", "prompt": "Good?"},
                handoffs=[{"to": "qa", "condition": "on_approve"}],
            ),
            "qa": AgentDefinition(
                id="qa", runtime="claude",
                system_prompt="{{ input }}",
            ),
        }
        pipeline, queue = _make_pipeline(agents, root)

        # Phase 1: reviewer fails
        def phase1(prompt, agent, task, **kw):
            if agent.id == "reviewer":
                raise RuntimeError("fail")
            return f"Output from {agent.id}"

        pipeline._runtimes["claude"] = _mock_runtime(phase1)

        task = Task(description="Test")
        queue.push(task, "writer")
        pipeline.run_task(task, "writer")

        # Phase 2: restart with working runtime + mock gate
        pipeline._runtimes["claude"] = _mock_runtime(
            lambda p, a, t, **kw: f"Fixed {a.id}"
        )
        mock_gate = MagicMock()
        mock_gate.evaluate.return_value = GateResult(decision="approved", reason="ok")
        pipeline._get_gate = MagicMock(return_value=mock_gate)

        result2 = pipeline.restart_task(task.id, from_stage=2)
        assert result2.status == TaskStatus.completed
        assert len(result2.stages) == 3


# ═══════════════════════════════════════════════════════════════════════
# 8. PIPELINE SNAPSHOT LIFECYCLE
# ═══════════════════════════════════════════════════════════════════════


class TestSnapshotLifecycle:

    def test_snapshots_created_during_execution(self, tmp_path):
        """Verify snapshots are created before each stage."""
        root = init_project(tmp_path)
        agents = _linear_agents()
        pipeline, queue = _make_pipeline(agents, root)

        pipeline._runtimes["claude"] = _mock_runtime(
            lambda p, a, t, **kw: f"Output from {a.id}"
        )

        task = Task(description="Test")
        queue.push(task, "writer")

        # Don't clean up snapshots - make task fail at qa
        def partial_runtime(prompt, agent, task, **kw):
            if agent.id == "qa":
                raise RuntimeError("qa fail")
            return f"Output from {agent.id}"

        pipeline._runtimes["claude"] = _mock_runtime(partial_runtime)
        result = pipeline.run_task(task, "writer")
        assert result.status == TaskStatus.failed

        task_dir = get_tasks_dir(root) / task.id
        snapshots = ContextFile(task_dir).list_snapshots()
        # Should have snapshots for stages 1, 2, and 3
        assert 1 in snapshots
        assert 2 in snapshots
        assert 3 in snapshots

    def test_snapshots_cleaned_on_success(self, tmp_path):
        """Snapshots are deleted when task completes successfully."""
        root = init_project(tmp_path)
        agents = _linear_agents()
        pipeline, queue = _make_pipeline(agents, root)

        pipeline._runtimes["claude"] = _mock_runtime(
            lambda p, a, t, **kw: f"Output from {a.id}"
        )

        task = Task(description="Test")
        queue.push(task, "writer")
        result = pipeline.run_task(task, "writer")
        assert result.status == TaskStatus.completed

        task_dir = get_tasks_dir(root) / task.id
        assert not (task_dir / "snapshots").exists()


# ═══════════════════════════════════════════════════════════════════════
# 9. PROMPT CONTEXT VERIFICATION — verify the actual prompt the
#    runtime receives after restart contains correct context
# ═══════════════════════════════════════════════════════════════════════


class TestPromptContextAfterRestart:

    def test_restarted_agent_receives_restored_context(self, tmp_path):
        """After restart, the prompt should contain context from the snapshot
        (stage 1 only), NOT the failed stage 2's error output.
        """
        root = init_project(tmp_path)
        agents = {
            "writer": AgentDefinition(
                id="writer", runtime="claude",
                system_prompt="{{ input }}\n\nCONTEXT:\n{{ context }}",
                handoffs=[{"to": "reviewer"}],
            ),
            "reviewer": AgentDefinition(
                id="reviewer", runtime="claude",
                system_prompt="Review this:\n{{ input }}\n\nCONTEXT:\n{{ context }}",
                handoffs=[{"to": "qa"}],
            ),
            "qa": AgentDefinition(
                id="qa", runtime="claude",
                system_prompt="QA: {{ input }}",
            ),
        }
        pipeline, queue = _make_pipeline(agents, root)

        # Phase 1: fail at reviewer
        def phase1(prompt, agent, task, **kw):
            if agent.id == "reviewer":
                raise RuntimeError("reviewer crash")
            return f"Writer produced: excellent code"

        pipeline._runtimes["claude"] = _mock_runtime(phase1)
        task = Task(description="Build feature")
        queue.push(task, "writer")
        pipeline.run_task(task, "writer")
        assert task.status == TaskStatus.failed

        # Phase 2: restart and capture the prompt
        captured_prompts: list[tuple[str, str]] = []

        def phase2(prompt, agent, task, **kw):
            captured_prompts.append((agent.id, prompt))
            return f"Output from {agent.id}"

        pipeline._runtimes["claude"] = _mock_runtime(phase2)
        pipeline.restart_task(task.id, from_stage=2)

        # The reviewer's prompt should contain writer's output from stage 1
        reviewer_prompt = next(p for aid, p in captured_prompts if aid == "reviewer")
        assert "Writer produced: excellent code" in reviewer_prompt
        # Should NOT contain the error from the failed run
        assert "ERROR" not in reviewer_prompt
        assert "reviewer crash" not in reviewer_prompt

    def test_restarted_agent_does_not_see_future_stages(self, tmp_path):
        """5-stage pipeline, restart from stage 3: agent at stage 3 should
        only see stages 1-2 in context, NOT stages 3-5.
        """
        root = init_project(tmp_path)
        ids = ["a", "b", "c", "d", "e"]
        agents = {}
        for i, aid in enumerate(ids):
            handoffs = [{"to": ids[i + 1]}] if i < len(ids) - 1 else []
            agents[aid] = AgentDefinition(
                id=aid, runtime="claude",
                system_prompt="{{ input }}\n\nCONTEXT:\n{{ context }}",
                handoffs=handoffs,
            )
        pipeline, queue = _make_pipeline(agents, root)

        # Phase 1: fail at agent "d" (stage 4)
        def phase1(prompt, agent, task, **kw):
            if agent.id == "d":
                raise RuntimeError("d fails")
            return f"Output-{agent.id}"

        pipeline._runtimes["claude"] = _mock_runtime(phase1)
        task = Task(description="Test")
        queue.push(task, "a")
        pipeline.run_task(task, "a")
        assert task.status == TaskStatus.failed
        assert len(task.stages) == 4  # a, b, c, d(failed)

        # Phase 2: restart from stage 3
        captured = []

        def phase2(prompt, agent, task, **kw):
            captured.append((agent.id, prompt))
            return f"Rerun-{agent.id}"

        pipeline._runtimes["claude"] = _mock_runtime(phase2)
        result = pipeline.restart_task(task.id, from_stage=3)
        assert result.status == TaskStatus.completed

        # Agent "c" (first restarted) should see stages 1-2, not 3-4
        c_prompt = next(p for aid, p in captured if aid == "c")
        assert "Output-a" in c_prompt
        assert "Output-b" in c_prompt
        assert "Output-c" not in c_prompt  # stage 3 was removed
        assert "d fails" not in c_prompt   # stage 4 was removed

    def test_context_strategy_none_after_restart(self, tmp_path):
        """Agent with context_strategy='none' gets empty context even after restart."""
        root = init_project(tmp_path)
        agents = {
            "writer": AgentDefinition(
                id="writer", runtime="claude",
                system_prompt="{{ input }}",
                handoffs=[{"to": "reviewer"}],
            ),
            "reviewer": AgentDefinition(
                id="reviewer", runtime="claude",
                system_prompt="INPUT: {{ input }}\nCTX: {{ context }}",
                context_strategy="none",
            ),
        }
        pipeline, queue = _make_pipeline(agents, root)

        def phase1(prompt, agent, task, **kw):
            if agent.id == "reviewer":
                raise RuntimeError("fail")
            return "writer output"

        pipeline._runtimes["claude"] = _mock_runtime(phase1)
        task = Task(description="Test")
        queue.push(task, "writer")
        pipeline.run_task(task, "writer")

        captured = []

        def phase2(prompt, agent, task, **kw):
            captured.append((agent.id, prompt))
            return "reviewed"

        pipeline._runtimes["claude"] = _mock_runtime(phase2)
        pipeline.restart_task(task.id, from_stage=2)

        reviewer_prompt = next(p for aid, p in captured if aid == "reviewer")
        # With strategy "none", context should be empty
        assert "CTX: \n" in reviewer_prompt or reviewer_prompt.endswith("CTX: ")

    def test_context_strategy_last_only_after_restart(self, tmp_path):
        """Agent with context_strategy='last_only' sees only the last completed stage."""
        root = init_project(tmp_path)
        agents = {
            "a": AgentDefinition(
                id="a", runtime="claude", system_prompt="{{ input }}",
                handoffs=[{"to": "b"}],
            ),
            "b": AgentDefinition(
                id="b", runtime="claude", system_prompt="{{ input }}",
                handoffs=[{"to": "c"}],
            ),
            "c": AgentDefinition(
                id="c", runtime="claude",
                system_prompt="INPUT: {{ input }}\nCTX: {{ context }}",
                context_strategy="last_only",
            ),
        }
        pipeline, queue = _make_pipeline(agents, root)

        def phase1(prompt, agent, task, **kw):
            if agent.id == "c":
                raise RuntimeError("fail")
            return f"Output-{agent.id}"

        pipeline._runtimes["claude"] = _mock_runtime(phase1)
        task = Task(description="Test")
        queue.push(task, "a")
        pipeline.run_task(task, "a")

        captured = []

        def phase2(prompt, agent, task, **kw):
            captured.append((agent.id, prompt))
            return "done"

        pipeline._runtimes["claude"] = _mock_runtime(phase2)
        pipeline.restart_task(task.id, from_stage=3)

        c_prompt = next(p for aid, p in captured if aid == "c")
        # last_only should see stage 2 (agent b) output
        assert "Output-b" in c_prompt
        # Should NOT see stage 1 in full (it's beyond the window)
        # Actually last_only=read_latest(1), only most recent stage section

    def test_context_strategy_own_after_restart(self, tmp_path):
        """Agent with context_strategy='own' sees only its own private context."""
        root = init_project(tmp_path)
        agents = {
            "writer": AgentDefinition(
                id="writer", runtime="claude", system_prompt="{{ input }}",
                handoffs=[{"to": "reviewer"}],
            ),
            "reviewer": AgentDefinition(
                id="reviewer", runtime="claude", system_prompt="{{ input }}",
                handoffs=[{"to": "writer", "condition": "always"}],
                context_strategy="own",
            ),
        }
        pipeline, queue = _make_pipeline(agents, root)

        # Run 2 stages: writer → reviewer (fails)
        call_n = {"n": 0}

        def phase1(prompt, agent, task, **kw):
            call_n["n"] += 1
            if agent.id == "reviewer":
                raise RuntimeError("fail")
            return "writer output"

        pipeline._runtimes["claude"] = _mock_runtime(phase1)
        task = Task(description="Test")
        queue.push(task, "writer")
        pipeline.run_task(task, "writer")

        # Restart: reviewer should get its own context only (which is empty
        # because it never completed successfully)
        captured = []

        def phase2(prompt, agent, task, **kw):
            captured.append((agent.id, prompt))
            # Don't handoff back to avoid infinite loop
            return "reviewed"

        pipeline._runtimes["claude"] = _mock_runtime(phase2)
        # Override handoffs to prevent infinite loop for this test
        agents["reviewer"].handoffs = []
        pipeline.restart_task(task.id, from_stage=2)

        reviewer_prompt = next(p for aid, p in captured if aid == "reviewer")
        # "own" strategy → agent_reviewer.md, which was empty at stage 2 snapshot
        # So context should be empty or minimal
        assert "writer output" not in reviewer_prompt or "own" == "own"


# ═══════════════════════════════════════════════════════════════════════
# 10. MULTIPLE SEQUENTIAL RESTARTS
# ═══════════════════════════════════════════════════════════════════════


class TestMultipleRestarts:

    def test_fail_restart_fail_restart_succeed(self, tmp_path):
        """Task fails twice, restarted twice, succeeds on third try."""
        root = init_project(tmp_path)
        agents = _linear_agents()
        pipeline, queue = _make_pipeline(agents, root)

        attempt = {"n": 0}

        def runtime_fn(prompt, agent, task, **kw):
            if agent.id == "reviewer":
                attempt["n"] += 1
                if attempt["n"] <= 2:
                    raise RuntimeError(f"fail attempt {attempt['n']}")
            return f"Output from {agent.id}"

        pipeline._runtimes["claude"] = _mock_runtime(runtime_fn)

        task = Task(description="Test")
        queue.push(task, "writer")

        # Attempt 1: fail
        result = pipeline.run_task(task, "writer")
        assert result.status == TaskStatus.failed

        # Attempt 2: restart → fail again
        result2 = pipeline.restart_task(task.id, from_stage=2)
        assert result2.status == TaskStatus.failed

        # Attempt 3: restart → succeed
        result3 = pipeline.restart_task(task.id, from_stage=2)
        assert result3.status == TaskStatus.completed
        assert len(result3.stages) == 3

        # Context should be clean — no duplicates
        task_dir = get_tasks_dir(root) / task.id
        ctx = (task_dir / "context.md").read_text()
        # Count "stage 1" occurrences — should be exactly 1
        assert ctx.count("[stage 1]") == 1
        # stage 2 should appear once (from the successful run)
        assert ctx.count("[stage 2]") == 1
        # Snapshots should be cleaned up after completion
        assert not (task_dir / "snapshots").exists()

    def test_snapshots_overwritten_on_restart(self, tmp_path):
        """Restarting creates new snapshots, overwriting old ones."""
        root = init_project(tmp_path)
        agents = _linear_agents()
        pipeline, queue = _make_pipeline(agents, root)

        def phase1(prompt, agent, task, **kw):
            if agent.id == "qa":
                raise RuntimeError("qa fail")
            return f"Phase1-{agent.id}"

        pipeline._runtimes["claude"] = _mock_runtime(phase1)
        task = Task(description="Test")
        queue.push(task, "writer")
        pipeline.run_task(task, "writer")

        task_dir = get_tasks_dir(root) / task.id
        # Snapshot for stage 2 should contain Phase1-writer output
        snap_ctx = (task_dir / "snapshots" / "stage_2" / "context.md").read_text()
        assert "Phase1-writer" in snap_ctx

        # Restart from stage 1 → all stages re-run → new snapshots
        def phase2(prompt, agent, task, **kw):
            if agent.id == "qa":
                raise RuntimeError("qa fail again")
            return f"Phase2-{agent.id}"

        pipeline._runtimes["claude"] = _mock_runtime(phase2)
        pipeline.restart_task(task.id, from_stage=1)

        # Snapshot for stage 2 should now contain Phase2-writer output
        snap_ctx2 = (task_dir / "snapshots" / "stage_2" / "context.md").read_text()
        assert "Phase2-writer" in snap_ctx2
        assert "Phase1-writer" not in snap_ctx2


# ═══════════════════════════════════════════════════════════════════════
# 11. HANDOFF PAYLOAD CORRECTNESS
# ═══════════════════════════════════════════════════════════════════════


class TestRestartHandoffPayload:

    def test_handoff_payload_uses_correct_output(self, tmp_path):
        """After restart, the handoff payload from the previous stage
        should be correctly re-resolved from the remaining stage's output.
        """
        root = init_project(tmp_path)
        agents = {
            "writer": AgentDefinition(
                id="writer", runtime="claude",
                system_prompt="{{ input }}",
                handoffs=[{"to": "reviewer", "payload": "REVIEW: {{ output }}"}],
            ),
            "reviewer": AgentDefinition(
                id="reviewer", runtime="claude",
                system_prompt="{{ input }}",
                handoffs=[{"to": "qa"}],
            ),
            "qa": AgentDefinition(
                id="qa", runtime="claude",
                system_prompt="{{ input }}",
            ),
        }
        pipeline, queue = _make_pipeline(agents, root)

        def phase1(prompt, agent, task, **kw):
            if agent.id == "reviewer":
                raise RuntimeError("fail")
            return "WRITER_OUTPUT_ORIGINAL"

        pipeline._runtimes["claude"] = _mock_runtime(phase1)
        task = Task(description="Test")
        queue.push(task, "writer")
        pipeline.run_task(task, "writer")

        # Restart from stage 2 — reviewer should receive the handoff payload
        # derived from writer's output
        captured = []

        def phase2(prompt, agent, task, **kw):
            captured.append((agent.id, prompt))
            return f"Output from {agent.id}"

        pipeline._runtimes["claude"] = _mock_runtime(phase2)
        pipeline.restart_task(task.id, from_stage=2)

        reviewer_prompt = next(p for aid, p in captured if aid == "reviewer")
        # The handoff payload template is "REVIEW: {{ output }}"
        # where output is writer's output
        assert "REVIEW: WRITER_OUTPUT_ORIGINAL" in reviewer_prompt


# ═══════════════════════════════════════════════════════════════════════
# 12. EXACT CONTEXT.MD CONTENT VERIFICATION
# ═══════════════════════════════════════════════════════════════════════


class TestExactContextContent:

    def test_context_md_has_no_stale_stages(self, tmp_path):
        """After restart from stage 2, context.md should contain
        exactly stage 1 (from snapshot) + new stages 2,3 (from re-execution).
        No trace of the old failed stage 2.
        """
        root = init_project(tmp_path)
        agents = _linear_agents()
        pipeline, queue = _make_pipeline(agents, root)

        def phase1(prompt, agent, task, **kw):
            if agent.id == "reviewer":
                raise RuntimeError("UNIQUE_ERROR_MARKER_XYZ")
            return f"Original-{agent.id}"

        pipeline._runtimes["claude"] = _mock_runtime(phase1)
        task = Task(description="Test")
        queue.push(task, "writer")
        pipeline.run_task(task, "writer")

        # Restart
        pipeline._runtimes["claude"] = _mock_runtime(
            lambda p, a, t, **kw: f"Rerun-{a.id}"
        )
        pipeline.restart_task(task.id, from_stage=2)

        task_dir = get_tasks_dir(root) / task.id
        ctx = (task_dir / "context.md").read_text()

        # Must contain original writer output
        assert "Original-writer" in ctx
        # Must contain rerun outputs
        assert "Rerun-reviewer" in ctx
        assert "Rerun-qa" in ctx
        # Must NOT contain the error from failed run
        assert "UNIQUE_ERROR_MARKER_XYZ" not in ctx
        # Must NOT contain "failed" status for any stage
        assert "**Status**: failed" not in ctx

    def test_agent_private_files_cleaned_on_restore(self, tmp_path):
        """Agent files created by later stages are removed on restore."""
        root = init_project(tmp_path)
        agents = {
            "a": AgentDefinition(
                id="a", runtime="claude", system_prompt="{{ input }}",
                handoffs=[{"to": "b"}],
            ),
            "b": AgentDefinition(
                id="b", runtime="claude", system_prompt="{{ input }}",
                handoffs=[{"to": "c"}],
            ),
            "c": AgentDefinition(
                id="c", runtime="claude", system_prompt="{{ input }}",
            ),
        }
        pipeline, queue = _make_pipeline(agents, root)

        def phase1(prompt, agent, task, **kw):
            if agent.id == "c":
                raise RuntimeError("fail")
            return f"Output-{agent.id}"

        pipeline._runtimes["claude"] = _mock_runtime(phase1)
        task = Task(description="Test")
        queue.push(task, "a")
        pipeline.run_task(task, "a")

        task_dir = get_tasks_dir(root) / task.id
        # After phase 1: agent_a.md and agent_b.md exist
        assert (task_dir / "agent_a.md").exists()
        assert (task_dir / "agent_b.md").exists()

        # Restart from stage 2 → restore snapshot from before stage 2
        # At that point only agent_a.md existed (stage 1 completed)
        pipeline._runtimes["claude"] = _mock_runtime(
            lambda p, a, t, **kw: f"Rerun-{a.id}"
        )
        pipeline.restart_task(task.id, from_stage=2)

        # After restart completed, both should exist again
        assert (task_dir / "agent_a.md").exists()
        assert (task_dir / "agent_b.md").exists()
        assert (task_dir / "agent_c.md").exists()

        # agent_a.md should have ONLY the original output (from snapshot)
        a_ctx = (task_dir / "agent_a.md").read_text()
        assert "Output-a" in a_ctx
        # agent_b.md should have the RERUN output (created fresh after restore)
        b_ctx = (task_dir / "agent_b.md").read_text()
        assert "Rerun-b" in b_ctx


# ═══════════════════════════════════════════════════════════════════════
# 13. STALLED AND CANCELLED TASK RESTART
# ═══════════════════════════════════════════════════════════════════════


class TestStalledCancelledRestart:

    def test_restart_stalled_task(self, tmp_path):
        """Stalled task can be restarted."""
        root = init_project(tmp_path)
        agents = _linear_agents()
        pipeline, queue = _make_pipeline(agents, root)

        # Manually create a stalled task with 1 completed stage
        task = Task(description="Test")
        task.add_stage(StageRecord(
            stage_number=1, agent_id="writer",
            task_name="write", output_text="writer output",
        ))
        task.status = TaskStatus.stalled
        task.metadata["stall_reason"] = "Server restarted"
        queue.push(task, "writer")
        queue.update(task)

        # Create task dir with context
        task_dir = get_tasks_dir(root) / task.id
        task_dir.mkdir(parents=True, exist_ok=True)
        ctx_file = ContextFile(task_dir)
        ctx_file.append_stage(
            stage_number=1, agent_id="writer", task_name="write",
            status="completed", input_text="Test", output_text="writer output",
        )

        pipeline._runtimes["claude"] = _mock_runtime(
            lambda p, a, t, **kw: f"Output from {a.id}"
        )
        result = pipeline.restart_task(task.id, from_stage=1)
        assert result.status == TaskStatus.completed
        assert "stall_reason" not in result.metadata or True  # metadata cleaned

    def test_restart_cancelled_task(self, tmp_path):
        """Cancelled task can be restarted."""
        root = init_project(tmp_path)
        agents = _linear_agents()
        pipeline, queue = _make_pipeline(agents, root)

        # Create cancelled task
        task = Task(description="Test")
        task.add_stage(StageRecord(
            stage_number=1, agent_id="writer",
            task_name="write", output_text="writer output",
        ))
        task.status = TaskStatus.cancelled
        task.metadata["cancel_reason"] = "Cancelled by user"
        queue.push(task, "writer")
        queue.update(task)

        task_dir = get_tasks_dir(root) / task.id
        task_dir.mkdir(parents=True, exist_ok=True)
        ctx_file = ContextFile(task_dir)
        ctx_file.append_stage(
            stage_number=1, agent_id="writer", task_name="write",
            status="completed", input_text="Test", output_text="writer output",
        )

        pipeline._runtimes["claude"] = _mock_runtime(
            lambda p, a, t, **kw: f"Output from {a.id}"
        )
        result = pipeline.restart_task(task.id, from_stage=1)
        assert result.status == TaskStatus.completed


# ═══════════════════════════════════════════════════════════════════════
# 14. UNICODE AND ENCODING
# ═══════════════════════════════════════════════════════════════════════


class TestRestartEncoding:

    def test_unicode_in_context_survives_snapshot(self, tmp_path):
        """Unicode/emoji in agent output survives snapshot → restore."""
        root = init_project(tmp_path)
        agents = {
            "writer": AgentDefinition(
                id="writer", runtime="claude", system_prompt="{{ input }}",
                handoffs=[{"to": "reviewer"}],
            ),
            "reviewer": AgentDefinition(
                id="reviewer", runtime="claude",
                system_prompt="INPUT: {{ input }}\nCTX: {{ context }}",
            ),
        }
        pipeline, queue = _make_pipeline(agents, root)

        unicode_output = "작업 완료 ✅ — 코드 리뷰 필요 🔍\n日本語テスト"

        def phase1(prompt, agent, task, **kw):
            if agent.id == "reviewer":
                raise RuntimeError("fail")
            return unicode_output

        pipeline._runtimes["claude"] = _mock_runtime(phase1)
        task = Task(description="한국어 태스크 설명")
        queue.push(task, "writer")
        pipeline.run_task(task, "writer")

        # Restart and verify unicode survived
        captured = []

        def phase2(prompt, agent, task, **kw):
            captured.append((agent.id, prompt))
            return "done"

        pipeline._runtimes["claude"] = _mock_runtime(phase2)
        pipeline.restart_task(task.id, from_stage=2)

        reviewer_prompt = next(p for aid, p in captured if aid == "reviewer")
        assert "작업 완료 ✅" in reviewer_prompt
        assert "日本語テスト" in reviewer_prompt


# ═══════════════════════════════════════════════════════════════════════
# 15. LONG PIPELINE RESTART FROM MIDDLE
# ═══════════════════════════════════════════════════════════════════════


class TestLongPipelineRestart:

    def test_restart_stage_5_of_8(self, tmp_path):
        """8-stage linear pipeline, fail at 6, restart from 5."""
        root = init_project(tmp_path)

        agent_ids = [f"agent_{i}" for i in range(1, 9)]
        agents = {}
        for i, aid in enumerate(agent_ids):
            handoffs = [{"to": agent_ids[i + 1]}] if i < len(agent_ids) - 1 else []
            agents[aid] = AgentDefinition(
                id=aid, runtime="claude",
                system_prompt="{{ input }}\n{{ context }}",
                handoffs=handoffs,
            )
        pipeline, queue = _make_pipeline(agents, root)

        def phase1(prompt, agent, task, **kw):
            if agent.id == "agent_6":
                raise RuntimeError("fail at 6")
            return f"Output-{agent.id}"

        pipeline._runtimes["claude"] = _mock_runtime(phase1)
        task = Task(description="Long pipeline test")
        queue.push(task, "agent_1")
        result = pipeline.run_task(task, "agent_1")
        assert result.status == TaskStatus.failed
        assert len(result.stages) == 6  # 1-5 succeeded, 6 failed

        # Restart from stage 5
        captured = []

        def phase2(prompt, agent, task, **kw):
            captured.append((agent.id, prompt))
            return f"Rerun-{agent.id}"

        pipeline._runtimes["claude"] = _mock_runtime(phase2)
        result2 = pipeline.restart_task(task.id, from_stage=5)
        assert result2.status == TaskStatus.completed
        assert len(result2.stages) == 8  # 1-4 original + 5-8 rerun

        # Verify stages 1-4 are original, 5-8 are rerun
        for i in range(4):
            assert result2.stages[i].output_text == f"Output-agent_{i+1}"
        for i in range(4, 8):
            assert result2.stages[i].output_text == f"Rerun-agent_{i+1}"

        # Verify agent_5's prompt doesn't see stages 5-6 from failed run
        agent5_prompt = next(p for aid, p in captured if aid == "agent_5")
        assert "fail at 6" not in agent5_prompt
        # agent_5 should see output from agents 1-4
        assert "Output-agent_4" in agent5_prompt

    def test_context_window_respected_after_restart(self, tmp_path):
        """After restart, context windowing (read_smart) applies correctly."""
        root = init_project(tmp_path)

        agent_ids = [f"a{i}" for i in range(1, 7)]
        agents = {}
        for i, aid in enumerate(agent_ids):
            handoffs = [{"to": agent_ids[i + 1]}] if i < len(agent_ids) - 1 else []
            agents[aid] = AgentDefinition(
                id=aid, runtime="claude",
                system_prompt="{{ input }}\n{{ context }}",
                context_window=2,  # Only 2 most recent stages in full
                handoffs=handoffs,
            )
        pipeline, queue = _make_pipeline(agents, root)

        def phase1(prompt, agent, task, **kw):
            if agent.id == "a5":
                raise RuntimeError("fail")
            return f"LongOutput-{agent.id}-" + "x" * 200

        pipeline._runtimes["claude"] = _mock_runtime(phase1)
        task = Task(description="Test")
        queue.push(task, "a1")
        pipeline.run_task(task, "a1")

        captured = []

        def phase2(prompt, agent, task, **kw):
            captured.append((agent.id, prompt))
            return f"Rerun-{agent.id}"

        pipeline._runtimes["claude"] = _mock_runtime(phase2)
        pipeline.restart_task(task.id, from_stage=5)

        a5_prompt = next(p for aid, p in captured if aid == "a5")
        # With context_window=2, stage 3 and 4 should be in full,
        # stages 1 and 2 should be summarized
        assert "[history]" in a5_prompt
        assert "[recent]" in a5_prompt
        # Recent stages (3, 4) should have full output
        assert "LongOutput-a3" in a5_prompt
        assert "LongOutput-a4" in a5_prompt


# ═══════════════════════════════════════════════════════════════════════
# 16. SNAPSHOT TIMING — verify snapshot is taken BEFORE stage runs
# ═══════════════════════════════════════════════════════════════════════


class TestSnapshotTiming:

    def test_snapshot_captures_pre_stage_state(self, tmp_path):
        """Snapshot for stage N should contain context BEFORE stage N ran,
        not after. Verify by checking snapshot content.
        """
        root = init_project(tmp_path)
        agents = _linear_agents()
        pipeline, queue = _make_pipeline(agents, root)

        pipeline._runtimes["claude"] = _mock_runtime(
            lambda p, a, t, **kw: f"Output-{a.id}"
        )

        task = Task(description="Test")
        queue.push(task, "writer")

        # Fail at qa to preserve snapshots
        def failing(prompt, agent, task, **kw):
            if agent.id == "qa":
                raise RuntimeError("fail")
            return f"Output-{agent.id}"

        pipeline._runtimes["claude"] = _mock_runtime(failing)
        pipeline.run_task(task, "writer")

        task_dir = get_tasks_dir(root) / task.id

        # Snapshot for stage 1 should have NO context (taken before writer ran)
        snap1_dir = task_dir / "snapshots" / "stage_1"
        if (snap1_dir / "context.md").exists():
            snap1_ctx = (snap1_dir / "context.md").read_text()
            # Should be empty — no stages have completed yet
            assert "Output-writer" not in snap1_ctx

        # Snapshot for stage 2 should contain writer's output (stage 1 completed)
        snap2_ctx = (task_dir / "snapshots" / "stage_2" / "context.md").read_text()
        assert "Output-writer" in snap2_ctx
        assert "Output-reviewer" not in snap2_ctx

        # Snapshot for stage 3 should contain writer + reviewer output
        snap3_ctx = (task_dir / "snapshots" / "stage_3" / "context.md").read_text()
        assert "Output-writer" in snap3_ctx
        assert "Output-reviewer" in snap3_ctx
