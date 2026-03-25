"""File-based context accumulation — context.md and stage file management."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path


class ContextFile:
    """Manages the context.md and transcript.md files for each task.

    Each time an agent runs, its result is appended to context.md,
    and the next agent reads this file to understand the overall flow.
    Humans can also open and review this file directly.

    For conversational sessions, transcript.md holds the meeting minutes
    and is injected into agent prompts via the ``{{ transcript }}`` variable.
    """

    def __init__(self, task_dir: Path) -> None:
        self.task_dir = Path(task_dir)
        self.context_path = self.task_dir / "context.md"
        self.transcript_path = self.task_dir / "transcript.md"

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
        """Append a stage section to context.md.

        No longer creates individual ``stage_*.md`` files — context.md
        is the single source of truth and ``read_smart()`` handles
        efficient extraction.
        """
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

        return self.context_path

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

    # ── Per-agent context ─────────────────────────────────────────────

    def agent_context_path(self, agent_id: str) -> Path:
        """Path to the per-agent context file."""
        return self.task_dir / f"agent_{agent_id}.md"

    def read_agent_context(self, agent_id: str) -> str:
        """Return contents of the agent's private context file."""
        path = self.agent_context_path(agent_id)
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8")

    def append_agent_context(
        self,
        *,
        agent_id: str,
        stage_number: int,
        input_text: str,
        output_text: str,
    ) -> None:
        """Append agent's output to its private context file.

        Only stores the output — input is already in context.md and
        would be duplicated.  The agent's private file serves as a
        lightweight memory of what *this* agent produced.
        """
        self.ensure_dir()
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        section = (
            f"## [stage {stage_number}]\n"
            f"**Time**: {now}\n\n"
            f"{output_text}\n\n---\n\n"
        )
        path = self.agent_context_path(agent_id)
        with open(path, "a", encoding="utf-8") as f:
            f.write(section)

    def read_smart(self, context_window: int = 3) -> str:
        """Return a token-efficient view of context.md.

        Old stages (before the window) are compressed to one-line
        summaries.  Recent stages within the window are included in
        full.  When ``context_window`` is 0, the entire file is
        returned unmodified (backward-compatible).

        This typically reduces token cost by 60-80 % for pipelines
        with more than a few stages.
        """
        content = self.read()
        if not content or context_window == 0:
            return content

        sections = content.split("\n---\n")
        sections = [s.strip() for s in sections if s.strip()]

        if len(sections) <= context_window:
            return content  # everything fits in the window

        # Summarize old sections
        old = sections[:-context_window]
        recent = sections[-context_window:]

        summaries: list[str] = []
        for sec in old:
            summaries.append(self._summarize_section(sec))

        parts = ["## Pipeline History (summarized)"]
        parts.extend(f"- {s}" for s in summaries)
        parts.append("")
        parts.append("## Recent Stages (full)")
        parts.append("\n\n---\n\n".join(recent))

        return "\n".join(parts)

    @staticmethod
    def _summarize_section(section: str) -> str:
        """Compress a context.md section into a one-line summary."""
        import re

        # Extract header: ## [stage N] agent_id — task_name
        header_match = re.match(r"##\s*\[([^\]]+)\]\s*(.*)", section)
        header = header_match.group(0).lstrip("# ").strip() if header_match else "unknown"

        # Extract status
        status_match = re.search(r"\*\*Status\*\*:\s*(\S+)", section)
        status = status_match.group(1) if status_match else ""

        # Extract output preview
        output_match = re.search(r"### Output\n(.+)", section, re.DOTALL)
        if output_match:
            output_raw = output_match.group(1).strip()
            # First meaningful line, max 120 chars
            preview = ""
            for line in output_raw.split("\n"):
                line = line.strip()
                if line and not line.startswith("**") and not line.startswith("#"):
                    preview = line[:120]
                    break
            if not preview:
                preview = output_raw[:120]
            if len(output_raw) > 120:
                preview += "..."
        else:
            preview = "(no output)"

        return f"{header} [{status}]: {preview}"

    def read_for_strategy(
        self, agent_id: str, strategy: str, context_window: int = 3,
    ) -> str:
        """Return context based on the agent's context_strategy.

        - ``own``:    agent's private file only (token-efficient)
        - ``shared``: smart-windowed shared context
        - ``both``:   smart-windowed shared + agent's notes (default)

        The ``context_window`` controls how many recent stages are
        included in full; older stages are compressed to summaries.
        Set to 0 for full (unwindowed) context.
        """
        if strategy == "own":
            return self.read_agent_context(agent_id)
        elif strategy == "shared":
            return self.read_smart(context_window)
        else:  # "both"
            shared = self.read_smart(context_window)
            own = self.read_agent_context(agent_id)
            if not own:
                return shared
            return f"{shared}\n\n--- Agent Notes ({agent_id}) ---\n\n{own}"

    # ── Transcript (conversational sessions) ──────────────────────────

    def init_transcript(
        self,
        *,
        topic: str,
        participants: list[str],
    ) -> None:
        """Write the transcript header."""
        self.ensure_dir()
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        header = (
            f"# Meeting Transcript\n"
            f"**Topic**: {topic}\n"
            f"**Participants**: {', '.join(participants)}\n"
            f"**Started**: {now}\n\n---\n\n"
        )
        self.transcript_path.write_text(header, encoding="utf-8")

    def append_turn(
        self,
        *,
        round_number: int,
        agent_id: str,
        message: str,
        is_round_start: bool = False,
    ) -> None:
        """Append a single turn to the transcript."""
        self.ensure_dir()
        now = datetime.now(timezone.utc).strftime("%H:%M:%S")
        parts: list[str] = []
        if is_round_start:
            parts.append(f"## Round {round_number}\n\n")
        parts.append(f"### [{agent_id}] ({now})\n{message}\n\n")
        with open(self.transcript_path, "a", encoding="utf-8") as f:
            f.write("".join(parts))

    def append_consensus(
        self,
        *,
        round_number: int,
        agreed_by: list[str],
        summary: str,
    ) -> None:
        """Append the consensus section to the transcript."""
        section = (
            f"---\n\n"
            f"## Consensus Reached (Round {round_number})\n"
            f"**Agreed by**: {', '.join(agreed_by)}\n\n"
            f"### Final Summary\n{summary}\n"
        )
        with open(self.transcript_path, "a", encoding="utf-8") as f:
            f.write(section)

    def read_transcript(self) -> str:
        """Return the full transcript contents."""
        if not self.transcript_path.exists():
            return ""
        return self.transcript_path.read_text(encoding="utf-8")

    # ── Human input recording ─────────────────────────────────────

    def append_human_input(
        self,
        *,
        agent_id: str,
        question: str,
        response: str,
    ) -> None:
        """Record a human input exchange in both shared and agent context."""
        self.ensure_dir()
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")

        section = (
            f"## [human input] for {agent_id}\n"
            f"**Time**: {now}\n\n"
            f"### Question\n{question}\n\n"
            f"### User Response\n{response}\n\n---\n\n"
        )

        # Append to shared context
        with open(self.context_path, "a", encoding="utf-8") as f:
            f.write(section)

        # Append to agent's private context
        path = self.agent_context_path(agent_id)
        with open(path, "a", encoding="utf-8") as f:
            f.write(section)
