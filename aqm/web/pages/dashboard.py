"""Dashboard page — task stats, run pipeline form, task list."""

from __future__ import annotations

from aqm.core.agent import AgentDefinition
from aqm.core.task import Task, TaskStatus
from aqm.web.templates import badge, esc, fmt_time, layout


def render_dashboard(
    tasks: list[Task],
    agents: dict[str, AgentDefinition],
) -> str:
    total = len(tasks)
    completed = sum(1 for t in tasks if t.status == TaskStatus.completed)
    failed = sum(1 for t in tasks if t.status == TaskStatus.failed)
    awaiting = sum(1 for t in tasks if t.status == TaskStatus.awaiting_gate)
    running = sum(1 for t in tasks if t.status == TaskStatus.in_progress)

    stats = f"""\
<div class="stats">
  <div class="stat-card"><div class="value">{total}</div><div class="label">Total</div></div>
  <div class="stat-card blue"><div class="value">{running}</div><div class="label">Running</div></div>
  <div class="stat-card green"><div class="value">{completed}</div><div class="label">Completed</div></div>
  <div class="stat-card red"><div class="value">{failed}</div><div class="label">Failed</div></div>
  <div class="stat-card orange"><div class="value">{awaiting}</div><div class="label">Awaiting Gate</div></div>
</div>"""

    # Agent options for dropdown
    agent_options = "".join(
        f'<option value="{esc(a.id)}">{esc(a.name)} ({esc(a.id)})</option>'
        for a in agents.values()
    )

    # Run pipeline form
    form = f"""\
<div class="card">
  <h3>Run Pipeline</h3>
  <form id="runForm" style="margin-top:12px;">
    <div class="form-group">
      <label for="runDesc">Task description</label>
      <textarea id="runDesc" placeholder="Describe what you want the pipeline to do..."></textarea>
    </div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;">
      <div class="form-group">
        <label for="runAgent">Starting agent</label>
        <select id="runAgent"><option value="">Default (first agent)</option>{agent_options}</select>
      </div>
      <div class="form-group">
        <label for="runPriority">Priority</label>
        <select id="runPriority">
          <option value="critical">Critical</option>
          <option value="high">High</option>
          <option value="normal" selected>Normal</option>
          <option value="low">Low</option>
        </select>
      </div>
    </div>
    <button type="submit" class="btn btn-primary">Start Run</button>
    <span id="runResult" style="margin-left:12px;font-size:13px;"></span>
  </form>
</div>
<script>
document.getElementById('runForm').addEventListener('submit', async (e) => {{
  e.preventDefault();
  const desc = document.getElementById('runDesc').value.trim();
  if (!desc) return;
  const agent = document.getElementById('runAgent').value;
  const priority = document.getElementById('runPriority').value;
  const body = {{description: desc, priority: priority}};
  if (agent) body.agent_id = agent;
  const btn = e.target.querySelector('button[type=submit]');
  btn.disabled = true; btn.textContent = 'Starting...';
  try {{
    const data = await apiFetch('/api/run', {{method:'POST', body:JSON.stringify(body)}});
    showToast('Pipeline started: ' + data.task_id);
    setTimeout(() => location.href = '/tasks/' + data.task_id, 600);
  }} catch(err) {{
    btn.disabled = false; btn.textContent = 'Start Run';
  }}
}});
</script>"""

    # Task table
    if not tasks:
        rows = '<tr><td colspan="8" class="empty-state">No tasks yet. Run a pipeline above.</td></tr>'
    else:
        row_list = []
        for t in tasks:
            desc = esc(t.description[:60])
            if len(t.description) > 60:
                desc += "..."
            agent = esc(t.current_agent_id or "-")

            actions = f'<a href="/tasks/{esc(t.id)}" class="btn btn-sm">View</a>'
            if t.status in (TaskStatus.completed, TaskStatus.failed):
                actions += f' <button class="btn btn-sm" onclick="showFixForm(\'{esc(t.id)}\')">Fix</button>'
            if t.status == TaskStatus.awaiting_gate:
                actions += f' <a href="/tasks/{esc(t.id)}" class="btn btn-sm btn-green">Approve</a>'
            if t.status in (TaskStatus.pending, TaskStatus.in_progress, TaskStatus.awaiting_gate):
                actions += f' <button class="btn btn-sm btn-red" onclick="cancelTask(\'{esc(t.id)}\')">Cancel</button>'

            priority_colors = {"critical": "var(--red)", "high": "var(--orange)", "normal": "var(--text-dim)", "low": "var(--text-dim)"}
            p_name = t.priority.name
            p_color = priority_colors.get(p_name, "var(--text-dim)")
            priority_html = (
                f'<select onchange="changePriority(\'{esc(t.id)}\',this.value)" '
                f'style="background:var(--surface2);border:1px solid var(--border);color:{p_color};'
                f'border-radius:4px;padding:2px 4px;font-size:12px;cursor:pointer;">'
                f'<option value="critical"{"selected" if p_name=="critical" else ""}>critical</option>'
                f'<option value="high"{"selected" if p_name=="high" else ""}>high</option>'
                f'<option value="normal"{"selected" if p_name=="normal" else ""}>normal</option>'
                f'<option value="low"{"selected" if p_name=="low" else ""}>low</option>'
                f'</select>'
            )

            row_list.append(
                f'<tr>'
                f'<td><a href="/tasks/{esc(t.id)}">{esc(t.id)}</a></td>'
                f'<td>{badge(t.status.value)}</td>'
                f'<td>{priority_html}</td>'
                f'<td>{agent}</td>'
                f'<td>{desc}</td>'
                f'<td>{len(t.stages)}</td>'
                f'<td>{fmt_time(t.created_at)}</td>'
                f'<td>{actions}</td>'
                f'</tr>'
            )
        rows = "\n".join(row_list)

    table = f"""\
<h2 style="margin-top:24px;">Tasks</h2>
<table>
<thead><tr><th>ID</th><th>Status</th><th>Priority</th><th>Agent</th><th>Description</th><th>Stages</th><th>Created</th><th>Actions</th></tr></thead>
<tbody>{rows}</tbody>
</table>"""

    # Fix modal
    fix_modal = """\
<div class="modal-overlay" id="fixModal">
  <div class="modal">
    <h3>Fix Task <span id="fixTaskId"></span></h3>
    <form id="fixForm">
      <input type="hidden" id="fixParentId">
      <div class="form-group">
        <label for="fixDesc">What needs to be fixed?</label>
        <textarea id="fixDesc" placeholder="Describe the fix..."></textarea>
      </div>
      <button type="submit" class="btn btn-primary">Start Fix</button>
      <button type="button" class="btn" onclick="document.getElementById('fixModal').classList.remove('show')">Cancel</button>
    </form>
  </div>
</div>
<script>
async function changePriority(taskId, priority) {
  try {
    await apiFetch('/api/tasks/' + taskId + '/priority', {method:'POST', body:JSON.stringify({priority:priority})});
    showToast('Priority updated');
  } catch(e) {}
}
async function cancelTask(taskId) {
  if (!confirm('Cancel task ' + taskId + '?')) return;
  try {
    await apiFetch('/api/tasks/' + taskId + '/cancel', {method:'POST', body:'{}'});
    showToast('Task cancelled');
    setTimeout(() => location.reload(), 600);
  } catch(e) {}
}
function showFixForm(taskId) {
  document.getElementById('fixParentId').value = taskId;
  document.getElementById('fixTaskId').textContent = taskId;
  document.getElementById('fixModal').classList.add('show');
}
document.getElementById('fixForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const parentId = document.getElementById('fixParentId').value;
  const desc = document.getElementById('fixDesc').value.trim();
  if (!desc) return;
  try {
    const data = await apiFetch('/api/fix', {method:'POST', body:JSON.stringify({parent_task_id:parentId, description:desc})});
    showToast('Fix started: ' + data.task_id);
    setTimeout(() => location.href = '/tasks/' + data.task_id, 600);
  } catch(err) {}
});
document.getElementById('fixModal').addEventListener('click', (e) => {
  if (e.target === e.currentTarget) e.target.classList.remove('show');
});
</script>"""

    return layout(
        "Dashboard",
        f"<h1>Dashboard</h1>\n{stats}\n{form}\n{table}\n{fix_modal}",
        active="tasks",
    )
