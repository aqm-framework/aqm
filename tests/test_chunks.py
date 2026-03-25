"""Tests for chunk decomposition feature."""

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
from aqm.core.chunks import (
    Chunk,
    ChunkList,
    ChunkManager,
    ChunkStatus,
    parse_chunk_directives,
)
from aqm.core.pipeline import Pipeline
from aqm.core.task import Task, TaskStatus
from aqm.queue.file import FileQueue


# ── Chunk model ───────────────────────────────────────────────────────


class TestChunkModel:
    def test_default_status(self):
        c = Chunk(id="C-001", description="Setup project")
        assert c.status == ChunkStatus.pending
        assert c.created_by == ""

    def test_chunk_list_empty(self):
        cl = ChunkList()
        assert cl.chunks == []

    def test_chunk_serialization(self):
        c = Chunk(id="C-001", description="Test", created_by="user")
        data = c.model_dump(mode="json")
        restored = Chunk.model_validate(data)
        assert restored.id == "C-001"
        assert restored.description == "Test"


# ── ChunkManager ─────────────────────────────────────────────────────


class TestChunkManager:
    def test_load_empty(self, tmp_path):
        mgr = ChunkManager(tmp_path / "task-1")
        cl = mgr.load()
        assert cl.chunks == []

    def test_add_and_load(self, tmp_path):
        mgr = ChunkManager(tmp_path / "task-1")
        chunk = mgr.add("Setup project", created_by="user")
        assert chunk.id == "C-001"
        assert chunk.description == "Setup project"

        cl = mgr.load()
        assert len(cl.chunks) == 1
        assert cl.chunks[0].id == "C-001"

    def test_add_multiple_sequential_ids(self, tmp_path):
        mgr = ChunkManager(tmp_path / "task-1")
        c1 = mgr.add("First")
        c2 = mgr.add("Second")
        c3 = mgr.add("Third")
        assert c1.id == "C-001"
        assert c2.id == "C-002"
        assert c3.id == "C-003"

    def test_remove(self, tmp_path):
        mgr = ChunkManager(tmp_path / "task-1")
        mgr.add("First")
        mgr.add("Second")
        assert mgr.remove("C-001") is True
        cl = mgr.load()
        assert len(cl.chunks) == 1
        assert cl.chunks[0].id == "C-002"

    def test_remove_nonexistent(self, tmp_path):
        mgr = ChunkManager(tmp_path / "task-1")
        assert mgr.remove("C-999") is False

    def test_mark_done(self, tmp_path):
        mgr = ChunkManager(tmp_path / "task-1")
        mgr.add("Task")
        assert mgr.mark_done("C-001", completed_by="arch") is True
        cl = mgr.load()
        assert cl.chunks[0].status == ChunkStatus.done
        assert cl.chunks[0].completed_by == "arch"

    def test_mark_done_nonexistent(self, tmp_path):
        mgr = ChunkManager(tmp_path / "task-1")
        assert mgr.mark_done("C-999") is False

    def test_mark_in_progress(self, tmp_path):
        mgr = ChunkManager(tmp_path / "task-1")
        mgr.add("Task")
        assert mgr.mark_in_progress("C-001") is True
        cl = mgr.load()
        assert cl.chunks[0].status == ChunkStatus.in_progress

    def test_all_done_empty(self, tmp_path):
        mgr = ChunkManager(tmp_path / "task-1")
        assert mgr.all_done() is True

    def test_all_done_false(self, tmp_path):
        mgr = ChunkManager(tmp_path / "task-1")
        mgr.add("A")
        mgr.add("B")
        mgr.mark_done("C-001")
        assert mgr.all_done() is False

    def test_all_done_true(self, tmp_path):
        mgr = ChunkManager(tmp_path / "task-1")
        mgr.add("A")
        mgr.add("B")
        mgr.mark_done("C-001")
        mgr.mark_done("C-002")
        assert mgr.all_done() is True

    def test_counts(self, tmp_path):
        mgr = ChunkManager(tmp_path / "task-1")
        mgr.add("A")
        mgr.add("B")
        mgr.add("C")
        mgr.mark_done("C-001")
        total, done, pending = mgr.counts()
        assert total == 3
        assert done == 1
        assert pending == 2

    def test_summary_empty(self, tmp_path):
        mgr = ChunkManager(tmp_path / "task-1")
        assert "(no chunks defined)" in mgr.summary()

    def test_summary_with_chunks(self, tmp_path):
        mgr = ChunkManager(tmp_path / "task-1")
        mgr.add("Setup")
        mgr.add("Implement")
        mgr.mark_done("C-001")
        summary = mgr.summary()
        assert "C-001" in summary
        assert "C-002" in summary
        assert "done" in summary
        assert "pending" in summary
        assert "1/2 done" in summary

    def test_init_from_config(self, tmp_path):
        mgr = ChunkManager(tmp_path / "task-1")
        mgr.init_from_config(["Setup", "Build", "Test"])
        cl = mgr.load()
        assert len(cl.chunks) == 3
        assert cl.chunks[0].description == "Setup"
        assert cl.chunks[2].id == "C-003"

    def test_init_from_config_idempotent(self, tmp_path):
        mgr = ChunkManager(tmp_path / "task-1")
        mgr.init_from_config(["A", "B"])
        mgr.init_from_config(["C", "D"])  # Should not overwrite
        cl = mgr.load()
        assert len(cl.chunks) == 2
        assert cl.chunks[0].description == "A"


# ── Directive parsing ─────────────────────────────────────────────────


class TestChunkDirectiveParsing:
    def test_parse_chunk_add(self, tmp_path):
        mgr = ChunkManager(tmp_path / "task-1")
        actions = parse_chunk_directives(
            "Let's add this. CHUNK_ADD: Implement login page",
            mgr, "architect",
        )
        assert len(actions) == 1
        assert actions[0]["action"] == "add"
        assert actions[0]["description"] == "Implement login page"
        assert mgr.load().chunks[0].description == "Implement login page"

    def test_parse_chunk_done(self, tmp_path):
        mgr = ChunkManager(tmp_path / "task-1")
        mgr.add("Task")
        actions = parse_chunk_directives(
            "Completed. CHUNK_DONE: C-001",
            mgr, "dev",
        )
        assert len(actions) == 1
        assert actions[0]["action"] == "done"
        assert mgr.load().chunks[0].status == ChunkStatus.done

    def test_parse_chunk_remove(self, tmp_path):
        mgr = ChunkManager(tmp_path / "task-1")
        mgr.add("Unnecessary")
        actions = parse_chunk_directives(
            "Not needed. CHUNK_REMOVE: C-001",
            mgr, "reviewer",
        )
        assert len(actions) == 1
        assert actions[0]["action"] == "remove"
        assert len(mgr.load().chunks) == 0

    def test_parse_multiple_directives(self, tmp_path):
        mgr = ChunkManager(tmp_path / "task-1")
        mgr.add("Existing")
        message = (
            "CHUNK_ADD: New feature\n"
            "CHUNK_DONE: C-001\n"
            "CHUNK_ADD: Another feature"
        )
        actions = parse_chunk_directives(message, mgr, "agent")
        assert len(actions) == 3
        cl = mgr.load()
        assert len(cl.chunks) == 3  # existing(done) + 2 new

    def test_parse_case_insensitive(self, tmp_path):
        mgr = ChunkManager(tmp_path / "task-1")
        actions = parse_chunk_directives(
            "chunk_add: case test",
            mgr, "agent",
        )
        assert len(actions) == 1

    def test_parse_no_directives(self, tmp_path):
        mgr = ChunkManager(tmp_path / "task-1")
        actions = parse_chunk_directives(
            "Just a normal message with no directives.",
            mgr, "agent",
        )
        assert len(actions) == 0


# ── YAML loading ──────────────────────────────────────────────────────


class TestChunksYAMLLoading:
    def test_load_chunks_config(self, tmp_project):
        yaml_content = {
            "agents": [
                {"id": "a", "runtime": "claude", "system_prompt": "{{ input }}"},
                {
                    "id": "session",
                    "type": "session",
                    "participants": ["a"],
                    "chunks": {
                        "enabled": True,
                        "initial": ["Setup", "Build"],
                    },
                },
            ]
        }
        yaml_path = tmp_project / ".aqm" / "agents.yaml"
        yaml_path.write_text(yaml.dump(yaml_content), encoding="utf-8")

        agents = load_agents(yaml_path)
        session = agents["session"]
        assert session.chunks is not None
        assert session.chunks.enabled is True
        assert session.chunks.initial == ["Setup", "Build"]

    def test_load_require_chunks_done(self, tmp_project):
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
                    "chunks": {"initial": ["Task 1"]},
                },
            ]
        }
        yaml_path = tmp_project / ".aqm" / "agents.yaml"
        yaml_path.write_text(yaml.dump(yaml_content), encoding="utf-8")

        agents = load_agents(yaml_path)
        assert agents["session"].consensus.require_chunks_done is True

    def test_no_chunks_backward_compatible(self, tmp_project):
        yaml_content = {
            "agents": [
                {"id": "a", "runtime": "claude", "system_prompt": "{{ input }}"},
                {
                    "id": "session",
                    "type": "session",
                    "participants": ["a"],
                },
            ]
        }
        yaml_path = tmp_project / ".aqm" / "agents.yaml"
        yaml_path.write_text(yaml.dump(yaml_content), encoding="utf-8")

        agents = load_agents(yaml_path)
        assert agents["session"].chunks is None


# ── Pipeline with chunks ──────────────────────────────────────────────


class TestPipelineWithChunks:
    def _make_agents(self, require_chunks_done=True, initial_chunks=None):
        return {
            "dev": AgentDefinition(
                id="dev", runtime="claude",
                system_prompt="{{ input }} {{ transcript }} {{ chunks }}",
            ),
            "review": AgentDefinition(
                id="review", runtime="claude",
                system_prompt="{{ input }} {{ transcript }} {{ chunks }}",
            ),
            "session": AgentDefinition(
                id="session",
                type="session",
                participants=["dev", "review"],
                max_rounds=5,
                consensus=ConsensusConfig(
                    method="vote",
                    require="all",
                    require_chunks_done=require_chunks_done,
                ),
                chunks=ChunksConfig(
                    initial=initial_chunks if initial_chunks is not None else ["Setup", "Build", "Test"],
                ),
            ),
        }

    def test_chunks_initialized_from_config(self, tmp_project):
        agents = self._make_agents()
        queue = FileQueue(tmp_project / ".aqm" / "file-queue")
        pipeline = Pipeline(agents, queue, tmp_project)

        mock_rt = MagicMock()
        mock_rt.name = "mock"
        # Everyone agrees immediately but chunks are not done
        mock_rt.run.return_value = "VOTE: AGREE"
        pipeline._runtimes["claude_text"] = mock_rt

        task = Task(description="Build todo app")
        queue.push(task, "session")
        result = pipeline.run_task(task, "session")

        # Chunks should block consensus even though votes pass
        assert result.metadata.get("session_consensus") is False
        assert result.metadata.get("chunks_total") == 3
        assert result.metadata.get("chunks_done") == 0

    def test_chunks_done_via_directives(self, tmp_project):
        agents = self._make_agents(initial_chunks=["Setup", "Build"])
        queue = FileQueue(tmp_project / ".aqm" / "file-queue")
        pipeline = Pipeline(agents, queue, tmp_project)

        mock_rt = MagicMock()
        mock_rt.name = "mock"
        mock_rt.run.side_effect = [
            # Round 1: complete chunks
            "Done setup. CHUNK_DONE: C-001\nVOTE: AGREE",
            "Done build. CHUNK_DONE: C-002\nVOTE: AGREE",
        ]
        pipeline._runtimes["claude_text"] = mock_rt

        task = Task(description="Build app")
        queue.push(task, "session")
        result = pipeline.run_task(task, "session")

        assert result.metadata.get("session_consensus") is True
        assert result.metadata.get("chunks_total") == 2
        assert result.metadata.get("chunks_done") == 2

    def test_chunks_added_by_agents(self, tmp_project):
        agents = self._make_agents(initial_chunks=[])
        queue = FileQueue(tmp_project / ".aqm" / "file-queue")
        pipeline = Pipeline(agents, queue, tmp_project)

        mock_rt = MagicMock()
        mock_rt.name = "mock"
        mock_rt.run.side_effect = [
            # Round 1: add chunks
            "CHUNK_ADD: Setup project\nCHUNK_ADD: Write tests",
            "Looks good.",
            # Round 2: complete and vote
            "CHUNK_DONE: C-001\nCHUNK_DONE: C-002\nVOTE: AGREE",
            "VOTE: AGREE",
        ]
        pipeline._runtimes["claude_text"] = mock_rt

        task = Task(description="Plan work")
        queue.push(task, "session")
        result = pipeline.run_task(task, "session")

        assert result.metadata.get("session_consensus") is True
        assert result.metadata.get("chunks_total") == 2
        assert result.metadata.get("chunks_done") == 2

    def test_chunks_without_require_done(self, tmp_project):
        """When require_chunks_done=False, chunks are informational only."""
        agents = self._make_agents(require_chunks_done=False)
        queue = FileQueue(tmp_project / ".aqm" / "file-queue")
        pipeline = Pipeline(agents, queue, tmp_project)

        mock_rt = MagicMock()
        mock_rt.name = "mock"
        mock_rt.run.side_effect = [
            "VOTE: AGREE",
            "VOTE: AGREE",
        ]
        pipeline._runtimes["claude_text"] = mock_rt

        task = Task(description="Info only")
        queue.push(task, "session")
        result = pipeline.run_task(task, "session")

        # Consensus reached even though chunks are not done
        assert result.metadata.get("session_consensus") is True
        assert result.metadata.get("chunks_done") == 0
        assert result.metadata.get("chunks_total") == 3

    def test_chunks_injected_into_prompt(self, tmp_project):
        """Verify {{ chunks }} template variable is passed to build_prompt."""
        agents = self._make_agents(initial_chunks=["Task A"])
        queue = FileQueue(tmp_project / ".aqm" / "file-queue")
        pipeline = Pipeline(agents, queue, tmp_project)

        mock_rt = MagicMock()
        mock_rt.name = "mock"
        mock_rt.run.side_effect = [
            "CHUNK_DONE: C-001\nVOTE: AGREE",
            "VOTE: AGREE",
        ]
        pipeline._runtimes["claude_text"] = mock_rt

        task = Task(description="Prompt test")
        queue.push(task, "session")
        pipeline.run_task(task, "session")

        # Check that the prompt passed to the first agent included chunk data
        first_call_prompt = mock_rt.run.call_args_list[0][0][0]
        assert "C-001" in first_call_prompt
        assert "Task A" in first_call_prompt

    def test_chunk_remove_directive(self, tmp_project):
        agents = self._make_agents(initial_chunks=["Keep", "Remove me"])
        queue = FileQueue(tmp_project / ".aqm" / "file-queue")
        pipeline = Pipeline(agents, queue, tmp_project)

        mock_rt = MagicMock()
        mock_rt.name = "mock"
        mock_rt.run.side_effect = [
            "CHUNK_REMOVE: C-002\nCHUNK_DONE: C-001\nVOTE: AGREE",
            "VOTE: AGREE",
        ]
        pipeline._runtimes["claude_text"] = mock_rt

        task = Task(description="Remove test")
        queue.push(task, "session")
        result = pipeline.run_task(task, "session")

        assert result.metadata.get("session_consensus") is True
        assert result.metadata.get("chunks_total") == 1  # Only "Keep" remains
        assert result.metadata.get("chunks_done") == 1

    def test_no_chunks_config_backward_compatible(self, tmp_project):
        """Session without chunks config works exactly as before."""
        agents = {
            "a": AgentDefinition(
                id="a", runtime="claude", system_prompt="{{ input }}",
            ),
            "session": AgentDefinition(
                id="session",
                type="session",
                participants=["a"],
                max_rounds=3,
                consensus=ConsensusConfig(method="vote", require="all"),
                # No chunks config
            ),
        }
        queue = FileQueue(tmp_project / ".aqm" / "file-queue")
        pipeline = Pipeline(agents, queue, tmp_project)

        mock_rt = MagicMock()
        mock_rt.name = "mock"
        mock_rt.run.return_value = "VOTE: AGREE"
        pipeline._runtimes["claude_text"] = mock_rt

        task = Task(description="No chunks")
        queue.push(task, "session")
        result = pipeline.run_task(task, "session")

        assert result.metadata.get("session_consensus") is True
        assert "chunks_total" not in result.metadata
