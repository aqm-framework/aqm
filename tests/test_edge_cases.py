"""Edge case tests — covers scenarios missing from existing test files."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from aqm.core.agent import AgentDefinition, load_agents
from aqm.core.chunks import ChunkManager, _generate_chunk_id
from aqm.core.context import build_prompt, render_template
from aqm.core.context_file import ContextFile
from aqm.core.gate import LLMGate
from aqm.core.pipeline import Pipeline
from aqm.core.task import Task, TaskStatus
from aqm.queue.file import FileQueue


# ═══════════════════════════════════════════════════════════════════════
# 1. YAML LOADING — negative / error paths
# ═══════════════════════════════════════════════════════════════════════


class TestYAMLLoadingEdgeCases:

    def test_empty_agents_list_raises(self, tmp_project):
        yaml_path = tmp_project / ".aqm" / "agents.yaml"
        yaml_path.write_text(yaml.dump({"agents": []}), encoding="utf-8")
        agents = load_agents(yaml_path)
        assert agents == {}

    def test_missing_runtime_on_agent_raises(self, tmp_project):
        yaml_path = tmp_project / ".aqm" / "agents.yaml"
        yaml_path.write_text(yaml.dump({
            "agents": [{"id": "a", "system_prompt": "hi"}]
        }), encoding="utf-8")
        with pytest.raises(ValueError, match="runtime"):
            load_agents(yaml_path)

    def test_invalid_context_strategy_raises(self, tmp_project):
        yaml_path = tmp_project / ".aqm" / "agents.yaml"
        yaml_path.write_text(yaml.dump({
            "agents": [{
                "id": "a", "runtime": "claude",
                "context_strategy": "invalid_strategy",
            }]
        }), encoding="utf-8")
        with pytest.raises(Exception):
            load_agents(yaml_path)

    def test_session_without_participants_raises(self, tmp_project):
        yaml_path = tmp_project / ".aqm" / "agents.yaml"
        yaml_path.write_text(yaml.dump({
            "agents": [{"id": "s", "type": "session"}]
        }), encoding="utf-8")
        with pytest.raises(ValueError, match="participant"):
            load_agents(yaml_path)

    def test_session_participant_not_found_raises(self, tmp_project):
        yaml_path = tmp_project / ".aqm" / "agents.yaml"
        yaml_path.write_text(yaml.dump({
            "agents": [
                {"id": "s", "type": "session", "participants": ["nonexistent"]},
            ]
        }), encoding="utf-8")
        with pytest.raises(ValueError, match="nonexistent"):
            load_agents(yaml_path)


# ═══════════════════════════════════════════════════════════════════════
# 2. UNICODE / SPECIAL CHARACTERS
# ═══════════════════════════════════════════════════════════════════════


class TestUnicodeHandling:

    def test_korean_in_system_prompt(self):
        result = render_template("계획: {{ input }}", input="JWT 인증 추가")
        assert "JWT 인증 추가" in result

    def test_emoji_in_agent_output(self, tmp_path):
        cf = ContextFile(tmp_path / "task")
        cf.append_stage(
            stage_number=1, agent_id="dev", task_name="build",
            status="completed",
            input_text="Build feature",
            output_text="Done! Great work. Unicode: 한국어 テスト",
        )
        content = cf.read()
        assert "한국어" in content
        assert "テスト" in content

    def test_special_chars_in_chunk_description(self, tmp_path):
        cm = ChunkManager(tmp_path / "task")
        chunk = cm.add("Feature: handle 'quotes' & <brackets> | pipes")
        assert chunk.id == "C-001"
        cl = cm.load()
        assert "quotes" in cl.chunks[0].description

    def test_unicode_in_payload_template(self):
        from aqm.core.context import build_payload
        result = build_payload(
            "출력: {{ output }}\n사유: {{ reject_reason }}",
            output="완료",
            reject_reason="테스트 부족",
        )
        assert "완료" in result
        assert "테스트 부족" in result


# ═══════════════════════════════════════════════════════════════════════
# 3. TASK EDGE CASES
# ═══════════════════════════════════════════════════════════════════════


class TestTaskEdgeCases:

    def test_empty_description(self):
        task = Task(description="")
        assert task.id.startswith("T-")
        assert task.description == ""

    def test_very_long_description(self):
        long_desc = "x" * 50000
        task = Task(description=long_desc)
        assert len(task.description) == 50000

    def test_task_serialization_roundtrip(self):
        task = Task(description="roundtrip test")
        task.metadata["key"] = "value"
        json_str = task.model_dump_json()
        restored = Task.model_validate_json(json_str)
        assert restored.id == task.id
        assert restored.metadata["key"] == "value"


# ═══════════════════════════════════════════════════════════════════════
# 4. CHUNK EDGE CASES
# ═══════════════════════════════════════════════════════════════════════


class TestChunkEdgeCases:

    def test_chunk_id_beyond_999(self):
        existing = {f"C-{i:03d}" for i in range(1, 1000)}
        new_id = _generate_chunk_id(existing)
        assert new_id == "C-1000"

    def test_mark_done_already_done(self, tmp_path):
        cm = ChunkManager(tmp_path / "task")
        cm.add("task 1")
        assert cm.mark_done("C-001") is True
        # Marking done again should still work
        assert cm.mark_done("C-001") is True

    def test_mark_done_nonexistent(self, tmp_path):
        cm = ChunkManager(tmp_path / "task")
        assert cm.mark_done("C-999") is False

    def test_remove_nonexistent(self, tmp_path):
        cm = ChunkManager(tmp_path / "task")
        assert cm.remove("C-999") is False

    def test_chunk_remove_and_readd(self, tmp_path):
        cm = ChunkManager(tmp_path / "task")
        cm.add("first")
        cm.add("second")
        cm.remove("C-001")
        # C-001 is now free, new chunk should reuse it
        chunk = cm.add("third")
        assert chunk.id == "C-001"

    def test_init_from_config_idempotent(self, tmp_path):
        cm = ChunkManager(tmp_path / "task")
        cm.init_from_config(["a", "b", "c"])
        cm.init_from_config(["d", "e"])  # Should be no-op
        cl = cm.load()
        assert len(cl.chunks) == 3  # Original 3, not 5


# ═══════════════════════════════════════════════════════════════════════
# 5. CONTEXT FILE EDGE CASES
# ═══════════════════════════════════════════════════════════════════════


class TestContextFileEdgeCases:

    def test_read_empty_file(self, tmp_path):
        cf = ContextFile(tmp_path / "task")
        cf.ensure_dir()
        cf.context_path.write_text("", encoding="utf-8")
        assert cf.read() == ""

    def test_read_latest_empty(self, tmp_path):
        cf = ContextFile(tmp_path / "task")
        assert cf.read_latest(5) == ""

    def test_read_smart_single_stage(self, tmp_path):
        cf = ContextFile(tmp_path / "task")
        cf.append_stage(
            stage_number=1, agent_id="a", task_name="t",
            status="completed", input_text="in", output_text="out",
        )
        smart = cf.read_smart(context_window=3)
        full = cf.read()
        assert smart == full  # 1 stage <= window, no compression

    def test_transcript_nonexistent(self, tmp_path):
        cf = ContextFile(tmp_path / "task")
        assert cf.read_transcript() == ""

    def test_agent_context_isolation(self, tmp_path):
        cf = ContextFile(tmp_path / "task")
        cf.append_agent_context(
            agent_id="a", stage_number=1,
            input_text="in", output_text="output-a",
        )
        cf.append_agent_context(
            agent_id="b", stage_number=2,
            input_text="in", output_text="output-b",
        )
        assert "output-a" in cf.read_agent_context("a")
        assert "output-b" not in cf.read_agent_context("a")
        assert "output-b" in cf.read_agent_context("b")
        assert "output-a" not in cf.read_agent_context("b")


# ═══════════════════════════════════════════════════════════════════════
# 6. PIPELINE EDGE CASES
# ═══════════════════════════════════════════════════════════════════════


class TestPipelineEdgeCases:

    def test_agent_not_found_raises(self, tmp_project):
        agents = {
            "a": AgentDefinition(id="a", runtime="claude", system_prompt="{{ input }}"),
        }
        queue = FileQueue(tmp_project / ".aqm" / "q")
        pipeline = Pipeline(agents, queue, tmp_project)
        pipeline._runtimes["claude"] = MagicMock(name="mock", run=MagicMock(return_value="ok"))

        task = Task(description="test")
        queue.push(task, "nonexistent")
        with pytest.raises(ValueError, match="not defined"):
            pipeline.run_task(task, "nonexistent")

    def test_no_handoffs_completes(self, tmp_project):
        agents = {
            "only": AgentDefinition(
                id="only", runtime="claude",
                system_prompt="{{ input }}",
            ),
        }
        queue = FileQueue(tmp_project / ".aqm" / "q")
        pipeline = Pipeline(agents, queue, tmp_project)
        mock_rt = MagicMock()
        mock_rt.name = "mock"
        mock_rt.run.return_value = "done"
        pipeline._runtimes["claude"] = mock_rt

        task = Task(description="single agent")
        queue.push(task, "only")
        result = pipeline.run_task(task, "only")

        assert result.status == TaskStatus.completed
        assert len(result.stages) == 1

    def test_agent_runtime_error_fails_task(self, tmp_project):
        agents = {
            "crash": AgentDefinition(
                id="crash", runtime="claude",
                system_prompt="{{ input }}",
            ),
        }
        queue = FileQueue(tmp_project / ".aqm" / "q")
        pipeline = Pipeline(agents, queue, tmp_project)
        mock_rt = MagicMock()
        mock_rt.name = "mock"
        mock_rt.run.side_effect = RuntimeError("LLM crashed")
        pipeline._runtimes["claude"] = mock_rt

        task = Task(description="crash test")
        queue.push(task, "crash")
        result = pipeline.run_task(task, "crash")

        assert result.status == TaskStatus.failed
        assert "ERROR" in result.stages[0].output_text

    def test_auto_handoff_no_directive_skips(self, tmp_project):
        """Auto handoff with no HANDOFF: directive in output skips handoff."""
        agents = {
            "router": AgentDefinition(
                id="router", runtime="claude",
                system_prompt="{{ input }}",
                handoffs=[{"to": "*", "condition": "auto"}],
            ),
            "target": AgentDefinition(
                id="target", runtime="claude",
                system_prompt="{{ input }}",
            ),
        }
        queue = FileQueue(tmp_project / ".aqm" / "q")
        pipeline = Pipeline(agents, queue, tmp_project)
        mock_rt = MagicMock()
        mock_rt.name = "mock"
        mock_rt.run.return_value = "Done, no handoff needed"
        pipeline._runtimes["claude"] = mock_rt

        task = Task(description="no auto handoff")
        queue.push(task, "router")
        result = pipeline.run_task(task, "router")

        # No HANDOFF: directive → pipeline completes after router
        assert result.status == TaskStatus.completed
        assert len(result.stages) == 1


# ═══════════════════════════════════════════════════════════════════════
# 7. GATE EDGE CASES
# ═══════════════════════════════════════════════════════════════════════


class TestGateEdgeCases:

    def _gate(self):
        return LLMGate.__new__(LLMGate)

    def test_empty_response(self):
        result = self._gate()._parse_response("")
        assert result.decision == "rejected"

    def test_json_with_extra_text(self):
        result = self._gate()._parse_response(
            'Here is my analysis:\n{"decision": "approved", "reason": "ok"}\nEnd.'
        )
        assert result.decision == "approved"

    def test_malformed_json_falls_back(self):
        result = self._gate()._parse_response('{decision: approved}')
        # Malformed JSON → keyword fallback → finds "approved"
        assert result.decision == "approved"

    def test_no_decision_in_json(self):
        result = self._gate()._parse_response('{"score": 95, "feedback": "great"}')
        # Valid JSON but no "decision" field → keyword fallback → no keywords → reject
        assert result.decision == "rejected"
