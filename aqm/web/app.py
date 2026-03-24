"""Web UI dashboard for aqm — FastAPI app factory.

All HTML rendering is in pages/, all API endpoints in api/.
This module just wires everything together.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse

from aqm.core.agent import load_agents
from aqm.core.context_file import ContextFile
from aqm.core.project import get_agents_yaml_path, get_db_path, get_tasks_dir
from aqm.queue.sqlite import SQLiteQueue


def create_app(project_root: Path) -> FastAPI:
    """Create and return the FastAPI application for the aqm dashboard."""
    app = FastAPI(title="aqm Dashboard", docs_url="/docs")

    project_root = Path(project_root).resolve()
    db_path = get_db_path(project_root)
    agents_yaml_path = get_agents_yaml_path(project_root)

    def _get_queue() -> SQLiteQueue:
        return SQLiteQueue(db_path)

    def _get_agents():
        if agents_yaml_path.exists():
            return load_agents(agents_yaml_path)
        return {}

    # ── Startup: recover stale tasks ──────────────────────────────────

    @app.on_event("startup")
    async def _recover_stale():
        import logging
        log = logging.getLogger("aqm.web")
        queue = _get_queue()
        try:
            stalled = queue.recover_stale_tasks()
            if stalled:
                log.warning(
                    "Recovered %d stale task(s): %s",
                    len(stalled),
                    ", ".join(t.id for t in stalled),
                )
        finally:
            queue.close()

    # ── HTML Pages ────────────────────────────────────────────────────

    @app.get("/", response_class=HTMLResponse)
    async def dashboard():
        from aqm.web.pages.dashboard import render_dashboard
        queue = _get_queue()
        try:
            tasks = queue.list_tasks()
            agents = _get_agents()
            return render_dashboard(tasks, agents)
        finally:
            queue.close()

    @app.get("/agents", response_class=HTMLResponse)
    async def agents_page():
        from aqm.web.pages.agents import render_agents
        agents = _get_agents()
        return render_agents(agents)

    @app.get("/tasks/{task_id}", response_class=HTMLResponse)
    async def task_detail(task_id: str):
        from aqm.web.pages.task_detail import render_task_detail
        queue = _get_queue()
        try:
            task = queue.get(task_id)
            if task is None:
                raise HTTPException(status_code=404, detail="Task not found")
            agents = _get_agents()
            tasks_dir = get_tasks_dir(project_root)
            ctx_file = ContextFile(tasks_dir / task_id)
            context_md = ctx_file.read()
            return render_task_detail(task, agents, context_md)
        finally:
            queue.close()

    @app.get("/registry", response_class=HTMLResponse)
    async def registry_page():
        from aqm.web.pages.registry import render_registry
        return render_registry()

    @app.get("/validate", response_class=HTMLResponse)
    async def validate_page():
        from aqm.web.pages.validate import render_validate
        return render_validate()

    # ── API Routes ────────────────────────────────────────────────────

    from aqm.web.api.tasks import create_tasks_router
    from aqm.web.api.registry import create_registry_router
    from aqm.web.api.validate import create_validate_router

    app.include_router(create_tasks_router(project_root))
    app.include_router(create_registry_router(project_root))
    app.include_router(create_validate_router(project_root))

    return app
