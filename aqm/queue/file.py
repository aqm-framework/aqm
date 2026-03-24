"""FileQueue — file-based queue (for testing/debugging)."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

from aqm.core.task import Task, TaskStatus
from aqm.queue.base import AbstractQueue

# Monotonically increasing counter to ensure file ordering
_push_counter = 0


class FileQueue(AbstractQueue):
    """Simple queue implementation that stores tasks as JSON files."""

    def __init__(self, base_dir: Path) -> None:
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _queue_dir(self, queue_name: str) -> Path:
        d = self.base_dir / queue_name
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _task_path(self, queue_name: str, task_id: str, seq: int = 0) -> Path:
        return self._queue_dir(queue_name) / f"{seq:010d}_{task_id}.json"

    def _find_task_file(self, queue_name: str, task_id: str) -> Path | None:
        """Find a file containing the task_id in the queue directory."""
        q_dir = self._queue_dir(queue_name)
        for f in q_dir.glob(f"*_{task_id}.json"):
            return f
        return None

    def push(self, task: Task, queue_name: str) -> None:
        global _push_counter

        if task.current_queue and task.current_queue != queue_name:
            old_file = self._find_task_file(task.current_queue, task.id)
            if old_file and old_file.exists():
                old_file.unlink()

        task.current_queue = queue_name
        task.touch()
        _push_counter += 1
        path = self._task_path(queue_name, task.id, _push_counter)
        path.write_text(task.model_dump_json(indent=2), encoding="utf-8")

    def pop(self, queue_name: str) -> Optional[Task]:
        q_dir = self._queue_dir(queue_name)
        files = sorted(q_dir.glob("*.json"))
        for f in files:
            task = Task.model_validate_json(f.read_text(encoding="utf-8"))
            if task.status == TaskStatus.pending:
                task.status = TaskStatus.in_progress
                task.touch()
                f.write_text(task.model_dump_json(indent=2), encoding="utf-8")
                return task
        return None

    def peek(self, queue_name: str) -> Optional[Task]:
        q_dir = self._queue_dir(queue_name)
        files = sorted(q_dir.glob("*.json"))
        for f in files:
            task = Task.model_validate_json(f.read_text(encoding="utf-8"))
            if task.status == TaskStatus.pending:
                return task
        return None

    def update(self, task: Task) -> None:
        task.touch()
        if task.current_queue:
            f = self._find_task_file(task.current_queue, task.id)
            if f:
                f.write_text(task.model_dump_json(indent=2), encoding="utf-8")

    def get(self, task_id: str) -> Optional[Task]:
        for q_dir in self.base_dir.iterdir():
            if not q_dir.is_dir():
                continue
            for f in q_dir.glob(f"*_{task_id}.json"):
                return Task.model_validate_json(
                    f.read_text(encoding="utf-8")
                )
        return None

    def list_tasks(
        self,
        status: Optional[TaskStatus] = None,
        queue_name: Optional[str] = None,
    ) -> list[Task]:
        tasks: list[Task] = []
        dirs = (
            [self._queue_dir(queue_name)]
            if queue_name
            else [d for d in self.base_dir.iterdir() if d.is_dir()]
        )
        for q_dir in dirs:
            for f in q_dir.glob("*.json"):
                task = Task.model_validate_json(
                    f.read_text(encoding="utf-8")
                )
                if status is None or task.status == status:
                    tasks.append(task)
        return sorted(tasks, key=lambda t: t.created_at, reverse=True)

    def list_queues(self) -> list[str]:
        return sorted(
            d.name
            for d in self.base_dir.iterdir()
            if d.is_dir() and list(d.glob("*.json"))
        )
