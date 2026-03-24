"""AbstractQueue — queue interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from agent_queue.core.task import Task, TaskStatus


class AbstractQueue(ABC):
    @abstractmethod
    def push(self, task: Task, queue_name: str) -> None:
        """Add a task to the queue."""
        ...

    @abstractmethod
    def pop(self, queue_name: str) -> Optional[Task]:
        """Retrieve the oldest pending task from the queue (FIFO)."""
        ...

    @abstractmethod
    def peek(self, queue_name: str) -> Optional[Task]:
        """Check the next task in the queue (without removing it)."""
        ...

    @abstractmethod
    def update(self, task: Task) -> None:
        """Update the task status."""
        ...

    @abstractmethod
    def get(self, task_id: str) -> Optional[Task]:
        """Retrieve a task by ID."""
        ...

    @abstractmethod
    def list_tasks(
        self,
        status: Optional[TaskStatus] = None,
        queue_name: Optional[str] = None,
    ) -> list[Task]:
        """List tasks."""
        ...

    @abstractmethod
    def list_queues(self) -> list[str]:
        """Return all queue names."""
        ...

    def awaiting_gate(self) -> list[Task]:
        """List tasks awaiting gate approval."""
        return self.list_tasks(status=TaskStatus.awaiting_gate)
