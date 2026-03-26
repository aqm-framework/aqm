"""Tests for human_input (human-in-the-loop) feature."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import jsonschema
import pytest
import yaml

from aqm.core.agent import AgentDefinition, HumanInputConfig, load_agents
from aqm.core.context_file import ContextFile
from aqm.core.pipeline import Pipeline
from aqm.core.task import Task, TaskStatus
from aqm.queue.file import FileQueue


# ── HumanInputConfig Schema ─────────────────────────────────────────


class TestHumanInputConfig:
    def test_shorthand_true(self):
        agent = AgentDefinition(id="a", runtime="claude", human_input=True)
        assert agent.human_input is not None
        assert agent.human_input.enabled is True
        assert agent.human_input.mode == "on_demand"

    def test_shorthand_string_before(self):
        agent = AgentDefinition(id="a", runtime="claude", human_input="before")
        assert agent.human_input.mode == "before"

    def test_shorthand_string_both(self):
        agent = AgentDefinition(id="a", runtime="claude", human_input="both")
        assert agent.human_input.mode == "both"

    def test_full_config(self):
        agent = AgentDefinition(
            id="a",
            runtime="claude",
            human_input={
                "enabled": True,
                "mode": "before",
                "prompt": "What features do you need?",
            },
        )
        assert agent.human_input.enabled is True
        assert agent.human_input.mode == "before"
        assert agent.human_input.prompt == "What features do you need?"

    def test_none_by_default(self):
        agent = AgentDefinition(id="a", runtime="claude")
        assert agent.human_input is None

    def test_disabled(self):
        agent = AgentDefinition(
            id="a",
            runtime="claude",
            human_input={"enabled": False, "mode": "before"},
        )
        assert agent.human_input.enabled is False

    def test_load_from_yaml(self, tmp_project):
        yaml_content = {
            "agents": [
                {
                    "id": "planner",
                    "runtime": "claude",
                    "human_input": True,
                    "system_prompt": "Plan: {{ input }}",
                },
                {
                    "id": "gatherer",
                    "runtime": "claude",
                    "human_input": {
                        "enabled": True,
                        "mode": "before",
                        "prompt": "What do you need?",
                    },
                    "system_prompt": "Gather: {{ input }}",
                },
                {
                    "id": "worker",
                    "runtime": "claude",
                    "system_prompt": "Work: {{ input }}",
                },
            ]
        }
        yaml_path = tmp_project / ".aqm" / "agents.yaml"
        yaml_path.write_text(yaml.dump(yaml_content), encoding="utf-8")
        agents = load_agents(yaml_path)

        assert agents["planner"].human_input.mode == "on_demand"
        assert agents["gatherer"].human_input.mode == "before"
        assert agents["gatherer"].human_input.prompt == "What do you need?"
        assert agents["worker"].human_input is None


# ── TaskStatus ───────────────────────────────────────────────────────


class TestTaskStatus:
    def test_awaiting_human_input_status(self):
        task = Task(description="test")
        task.status = TaskStatus.awaiting_human_input
        assert task.status == TaskStatus.awaiting_human_input
        assert task.status.value == "awaiting_human_input"

    def test_serialization_roundtrip(self):
        task = Task(description="test")
        task.status = TaskStatus.awaiting_human_input
        json_str = task.model_dump_json()
        restored = Task.model_validate_json(json_str)
        assert restored.status == TaskStatus.awaiting_human_input


# ── ContextFile.append_human_input ───────────────────────────────────


class TestContextFileHumanInput:
    def test_append_human_input(self, tmp_path):
        ctx = ContextFile(tmp_path / "task_test")
        ctx.append_human_input(
            agent_id="planner",
            question="- What database do you prefer?",
            response="PostgreSQL",
        )

        # Check shared context
        shared = ctx.read()
        assert "[human input] for planner" in shared
        assert "What database do you prefer?" in shared
        assert "PostgreSQL" in shared

        # Check agent context
        agent_ctx = ctx.read_agent_context("planner")
        assert "[human input] for planner" in agent_ctx
        assert "PostgreSQL" in agent_ctx

    def test_multiple_inputs(self, tmp_path):
        ctx = ContextFile(tmp_path / "task_multi")
        ctx.append_human_input(
            agent_id="a",
            question="- Q1",
            response="A1",
        )
        ctx.append_human_input(
            agent_id="b",
            question="- Q2",
            response="A2",
        )
        shared = ctx.read()
        assert "Q1" in shared
        assert "Q2" in shared
        assert "A1" in shared
        assert "A2" in shared

        # Each agent only sees their own in private context
        a_ctx = ctx.read_agent_context("a")
        assert "Q1" in a_ctx
        assert "Q2" not in a_ctx


# ── HUMAN_INPUT Directive Parsing ────────────────────────────────────


class TestHumanInputParsing:
    def test_parse_single_question(self):
        output = "Here's my plan.\nHUMAN_INPUT: What database do you prefer?"
        questions = Pipeline._parse_human_input_requests(output)
        assert questions == ["What database do you prefer?"]

    def test_parse_multiple_questions(self):
        output = (
            "I need some info:\n"
            "HUMAN_INPUT: What's the target audience?\n"
            "HUMAN_INPUT: Budget constraints?\n"
            "Let me know."
        )
        questions = Pipeline._parse_human_input_requests(output)
        assert len(questions) == 2
        assert "What's the target audience?" in questions
        assert "Budget constraints?" in questions

    def test_parse_no_questions(self):
        output = "Here's my plan. All good."
        questions = Pipeline._parse_human_input_requests(output)
        assert questions == []

    def test_parse_case_insensitive(self):
        output = "human_input: Is dark mode needed?"
        questions = Pipeline._parse_human_input_requests(output)
        assert questions == ["Is dark mode needed?"]

    def test_parse_empty_question_ignored(self):
        output = "HUMAN_INPUT:   \nSome text\nHUMAN_INPUT: Real question"
        questions = Pipeline._parse_human_input_requests(output)
        assert questions == ["Real question"]


# ── Pipeline: before mode ────────────────────────────────────────────


class TestPipelineBeforeMode:
    def _make_pipeline(self, tmp_path, agents_dict):
        yaml_content = {"agents": agents_dict}
        project_root = tmp_path / "project"
        aqm_dir = project_root / ".aqm"
        aqm_dir.mkdir(parents=True)
        yaml_path = aqm_dir / "agents.yaml"
        yaml_path.write_text(yaml.dump(yaml_content), encoding="utf-8")

        agents = load_agents(yaml_path)
        queue = FileQueue(aqm_dir / "file-queue")
        pipeline = Pipeline(agents, queue, project_root)

        # Inject mock runtime
        mock_rt = MagicMock()
        mock_rt.name = "mock"
        mock_rt.run.return_value = "Agent output"
        pipeline._runtimes["claude"] = mock_rt

        return pipeline, agents, queue, mock_rt

    def test_before_mode_pauses_pipeline(self, tmp_path):
        pipeline, agents, queue, mock_rt = self._make_pipeline(tmp_path, [
            {
                "id": "planner",
                "runtime": "claude",
                "human_input": {"enabled": True, "mode": "before", "prompt": "What features?"},
                "system_prompt": "Plan: {{ input }}",
            },
        ])

        task = Task(description="Build a todo app")
        queue.push(task, "planner")

        result = pipeline.run_task(task, "planner")

        # Should pause for human input
        assert result.status == TaskStatus.awaiting_human_input
        assert "_human_input_pending" in result.metadata
        pending = result.metadata["_human_input_pending"]
        assert pending["agent_id"] == "planner"
        assert pending["mode"] == "before"
        assert "What features?" in pending["questions"][0]
        # Runtime should NOT have been called yet
        mock_rt.run.assert_not_called()

    def test_before_mode_resume_runs_agent(self, tmp_path):
        pipeline, agents, queue, mock_rt = self._make_pipeline(tmp_path, [
            {
                "id": "planner",
                "runtime": "claude",
                "human_input": {"enabled": True, "mode": "before"},
                "system_prompt": "Plan: {{ input }}",
            },
        ])

        task = Task(description="Build a todo app")
        queue.push(task, "planner")

        # First run — pauses
        result = pipeline.run_task(task, "planner")
        assert result.status == TaskStatus.awaiting_human_input

        # Resume with human input
        result = pipeline.resume_human_input(task.id, "I want PostgreSQL and dark mode")

        assert result.status == TaskStatus.completed
        mock_rt.run.assert_called_once()
        # Verify human response was injected into prompt
        call_args = mock_rt.run.call_args
        prompt = call_args[0][0]  # first positional arg
        assert "User Input" in prompt or "PostgreSQL" in prompt or "dark mode" in prompt

    def test_before_mode_disabled_skips(self, tmp_path):
        pipeline, agents, queue, mock_rt = self._make_pipeline(tmp_path, [
            {
                "id": "worker",
                "runtime": "claude",
                "human_input": {"enabled": False, "mode": "before"},
                "system_prompt": "Work: {{ input }}",
            },
        ])

        task = Task(description="Do work")
        queue.push(task, "worker")
        result = pipeline.run_task(task, "worker")

        # Should run normally without pausing
        assert result.status == TaskStatus.completed
        mock_rt.run.assert_called_once()


# ── Pipeline: on_demand mode ─────────────────────────────────────────


class TestPipelineOnDemandMode:
    def _make_pipeline(self, tmp_path, agents_dict):
        yaml_content = {"agents": agents_dict}
        project_root = tmp_path / "project"
        aqm_dir = project_root / ".aqm"
        aqm_dir.mkdir(parents=True)
        yaml_path = aqm_dir / "agents.yaml"
        yaml_path.write_text(yaml.dump(yaml_content), encoding="utf-8")

        agents = load_agents(yaml_path)
        queue = FileQueue(aqm_dir / "file-queue")
        pipeline = Pipeline(agents, queue, project_root)

        mock_rt = MagicMock()
        mock_rt.name = "mock"
        pipeline._runtimes["claude"] = mock_rt

        return pipeline, agents, queue, mock_rt

    def test_on_demand_pauses_on_directive(self, tmp_path):
        pipeline, agents, queue, mock_rt = self._make_pipeline(tmp_path, [
            {
                "id": "dev",
                "runtime": "claude",
                "human_input": True,  # shorthand for on_demand
                "system_prompt": "Dev: {{ input }}",
            },
        ])

        mock_rt.run.return_value = (
            "I'll start implementing.\n"
            "HUMAN_INPUT: What authentication method do you prefer?\n"
            "HUMAN_INPUT: Should I add tests?\n"
        )

        task = Task(description="Build auth system")
        queue.push(task, "dev")
        result = pipeline.run_task(task, "dev")

        assert result.status == TaskStatus.awaiting_human_input
        pending = result.metadata["_human_input_pending"]
        assert pending["mode"] == "on_demand"
        assert len(pending["questions"]) == 2
        assert "authentication" in pending["questions"][0].lower()

    def test_on_demand_no_directive_completes(self, tmp_path):
        pipeline, agents, queue, mock_rt = self._make_pipeline(tmp_path, [
            {
                "id": "dev",
                "runtime": "claude",
                "human_input": True,
                "system_prompt": "Dev: {{ input }}",
            },
        ])

        mock_rt.run.return_value = "Done. Everything looks good."

        task = Task(description="Simple task")
        queue.push(task, "dev")
        result = pipeline.run_task(task, "dev")

        assert result.status == TaskStatus.completed

    def test_on_demand_resume(self, tmp_path):
        pipeline, agents, queue, mock_rt = self._make_pipeline(tmp_path, [
            {
                "id": "dev",
                "runtime": "claude",
                "human_input": True,
                "system_prompt": "Dev: {{ input }}",
            },
        ])

        # First call: asks a question
        mock_rt.run.side_effect = [
            "HUMAN_INPUT: Which framework?\nLet me know.",
            "Implemented with Next.js. Done.",
        ]

        task = Task(description="Build frontend")
        queue.push(task, "dev")

        # First run — pauses
        result = pipeline.run_task(task, "dev")
        assert result.status == TaskStatus.awaiting_human_input

        # Resume
        result = pipeline.resume_human_input(task.id, "Use Next.js")
        assert result.status == TaskStatus.completed
        assert mock_rt.run.call_count == 2


# ── Pipeline: callback ───────────────────────────────────────────────


class TestHumanInputCallback:
    def test_on_human_input_request_callback(self, tmp_path):
        yaml_content = {
            "agents": [
                {
                    "id": "planner",
                    "runtime": "claude",
                    "human_input": {"enabled": True, "mode": "before", "prompt": "What features?"},
                    "system_prompt": "Plan: {{ input }}",
                },
            ]
        }
        project_root = tmp_path / "project"
        aqm_dir = project_root / ".aqm"
        aqm_dir.mkdir(parents=True)
        yaml_path = aqm_dir / "agents.yaml"
        yaml_path.write_text(yaml.dump(yaml_content), encoding="utf-8")

        agents = load_agents(yaml_path)
        queue = FileQueue(aqm_dir / "file-queue")
        pipeline = Pipeline(agents, queue, project_root)

        mock_rt = MagicMock()
        mock_rt.name = "mock"
        pipeline._runtimes["claude"] = mock_rt

        callback = MagicMock()

        task = Task(description="Build app")
        queue.push(task, "planner")
        pipeline.run_task(task, "planner", on_human_input_request=callback)

        callback.assert_called_once()
        args = callback.call_args[0]
        assert args[0].id == task.id  # task
        assert args[1] == "planner"   # agent_id
        assert len(args[2]) == 1      # questions list


# ── JSON Schema Validation ──────────────────────────────────────────


SCHEMA_PATH = Path(__file__).resolve().parent.parent / "aqm" / "schema" / "agents-schema.json"


@pytest.fixture
def agents_schema():
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        return json.load(f)


class TestHumanInputSchemaValidation:
    """Ensure human_input in agents.yaml passes JSON Schema validation."""

    def test_human_input_bool_shorthand(self, agents_schema):
        doc = {
            "agents": [
                {"id": "planner", "runtime": "claude", "human_input": True}
            ]
        }
        jsonschema.validate(doc, agents_schema)

    def test_human_input_string_shorthand(self, agents_schema):
        doc = {
            "agents": [
                {"id": "planner", "runtime": "claude", "human_input": "before"}
            ]
        }
        jsonschema.validate(doc, agents_schema)

    def test_human_input_full_config(self, agents_schema):
        doc = {
            "agents": [
                {
                    "id": "planner",
                    "runtime": "claude",
                    "human_input": {
                        "enabled": True,
                        "mode": "before",
                        "prompt": "What features?",
                    },
                }
            ]
        }
        jsonschema.validate(doc, agents_schema)

    def test_human_input_null(self, agents_schema):
        doc = {
            "agents": [
                {"id": "planner", "runtime": "claude", "human_input": None}
            ]
        }
        jsonschema.validate(doc, agents_schema)

    def test_human_input_absent(self, agents_schema):
        doc = {
            "agents": [
                {"id": "planner", "runtime": "claude"}
            ]
        }
        jsonschema.validate(doc, agents_schema)

    def test_context_window_in_schema(self, agents_schema):
        doc = {
            "agents": [
                {"id": "planner", "runtime": "claude", "context_window": 5}
            ]
        }
        jsonschema.validate(doc, agents_schema)
