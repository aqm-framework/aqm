"""AbstractRuntime — runtime interface."""

from __future__ import annotations

from abc import ABC, abstractmethod

from agent_queue.core.agent import AgentDefinition
from agent_queue.core.task import Task


class AbstractRuntime(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        ...

    @abstractmethod
    def run(self, prompt: str, agent: AgentDefinition, task: Task) -> str:
        """Run the agent and return text output."""
        ...
