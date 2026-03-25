"""Test fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from aqm.core.agent import AgentDefinition, load_agents
from aqm.core.project import init_project
from aqm.core.task import Task
from aqm.queue.file import FileQueue
from aqm.runtime.base import AbstractRuntime


class MockRuntime(AbstractRuntime):
    """Mock runtime for testing. Returns a fixed response."""

    def __init__(self, response: str = "mock output") -> None:
        self._response = response

    @property
    def name(self) -> str:
        return "mock"

    def run(self, prompt: str, agent: AgentDefinition, task: Task) -> str:
        return self._response


@pytest.fixture
def tmp_project(tmp_path: Path) -> Path:
    """Temporary project directory."""
    return init_project(tmp_path)


@pytest.fixture
def sample_agents_yaml(tmp_project: Path) -> Path:
    """Create agents.yaml for testing."""
    yaml_content = {
        "agents": [
            {
                "id": "agent_a",
                "name": "Agent A",
                "runtime": "claude",
                "system_prompt": "Process: {{ input }}",
                "handoffs": [
                    {
                        "to": "agent_b",
                        "task": "next_task",
                        "condition": "always",
                        "payload": "{{ output }}",
                    }
                ],
            },
            {
                "id": "agent_b",
                "name": "Agent B",
                "runtime": "claude",
                "system_prompt": "Handle: {{ input }}",
                "gate": {"type": "llm", "prompt": "Is this good?"},
                "handoffs": [
                    {
                        "to": "agent_c",
                        "task": "final_task",
                        "condition": "on_approve",
                    },
                    {
                        "to": "agent_a",
                        "task": "retry",
                        "condition": "on_reject",
                        "payload": "{{ output }}\nREJECT: {{ reject_reason }}",
                    },
                ],
            },
            {
                "id": "agent_c",
                "name": "Agent C",
                "runtime": "claude",
                "system_prompt": "Execute: {{ input }}",
                "mcp": [{"server": "filesystem"}],
            },
        ]
    }
    yaml_path = tmp_project / ".aqm" / "agents.yaml"
    yaml_path.write_text(yaml.dump(yaml_content), encoding="utf-8")
    return yaml_path


@pytest.fixture
def sample_agents(sample_agents_yaml: Path) -> dict[str, AgentDefinition]:
    return load_agents(sample_agents_yaml)


@pytest.fixture
def file_queue(tmp_project: Path) -> FileQueue:
    queue_dir = tmp_project / ".aqm" / "file-queue"
    return FileQueue(queue_dir)


@pytest.fixture
def mock_runtime() -> MockRuntime:
    return MockRuntime()
