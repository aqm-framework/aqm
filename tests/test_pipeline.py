"""Pipeline core functionality tests."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from agent_queue.core.agent import AgentDefinition, load_agents
from agent_queue.core.context import build_payload, build_prompt, render_template
from agent_queue.core.context_file import ContextFile
from agent_queue.core.gate import GateResult, LLMGate
from agent_queue.core.project import find_project_root, init_project
from agent_queue.core.task import Task, TaskStatus
from agent_queue.queue.file import FileQueue


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
        assert a.runtime == "api"
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
                    "runtime": "api",
                    "handoffs": [{"to": "nonexistent", "condition": "always"}],
                }
            ]
        }
        yaml_path = tmp_project / ".agent-queue" / "agents.yaml"
        yaml_path.write_text(yaml.dump(yaml_content), encoding="utf-8")

        with pytest.raises(ValueError, match="does not exist"):
            load_agents(yaml_path)

    def test_duplicate_agent_id(self, tmp_project):
        yaml_content = {
            "agents": [
                {"id": "dup", "name": "A", "runtime": "api"},
                {"id": "dup", "name": "B", "runtime": "api"},
            ]
        }
        yaml_path = tmp_project / ".agent-queue" / "agents.yaml"
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
                    "runtime": "api",
                    "mcp": [
                        {"server": "github"},
                        {"server": "filesystem", "args": ["/tmp"]},
                    ],
                }
            ]
        }
        yaml_path = tmp_project / ".agent-queue" / "agents.yaml"
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

    def test_stage_file_created(self, tmp_path):
        cf = ContextFile(tmp_path / "test-task")
        stage_file = cf.append_stage(
            stage_number=1,
            agent_id="agent_x",
            task_name="test",
            status="completed",
            input_text="in",
            output_text="out",
        )
        assert stage_file.exists()
        assert "agent_x" in stage_file.name

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
        assert (root / ".agent-queue").is_dir()
        assert (root / ".agent-queue" / "agents.yaml").exists()
        assert (root / ".agent-queue" / "tasks").is_dir()

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
        from agent_queue.core.pipeline import Pipeline

        pipeline = Pipeline.__new__(Pipeline)
        pipeline.agents = {"dev": True, "qa": True}
        targets = pipeline._parse_auto_handoff_targets(
            "Done reviewing.\nHANDOFF: dev\nGood luck."
        )
        assert targets == ["dev"]

    def test_auto_handoff_parse_multi(self):
        from agent_queue.core.pipeline import Pipeline

        pipeline = Pipeline.__new__(Pipeline)
        targets = pipeline._parse_auto_handoff_targets(
            "Results ready.\nHANDOFF: dev, qa\nAll done."
        )
        assert targets == ["dev", "qa"]

    def test_auto_handoff_parse_multiple_lines(self):
        from agent_queue.core.pipeline import Pipeline

        pipeline = Pipeline.__new__(Pipeline)
        targets = pipeline._parse_auto_handoff_targets(
            "HANDOFF: dev\nSome text\nHANDOFF: qa"
        )
        assert targets == ["dev", "qa"]

    def test_auto_handoff_dedup(self):
        from agent_queue.core.pipeline import Pipeline

        pipeline = Pipeline.__new__(Pipeline)
        targets = pipeline._parse_auto_handoff_targets(
            "HANDOFF: dev\nHANDOFF: dev, qa"
        )
        assert targets == ["dev", "qa"]

    def test_auto_handoff_case_insensitive(self):
        from agent_queue.core.pipeline import Pipeline

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
                    "runtime": "api",
                    "handoffs": [
                        {"to": "a, b", "condition": "always"},
                    ],
                },
                {"id": "a", "name": "A", "runtime": "api"},
                {"id": "b", "name": "B", "runtime": "api"},
            ]
        }
        yaml_path = tmp_project / ".agent-queue" / "agents.yaml"
        yaml_path.write_text(yaml.dump(yaml_content), encoding="utf-8")

        agents = load_agents(yaml_path)
        assert "router" in agents
        assert "a" in agents
        assert "b" in agents
