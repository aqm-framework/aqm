"""SQLiteQueue — SQLite-based queue implementation."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

from aqm.core.task import Task, TaskStatus
from aqm.queue.base import AbstractQueue

CREATE_TABLE = """\
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    queue_name TEXT NOT NULL,
    status TEXT NOT NULL,
    data TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

CREATE_INDEXES = """\
CREATE INDEX IF NOT EXISTS idx_queue_status ON tasks(queue_name, status);
CREATE INDEX IF NOT EXISTS idx_status ON tasks(status);
"""


class SQLiteQueue(AbstractQueue):
    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            str(self.db_path),
            check_same_thread=False,
        )
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._init_db()

    def _init_db(self) -> None:
        self._conn.executescript(CREATE_TABLE + CREATE_INDEXES)
        self._conn.commit()

    def push(self, task: Task, queue_name: str) -> None:
        task.current_queue = queue_name
        task.touch()
        self._conn.execute(
            "INSERT OR REPLACE INTO tasks "
            "(id, queue_name, status, data, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                task.id,
                queue_name,
                task.status.value,
                task.model_dump_json(),
                task.created_at.isoformat(),
                task.updated_at.isoformat(),
            ),
        )
        self._conn.commit()

    def pop(self, queue_name: str) -> Optional[Task]:
        cursor = self._conn.execute(
            "SELECT data FROM tasks WHERE queue_name = ? AND status = ? "
            "ORDER BY created_at ASC LIMIT 1",
            (queue_name, TaskStatus.pending.value),
        )
        row = cursor.fetchone()
        if not row:
            return None

        task = Task.model_validate_json(row[0])
        task.status = TaskStatus.in_progress
        task.touch()
        self.update(task)
        return task

    def peek(self, queue_name: str) -> Optional[Task]:
        cursor = self._conn.execute(
            "SELECT data FROM tasks WHERE queue_name = ? AND status = ? "
            "ORDER BY created_at ASC LIMIT 1",
            (queue_name, TaskStatus.pending.value),
        )
        row = cursor.fetchone()
        if not row:
            return None
        return Task.model_validate_json(row[0])

    def update(self, task: Task) -> None:
        task.touch()
        self._conn.execute(
            "UPDATE tasks SET queue_name = ?, status = ?, data = ?, "
            "updated_at = ? WHERE id = ?",
            (
                task.current_queue or "",
                task.status.value,
                task.model_dump_json(),
                task.updated_at.isoformat(),
                task.id,
            ),
        )
        self._conn.commit()

    def get(self, task_id: str) -> Optional[Task]:
        cursor = self._conn.execute(
            "SELECT data FROM tasks WHERE id = ?", (task_id,)
        )
        row = cursor.fetchone()
        if not row:
            return None
        return Task.model_validate_json(row[0])

    def list_tasks(
        self,
        status: Optional[TaskStatus] = None,
        queue_name: Optional[str] = None,
    ) -> list[Task]:
        query = "SELECT data FROM tasks WHERE 1=1"
        params: list[str] = []

        if status is not None:
            query += " AND status = ?"
            params.append(status.value)
        if queue_name is not None:
            query += " AND queue_name = ?"
            params.append(queue_name)

        query += " ORDER BY created_at DESC"
        cursor = self._conn.execute(query, params)
        return [Task.model_validate_json(row[0]) for row in cursor.fetchall()]

    def list_queues(self) -> list[str]:
        cursor = self._conn.execute(
            "SELECT DISTINCT queue_name FROM tasks ORDER BY queue_name"
        )
        return [row[0] for row in cursor.fetchall()]

    def close(self) -> None:
        self._conn.close()
