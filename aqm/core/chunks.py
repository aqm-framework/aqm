"""Chunk decomposition — break tasks into trackable work units.

Chunks are small, atomic pieces of work that make up a larger task.
Agents can add, complete, or remove chunks during session discussions
via output directives (``CHUNK_ADD:``, ``CHUNK_DONE:``, ``CHUNK_REMOVE:``).
Users can also manage chunks via CLI (``aqm chunks``) and web API.

Storage: ``chunks.json`` in the task directory alongside ``context.md``
and ``transcript.md``.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class ChunkStatus(str, Enum):
    pending = "pending"
    in_progress = "in_progress"
    done = "done"


def _generate_chunk_id(existing_ids: set[str]) -> str:
    """Generate the next chunk ID in C-001, C-002, ... sequence."""
    n = 1
    while True:
        cid = f"C-{n:03d}"
        if cid not in existing_ids:
            return cid
        n += 1


class Chunk(BaseModel):
    """A single work unit within a task."""

    id: str
    description: str
    status: ChunkStatus = ChunkStatus.pending
    created_by: str = ""
    completed_by: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ChunkList(BaseModel):
    """Serializable collection of chunks."""

    chunks: list[Chunk] = Field(default_factory=list)


class ChunkManager:
    """Manage chunks.json for a single task directory."""

    def __init__(self, task_dir: Path) -> None:
        self.task_dir = Path(task_dir)
        self.chunks_path = self.task_dir / "chunks.json"

    def _ensure_dir(self) -> None:
        self.task_dir.mkdir(parents=True, exist_ok=True)

    def load(self) -> ChunkList:
        """Load chunks from disk. Returns empty list if file doesn't exist."""
        if not self.chunks_path.exists():
            return ChunkList()
        raw = self.chunks_path.read_text(encoding="utf-8")
        return ChunkList.model_validate_json(raw)

    def save(self, chunk_list: ChunkList) -> None:
        """Persist chunks to disk."""
        self._ensure_dir()
        self.chunks_path.write_text(
            chunk_list.model_dump_json(indent=2),
            encoding="utf-8",
        )

    def add(self, description: str, created_by: str = "user") -> Chunk:
        """Add a new chunk and return it."""
        cl = self.load()
        existing_ids = {c.id for c in cl.chunks}
        chunk = Chunk(
            id=_generate_chunk_id(existing_ids),
            description=description,
            created_by=created_by,
        )
        cl.chunks.append(chunk)
        self.save(cl)
        logger.info("[ChunkManager] Added %s: %s (by %s)", chunk.id, description, created_by)
        return chunk

    def remove(self, chunk_id: str) -> bool:
        """Remove a chunk by ID. Returns True if found and removed."""
        cl = self.load()
        before = len(cl.chunks)
        cl.chunks = [c for c in cl.chunks if c.id != chunk_id]
        if len(cl.chunks) < before:
            self.save(cl)
            logger.info("[ChunkManager] Removed %s", chunk_id)
            return True
        return False

    def mark_done(self, chunk_id: str, completed_by: str = "") -> bool:
        """Mark a chunk as done. Returns True if found."""
        cl = self.load()
        for c in cl.chunks:
            if c.id == chunk_id:
                c.status = ChunkStatus.done
                c.completed_by = completed_by
                c.updated_at = datetime.now(timezone.utc)
                self.save(cl)
                logger.info("[ChunkManager] %s marked done (by %s)", chunk_id, completed_by)
                return True
        return False

    def mark_in_progress(self, chunk_id: str) -> bool:
        """Mark a chunk as in_progress. Returns True if found."""
        cl = self.load()
        for c in cl.chunks:
            if c.id == chunk_id:
                c.status = ChunkStatus.in_progress
                c.updated_at = datetime.now(timezone.utc)
                self.save(cl)
                return True
        return False

    def all_done(self) -> bool:
        """Return True if all chunks are done (or no chunks exist)."""
        cl = self.load()
        if not cl.chunks:
            return True
        return all(c.status == ChunkStatus.done for c in cl.chunks)

    def counts(self) -> tuple[int, int, int]:
        """Return (total, done, pending) counts."""
        cl = self.load()
        total = len(cl.chunks)
        done = sum(1 for c in cl.chunks if c.status == ChunkStatus.done)
        return total, done, total - done

    def summary(self) -> str:
        """Render chunks as a markdown table for prompt injection via {{ chunks }}."""
        cl = self.load()
        if not cl.chunks:
            return "(no chunks defined)"
        lines = ["| ID | Status | Description |", "|---|---|---|"]
        for c in cl.chunks:
            status_mark = {"pending": "⬜", "in_progress": "🔄", "done": "✅"}.get(
                c.status.value, c.status.value
            )
            safe_desc = c.description.replace("|", "\\|")
            lines.append(f"| {c.id} | {status_mark} {c.status.value} | {safe_desc} |")
        total, done, pending = self.counts()
        lines.append(f"\n**Progress: {done}/{total} done, {pending} remaining**")
        return "\n".join(lines)

    def init_from_config(self, initial: list[str], created_by: str = "config") -> None:
        """Seed chunks from YAML config. Only runs if no chunks exist yet."""
        cl = self.load()
        if cl.chunks:
            return  # Already initialised
        for desc in initial:
            existing_ids = {c.id for c in cl.chunks}
            chunk = Chunk(
                id=_generate_chunk_id(existing_ids),
                description=desc,
                created_by=created_by,
            )
            cl.chunks.append(chunk)
        self.save(cl)
        logger.info("[ChunkManager] Initialised %d chunks from config", len(initial))


# ---------------------------------------------------------------------------
# Directive parsing (used by pipeline)
# ---------------------------------------------------------------------------

_CHUNK_ADD_RE = re.compile(r"CHUNK_ADD:\s*(.+)", re.IGNORECASE)
_CHUNK_DONE_RE = re.compile(r"CHUNK_DONE:\s*(\S+)", re.IGNORECASE)
_CHUNK_REMOVE_RE = re.compile(r"CHUNK_REMOVE:\s*(\S+)", re.IGNORECASE)


def parse_chunk_directives(
    message: str,
    chunk_mgr: ChunkManager,
    agent_id: str,
) -> list[dict]:
    """Parse CHUNK_ADD/DONE/REMOVE directives from agent output.

    Returns a list of action dicts for SSE broadcasting:
    ``[{"action": "add", "chunk_id": "C-003", "description": "...", "agent_id": "..."}]``
    """
    actions: list[dict] = []

    for m in _CHUNK_ADD_RE.finditer(message):
        desc = m.group(1).strip()
        if desc:
            chunk = chunk_mgr.add(desc, created_by=agent_id)
            actions.append({
                "action": "add",
                "chunk_id": chunk.id,
                "description": desc,
                "agent_id": agent_id,
            })

    for m in _CHUNK_DONE_RE.finditer(message):
        cid = m.group(1).strip()
        if chunk_mgr.mark_done(cid, completed_by=agent_id):
            actions.append({
                "action": "done",
                "chunk_id": cid,
                "agent_id": agent_id,
            })
        else:
            logger.warning(
                "[ChunkDirective] CHUNK_DONE: %s not found (agent=%s)",
                cid, agent_id,
            )

    for m in _CHUNK_REMOVE_RE.finditer(message):
        cid = m.group(1).strip()
        if chunk_mgr.remove(cid):
            actions.append({
                "action": "remove",
                "chunk_id": cid,
                "agent_id": agent_id,
            })
        else:
            logger.warning(
                "[ChunkDirective] CHUNK_REMOVE: %s not found (agent=%s)",
                cid, agent_id,
            )

    return actions
