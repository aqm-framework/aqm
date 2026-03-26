"""Pipeline core functionality tests."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from aqm.core.agent import AgentDefinition, load_agents
from aqm.core.context import build_payload, build_prompt, render_template
from aqm.core.context_file import ContextFile
from aqm.core.gate import GateResult, LLMGate
from aqm.core.project import find_project_root, init_project
from aqm.core.task import Task, TaskStatus
from aqm.queue.file import FileQueue


# ── Task ────────────────────────────────────────────────────────────────


class TestTask:
    def test_task_creation(self):
        task = Task(description="test task")
        assert task.id.startswith("T-")
        assert task.status == TaskStatus.pending
        assert task.description == "test task"
        assert task.stages == []

    def test_task_id_unique(self):
        t1 = Task(description="a")
        t2 = Task(description="b")
        assert t1.id != t2.id

    def test_task_serialization(self):
        task = Task(description="serialization test")
        json_str = task.model_dump_json()
        restored = Task.model_validate_json(json_str)
        assert restored.id == task.id
        assert restored.description == task.description


# ── Agent YAML ──────────────────────────────────────────────────────────


class TestAgentYAML:
    def test_load_agents(self, sample_agents):
        assert "agent_a" in sample_agents
        assert "agent_b" in sample_agents
        assert "agent_c" in sample_agents

    def test_agent_fields(self, sample_agents):
        a = sample_agents["agent_a"]
        assert a.name == "Agent A"
        assert a.runtime == "claude"
        assert len(a.handoffs) == 1
        assert a.handoffs[0].to == "agent_b"

    def test_mcp_config(self, sample_agents):
        c = sample_agents["agent_c"]
        assert len(c.mcp) == 1
        assert c.mcp[0].server == "filesystem"

    def test_gate_config(self, sample_agents):
        b = sample_agents["agent_b"]
        assert b.gate is not None
        assert b.gate.type == "llm"

    def test_invalid_handoff_target(self, tmp_project):
        yaml_content = {
            "agents": [
                {
                    "id": "a",
                    "name": "A",
                    "runtime": "claude",
                    "handoffs": [{"to": "nonexistent", "condition": "always"}],
                }
            ]
        }
        yaml_path = tmp_project / ".aqm" / "agents.yaml"
        yaml_path.write_text(yaml.dump(yaml_content), encoding="utf-8")

        with pytest.raises(ValueError, match="does not exist"):
            load_agents(yaml_path)

    def test_duplicate_agent_id(self, tmp_project):
        yaml_content = {
            "agents": [
                {"id": "dup", "name": "A", "runtime": "claude"},
                {"id": "dup", "name": "B", "runtime": "claude"},
            ]
        }
        yaml_path = tmp_project / ".aqm" / "agents.yaml"
        yaml_path.write_text(yaml.dump(yaml_content), encoding="utf-8")

        with pytest.raises(ValueError, match="Duplicate agent ID"):
            load_agents(yaml_path)

    def test_simple_mcp_format(self, tmp_project):
        """Test simplified MCP format."""
        yaml_content = {
            "agents": [
                {
                    "id": "test",
                    "name": "Test",
                    "runtime": "claude",
                    "mcp": [
                        {"server": "github"},
                        {"server": "filesystem", "args": ["/tmp"]},
                    ],
                }
            ]
        }
        yaml_path = tmp_project / ".aqm" / "agents.yaml"
        yaml_path.write_text(yaml.dump(yaml_content), encoding="utf-8")

        agents = load_agents(yaml_path)
        assert len(agents["test"].mcp) == 2
        assert agents["test"].mcp[0].server == "github"
        assert agents["test"].mcp[1].args == ["/tmp"]


# ── Queue ───────────────────────────────────────────────────────────────


class TestQueue:
    def test_push_pop(self, file_queue):
        task = Task(description="queue test")
        file_queue.push(task, "test_queue")

        popped = file_queue.pop("test_queue")
        assert popped is not None
        assert popped.id == task.id
        assert popped.status == TaskStatus.in_progress

    def test_pop_empty(self, file_queue):
        assert file_queue.pop("empty_queue") is None

    def test_fifo_order(self, file_queue):
        t1 = Task(description="first")
        t2 = Task(description="second")
        file_queue.push(t1, "q")
        file_queue.push(t2, "q")

        popped = file_queue.pop("q")
        assert popped is not None
        assert popped.description == "first"

    def test_get_by_id(self, file_queue):
        task = Task(description="lookup test")
        file_queue.push(task, "q")

        found = file_queue.get(task.id)
        assert found is not None
        assert found.id == task.id

    def test_list_tasks(self, file_queue):
        t1 = Task(description="a")
        t2 = Task(description="b")
        file_queue.push(t1, "q1")
        file_queue.push(t2, "q2")

        all_tasks = file_queue.list_tasks()
        assert len(all_tasks) == 2

    def test_list_tasks_filter(self, file_queue):
        t1 = Task(description="pending one")
        file_queue.push(t1, "q")
        file_queue.pop("q")  # Changes to in_progress

        pending = file_queue.list_tasks(status=TaskStatus.pending)
        assert len(pending) == 0

        in_progress = file_queue.list_tasks(status=TaskStatus.in_progress)
        assert len(in_progress) == 1

    def test_update(self, file_queue):
        task = Task(description="update test")
        file_queue.push(task, "q")

        task.status = TaskStatus.completed
        file_queue.update(task)

        found = file_queue.get(task.id)
        assert found is not None
        assert found.status == TaskStatus.completed


# ── Context ─────────────────────────────────────────────────────────────


class TestContext:
    def test_render_template(self):
        result = render_template("Hello {{ name }}", name="World")
        assert result == "Hello World"

    def test_undefined_variable(self):
        result = render_template("Hello {{ undefined_var }}")
        assert result == "Hello "

    def test_build_prompt(self):
        result = build_prompt(
            system_prompt_template="Process: {{ input }}",
            input_text="test input",
        )
        assert result == "Process: test input"

    def test_build_payload(self):
        result = build_payload(
            "{{ output }}\nREASON: {{ reject_reason }}",
            output="result",
            reject_reason="not good",
        )
        assert "result" in result
        assert "not good" in result


# ── ContextFile ─────────────────────────────────────────────────────────


class TestContextFile:
    def test_append_and_read(self, tmp_path):
        cf = ContextFile(tmp_path / "test-task")
        cf.append_stage(
            stage_number=1,
            agent_id="planner",
            task_name="plan",
            status="completed",
            input_text="input here",
            output_text="output here",
        )

        content = cf.read()
        assert "planner" in content
        assert "input here" in content
        assert "output here" in content

    def test_multiple_stages(self, tmp_path):
        cf = ContextFile(tmp_path / "test-task")
        cf.append_stage(
            stage_number=1,
            agent_id="a",
            task_name="t1",
            status="completed",
            input_text="in1",
            output_text="out1",
        )
        cf.append_stage(
            stage_number=2,
            agent_id="b",
            task_name="t2",
            status="completed",
            input_text="in2",
            output_text="out2",
        )

        content = cf.read()
        assert "stage 1" in content
        assert "stage 2" in content

    def test_stage_appended_to_context(self, tmp_path):
        cf = ContextFile(tmp_path / "test-task")
        result_path = cf.append_stage(
            stage_number=1,
            agent_id="agent_x",
            task_name="test",
            status="completed",
            input_text="in",
            output_text="out",
        )
        assert result_path.exists()
        assert result_path.name == "context.md"
        content = cf.read()
        assert "agent_x" in content

    def test_save_payload(self, tmp_path):
        cf = ContextFile(tmp_path / "test-task")
        path = cf.save_payload("test payload content")
        assert path.exists()
        assert path.read_text() == "test payload content"

    def test_read_latest(self, tmp_path):
        cf = ContextFile(tmp_path / "test-task")
        for i in range(5):
            cf.append_stage(
                stage_number=i + 1,
                agent_id=f"agent_{i}",
                task_name="t",
                status="completed",
                input_text="in",
                output_text=f"out_{i}",
            )

        latest = cf.read_latest(2)
        assert "out_4" in latest
        assert "out_3" in latest
        assert "out_0" not in latest


# ── Project ─────────────────────────────────────────────────────────────


class TestProject:
    def test_init_project(self, tmp_path):
        root = init_project(tmp_path)
        assert (root / ".aqm").is_dir()
        assert (root / ".aqm" / "pipelines" / "default.yaml").exists()
        assert (root / ".aqm" / "tasks").is_dir()

    def test_find_project_root(self, tmp_project):
        found = find_project_root(tmp_project)
        assert found == tmp_project

    def test_find_project_root_nested(self, tmp_project):
        nested = tmp_project / "src" / "deep"
        nested.mkdir(parents=True)
        found = find_project_root(nested)
        assert found == tmp_project

    def test_find_project_root_not_found(self, tmp_path):
        empty = tmp_path / "no_project"
        empty.mkdir()
        found = find_project_root(empty)
        assert found is None


# ── YAML Generation Helpers ────────────────────────────────────────────


class TestYamlGenerationHelpers:
    def test_strip_markdown_fences_yaml(self):
        from aqm.core.project import _strip_markdown_fences

        text = "```yaml\napiVersion: aqm/v0.1\nagents: []\n```"
        result = _strip_markdown_fences(text)
        assert result == "apiVersion: aqm/v0.1\nagents: []"

    def test_strip_markdown_fences_plain(self):
        from aqm.core.project import _strip_markdown_fences

        text = "```\napiVersion: aqm/v0.1\n```"
        result = _strip_markdown_fences(text)
        assert result == "apiVersion: aqm/v0.1"

    def test_strip_markdown_fences_no_fences(self):
        from aqm.core.project import _strip_markdown_fences

        text = "apiVersion: aqm/v0.1\nagents: []"
        result = _strip_markdown_fences(text)
        assert result == text

    def test_strip_leading_prose(self):
        from aqm.core.project import _strip_leading_prose

        text = (
            "Here is the generated YAML:\n"
            "\n"
            "apiVersion: aqm/v0.1\n"
            "agents:\n"
            "  - id: planner\n"
        )
        result = _strip_leading_prose(text)
        assert result.startswith("apiVersion: aqm/v0.1")
        assert "Here is" not in result

    def test_strip_leading_prose_no_prose(self):
        from aqm.core.project import _strip_leading_prose

        text = "apiVersion: aqm/v0.1\nagents: []"
        result = _strip_leading_prose(text)
        assert result == text

    def test_strip_leading_prose_starts_with_agents(self):
        from aqm.core.project import _strip_leading_prose

        text = "Some intro text\nagents:\n  - id: a"
        result = _strip_leading_prose(text)
        assert result.startswith("agents:")

    def test_combined_fences_and_prose(self):
        from aqm.core.project import _strip_markdown_fences, _strip_leading_prose

        text = (
            "```yaml\n"
            "I'll create a pipeline for you:\n"
            "\n"
            "apiVersion: aqm/v0.1\n"
            "agents:\n"
            "  - id: planner\n"
            "    name: Planner\n"
            "```"
        )
        result = _strip_markdown_fences(text)
        result = _strip_leading_prose(result)
        assert result.startswith("apiVersion: aqm/v0.1")
        assert "I'll create" not in result
        assert "```" not in result


# ── Gate ────────────────────────────────────────────────────────────────


class TestGate:
    def test_gate_parse_approved(self):
        gate = LLMGate.__new__(LLMGate)
        result = gate._parse_response('{"decision": "approved", "reason": "good"}')
        assert result.decision == "approved"
        assert result.reason == "good"

    def test_gate_parse_rejected(self):
        gate = LLMGate.__new__(LLMGate)
        result = gate._parse_response('{"decision": "rejected", "reason": "bad"}')
        assert result.decision == "rejected"

    def test_gate_parse_fallback_approved(self):
        gate = LLMGate.__new__(LLMGate)
        result = gate._parse_response("I think this is APPROVED because it looks good")
        assert result.decision == "approved"

    def test_gate_parse_fallback_rejected(self):
        gate = LLMGate.__new__(LLMGate)
        result = gate._parse_response("This should be REJECTED due to issues")
        assert result.decision == "rejected"

    def test_gate_parse_unknown(self):
        gate = LLMGate.__new__(LLMGate)
        result = gate._parse_response("I have no opinion")
        assert result.decision == "rejected"  # Default is reject


# ── Handoff Routing ────────────────────────────────────────────────────


class TestHandoffRouting:
    def test_auto_handoff_parse_single(self):
        from aqm.core.pipeline import Pipeline

        pipeline = Pipeline.__new__(Pipeline)
        pipeline.agents = {"dev": True, "qa": True}
        targets = pipeline._parse_auto_handoff_targets(
            "Done reviewing.\nHANDOFF: dev\nGood luck."
        )
        assert targets == ["dev"]

    def test_auto_handoff_parse_multi(self):
        from aqm.core.pipeline import Pipeline

        pipeline = Pipeline.__new__(Pipeline)
        targets = pipeline._parse_auto_handoff_targets(
            "Results ready.\nHANDOFF: dev, qa\nAll done."
        )
        assert targets == ["dev", "qa"]

    def test_auto_handoff_parse_multiple_lines(self):
        from aqm.core.pipeline import Pipeline

        pipeline = Pipeline.__new__(Pipeline)
        targets = pipeline._parse_auto_handoff_targets(
            "HANDOFF: dev\nSome text\nHANDOFF: qa"
        )
        assert targets == ["dev", "qa"]

    def test_auto_handoff_dedup(self):
        from aqm.core.pipeline import Pipeline

        pipeline = Pipeline.__new__(Pipeline)
        targets = pipeline._parse_auto_handoff_targets(
            "HANDOFF: dev\nHANDOFF: dev, qa"
        )
        assert targets == ["dev", "qa"]

    def test_auto_handoff_case_insensitive(self):
        from aqm.core.pipeline import Pipeline

        pipeline = Pipeline.__new__(Pipeline)
        targets = pipeline._parse_auto_handoff_targets("handoff: dev")
        assert targets == ["dev"]

    def test_fanout_comma_separated(self, tmp_project):
        """Fan-out: comma-separated to field should produce multiple targets."""
        yaml_content = {
            "agents": [
                {
                    "id": "router",
                    "name": "Router",
                    "runtime": "claude",
                    "handoffs": [
                        {"to": "a, b", "condition": "always"},
                    ],
                },
                {"id": "a", "name": "A", "runtime": "claude"},
                {"id": "b", "name": "B", "runtime": "claude"},
            ]
        }
        yaml_path = tmp_project / ".aqm" / "agents.yaml"
        yaml_path.write_text(yaml.dump(yaml_content), encoding="utf-8")

        agents = load_agents(yaml_path)
        assert "router" in agents
        assert "a" in agents
        assert "b" in agents


# ── Gate Reject Retry Limit ────────────────────────────────────────


class TestGateRejectRetryLimit:
    def test_reject_loop_fails_after_max_retries(self, tmp_project):
        """Gate that always rejects should fail after max_retries, not loop forever."""
        from unittest.mock import MagicMock, patch

        from aqm.core.pipeline import Pipeline
        from aqm.core.config import ProjectConfig

        yaml_content = {
            "agents": [
                {
                    "id": "worker",
                    "runtime": "claude",
                    "system_prompt": "Work: {{ input }}",
                    "gate": {
                        "type": "llm",
                        "prompt": "Review this",
                        "max_retries": 2,
                    },
                    "handoffs": [
                        {
                            "to": "worker",
                            "condition": "on_reject",
                            "payload": "{{ output }}\nREJECT: {{ reject_reason }}",
                        },
                    ],
                },
            ]
        }
        yaml_path = tmp_project / ".aqm" / "agents.yaml"
        yaml_path.write_text(yaml.dump(yaml_content), encoding="utf-8")

        agents = load_agents(yaml_path)
        queue = FileQueue(tmp_project / ".aqm" / "file-queue")
        pipeline = Pipeline(agents, queue, tmp_project)

        # Mock runtime
        mock_rt = MagicMock()
        mock_rt.name = "mock"
        mock_rt.run.return_value = "some output"
        pipeline._runtimes["claude"] = mock_rt

        # Mock gate to always reject
        mock_gate = MagicMock()
        mock_gate.evaluate.return_value = GateResult(
            decision="rejected", reason="not good enough"
        )
        pipeline._get_gate = MagicMock(return_value=mock_gate)

        task = Task(description="test reject loop")
        queue.push(task, "worker")
        result = pipeline.run_task(task, "worker")

        assert result.status == TaskStatus.failed
        assert "exceeded max gate retries" in result.metadata.get("error", "")
        # Should have run max_retries + 1 times (initial + retries), then failed
        # max_retries=2: run 1 (reject count 1), run 2 (reject count 2), run 3 (reject count 3 > 2 → fail)
        assert mock_rt.run.call_count == 3

    def test_reject_loop_default_max_retries(self, tmp_project):
        """Default max_retries is 3."""
        yaml_content = {
            "agents": [
                {
                    "id": "worker",
                    "runtime": "claude",
                    "gate": {"type": "llm", "prompt": "Review"},
                    "handoffs": [
                        {"to": "worker", "condition": "on_reject"},
                    ],
                },
            ]
        }
        yaml_path = tmp_project / ".aqm" / "agents.yaml"
        yaml_path.write_text(yaml.dump(yaml_content), encoding="utf-8")

        agents = load_agents(yaml_path)
        assert agents["worker"].gate.max_retries == 3
