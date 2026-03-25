"""Task API endpoints — CRUD, run, fix, resume, SSE."""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from starlette.responses import StreamingResponse

from aqm.core.agent import AgentDefinition, load_agents
from aqm.core.context_file import ContextFile
from aqm.core.pipeline import Pipeline
from aqm.core.project import get_agents_yaml_path, get_db_path, get_tasks_dir
from aqm.core.task import Task, TaskStatus
from aqm.queue.sqlite import SQLiteQueue
from aqm.web.api.sse import broadcast_event, subscribe

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class CreateTaskRequest(BaseModel):
    description: str
    agent_id: Optional[str] = None


class RunPipelineRequest(BaseModel):
    description: str
    agent_id: Optional[str] = None
    params: Optional[dict[str, str]] = None
    priority: str = "normal"
    pipeline: Optional[str] = None


class FixRequest(BaseModel):
    parent_task_id: str
    description: str
    agent_id: Optional[str] = None
    params: Optional[dict[str, str]] = None
    pipeline: Optional[str] = None


class GateActionRequest(BaseModel):
    reason: Optional[str] = None


class ResumeRequest(BaseModel):
    decision: str  # "approved" or "rejected"
    reason: Optional[str] = None


class PriorityRequest(BaseModel):
    priority: str  # critical, high, normal, low


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------

def create_tasks_router(project_root: Path) -> APIRouter:
    router = APIRouter()
    db_path = get_db_path(project_root)

    def _get_queue() -> SQLiteQueue:
        return SQLiteQueue(db_path)

    def _get_agents(cli_params=None, pipeline: str | None = None) -> dict[str, AgentDefinition]:
        try:
            path = get_agents_yaml_path(project_root, pipeline)
            if path.exists():
                return load_agents(path, cli_params=cli_params)
            return {}
        except ValueError as exc:
            raise HTTPException(
                400,
                f"Pipeline configuration error: {exc}. "
                f"Set required parameters via --param key=value or .aqm/params.yaml.",
            )

    def _run_pipeline_bg(task: Task, start_agent: str, input_text: str | None, cli_params=None, pipeline: str | None = None):
        """Run pipeline in a background thread with SSE broadcasting."""
        try:
            agents = _get_agents(cli_params=cli_params, pipeline=pipeline)
            queue = _get_queue()
            pipe = Pipeline(agents, queue, project_root)

            def on_stage_start(t, agent_id, stage_number):
                broadcast_event(t.id, "stage_start", {
                    "agent_id": agent_id, "stage_number": stage_number,
                })

            def on_stage_complete(t, stage):
                # Detect session turns for richer SSE events
                if stage.task_name.startswith("session:"):
                    parts = stage.task_name.split(":")
                    session_id = parts[1] if len(parts) > 1 else ""
                    round_str = parts[2] if len(parts) > 2 else ""
                    round_num = int(round_str[1:]) if round_str.startswith("r") else 0
                    has_vote = "VOTE: AGREE" in stage.output_text.upper()
                    broadcast_event(t.id, "turn_complete", {
                        "session_id": session_id,
                        "agent_id": stage.agent_id,
                        "round": round_num,
                        "stage_number": stage.stage_number,
                        "message_preview": stage.output_text[:300],
                        "agreed": has_vote,
                    })
                else:
                    broadcast_event(t.id, "stage_complete", {
                        "agent_id": stage.agent_id,
                        "stage_number": stage.stage_number,
                        "output_preview": stage.output_text[:200],
                        "gate_result": stage.gate_result,
                    })

            def on_output(line):
                broadcast_event(task.id, "stage_output", {"text": line})

            def on_thinking(line):
                broadcast_event(task.id, "stage_thinking", {"text": line})

            result = pipe.run_task(
                task, start_agent,
                input_text=input_text,
                on_stage_complete=on_stage_complete,
                on_stage_start=on_stage_start,
                on_output=on_output,
                on_thinking=on_thinking,
            )

            if result.status == TaskStatus.awaiting_gate:
                broadcast_event(task.id, "gate_waiting", {
                    "agent_id": result.current_agent_id,
                })
            elif result.status == TaskStatus.completed:
                event_data = {
                    "status": "completed",
                    "total_stages": len(result.stages),
                }
                # Include session consensus info
                if "session_consensus" in result.metadata:
                    event_data["session_consensus"] = result.metadata["session_consensus"]
                    event_data["session_rounds"] = result.metadata.get("session_rounds")
                broadcast_event(task.id, "task_complete", event_data)
            elif result.status == TaskStatus.failed:
                broadcast_event(task.id, "task_failed", {
                    "error": result.metadata.get("error", "Unknown error"),
                    "agent_id": result.current_agent_id,
                })
            queue.close()
        except Exception as e:
            logger.error("Pipeline execution failed: %s", e)
            broadcast_event(task.id, "task_failed", {"error": str(e)})

    # ── CRUD ───────────────────────────────────────────────────────────

    @router.post("/api/tasks")
    async def api_create_task(req: CreateTaskRequest):
        agents = _get_agents()
        if not agents:
            raise HTTPException(500, "No agents defined in agents.yaml")
        agent_id = req.agent_id
        if agent_id and agent_id not in agents:
            raise HTTPException(400, f"Agent '{agent_id}' not found")
        if not agent_id:
            agent_id = next(iter(agents))
        task = Task(description=req.description, current_agent_id=agent_id)
        queue = _get_queue()
        try:
            queue.push(task, queue_name=agent_id)
            return {"id": task.id, "status": task.status.value, "agent_id": agent_id}
        finally:
            queue.close()

    @router.get("/api/tasks")
    async def api_list_tasks(
        status: Optional[str] = Query(None),
        limit: Optional[int] = Query(None, ge=1, le=500),
    ):
        queue = _get_queue()
        try:
            task_status = None
            if status:
                try:
                    task_status = TaskStatus(status)
                except ValueError:
                    raise HTTPException(400, f"Invalid status: {status}")
            tasks = queue.list_tasks(status=task_status)
            if limit:
                tasks = tasks[:limit]
            return [
                {
                    "id": t.id,
                    "description": t.description,
                    "status": t.status.value,
                    "current_agent_id": t.current_agent_id,
                    "stage_count": len(t.stages),
                    "created_at": t.created_at.isoformat(),
                    "updated_at": t.updated_at.isoformat(),
                }
                for t in tasks
            ]
        finally:
            queue.close()

    @router.get("/api/tasks/{task_id}")
    async def api_get_task(task_id: str):
        queue = _get_queue()
        try:
            task = queue.get(task_id)
            if task is None:
                raise HTTPException(404, "Task not found")
            return task.model_dump(mode="json")
        finally:
            queue.close()

    # ── Run Pipeline ───────────────────────────────────────────────────

    @router.post("/api/run")
    async def api_run_pipeline(req: RunPipelineRequest):
        agents = _get_agents(cli_params=req.params, pipeline=req.pipeline)
        if not agents:
            raise HTTPException(500, "No agents defined in agents.yaml")

        if req.agent_id:
            start_agent = req.agent_id
        else:
            from aqm.core.agent import get_entry_point, resolve_start_agent
            entry_point = get_entry_point(get_agents_yaml_path(project_root, req.pipeline))
            if entry_point == "auto":
                start_agent = resolve_start_agent(req.description, agents)
            else:
                start_agent = next(iter(agents))

        if start_agent not in agents:
            raise HTTPException(400, f"Agent '{start_agent}' not found")

        from aqm.core.task import TaskPriority
        try:
            task_priority = TaskPriority[req.priority]
        except KeyError:
            raise HTTPException(400, f"Invalid priority: {req.priority}")

        task = Task(
            description=req.description,
            current_agent_id=start_agent,
            priority=task_priority,
            metadata={"pipeline": req.pipeline} if req.pipeline else {},
        )
        queue = _get_queue()
        try:
            queue.push(task, queue_name=start_agent)
        finally:
            queue.close()

        thread = threading.Thread(
            target=_run_pipeline_bg,
            args=(task, start_agent, None, req.params, req.pipeline),
            daemon=True,
        )
        thread.start()

        return {"task_id": task.id, "status": "started", "agent_id": start_agent}

    # ── Fix ────────────────────────────────────────────────────────────

    @router.post("/api/fix")
    async def api_fix(req: FixRequest):
        queue = _get_queue()
        try:
            parent_task = queue.get(req.parent_task_id)
            if not parent_task:
                raise HTTPException(404, f"Task '{req.parent_task_id}' not found")
        finally:
            queue.close()

        # Load parent context
        tasks_dir = get_tasks_dir(project_root)
        context_path = tasks_dir / req.parent_task_id / "context.md"
        parent_context = ""
        if context_path.exists():
            parent_context = context_path.read_text(encoding="utf-8")

        agents = _get_agents(cli_params=req.params)
        if not agents:
            raise HTTPException(500, "No agents defined")

        start_agent = req.agent_id or next(iter(agents))

        followup_input = (
            f"[FIX — follow-up from {req.parent_task_id}]\n"
            f"Description: {parent_task.description}\n\n"
            f"--- Previous context ---\n{parent_context}\n"
            f"--- Fix request ---\n{req.description}"
        )

        task = Task(
            description=f"[fix] {req.description}",
            parent_task_id=req.parent_task_id,
            metadata={"kind": "fix", "parent_task_id": req.parent_task_id},
            current_agent_id=start_agent,
        )

        queue = _get_queue()
        try:
            queue.push(task, queue_name=start_agent)
        finally:
            queue.close()

        thread = threading.Thread(
            target=_run_pipeline_bg,
            args=(task, start_agent, followup_input, req.params),
            daemon=True,
        )
        thread.start()

        return {"task_id": task.id, "status": "started", "agent_id": start_agent}

    # ── Gate Actions ───────────────────────────────────────────────────

    @router.post("/api/tasks/{task_id}/approve")
    async def api_approve(task_id: str, req: Optional[GateActionRequest] = None):
        queue = _get_queue()
        try:
            task = queue.get(task_id)
            if not task:
                raise HTTPException(404, "Task not found")
            if task.status != TaskStatus.awaiting_gate:
                raise HTTPException(400, f"Task not awaiting gate (status: {task.status.value})")

            latest = task.latest_stage
            if latest:
                latest.gate_result = "approved"
                latest.reject_reason = req.reason if req and req.reason else ""
                latest.finished_at = datetime.now(timezone.utc)

            task.status = TaskStatus.approved
            task.touch()
            queue.update(task)
        finally:
            queue.close()

        # Resume pipeline in background
        reason = req.reason if req and req.reason else ""
        thread = threading.Thread(
            target=_resume_pipeline_bg,
            args=(project_root, task_id, "approved", reason),
            daemon=True,
        )
        thread.start()

        return {"id": task_id, "status": "approved", "message": "Task approved, pipeline resuming"}

    @router.post("/api/tasks/{task_id}/reject")
    async def api_reject(task_id: str, req: GateActionRequest):
        if not req.reason:
            raise HTTPException(400, "Reason is required for rejection")

        queue = _get_queue()
        try:
            task = queue.get(task_id)
            if not task:
                raise HTTPException(404, "Task not found")
            if task.status != TaskStatus.awaiting_gate:
                raise HTTPException(400, f"Task not awaiting gate (status: {task.status.value})")

            latest = task.latest_stage
            if latest:
                latest.gate_result = "rejected"
                latest.reject_reason = req.reason
                latest.finished_at = datetime.now(timezone.utc)

            task.status = TaskStatus.rejected
            task.touch()
            queue.update(task)
        finally:
            queue.close()

        # Resume pipeline in background
        thread = threading.Thread(
            target=_resume_pipeline_bg,
            args=(project_root, task_id, "rejected", req.reason),
            daemon=True,
        )
        thread.start()

        return {"id": task_id, "status": "rejected", "message": "Task rejected, pipeline resuming"}

    # ── Priority ───────────────────────────────────────────────────────

    @router.post("/api/tasks/{task_id}/priority")
    async def api_set_priority(task_id: str, req: PriorityRequest):
        from aqm.core.task import TaskPriority
        try:
            new_priority = TaskPriority[req.priority]
        except KeyError:
            raise HTTPException(400, f"Invalid priority: {req.priority}")

        queue = _get_queue()
        try:
            task = queue.get(task_id)
            if not task:
                raise HTTPException(404, "Task not found")
            old = task.priority.name
            task.priority = new_priority
            task.touch()
            queue.update(task)
            return {"id": task_id, "old_priority": old, "new_priority": req.priority}
        finally:
            queue.close()

    # ── Cancel ─────────────────────────────────────────────────────────

    @router.post("/api/tasks/{task_id}/cancel")
    async def api_cancel(task_id: str):
        from aqm.core.pipeline import cancel_task as signal_cancel

        queue = _get_queue()
        try:
            task = queue.get(task_id)
            if not task:
                raise HTTPException(404, "Task not found")
            if task.status.value in ("completed", "failed", "cancelled"):
                raise HTTPException(400, f"Task already {task.status.value}")

            # Allow cancelling stalled tasks too
            # (stalled = server crashed while task was in_progress)

            if task.status == TaskStatus.in_progress:
                # Signal the pipeline loop to stop at next check
                signal_cancel(task_id)

            # Update status immediately in DB for all cancellable states
            task.status = TaskStatus.cancelled
            task.metadata["cancel_reason"] = "Cancelled by user"
            task.touch()
            queue.update(task)
            broadcast_event(task_id, "task_cancelled", {"reason": "Cancelled by user"})
            return {"id": task_id, "status": "cancelled", "message": "Task cancelled"}
        finally:
            queue.close()

    # ── Chunks ─────────────────────────────────────────────────────────

    class AddChunkRequest(BaseModel):
        description: str

    class UpdateChunkRequest(BaseModel):
        status: str  # "pending" | "in_progress" | "done"

    @router.get("/api/tasks/{task_id}/chunks")
    async def api_list_chunks(task_id: str):
        from aqm.core.chunks import ChunkManager
        tasks_dir = get_tasks_dir(project_root)
        task_dir = tasks_dir / task_id
        mgr = ChunkManager(task_dir)
        cl = mgr.load()
        return [c.model_dump(mode="json") for c in cl.chunks]

    @router.post("/api/tasks/{task_id}/chunks")
    async def api_add_chunk(task_id: str, req: AddChunkRequest):
        from aqm.core.chunks import ChunkManager
        tasks_dir = get_tasks_dir(project_root)
        task_dir = tasks_dir / task_id
        task_dir.mkdir(parents=True, exist_ok=True)
        mgr = ChunkManager(task_dir)
        chunk = mgr.add(req.description, created_by="user")
        broadcast_event(task_id, "chunk_update", {
            "action": "add",
            "chunk_id": chunk.id,
            "description": req.description,
        })
        return chunk.model_dump(mode="json")

    @router.patch("/api/tasks/{task_id}/chunks/{chunk_id}")
    async def api_update_chunk(task_id: str, chunk_id: str, req: UpdateChunkRequest):
        from aqm.core.chunks import ChunkManager
        tasks_dir = get_tasks_dir(project_root)
        mgr = ChunkManager(tasks_dir / task_id)
        if req.status == "done":
            ok = mgr.mark_done(chunk_id, completed_by="user")
        elif req.status == "in_progress":
            ok = mgr.mark_in_progress(chunk_id)
        elif req.status == "pending":
            cl = mgr.load()
            ok = False
            for c in cl.chunks:
                if c.id == chunk_id:
                    from aqm.core.chunks import ChunkStatus
                    c.status = ChunkStatus.pending
                    from datetime import datetime, timezone
                    c.updated_at = datetime.now(timezone.utc)
                    mgr.save(cl)
                    ok = True
                    break
        else:
            raise HTTPException(400, f"Invalid status: {req.status}")
        if not ok:
            raise HTTPException(404, f"Chunk {chunk_id} not found")
        broadcast_event(task_id, "chunk_update", {
            "action": "status",
            "chunk_id": chunk_id,
            "status": req.status,
        })
        return {"chunk_id": chunk_id, "status": req.status}

    @router.delete("/api/tasks/{task_id}/chunks/{chunk_id}")
    async def api_delete_chunk(task_id: str, chunk_id: str):
        from aqm.core.chunks import ChunkManager
        tasks_dir = get_tasks_dir(project_root)
        mgr = ChunkManager(tasks_dir / task_id)
        if not mgr.remove(chunk_id):
            raise HTTPException(404, f"Chunk {chunk_id} not found")
        broadcast_event(task_id, "chunk_update", {
            "action": "remove",
            "chunk_id": chunk_id,
        })
        return {"chunk_id": chunk_id, "removed": True}

    # ── SSE Events ─────────────────────────────────────────────────────

    @router.get("/api/tasks/{task_id}/events")
    async def task_events(task_id: str):
        return StreamingResponse(
            subscribe(task_id),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
        )

    return router


def _resume_pipeline_bg(project_root: Path, task_id: str, decision: str, reason: str):
    """Resume pipeline after gate decision in background thread."""
    try:
        # Recover pipeline name from task metadata
        db_path = get_db_path(project_root)
        _q = SQLiteQueue(db_path)
        _task = _q.get(task_id)
        pipeline_name = _task.metadata.get("pipeline") if _task else None
        _q.close()

        agents_yaml_path = get_agents_yaml_path(project_root, pipeline_name)
        agents = load_agents(agents_yaml_path)
        db_path = get_db_path(project_root)
        queue = SQLiteQueue(db_path)
        pipeline = Pipeline(agents, queue, project_root)

        broadcast_event(task_id, "pipeline_resuming", {"decision": decision})

        def on_stage_complete(t, stage):
            broadcast_event(t.id, "stage_complete", {
                "agent_id": stage.agent_id,
                "stage_number": stage.stage_number,
                "output_preview": stage.output_text[:200],
                "gate_result": stage.gate_result,
            })

        def on_stage_start(t, agent_id, stage_number):
            broadcast_event(t.id, "stage_start", {
                "agent_id": agent_id, "stage_number": stage_number,
            })

        def on_output(line):
            broadcast_event(task_id, "stage_output", {"text": line})

        def on_thinking(line):
            broadcast_event(task_id, "stage_thinking", {"text": line})

        result = pipeline.resume_task(
            task_id, decision, reason,
            on_stage_complete=on_stage_complete,
            on_stage_start=on_stage_start,
            on_output=on_output,
            on_thinking=on_thinking,
        )

        if result.status == TaskStatus.awaiting_gate:
            broadcast_event(task_id, "gate_waiting", {
                "agent_id": result.current_agent_id,
            })
        elif result.status == TaskStatus.completed:
            broadcast_event(task_id, "task_complete", {
                "status": "completed",
                "total_stages": len(result.stages),
            })
        elif result.status == TaskStatus.failed:
            broadcast_event(task_id, "task_failed", {
                "error": result.metadata.get("error", ""),
            })
        queue.close()
    except Exception as e:
        logger.error("Pipeline resume failed: %s", e)
        broadcast_event(task_id, "task_failed", {"error": str(e)})
