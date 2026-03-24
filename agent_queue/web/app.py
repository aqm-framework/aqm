"""Web UI dashboard for agent-queue — FastAPI + embedded HTML templates."""

from __future__ import annotations

import html
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from agent_queue.core.agent import AgentDefinition, load_agents
from agent_queue.core.context_file import ContextFile
from agent_queue.core.project import get_agents_yaml_path, get_db_path, get_tasks_dir
from agent_queue.core.task import Task, TaskStatus
from agent_queue.queue.sqlite import SQLiteQueue


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class CreateTaskRequest(BaseModel):
    description: str
    agent_id: Optional[str] = None


class GateActionRequest(BaseModel):
    reason: Optional[str] = None


# ---------------------------------------------------------------------------
# HTML Templates (embedded)
# ---------------------------------------------------------------------------

_CSS = """\
:root {
  --bg: #0d1117;
  --surface: #161b22;
  --surface2: #21262d;
  --border: #30363d;
  --text: #e6edf3;
  --text-dim: #8b949e;
  --accent: #58a6ff;
  --green: #3fb950;
  --red: #f85149;
  --orange: #d29922;
  --purple: #bc8cff;
  --cyan: #39d2c0;
  --radius: 8px;
}
* { margin: 0; padding: 0; box-sizing: border-box; }
body { background: var(--bg); color: var(--text); font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif; line-height: 1.6; }
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }

.container { max-width: 1200px; margin: 0 auto; padding: 24px 16px; }

nav { background: var(--surface); border-bottom: 1px solid var(--border); padding: 12px 0; position: sticky; top: 0; z-index: 100; }
nav .inner { max-width: 1200px; margin: 0 auto; padding: 0 16px; display: flex; align-items: center; gap: 24px; }
nav .logo { font-size: 18px; font-weight: 700; color: var(--text); }
nav .logo span { color: var(--accent); }
nav a.nav-link { color: var(--text-dim); font-size: 14px; }
nav a.nav-link:hover { color: var(--text); text-decoration: none; }

h1 { font-size: 24px; margin-bottom: 16px; }
h2 { font-size: 20px; margin-bottom: 12px; }
h3 { font-size: 16px; margin-bottom: 8px; }

.stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; margin-bottom: 24px; }
.stat-card { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 16px; text-align: center; }
.stat-card .value { font-size: 32px; font-weight: 700; color: var(--accent); }
.stat-card .label { font-size: 13px; color: var(--text-dim); margin-top: 4px; }
.stat-card.green .value { color: var(--green); }
.stat-card.red .value { color: var(--red); }
.stat-card.orange .value { color: var(--orange); }

table { width: 100%; border-collapse: collapse; background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); overflow: hidden; }
thead { background: var(--surface2); }
th, td { padding: 10px 14px; text-align: left; font-size: 14px; border-bottom: 1px solid var(--border); }
th { font-weight: 600; color: var(--text-dim); font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px; }
tr:last-child td { border-bottom: none; }
tr:hover td { background: rgba(88, 166, 255, 0.04); }

.badge { display: inline-block; padding: 2px 10px; border-radius: 12px; font-size: 12px; font-weight: 600; }
.badge-pending { background: #30363d; color: #8b949e; }
.badge-in_progress { background: #0d419d; color: #58a6ff; }
.badge-awaiting_gate { background: #462c08; color: #d29922; }
.badge-approved { background: #0f2d16; color: #3fb950; }
.badge-completed { background: #0f2d16; color: #3fb950; }
.badge-rejected { background: #3d1214; color: #f85149; }
.badge-failed { background: #3d1214; color: #f85149; }

.btn { display: inline-block; padding: 8px 16px; border-radius: 6px; font-size: 14px; font-weight: 600; border: 1px solid var(--border); cursor: pointer; transition: 0.15s; }
.btn-primary { background: var(--accent); color: #fff; border-color: var(--accent); }
.btn-primary:hover { opacity: 0.9; }
.btn-green { background: var(--green); color: #fff; border-color: var(--green); }
.btn-green:hover { opacity: 0.9; }
.btn-red { background: var(--red); color: #fff; border-color: var(--red); }
.btn-red:hover { opacity: 0.9; }

.card { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 20px; margin-bottom: 16px; }

.timeline { position: relative; padding-left: 28px; }
.timeline::before { content: ''; position: absolute; left: 10px; top: 0; bottom: 0; width: 2px; background: var(--border); }
.timeline-item { position: relative; margin-bottom: 20px; }
.timeline-item::before { content: ''; position: absolute; left: -22px; top: 6px; width: 12px; height: 12px; border-radius: 50%; background: var(--accent); border: 2px solid var(--bg); }
.timeline-item.approved::before { background: var(--green); }
.timeline-item.rejected::before { background: var(--red); }
.timeline-item.awaiting::before { background: var(--orange); }
.timeline-item.failed::before { background: var(--red); }

details { margin-top: 8px; }
details summary { cursor: pointer; color: var(--text-dim); font-size: 13px; }
details summary:hover { color: var(--text); }
details pre { margin-top: 8px; }

pre { background: var(--surface2); border: 1px solid var(--border); border-radius: 6px; padding: 12px; overflow-x: auto; font-size: 13px; color: var(--text); white-space: pre-wrap; word-break: break-word; max-height: 400px; overflow-y: auto; }

.meta-row { display: flex; gap: 24px; flex-wrap: wrap; margin-bottom: 8px; }
.meta-row .meta-item { font-size: 14px; }
.meta-row .meta-label { color: var(--text-dim); margin-right: 6px; }

.form-group { margin-bottom: 12px; }
.form-group label { display: block; font-size: 13px; color: var(--text-dim); margin-bottom: 4px; }
.form-group input, .form-group textarea { width: 100%; background: var(--surface2); border: 1px solid var(--border); border-radius: 6px; padding: 8px 12px; color: var(--text); font-size: 14px; font-family: inherit; }
.form-group textarea { min-height: 80px; resize: vertical; }

/* Agent diagram */
.agent-graph { display: flex; flex-wrap: wrap; gap: 40px; align-items: flex-start; padding: 20px 0; }
.agent-node { background: var(--surface); border: 2px solid var(--border); border-radius: var(--radius); padding: 16px 20px; min-width: 200px; position: relative; }
.agent-node .agent-title { font-weight: 700; font-size: 16px; margin-bottom: 8px; color: var(--accent); }
.agent-node .agent-id { font-size: 12px; color: var(--text-dim); }
.agent-node .agent-meta { font-size: 12px; color: var(--text-dim); margin-top: 8px; }
.agent-node .mcp-list { margin-top: 8px; }
.agent-node .mcp-item { display: inline-block; background: var(--surface2); border: 1px solid var(--border); border-radius: 4px; padding: 2px 8px; font-size: 11px; color: var(--cyan); margin: 2px 2px; }
.agent-node .gate-badge { display: inline-block; margin-top: 8px; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 600; }
.gate-llm { background: #1a1040; color: var(--purple); border: 1px solid #3b2d6b; }
.gate-human { background: #462c08; color: var(--orange); border: 1px solid #6b4f1d; }

.handoff-row { display: flex; align-items: center; gap: 12px; margin-bottom: 12px; background: var(--surface); border: 1px solid var(--border); border-radius: 6px; padding: 10px 16px; }
.handoff-arrow { color: var(--accent); font-weight: 700; font-size: 18px; }
.handoff-info { font-size: 13px; }
.handoff-info .condition { color: var(--purple); }

.empty-state { text-align: center; padding: 48px; color: var(--text-dim); }

@media (max-width: 768px) {
  .stats { grid-template-columns: repeat(2, 1fr); }
  .meta-row { flex-direction: column; gap: 4px; }
  .agent-graph { flex-direction: column; }
  th, td { padding: 8px 10px; font-size: 13px; }
}
"""

_NAV = """\
<nav>
  <div class="inner">
    <div class="logo"><span>AQ</span> Dashboard</div>
    <a class="nav-link" href="/">Tasks</a>
    <a class="nav-link" href="/agents">Agents</a>
  </div>
</nav>
"""


def _layout(title: str, body: str) -> str:
    return f"""\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title} - Agent Queue</title>
  <style>{_CSS}</style>
</head>
<body>
{_NAV}
<div class="container">
{body}
</div>
</body>
</html>"""


def _esc(text: str) -> str:
    return html.escape(str(text)) if text else ""


def _fmt_time(dt: Optional[datetime]) -> str:
    if dt is None:
        return "-"
    return dt.strftime("%Y-%m-%d %H:%M:%S UTC")


def _badge(status: str) -> str:
    return f'<span class="badge badge-{_esc(status)}">{_esc(status)}</span>'


# ---------------------------------------------------------------------------
# Page renderers
# ---------------------------------------------------------------------------

def _render_dashboard(tasks: list[Task], agents: dict[str, AgentDefinition]) -> str:
    total = len(tasks)
    completed = sum(1 for t in tasks if t.status == TaskStatus.completed)
    failed = sum(1 for t in tasks if t.status == TaskStatus.failed)
    awaiting = sum(1 for t in tasks if t.status == TaskStatus.awaiting_gate)

    stats = f"""\
<div class="stats">
  <div class="stat-card"><div class="value">{total}</div><div class="label">Total Tasks</div></div>
  <div class="stat-card green"><div class="value">{completed}</div><div class="label">Completed</div></div>
  <div class="stat-card red"><div class="value">{failed}</div><div class="label">Failed</div></div>
  <div class="stat-card orange"><div class="value">{awaiting}</div><div class="label">Awaiting Gate</div></div>
</div>"""

    if not tasks:
        rows = '<tr><td colspan="6" class="empty-state">No tasks yet. Create one via the API.</td></tr>'
    else:
        row_list = []
        for t in tasks:
            desc = _esc(t.description[:80])
            if len(t.description) > 80:
                desc += "..."
            agent = _esc(t.current_agent_id or "-")
            row_list.append(
                f'<tr>'
                f'<td><a href="/tasks/{_esc(t.id)}">{_esc(t.id)}</a></td>'
                f'<td>{_badge(t.status.value)}</td>'
                f'<td>{agent}</td>'
                f'<td>{desc}</td>'
                f'<td>{len(t.stages)}</td>'
                f'<td>{_fmt_time(t.created_at)}</td>'
                f'</tr>'
            )
        rows = "\n".join(row_list)

    table = f"""\
<h2>Tasks</h2>
<table>
<thead><tr><th>ID</th><th>Status</th><th>Agent</th><th>Description</th><th>Stages</th><th>Created</th></tr></thead>
<tbody>
{rows}
</tbody>
</table>"""

    # Create task form
    form = """\
<div class="card" style="margin-top: 24px;">
  <h3>Create New Task</h3>
  <form id="createTaskForm" style="margin-top: 12px;">
    <div class="form-group">
      <label for="desc">Description</label>
      <textarea id="desc" placeholder="Describe the task..."></textarea>
    </div>
    <div class="form-group">
      <label for="agent">Starting Agent ID (optional)</label>
      <input id="agent" type="text" placeholder="e.g. planner">
    </div>
    <button type="submit" class="btn btn-primary">Create Task</button>
    <span id="createResult" style="margin-left: 12px; font-size: 13px;"></span>
  </form>
</div>
<script>
document.getElementById('createTaskForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const desc = document.getElementById('desc').value.trim();
  if (!desc) return;
  const agent = document.getElementById('agent').value.trim();
  const body = {description: desc};
  if (agent) body.agent_id = agent;
  try {
    const res = await fetch('/api/tasks', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
    const data = await res.json();
    if (res.ok) {
      document.getElementById('createResult').textContent = 'Created: ' + data.id;
      document.getElementById('createResult').style.color = '#3fb950';
      setTimeout(() => location.reload(), 800);
    } else {
      document.getElementById('createResult').textContent = 'Error: ' + (data.detail || 'Unknown');
      document.getElementById('createResult').style.color = '#f85149';
    }
  } catch(err) {
    document.getElementById('createResult').textContent = 'Error: ' + err.message;
    document.getElementById('createResult').style.color = '#f85149';
  }
});
</script>"""

    return _layout("Dashboard", f"<h1>Dashboard</h1>\n{stats}\n{table}\n{form}")


def _render_agents(agents: dict[str, AgentDefinition]) -> str:
    if not agents:
        body = '<div class="empty-state">No agents defined. Create .agent-queue/agents.yaml first.</div>'
        return _layout("Agents", f"<h1>Agent Diagram</h1>\n{body}")

    # Agent nodes
    nodes = []
    for agent in agents.values():
        mcp_html = ""
        if agent.mcp:
            items = "".join(
                f'<span class="mcp-item">{_esc(m.server)}</span>' for m in agent.mcp
            )
            mcp_html = f'<div class="mcp-list"><div style="font-size:11px;color:var(--text-dim);margin-bottom:2px;">MCP Servers</div>{items}</div>'

        gate_html = ""
        if agent.gate:
            cls = "gate-llm" if agent.gate.type == "llm" else "gate-human"
            gate_html = f'<div class="gate-badge {cls}">Gate: {_esc(agent.gate.type)}</div>'

        nodes.append(
            f'<div class="agent-node" id="node-{_esc(agent.id)}">'
            f'<div class="agent-title">{_esc(agent.name)}</div>'
            f'<div class="agent-id">{_esc(agent.id)} &middot; {_esc(agent.runtime)}</div>'
            f'{gate_html}{mcp_html}'
            f'</div>'
        )

    graph = f'<div class="agent-graph">{"".join(nodes)}</div>'

    # Handoff connections
    handoffs_html_parts = []
    for agent in agents.values():
        for h in agent.handoffs:
            target = agents.get(h.to)
            target_name = target.name if target else h.to
            cond = f' <span class="condition">[{_esc(h.condition)}]</span>' if h.condition != "always" else ""
            task_label = f" &mdash; {_esc(h.task)}" if h.task else ""
            handoffs_html_parts.append(
                f'<div class="handoff-row">'
                f'<strong>{_esc(agent.name)}</strong>'
                f'<span class="handoff-arrow">&rarr;</span>'
                f'<strong>{_esc(target_name)}</strong>'
                f'<span class="handoff-info">{task_label}{cond}</span>'
                f'</div>'
            )

    handoffs_section = ""
    if handoffs_html_parts:
        handoffs_section = f'<h2 style="margin-top:32px;">Handoff Connections</h2>\n{"".join(handoffs_html_parts)}'

    return _layout("Agents", f"<h1>Agent Diagram</h1>\n{graph}\n{handoffs_section}")


def _render_task_detail(task: Task, agents: dict[str, AgentDefinition], context_md: str) -> str:
    # Meta info
    meta = f"""\
<div class="card">
  <div class="meta-row">
    <div class="meta-item"><span class="meta-label">ID:</span> {_esc(task.id)}</div>
    <div class="meta-item"><span class="meta-label">Status:</span> {_badge(task.status.value)}</div>
    <div class="meta-item"><span class="meta-label">Agent:</span> {_esc(task.current_agent_id or '-')}</div>
  </div>
  <div class="meta-row">
    <div class="meta-item"><span class="meta-label">Created:</span> {_fmt_time(task.created_at)}</div>
    <div class="meta-item"><span class="meta-label">Updated:</span> {_fmt_time(task.updated_at)}</div>
  </div>
  <div style="margin-top: 12px;">
    <span class="meta-label">Description:</span>
    <p style="margin-top: 4px;">{_esc(task.description)}</p>
  </div>
</div>"""

    # Gate action buttons
    gate_actions = ""
    if task.status == TaskStatus.awaiting_gate:
        gate_actions = f"""\
<div class="card" style="border-color: var(--orange);">
  <h3 style="color: var(--orange);">Awaiting Gate Approval</h3>
  <div style="margin-top: 12px;">
    <div class="form-group">
      <label for="gateReason">Reason (required for rejection)</label>
      <input id="gateReason" type="text" placeholder="Optional reason...">
    </div>
    <button class="btn btn-green" onclick="gateAction('approve')">Approve</button>
    <button class="btn btn-red" style="margin-left: 8px;" onclick="gateAction('reject')">Reject</button>
    <span id="gateResult" style="margin-left: 12px; font-size: 13px;"></span>
  </div>
</div>
<script>
async function gateAction(action) {{
  const reason = document.getElementById('gateReason').value.trim();
  if (action === 'reject' && !reason) {{
    document.getElementById('gateResult').textContent = 'Reason is required for rejection';
    document.getElementById('gateResult').style.color = '#f85149';
    return;
  }}
  try {{
    const res = await fetch('/api/tasks/{_esc(task.id)}/' + action, {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{reason: reason || undefined}})
    }});
    const data = await res.json();
    if (res.ok) {{
      document.getElementById('gateResult').textContent = 'Done! Reloading...';
      document.getElementById('gateResult').style.color = '#3fb950';
      setTimeout(() => location.reload(), 800);
    }} else {{
      document.getElementById('gateResult').textContent = 'Error: ' + (data.detail || 'Unknown');
      document.getElementById('gateResult').style.color = '#f85149';
    }}
  }} catch(err) {{
    document.getElementById('gateResult').textContent = 'Error: ' + err.message;
    document.getElementById('gateResult').style.color = '#f85149';
  }}
}}
</script>"""

    # Stage timeline
    if not task.stages:
        timeline = '<div class="empty-state" style="padding: 24px;">No stages recorded yet.</div>'
    else:
        items = []
        for s in task.stages:
            status_class = ""
            if s.gate_result == "approved":
                status_class = "approved"
            elif s.gate_result == "rejected":
                status_class = "rejected"
            elif s.gate_result is None and task.status == TaskStatus.awaiting_gate and s == task.stages[-1]:
                status_class = "awaiting"

            agent_def = agents.get(s.agent_id)
            agent_name = agent_def.name if agent_def else s.agent_id

            gate_info = ""
            if s.gate_result:
                gate_info = f' &middot; Gate: {_badge(s.gate_result)}'
                if s.reject_reason:
                    gate_info += f' <span style="font-size:12px;color:var(--text-dim);">({_esc(s.reject_reason[:100])})</span>'

            items.append(
                f'<div class="timeline-item {status_class}">'
                f'<div><strong>Stage {s.stage_number}</strong> &middot; '
                f'<span style="color:var(--accent);">{_esc(agent_name)}</span>'
                f'{gate_info}</div>'
                f'<div style="font-size:12px;color:var(--text-dim);">'
                f'{_fmt_time(s.started_at)} &rarr; {_fmt_time(s.finished_at)}</div>'
                f'<details><summary>Show Input</summary><pre>{_esc(s.input_text)}</pre></details>'
                f'<details><summary>Show Output</summary><pre>{_esc(s.output_text)}</pre></details>'
                f'</div>'
            )
        timeline = f'<div class="timeline">{"".join(items)}</div>'

    # Context.md viewer
    context_section = ""
    if context_md:
        context_section = f"""\
<div class="card" style="margin-top: 16px;">
  <details>
    <summary style="font-size: 15px; font-weight: 600; color: var(--text);">Context.md</summary>
    <pre style="margin-top: 12px;">{_esc(context_md)}</pre>
  </details>
</div>"""

    return _layout(
        f"Task {task.short_id}",
        f'<h1>Task {_esc(task.short_id)}</h1>\n'
        f'{meta}\n{gate_actions}\n'
        f'<h2 style="margin-top: 24px;">Stage Timeline</h2>\n{timeline}\n'
        f'{context_section}'
    )


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def create_app(project_root: Path) -> FastAPI:
    """Create and return a FastAPI application for the agent-queue dashboard.

    Args:
        project_root: Path to the project root containing .agent-queue/
    """
    app = FastAPI(title="Agent Queue Dashboard", docs_url="/docs")

    project_root = Path(project_root).resolve()
    db_path = get_db_path(project_root)
    agents_yaml_path = get_agents_yaml_path(project_root)

    def _get_queue() -> SQLiteQueue:
        return SQLiteQueue(db_path)

    def _get_agents() -> dict[str, AgentDefinition]:
        if agents_yaml_path.exists():
            return load_agents(agents_yaml_path)
        return {}

    # -----------------------------------------------------------------------
    # HTML pages
    # -----------------------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    async def dashboard():
        queue = _get_queue()
        try:
            tasks = queue.list_tasks()
            agents = _get_agents()
            return _render_dashboard(tasks, agents)
        finally:
            queue.close()

    @app.get("/agents", response_class=HTMLResponse)
    async def agents_page():
        agents = _get_agents()
        return _render_agents(agents)

    @app.get("/tasks/{task_id}", response_class=HTMLResponse)
    async def task_detail(task_id: str):
        queue = _get_queue()
        try:
            task = queue.get(task_id)
            if task is None:
                raise HTTPException(status_code=404, detail="Task not found")
            agents = _get_agents()
            # Read context.md
            context_md = ""
            tasks_dir = get_tasks_dir(project_root)
            ctx_file = ContextFile(tasks_dir / task_id)
            context_md = ctx_file.read()
            return _render_task_detail(task, agents, context_md)
        finally:
            queue.close()

    # -----------------------------------------------------------------------
    # API endpoints
    # -----------------------------------------------------------------------

    @app.post("/api/tasks")
    async def api_create_task(req: CreateTaskRequest):
        agents = _get_agents()
        if not agents:
            raise HTTPException(status_code=500, detail="No agents defined in agents.yaml")

        agent_id = req.agent_id
        if agent_id and agent_id not in agents:
            raise HTTPException(status_code=400, detail=f"Agent '{agent_id}' not found")
        if not agent_id:
            agent_id = next(iter(agents))

        task = Task(description=req.description, current_agent_id=agent_id)

        queue = _get_queue()
        try:
            queue.push(task, queue_name=agent_id)
            return {"id": task.id, "status": task.status.value, "agent_id": agent_id}
        finally:
            queue.close()

    @app.get("/api/tasks")
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
                    raise HTTPException(status_code=400, detail=f"Invalid status: {status}")

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

    @app.get("/api/tasks/{task_id}")
    async def api_get_task(task_id: str):
        queue = _get_queue()
        try:
            task = queue.get(task_id)
            if task is None:
                raise HTTPException(status_code=404, detail="Task not found")
            return task.model_dump(mode="json")
        finally:
            queue.close()

    @app.post("/api/tasks/{task_id}/approve")
    async def api_approve_task(task_id: str, req: Optional[GateActionRequest] = None):
        queue = _get_queue()
        try:
            task = queue.get(task_id)
            if task is None:
                raise HTTPException(status_code=404, detail="Task not found")
            if task.status != TaskStatus.awaiting_gate:
                raise HTTPException(
                    status_code=400,
                    detail=f"Task is not awaiting gate (current status: {task.status.value})",
                )

            latest = task.latest_stage
            if latest:
                latest.gate_result = "approved"
                latest.reject_reason = req.reason if req and req.reason else ""
                latest.finished_at = datetime.now(timezone.utc)

            task.status = TaskStatus.approved
            task.touch()
            queue.update(task)

            return {"id": task.id, "status": task.status.value, "message": "Task approved"}
        finally:
            queue.close()

    @app.post("/api/tasks/{task_id}/reject")
    async def api_reject_task(task_id: str, req: GateActionRequest):
        if not req.reason:
            raise HTTPException(status_code=400, detail="Reason is required for rejection")

        queue = _get_queue()
        try:
            task = queue.get(task_id)
            if task is None:
                raise HTTPException(status_code=404, detail="Task not found")
            if task.status != TaskStatus.awaiting_gate:
                raise HTTPException(
                    status_code=400,
                    detail=f"Task is not awaiting gate (current status: {task.status.value})",
                )

            latest = task.latest_stage
            if latest:
                latest.gate_result = "rejected"
                latest.reject_reason = req.reason
                latest.finished_at = datetime.now(timezone.utc)

            task.status = TaskStatus.rejected
            task.touch()
            queue.update(task)

            return {"id": task.id, "status": task.status.value, "message": "Task rejected"}
        finally:
            queue.close()

    return app
