"""Task detail page — meta, gate actions, stage timeline, live progress, context."""

from __future__ import annotations

from aqm.core.agent import AgentDefinition
from aqm.core.task import Task, TaskStatus
from aqm.web.templates import badge, esc, fmt_time, layout


def render_task_detail(
    task: Task,
    agents: dict[str, AgentDefinition],
    context_md: str,
) -> str:
    short_id = task.id[:10]

    # Meta card
    meta = f"""\
<div class="card">
  <div class="meta-row">
    <div class="meta-item"><span class="meta-label">ID:</span> {esc(task.id)}</div>
    <div class="meta-item"><span class="meta-label">Status:</span> {badge(task.status.value)}</div>
    <div class="meta-item"><span class="meta-label">Agent:</span> {esc(task.current_agent_id or '-')}</div>
  </div>
  <div class="meta-row">
    <div class="meta-item"><span class="meta-label">Created:</span> {fmt_time(task.created_at)}</div>
    <div class="meta-item"><span class="meta-label">Updated:</span> {fmt_time(task.updated_at)}</div>
    <div class="meta-item"><span class="meta-label">Stages:</span> {len(task.stages)}</div>
  </div>
  <div style="margin-top:12px;">
    <span class="meta-label">Description:</span>
    <p style="margin-top:4px;">{esc(task.description)}</p>
  </div>
</div>"""

    # Gate action panel
    gate_actions = ""
    if task.status == TaskStatus.awaiting_gate:
        gate_actions = f"""\
<div class="card" style="border-color:var(--orange);">
  <h3 style="color:var(--orange);"><span class="live-dot" style="background:var(--orange);"></span>Awaiting Gate Approval</h3>
  <div style="margin-top:12px;">
    <div class="form-group">
      <label for="gateReason">Reason (required for rejection)</label>
      <input id="gateReason" type="text" placeholder="Optional reason...">
    </div>
    <button class="btn btn-green" onclick="gateAction('approve')">Approve</button>
    <button class="btn btn-red" style="margin-left:8px;" onclick="gateAction('reject')">Reject</button>
  </div>
</div>
<script>
async function gateAction(action) {{
  const reason = document.getElementById('gateReason').value.trim();
  if (action === 'reject' && !reason) {{ showToast('Reason is required for rejection','error'); return; }}
  try {{
    await apiFetch('/api/tasks/{esc(task.id)}/' + action, {{
      method:'POST', body:JSON.stringify({{reason:reason||undefined}})
    }});
    showToast('Task ' + action + 'd');
    setTimeout(() => location.reload(), 600);
  }} catch(e) {{}}
}}
</script>"""

    # Live progress panel (for in_progress tasks)
    live_panel = ""
    if task.status == TaskStatus.in_progress:
        live_panel = f"""\
<div class="card" style="border-color:var(--accent);">
  <h3><span class="live-dot"></span>Pipeline Running</h3>
  <div class="progress-bar" style="margin-top:8px;"><div class="fill" id="progressFill" style="width:0%"></div></div>
  <div id="liveStatus" style="margin-top:8px;font-size:13px;color:var(--text-dim);"></div>
</div>
<script>
(function() {{
  const es = new EventSource('/api/tasks/{esc(task.id)}/events');
  const statusEl = document.getElementById('liveStatus');
  const fillEl = document.getElementById('progressFill');
  let stageCount = {len(task.stages)};

  es.addEventListener('stage_start', (e) => {{
    const d = JSON.parse(e.data);
    statusEl.innerHTML = '<span class="live-dot"></span>Stage ' + d.stage_number + ': <strong>' + d.agent_id + '</strong> running...';
  }});
  es.addEventListener('stage_complete', (e) => {{
    const d = JSON.parse(e.data);
    stageCount++;
    statusEl.innerHTML = 'Stage ' + d.stage_number + ': <strong>' + d.agent_id + '</strong> — ' + (d.gate_result || 'done');
  }});
  es.addEventListener('gate_waiting', (e) => {{
    es.close();
    showToast('Awaiting gate approval');
    setTimeout(() => location.reload(), 800);
  }});
  es.addEventListener('task_complete', (e) => {{
    es.close();
    showToast('Pipeline completed');
    setTimeout(() => location.reload(), 800);
  }});
  es.addEventListener('task_failed', (e) => {{
    es.close();
    const d = JSON.parse(e.data);
    showToast('Pipeline failed: ' + (d.error||''), 'error');
    setTimeout(() => location.reload(), 1500);
  }});
  es.onerror = () => {{ es.close(); }};
}})();
</script>"""

    # Stage timeline
    if not task.stages:
        timeline = '<div class="empty-state" style="padding:24px;">No stages recorded yet.</div>'
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
                gate_info = f' · Gate: {badge(s.gate_result)}'
                if s.reject_reason:
                    gate_info += f' <span style="font-size:12px;color:var(--text-dim);">({esc(s.reject_reason[:100])})</span>'

            output_preview = esc(s.output_text[:200]) + ("..." if len(s.output_text) > 200 else "")

            items.append(
                f'<div class="timeline-item {status_class}">'
                f'<div><strong>Stage {s.stage_number}</strong> · '
                f'<span style="color:var(--accent);">{esc(agent_name)}</span>'
                f'{gate_info}</div>'
                f'<div style="font-size:12px;color:var(--text-dim);">'
                f'{fmt_time(s.started_at)} → {fmt_time(s.finished_at)}</div>'
                f'<div style="font-size:13px;margin-top:4px;color:var(--text-dim);">{output_preview}</div>'
                f'<details><summary>Full Input</summary><pre>{esc(s.input_text)}</pre></details>'
                f'<details><summary>Full Output</summary><pre>{esc(s.output_text)}</pre></details>'
                f'</div>'
            )
        timeline = f'<div class="timeline">{"".join(items)}</div>'

    # Context.md viewer
    context_section = ""
    if context_md:
        context_section = f"""\
<div class="card" style="margin-top:16px;">
  <details>
    <summary style="font-size:15px;font-weight:600;color:var(--text);">Context.md</summary>
    <pre style="margin-top:12px;">{esc(context_md)}</pre>
  </details>
</div>"""

    # Fix button for completed/failed tasks
    fix_section = ""
    if task.status in (TaskStatus.completed, TaskStatus.failed):
        fix_section = f"""\
<div class="card" style="margin-top:16px;">
  <h3>Follow-up Fix</h3>
  <form id="fixForm" style="margin-top:8px;">
    <div class="form-group">
      <label for="fixDesc">What needs to be fixed?</label>
      <textarea id="fixDesc" placeholder="Describe the fix..."></textarea>
    </div>
    <button type="submit" class="btn btn-primary">Start Fix</button>
  </form>
</div>
<script>
document.getElementById('fixForm').addEventListener('submit', async (e) => {{
  e.preventDefault();
  const desc = document.getElementById('fixDesc').value.trim();
  if (!desc) return;
  try {{
    const data = await apiFetch('/api/fix', {{method:'POST', body:JSON.stringify({{parent_task_id:'{esc(task.id)}', description:desc}})}});
    showToast('Fix started: ' + data.task_id);
    setTimeout(() => location.href = '/tasks/' + data.task_id, 600);
  }} catch(e) {{}}
}});
</script>"""

    return layout(
        f"Task {short_id}",
        f'<h1>Task {esc(short_id)}</h1>\n'
        f'{meta}\n{gate_actions}\n{live_panel}\n'
        f'<h2 style="margin-top:24px;">Stage Timeline</h2>\n{timeline}\n'
        f'{context_section}\n{fix_section}',
        active="tasks",
    )
