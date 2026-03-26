"""Task, StageRecord, TaskStatus — task data models."""

from __future__ import annotations

import hashlib
import time
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class TaskStatus(str, Enum):
    pending = "pending"
    in_progress = "in_progress"
    awaiting_gate = "awaiting_gate"
    awaiting_human_input = "awaiting_human_input"
    approved = "approved"
    rejected = "rejected"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"
    stalled = "stalled"  # was in_progress when server restarted


def _generate_task_id() -> str:
    """Generate a short task ID in T-XXXXXX format."""
    raw = hashlib.sha256(f"{time.time_ns()}".encode()).hexdigest()[:6].upper()
    return f"T-{raw}"


class StageRecord(BaseModel):
    """Record for a single agent execution stage."""

    stage_number: int
    agent_id: str
    task_name: str = ""
    input_text: str = ""
    output_text: str = ""
    gate_result: Optional[Literal["approved", "rejected"]] = None
    reject_reason: Optional[str] = None
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: Optional[datetime] = None


class TaskPriority(int, Enum):
    """Task execution priority. Lower number = higher priority."""
    critical = 0
    high = 1
    normal = 2
    low = 3


class Task(BaseModel):
    """A task passed between agents through queues."""

    id: str = Field(default_factory=_generate_task_id)
    description: str
    status: TaskStatus = TaskStatus.pending
    priority: TaskPriority = TaskPriority.normal
    current_agent_id: Optional[str] = None
    current_queue: Optional[str] = None
    stages: list[StageRecord] = Field(default_factory=list)
    context_dir: Optional[str] = None
    parent_task_id: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = Field(default_factory=dict)

    def touch(self) -> None:
        self.updated_at = datetime.now(timezone.utc)

    def add_stage(self, stage: StageRecord) -> None:
        self.stages.append(stage)
        self.touch()

    @property
    def latest_stage(self) -> Optional[StageRecord]:
        return self.stages[-1] if self.stages else None

    @property
    def next_stage_number(self) -> int:
        return len(self.stages) + 1

    def truncate_stages(self, keep_before: int) -> list[StageRecord]:
        """Remove stages with ``stage_number >= keep_before``.

        Returns the removed stages.  ``next_stage_number`` adjusts
        automatically since it is derived from ``len(self.stages)``.
        """
        removed = [s for s in self.stages if s.stage_number >= keep_before]
        self.stages = [s for s in self.stages if s.stage_number < keep_before]
        self.touch()
        return removed

    @property
    def short_id(self) -> str:
        return self.id
