"""Task detail page — tabbed interface with overview, live output, timeline, context, chunks."""

from __future__ import annotations

from aqm.core.agent import AgentDefinition
from aqm.core.task import Task, TaskStatus
from aqm.web.templates import badge, copy_pre, esc, fmt_duration, fmt_time, layout


def render_task_detail(
    task: Task,
    agents: dict[str, AgentDefinition],
    context_md: str,
) -> str:
    short_id = task.id[:10]
    show_live = task.status in (TaskStatus.in_progress, TaskStatus.awaiting_gate, TaskStatus.awaiting_human_input)

    # ── Agent tooltip helper ──────────────────────────────────────────
    def agent_tip(agent_id: str) -> str:
        a = agents.get(agent_id)
        if not a:
            return esc(agent_id)
        tip_parts = [f"Runtime: {a.runtime or 'N/A'}"]
        if a.gate:
            tip_parts.append(f"Gate: {a.gate.type}")
        if a.mcp:
            tip_parts.append(f"MCP: {', '.join(m.server for m in a.mcp)}")
        tip_text = esc("\\n".join(tip_parts))
        name = esc(a.name or a.id)
        return f'<span class="agent-tip" data-tip="{tip_text}">{name}</span>'

    # ── Meta card ─────────────────────────────────────────────────────
    meta = f"""\
<div class="card">
  <div class="meta-row">
    <div class="meta-item"><span class="meta-label">ID:</span> {esc(task.id)}</div>
    <div class="meta-item"><span class="meta-label">Status:</span> {badge(task.status.value)}</div>
    <div class="meta-item"><span class="meta-label">Agent:</span> {agent_tip(task.current_agent_id or '-')}</div>
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

    # ── Gate action panel ─────────────────────────────────────────────
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
</div>"""

    # ── Human input panel ─────────────────────────────────────────────
    human_input_panel = ""
    if task.status == TaskStatus.awaiting_human_input:
        pending = task.metadata.get("_human_input_pending", {})
        hi_agent = pending.get("agent_id", "")
        hi_questions = pending.get("questions", [])
        hi_mode = pending.get("mode", "")
        questions_html = "".join(
            f'<div style="background:var(--surface2);border-left:3px solid var(--cyan);'
            f'padding:10px 14px;margin:8px 0;border-radius:0 6px 6px 0;font-size:14px;">'
            f'{esc(q)}</div>'
            for q in hi_questions
        )
        human_input_panel = f"""\
<div class="card" style="border-color:var(--cyan);">
  <h3 style="color:var(--cyan);"><span class="live-dot" style="background:var(--cyan);"></span>Agent Needs Your Input</h3>
  <div style="margin-top:4px;font-size:13px;color:var(--text-dim);">
    Agent <strong>{esc(hi_agent)}</strong> ({esc(hi_mode)} mode)
  </div>
  <div style="margin-top:12px;">
    {questions_html}
    <div class="form-group" style="margin-top:12px;">
      <label for="humanInput">Your Response</label>
      <textarea id="humanInput" rows="4" placeholder="Type your response..."></textarea>
    </div>
    <button class="btn btn-primary" onclick="submitHumanInput()">Submit Response</button>
  </div>
</div>"""

    # ── Restart panel ─────────────────────────────────────────────────
    restart_panel = ""
    if task.status in (TaskStatus.failed, TaskStatus.completed, TaskStatus.stalled, TaskStatus.cancelled):
        stage_options = "".join(
            f'<option value="{s.stage_number}">Stage {s.stage_number} — {esc(s.agent_id)}</option>'
            for s in task.stages
        )
        restart_panel = f"""\
<div class="card" style="border-color:var(--accent);">
  <h3>Restart Task</h3>
  <div style="margin-top:12px;">
    <div class="form-group">
      <label for="restartStage">Restart from stage</label>
      <select id="restartStage">
        <option value="">Default (auto-detect)</option>
        {stage_options}
      </select>
    </div>
    <button class="btn btn-green" onclick="restartTask()">Restart</button>
  </div>
</div>"""

    # ── Live progress panel ───────────────────────────────────────────
    live_panel = ""
    if show_live:
        panel_title = (
            "Pipeline Running" if task.status == TaskStatus.in_progress
            else "Awaiting Human Input" if task.status == TaskStatus.awaiting_human_input
            else "Awaiting Approval"
        )
        live_panel = f"""\
<div class="card" style="border-color:var(--accent);">
  <h3 id="liveTitle"><span class="live-dot"></span>{panel_title}</h3>
  <div class="progress-bar" style="margin-top:8px;"><div class="fill" id="progressFill" style="width:0%"></div></div>
  <div id="liveStatus" style="margin-top:8px;font-size:13px;color:var(--text-dim);"></div>
  <div id="thinkingPanel" style="display:none;margin-top:12px;">
    <div style="display:flex;align-items:center;gap:8px;cursor:pointer;" onclick="toggleThinking()">
      <span style="font-size:13px;font-weight:600;color:var(--purple);">
        <span id="thinkingIcon" style="display:inline-block;transition:transform .2s;">&#9654;</span> Thinking
      </span>
      <span id="thinkingBadge" style="font-size:11px;background:#1a1040;color:var(--purple);border:1px solid #3b2d6b;padding:1px 8px;border-radius:10px;"></span>
    </div>
    <pre id="thinkingOutput" style="background:#0d0a1a;border:1px solid #2d2050;border-radius:6px;padding:12px;font-size:11px;max-height:300px;overflow-y:auto;white-space:pre-wrap;color:#bc8cff;display:none;font-style:italic;"></pre>
  </div>
  <div style="margin-top:8px;">
    <div style="font-size:13px;font-weight:600;color:var(--accent);margin-bottom:6px;" id="outputLabel" style="display:none;">Output</div>
    <pre id="liveOutput" style="margin-top:0;max-height:400px;overflow-y:auto;display:none;"></pre>
  </div>
  <button class="btn btn-red btn-sm" style="margin-top:12px;" id="cancelBtn" onclick="cancelRunningTask()">Cancel</button>
</div>"""

    # ── Stage timeline ────────────────────────────────────────────────
    if not task.stages:
        timeline = '<div class="timeline" id="liveTimeline"><div class="empty-state" style="padding:24px;">No stages recorded yet.</div></div>'
    else:
        items = []
        for s in task.stages:
            status_class = ""
            if s.gate_result == "approved":
                status_class = "approved"
            elif s.gate_result == "rejected":
                status_class = "rejected"
            elif "ERROR" in s.output_text:
                status_class = "failed"
            elif s.gate_result is None and task.status == TaskStatus.awaiting_gate and s == task.stages[-1]:
                status_class = "awaiting"

            gate_info = ""
            if s.gate_result:
                gate_info = f' · Gate: {badge(s.gate_result)}'
                if s.reject_reason:
                    gate_info += f' <span style="font-size:12px;color:var(--text-dim);">({esc(s.reject_reason[:100])})</span>'

            duration = fmt_duration(s.started_at, s.finished_at)
            duration_html = f'<span class="duration">{duration}</span>' if duration else ""

            output_preview = esc(s.output_text[:200]) + ("..." if len(s.output_text) > 200 else "")

            items.append(
                f'<div class="timeline-item {status_class}">'
                f'<div><strong>Stage {s.stage_number}</strong> · '
                f'{agent_tip(s.agent_id)}'
                f'{gate_info}{duration_html}</div>'
                f'<div style="font-size:12px;color:var(--text-dim);">'
                f'{fmt_time(s.started_at)} → {fmt_time(s.finished_at)}</div>'
                f'<div style="font-size:13px;margin-top:4px;color:var(--text-dim);">{output_preview}</div>'
                f'<details><summary>Full Input</summary>{copy_pre(s.input_text)}</details>'
                f'<details><summary>Full Output</summary>{copy_pre(s.output_text)}</details>'
                f'</div>'
            )
        timeline = f'<div class="timeline" id="liveTimeline">{"".join(items)}</div>'

    # ── Context viewer ────────────────────────────────────────────────
    if context_md:
        context_section = f"""\
<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
  <span style="font-weight:600;">Context.md</span>
  <a href="/api/tasks/{esc(task.id)}/context" class="btn btn-sm" download="context.md">Download</a>
</div>
{copy_pre(context_md)}"""
    else:
        context_section = '<div class="empty-state" style="padding:24px;">No context file for this task.</div>'

    # ── Chunks panel ──────────────────────────────────────────────────
    chunks_section = f"""\
<div id="chunksPanel">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
    <span style="font-weight:600;">Work Chunks</span>
    <div style="display:flex;gap:8px;">
      <input id="newChunkDesc" type="text" placeholder="New chunk description..." style="width:260px;padding:6px 10px;border:1px solid var(--border);border-radius:4px;background:var(--surface2);color:var(--text);font-size:13px;">
      <button class="btn btn-sm btn-primary" onclick="addChunk()">Add</button>
    </div>
  </div>
  <div id="chunkList"><div class="empty-state" style="padding:16px;font-size:13px;">Loading chunks...</div></div>
</div>
<script>
async function loadChunks() {{
  try {{
    const chunks = await apiFetch('/api/tasks/{esc(task.id)}/chunks');
    const el = document.getElementById('chunkList');
    if (!chunks.length) {{ el.innerHTML = '<div class="empty-state" style="padding:16px;font-size:13px;">No chunks yet.</div>'; return; }}
    el.innerHTML = chunks.map(c => `
      <div class="chunk-item" data-id="${{c.id}}">
        <span class="badge badge-${{c.status}}">${{c.status}}</span>
        <span class="chunk-desc">${{c.id}}: ${{c.description}}</span>
        <select onchange="updateChunk('${{c.id}}', this.value)" style="width:auto;padding:4px 8px;font-size:12px;background:var(--surface2);border:1px solid var(--border);border-radius:4px;color:var(--text);">
          <option value="pending" ${{c.status==='pending'?'selected':''}}>Pending</option>
          <option value="in_progress" ${{c.status==='in_progress'?'selected':''}}>In Progress</option>
          <option value="done" ${{c.status==='done'?'selected':''}}>Done</option>
        </select>
        <button class="btn btn-sm" onclick="removeChunk('${{c.id}}')" style="color:var(--red);">Remove</button>
      </div>
    `).join('');
  }} catch(e) {{}}
}}
async function addChunk() {{
  const desc = document.getElementById('newChunkDesc').value.trim();
  if (!desc) return;
  try {{
    await apiFetch('/api/tasks/{esc(task.id)}/chunks', {{method:'POST', body:JSON.stringify({{description:desc}})}});
    document.getElementById('newChunkDesc').value = '';
    loadChunks();
  }} catch(e) {{}}
}}
async function updateChunk(chunkId, status) {{
  try {{
    await apiFetch('/api/tasks/{esc(task.id)}/chunks/' + chunkId, {{method:'PATCH', body:JSON.stringify({{status}})}});
    loadChunks();
  }} catch(e) {{}}
}}
async function removeChunk(chunkId) {{
  try {{
    await apiFetch('/api/tasks/{esc(task.id)}/chunks/' + chunkId, {{method:'DELETE'}});
    loadChunks();
  }} catch(e) {{}}
}}
loadChunks();
</script>"""

    # ── Fix form ──────────────────────────────────────────────────────
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
</div>"""

    # ── Assemble tabs ─────────────────────────────────────────────────
    # Tab: Overview (meta + action panels + fix)
    tab_overview = f"{meta}\n{gate_actions}\n{human_input_panel}\n{restart_panel}\n{fix_section}"

    # Tab: Live Output (only visible when task is active)
    tab_output = live_panel if show_live else '<div class="empty-state" style="padding:24px;">Task is not currently running.</div>'

    # Default tab
    default_tab = "tab-2" if show_live else "tab-1"

    body = f"""\
<h1>Task {esc(short_id)}</h1>

<input type="radio" name="tab" id="tab-1" {"checked" if default_tab == "tab-1" else ""}>
<input type="radio" name="tab" id="tab-2" {"checked" if default_tab == "tab-2" else ""}>
<input type="radio" name="tab" id="tab-3">
<input type="radio" name="tab" id="tab-4">
<input type="radio" name="tab" id="tab-5">

<div class="tabs">
  <label for="tab-1" class="tab">Overview</label>
  <label for="tab-2" class="tab">{"Live Output" if show_live else "Output"}</label>
  <label for="tab-3" class="tab">Timeline ({len(task.stages)})</label>
  <label for="tab-4" class="tab">Context</label>
  <label for="tab-5" class="tab">Chunks</label>
</div>

<div class="tab-panels">
  <div class="tab-panel" id="panel-1">{tab_overview}</div>
  <div class="tab-panel" id="panel-2">{tab_output}</div>
  <div class="tab-panel" id="panel-3">{timeline}</div>
  <div class="tab-panel" id="panel-4">{context_section}</div>
  <div class="tab-panel" id="panel-5">{chunks_section}</div>
</div>"""

    # ── Page JS ───────────────────────────────────────────────────────
    js = f"""\
<script>
/* Tab hash persistence */
function setTabFromHash() {{
  const hash = location.hash.replace('#', '');
  const map = {{'overview':'tab-1','output':'tab-2','timeline':'tab-3','context':'tab-4','chunks':'tab-5'}};
  const tabId = map[hash];
  if (tabId) document.getElementById(tabId).checked = true;
}}
setTabFromHash();
window.addEventListener('hashchange', setTabFromHash);
document.querySelectorAll('.tabs label').forEach(l => {{
  l.addEventListener('click', () => {{
    const map = {{'tab-1':'overview','tab-2':'output','tab-3':'timeline','tab-4':'context','tab-5':'chunks'}};
    const hash = map[l.getAttribute('for')];
    if (hash) history.replaceState(null, '', '#' + hash);
  }});
}});

/* Gate actions */
async function gateAction(action) {{
  const reason = document.getElementById('gateReason')?.value?.trim() || '';
  if (action === 'reject' && !reason) {{ showToast('Reason required for rejection', 'error'); return; }}
  try {{
    await apiFetch('/api/tasks/{esc(task.id)}/' + action, {{method:'POST', body:JSON.stringify({{reason:reason||undefined}})}});
    showToast('Task ' + action + 'd');
    setTimeout(() => location.reload(), 600);
  }} catch(e) {{}}
}}

/* Human input */
async function submitHumanInput() {{
  const response = document.getElementById('humanInput')?.value?.trim();
  if (!response) {{ showToast('Please enter a response', 'error'); return; }}
  try {{
    await apiFetch('/api/tasks/{esc(task.id)}/human-input', {{method:'POST', body:JSON.stringify({{response}})}});
    showToast('Response submitted');
    setTimeout(() => location.reload(), 600);
  }} catch(e) {{}}
}}

/* Restart */
async function restartTask() {{
  const stageVal = document.getElementById('restartStage')?.value;
  const body = stageVal ? {{from_stage: parseInt(stageVal)}} : {{}};
  try {{
    await apiFetch('/api/tasks/{esc(task.id)}/restart', {{method:'POST', body:JSON.stringify(body)}});
    showToast('Task restarting...');
    setTimeout(() => location.reload(), 800);
  }} catch(e) {{}}
}}

/* Cancel */
async function cancelRunningTask() {{
  if (!confirm('Cancel this running pipeline?')) return;
  try {{
    await apiFetch('/api/tasks/{esc(task.id)}/cancel', {{method:'POST', body:'{{}}'}});
    showToast('Cancellation requested');
    setTimeout(() => location.reload(), 1000);
  }} catch(e) {{}}
}}

/* Fix form */
document.getElementById('fixForm')?.addEventListener('submit', async (e) => {{
  e.preventDefault();
  const desc = document.getElementById('fixDesc')?.value?.trim();
  if (!desc) return;
  try {{
    const data = await apiFetch('/api/fix', {{method:'POST', body:JSON.stringify({{parent_task_id:'{esc(task.id)}', description:desc}})}});
    showToast('Fix started: ' + data.task_id);
    setTimeout(() => location.href = '/tasks/' + data.task_id, 600);
  }} catch(e) {{}}
}});

/* Thinking toggle */
let thinkingExpanded = false;
function toggleThinking() {{
  thinkingExpanded = !thinkingExpanded;
  const el = document.getElementById('thinkingOutput');
  const icon = document.getElementById('thinkingIcon');
  if (el) el.style.display = thinkingExpanded ? 'block' : 'none';
  if (icon) icon.style.transform = thinkingExpanded ? 'rotate(90deg)' : 'rotate(0deg)';
  if (thinkingExpanded && el) el.scrollTop = el.scrollHeight;
}}
</script>"""

    # ── SSE listener (separate, only when live) ───────────────────────
    sse_js = ""
    if show_live:
        sse_js = f"""\
<script>
(function() {{
  const es = new EventSource('/api/tasks/{esc(task.id)}/events');
  const statusEl = document.getElementById('liveStatus');
  const outputEl = document.getElementById('liveOutput');
  const titleEl = document.getElementById('liveTitle');
  const thinkingPanel = document.getElementById('thinkingPanel');
  const thinkingOutput = document.getElementById('thinkingOutput');
  const thinkingBadge = document.getElementById('thinkingBadge');
  const outputLabel = document.getElementById('outputLabel');
  let thinkingLines = 0;

  es.addEventListener('stage_start', (e) => {{
    const d = JSON.parse(e.data);
    titleEl.innerHTML = '<span class="live-dot"></span>Pipeline Running';
    statusEl.innerHTML = '<span class="live-dot"></span>Stage ' + d.stage_number + ': <strong>' + d.agent_id + '</strong>';
    if (outputEl) {{ outputEl.textContent = ''; outputEl.style.display = 'block'; }}
    if (outputLabel) outputLabel.style.display = 'block';
    if (thinkingOutput) thinkingOutput.textContent = '';
    thinkingLines = 0;
    if (thinkingPanel) thinkingPanel.style.display = 'none';
  }});
  es.addEventListener('stage_thinking', (e) => {{
    const d = JSON.parse(e.data);
    if (thinkingPanel) thinkingPanel.style.display = 'block';
    thinkingLines++;
    if (thinkingBadge) thinkingBadge.textContent = thinkingLines + ' chunks';
    if (thinkingOutput) {{ thinkingOutput.textContent += d.text + '\\n'; if (thinkingExpanded) thinkingOutput.scrollTop = thinkingOutput.scrollHeight; }}
  }});
  es.addEventListener('stage_output', (e) => {{
    const d = JSON.parse(e.data);
    if (outputEl) {{ outputEl.style.display = 'block'; outputEl.textContent += d.text + '\\n'; outputEl.scrollTop = outputEl.scrollHeight; }}
    if (outputLabel) outputLabel.style.display = 'block';
  }});
  es.addEventListener('stage_complete', (e) => {{
    const d = JSON.parse(e.data);
    statusEl.innerHTML = 'Stage ' + d.stage_number + ': <strong>' + d.agent_id + '</strong> — ' + (d.gate_result || 'done');
  }});
  es.addEventListener('pipeline_resuming', (e) => {{
    titleEl.innerHTML = '<span class="live-dot"></span>Resuming...';
    statusEl.innerHTML = 'Loading next agent...';
  }});
  es.addEventListener('human_input_waiting', () => {{ es.close(); setTimeout(() => location.reload(), 800); }});
  es.addEventListener('gate_waiting', () => {{ es.close(); setTimeout(() => location.reload(), 800); }});
  es.addEventListener('task_complete', () => {{
    es.close();
    showToast('Pipeline completed');
    titleEl.innerHTML = '<span style="color:var(--green);">&#10003;</span> Completed';
    document.getElementById('cancelBtn').style.display = 'none';
  }});
  es.addEventListener('task_cancelled', () => {{ es.close(); setTimeout(() => location.reload(), 800); }});
  es.addEventListener('task_failed', (e) => {{
    es.close();
    const d = JSON.parse(e.data);
    showToast('Pipeline failed: ' + (d.error || ''), 'error');
    titleEl.innerHTML = '<span style="color:var(--red);">&#10007;</span> Failed';
    document.getElementById('cancelBtn').style.display = 'none';
  }});
  es.onerror = () => es.close();
}})();
</script>"""

    return layout(
        f"Task {short_id}",
        f"{body}\n{js}\n{sse_js}",
        active="tasks",
        breadcrumbs=[("Dashboard", "/"), (f"Task {short_id}", None)],
    )
