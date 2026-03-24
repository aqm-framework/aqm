"""AbstractRuntime — runtime interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable, Optional

from aqm.core.agent import AgentDefinition
from aqm.core.task import Task

# Callback type for streaming output lines
OutputCallback = Optional[Callable[[str], None]]


class AbstractRuntime(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        ...

    @abstractmethod
    def run(
        self,
        prompt: str,
        agent: AgentDefinition,
        task: Task,
        on_output: OutputCallback = None,
    ) -> str:
        """Run the agent and return text output.

        Args:
            prompt: The prompt to send.
            agent: Agent definition.
            task: Current task.
            on_output: Optional callback invoked with each line of output
                       as it streams from the subprocess.
        """
        ...
