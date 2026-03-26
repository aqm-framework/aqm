"""AbstractRuntime — runtime interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Callable, Optional

from aqm.core.agent import AgentDefinition
from aqm.core.task import Task

# Callback type for streaming output lines
OutputCallback = Optional[Callable[[str], None]]
# Callback type for streaming thinking lines
ThinkingCallback = Optional[Callable[[str], None]]
# Callback type for tool use events: (event_type, data_dict)
#   event_type: "tool_start", "tool_input", "tool_result", "tool_error"
#   data_dict: {"tool": "Read", "input": {...}, "output": "...", ...}
ToolCallback = Optional[Callable[[str, dict[str, Any]], None]]


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
        on_thinking: ThinkingCallback = None,
        on_tool: ToolCallback = None,
    ) -> str:
        """Run the agent and return text output.

        Args:
            prompt: The prompt to send.
            agent: Agent definition.
            task: Current task.
            on_output: Optional callback invoked with each line of output
                       as it streams from the subprocess.
            on_thinking: Optional callback invoked with each thinking line
                         as it streams from the subprocess.
            on_tool: Optional callback invoked with tool use events
                     (tool_start, tool_input, tool_result, tool_error).
        """
        ...


class RuntimeExecutionError(RuntimeError):
    """Runtime failure that preserves partial output accumulated before the error."""

    def __init__(self, message: str, partial_output: str = ""):
        super().__init__(message)
        self.partial_output = partial_output
