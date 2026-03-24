"""File-based context accumulation — context.md and stage file management."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path


class ContextFile:
    """Manages the context.md file for each task.

    Each time an agent runs, its result is appended to context.md,
    and the next agent reads this file to understand the overall flow.
    Humans can also open and review this file directly.
    """

    def __init__(self, task_dir: Path) -> None:
        self.task_dir = Path(task_dir)
        self.context_path = self.task_dir / "context.md"

    def ensure_dir(self) -> None:
        self.task_dir.mkdir(parents=True, exist_ok=True)

    def append_stage(
        self,
        *,
        stage_number: int,
        agent_id: str,
        task_name: str,
        status: str,
        input_text: str,
        output_text: str,
        reject_reason: str | None = None,
    ) -> Path:
        """Append a stage section to context.md and save an individual stage file."""
        self.ensure_dir()
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")

        section = f"## [stage {stage_number}] {agent_id} — {task_name}\n"
        section += f"**Time**: {now}\n"
        section += f"**Status**: {status}\n"
        if reject_reason:
            section += f"**reject_reason**: {reject_reason}\n"
        section += f"\n### Input\n{input_text}\n\n### Output\n{output_text}\n\n---\n\n"

        with open(self.context_path, "a", encoding="utf-8") as f:
            f.write(section)

        # Save individual stage file
        stage_file = self.task_dir / f"stage_{stage_number:02d}_{agent_id}.md"
        stage_file.write_text(section, encoding="utf-8")

        return stage_file

    def save_payload(self, payload: str) -> Path:
        """Save the current payload to current_payload.md."""
        self.ensure_dir()
        path = self.task_dir / "current_payload.md"
        path.write_text(payload, encoding="utf-8")
        return path

    def read(self) -> str:
        """Return the full contents of context.md."""
        if not self.context_path.exists():
            return ""
        return self.context_path.read_text(encoding="utf-8")

    def read_latest(self, n: int = 1) -> str:
        """Return the last n sections."""
        content = self.read()
        if not content:
            return ""
        sections = content.split("---")
        sections = [s.strip() for s in sections if s.strip()]
        return "\n\n---\n\n".join(sections[-n:])
