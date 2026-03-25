"""Tests for context_strategy: per-agent context files and smart context selection."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from aqm.core.agent import AgentDefinition, ConsensusConfig, load_agents
from aqm.core.context_file import ContextFile
from aqm.core.pipeline import Pipeline
from aqm.core.task import Task, TaskStatus
from aqm.queue.file import FileQueue


# ── ContextFile per-agent methods ─────────────────────────────────────


class TestPerAgentContext:
    def test_agent_context_path(self, tmp_path):
        cf = ContextFile(tmp_path / "task-1")
        assert cf.agent_context_path("architect").name == "agent_architect.md"

    def test_read_agent_context_empty(self, tmp_path):
        cf = ContextFile(tmp_path / "task-1")
        assert cf.read_agent_context("nonexistent") == ""

    def test_append_and_read_agent_context(self, tmp_path):
        cf = ContextFile(tmp_path / "task-1")
        cf.append_agent_context(
            agent_id="dev",
            stage_number=1,
            input_text="build login",
            output_text="implemented auth flow",
        )
        content = cf.read_agent_context("dev")
        assert "dev" in content
        assert "implemented auth flow" in content
        assert "build login" in content

    def test_append_multiple_stages(self, tmp_path):
        cf = ContextFile(tmp_path / "task-1")
        cf.append_agent_context(
            agent_id="dev", stage_number=1,
            input_text="in1", output_text="out1",
        )
        cf.append_agent_context(
            agent_id="dev", stage_number=3,
            input_text="in2", output_text="out2",
        )
        content = cf.read_agent_context("dev")
        assert "stage 1" in content
        assert "stage 3" in content
        assert "out1" in content
        assert "out2" in content

    def test_separate_agents_separate_files(self, tmp_path):
        cf = ContextFile(tmp_path / "task-1")
        cf.append_agent_context(
            agent_id="dev", stage_number=1,
            input_text="dev task", output_text="dev output",
        )
        cf.append_agent_context(
            agent_id="reviewer", stage_number=2,
            input_text="review task", output_text="review output",
        )
        dev_ctx = cf.read_agent_context("dev")
        rev_ctx = cf.read_agent_context("reviewer")

        assert "dev output" in dev_ctx
        assert "review output" not in dev_ctx
        assert "review output" in rev_ctx
        assert "dev output" not in rev_ctx


# ── read_for_strategy ─────────────────────────────────────────────────


class TestReadForStrategy:
    def test_strategy_shared(self, tmp_path):
        cf = ContextFile(tmp_path / "task-1")
        cf.append_stage(
            stage_number=1, agent_id="planner", task_name="plan",
            status="completed", input_text="in", output_text="shared output",
        )
        cf.append_agent_context(
            agent_id="planner", stage_number=1,
            input_text="in", output_text="private notes",
        )
        result = cf.read_for_strategy("planner", "shared")
        assert "shared output" in result
        assert "private notes" not in result

    def test_strategy_own(self, tmp_path):
        cf = ContextFile(tmp_path / "task-1")
        cf.append_stage(
            stage_number=1, agent_id="planner", task_name="plan",
            status="completed", input_text="in", output_text="shared output",
        )
        cf.append_agent_context(
            agent_id="planner", stage_number=1,
            input_text="in", output_text="private notes",
        )
        result = cf.read_for_strategy("planner", "own")
        assert "private notes" in result
        assert "shared output" not in result

    def test_strategy_both(self, tmp_path):
        cf = ContextFile(tmp_path / "task-1")
        cf.append_stage(
            stage_number=1, agent_id="planner", task_name="plan",
            status="completed", input_text="in", output_text="shared output",
        )
        cf.append_agent_context(
            agent_id="planner", stage_number=1,
            input_text="in", output_text="private notes",
        )
        result = cf.read_for_strategy("planner", "both")
        assert "shared output" in result
        assert "private notes" in result
        assert "Agent Notes (planner)" in result

    def test_strategy_both_no_agent_file(self, tmp_path):
        """If agent has no private file, 'both' returns shared only."""
        cf = ContextFile(tmp_path / "task-1")
        cf.append_stage(
            stage_number=1, agent_id="planner", task_name="plan",
            status="completed", input_text="in", output_text="shared output",
        )
        result = cf.read_for_strategy("planner", "both")
        assert "shared output" in result
        assert "Agent Notes" not in result

    def test_strategy_own_empty(self, tmp_path):
        cf = ContextFile(tmp_path / "task-1")
        result = cf.read_for_strategy("nonexistent", "own")
        assert result == ""


# ── AgentDefinition defaults ──────────────────────────────────────────


class TestAgentContextStrategy:
    def test_default_is_both(self):
        a = AgentDefinition(id="test", runtime="claude")
        assert a.context_strategy == "both"

    def test_set_own(self):
        a = AgentDefinition(id="test", runtime="claude", context_strategy="own")
        assert a.context_strategy == "own"

    def test_set_shared(self):
        a = AgentDefinition(id="test", runtime="claude", context_strategy="shared")
        assert a.context_strategy == "shared"


# ── YAML loading ──────────────────────────────────────────────────────


class TestContextStrategyYAML:
    def test_load_with_strategy(self, tmp_project):
        yaml_content = {
            "agents": [
                {
                    "id": "dev",
                    "runtime": "claude",
                    "context_strategy": "own",
                    "system_prompt": "{{ input }}",
                },
            ]
        }
        yaml_path = tmp_project / ".aqm" / "agents.yaml"
        yaml_path.write_text(yaml.dump(yaml_content), encoding="utf-8")
        agents = load_agents(yaml_path)
        assert agents["dev"].context_strategy == "own"

    def test_load_without_strategy_defaults(self, tmp_project):
        yaml_content = {
            "agents": [
                {
                    "id": "dev",
                    "runtime": "claude",
                    "system_prompt": "{{ input }}",
                },
            ]
        }
        yaml_path = tmp_project / ".aqm" / "agents.yaml"
        yaml_path.write_text(yaml.dump(yaml_content), encoding="utf-8")
        agents = load_agents(yaml_path)
        assert agents["dev"].context_strategy == "both"


# ── Pipeline integration ──────────────────────────────────────────────


class TestPipelineContextStrategy:
    def test_pipeline_uses_strategy_in_prompt(self, tmp_project):
        """Agent with context_strategy='own' should only see its own notes."""
        agents = {
            "first": AgentDefinition(
                id="first", runtime="claude",
                system_prompt="{{ input }}",
                handoffs=[{"to": "second"}],
            ),
            "second": AgentDefinition(
                id="second", runtime="claude",
                context_strategy="own",
                system_prompt="{{ context }}",
            ),
        }
        queue = FileQueue(tmp_project / ".aqm" / "file-queue")
        pipeline = Pipeline(agents, queue, tmp_project)

        mock_rt = MagicMock()
        mock_rt.name = "mock"
        mock_rt.run.side_effect = [
            "first agent shared output",  # first agent
            "second agent output",         # second agent
        ]
        pipeline._runtimes["claude_text"] = mock_rt

        task = Task(description="Test strategy")
        queue.push(task, "first")
        pipeline.run_task(task, "first")

        # second agent's prompt (call_args_list[1]) should NOT contain
        # first agent's output (since strategy='own' and second has no prior notes)
        second_prompt = mock_rt.run.call_args_list[1][0][0]
        assert "first agent shared output" not in second_prompt

    def test_pipeline_both_strategy_sees_all(self, tmp_project):
        """Agent with context_strategy='both' sees shared + own."""
        agents = {
            "first": AgentDefinition(
                id="first", runtime="claude",
                system_prompt="{{ input }}",
                handoffs=[{"to": "second"}],
            ),
            "second": AgentDefinition(
                id="second", runtime="claude",
                context_strategy="both",
                system_prompt="{{ context }}",
            ),
        }
        queue = FileQueue(tmp_project / ".aqm" / "file-queue")
        pipeline = Pipeline(agents, queue, tmp_project)

        mock_rt = MagicMock()
        mock_rt.name = "mock"
        mock_rt.run.side_effect = [
            "first agent output",
            "second agent output",
        ]
        pipeline._runtimes["claude_text"] = mock_rt

        task = Task(description="Test both")
        queue.push(task, "first")
        pipeline.run_task(task, "first")

        second_prompt = mock_rt.run.call_args_list[1][0][0]
        assert "first agent output" in second_prompt

    def test_session_creates_agent_files(self, tmp_project):
        """Session turns write to per-agent context files."""
        agents = {
            "a": AgentDefinition(
                id="a", runtime="claude",
                context_strategy="own",
                system_prompt="{{ input }} {{ transcript }}",
            ),
            "b": AgentDefinition(
                id="b", runtime="claude",
                context_strategy="own",
                system_prompt="{{ input }} {{ transcript }}",
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
            "Agent A round 1 thoughts. VOTE: AGREE",
            "Agent B round 1 analysis. VOTE: AGREE",
        ]
        pipeline._runtimes["claude_text"] = mock_rt

        task = Task(description="Session test")
        queue.push(task, "session")
        pipeline.run_task(task, "session")

        # Check per-agent files were created
        from aqm.core.context_file import ContextFile
        from aqm.core.project import get_tasks_dir

        tasks_dir = get_tasks_dir(tmp_project)
        cf = ContextFile(tasks_dir / task.id)

        a_ctx = cf.read_agent_context("a")
        b_ctx = cf.read_agent_context("b")

        assert "Agent A round 1" in a_ctx
        assert "Agent B round 1" not in a_ctx
        assert "Agent B round 1" in b_ctx
        assert "Agent A round 1" not in b_ctx

    def test_backward_compatible_no_strategy(self, tmp_project):
        """Existing pipelines without context_strategy work as before."""
        agents = {
            "first": AgentDefinition(
                id="first", runtime="claude",
                system_prompt="{{ input }}",
                handoffs=[{"to": "second"}],
            ),
            "second": AgentDefinition(
                id="second", runtime="claude",
                system_prompt="{{ context }}",
                # No context_strategy set → defaults to "both"
            ),
        }
        queue = FileQueue(tmp_project / ".aqm" / "file-queue")
        pipeline = Pipeline(agents, queue, tmp_project)

        mock_rt = MagicMock()
        mock_rt.name = "mock"
        mock_rt.run.side_effect = ["first output", "second output"]
        pipeline._runtimes["claude_text"] = mock_rt

        task = Task(description="Backward compat")
        queue.push(task, "first")
        pipeline.run_task(task, "first")

        second_prompt = mock_rt.run.call_args_list[1][0][0]
        assert "first output" in second_prompt
