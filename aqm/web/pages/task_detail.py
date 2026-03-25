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
    showToast('Task ' + action + 'd — pipeline resuming...');
    // Show live panel immediately instead of reloading
    const titleEl = document.getElementById('liveTitle');
    const statusEl = document.getElementById('liveStatus');
    const outputEl = document.getElementById('liveOutput');
    if (titleEl) {{
      titleEl.innerHTML = '<span class="live-dot"></span>Pipeline Resuming...';
      statusEl.innerHTML = 'Loading next agent...';
      if (outputEl) {{ outputEl.style.display = 'block'; outputEl.textContent = ''; }}
    }} else {{
      // Live panel not visible yet — reload to show it
      setTimeout(() => location.reload(), 600);
    }}
  }} catch(e) {{}}
}}
</script>"""

    # Human input action panel
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
    Agent <strong>{esc(hi_agent)}</strong> is asking for your input ({esc(hi_mode)} mode)
  </div>
  <div style="margin-top:12px;">
    {questions_html}
    <div class="form-group" style="margin-top:12px;">
      <label for="humanInput">Your Response</label>
      <textarea id="humanInput" rows="4" placeholder="Type your response..."></textarea>
    </div>
    <button class="btn btn-primary" onclick="submitHumanInput()">Submit Response</button>
  </div>
</div>
<script>
async function submitHumanInput() {{
  const response = document.getElementById('humanInput').value.trim();
  if (!response) {{ showToast('Please enter a response', 'error'); return; }}
  try {{
    await apiFetch('/api/tasks/{esc(task.id)}/human-input', {{
      method:'POST', body:JSON.stringify({{response:response}})
    }});
    showToast('Response submitted — pipeline resuming...');
    const titleEl = document.getElementById('liveTitle');
    const statusEl = document.getElementById('liveStatus');
    if (titleEl) {{
      titleEl.innerHTML = '<span class="live-dot"></span>Pipeline Resuming...';
      statusEl.innerHTML = 'Processing your input...';
    }} else {{
      setTimeout(() => location.reload(), 600);
    }}
  }} catch(e) {{}}
}}
</script>"""

    # Live progress panel (for in_progress, awaiting_gate, or awaiting_human_input tasks)
    live_panel = ""
    show_live = task.status in (TaskStatus.in_progress, TaskStatus.awaiting_gate, TaskStatus.awaiting_human_input)
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

  <!-- Thinking panel -->
  <div id="thinkingPanel" style="display:none;margin-top:12px;">
    <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px;cursor:pointer;" onclick="toggleThinking()">
      <span style="font-size:13px;font-weight:600;color:var(--purple);">
        <span id="thinkingIcon" style="display:inline-block;transition:transform .2s;">&#9654;</span>
        Thinking
      </span>
      <span id="thinkingBadge" style="font-size:11px;background:#1a1040;color:var(--purple);border:1px solid #3b2d6b;
        padding:1px 8px;border-radius:10px;"></span>
      <span class="live-dot" id="thinkingDot" style="background:var(--purple);width:6px;height:6px;margin-left:4px;"></span>
    </div>
    <pre id="thinkingOutput" style="background:#0d0a1a;border:1px solid #2d2050;
      border-radius:6px;padding:12px;font-size:11px;max-height:300px;overflow-y:auto;white-space:pre-wrap;
      word-break:break-word;color:#bc8cff;display:none;font-style:italic;"></pre>
  </div>

  <!-- Output panel -->
  <div style="margin-top:8px;">
    <div style="font-size:13px;font-weight:600;color:var(--accent);margin-bottom:6px;" id="outputLabel" style="display:none;">Output</div>
    <pre id="liveOutput" style="margin-top:0;background:var(--surface2);border:1px solid var(--border);
      border-radius:6px;padding:12px;font-size:12px;max-height:400px;overflow-y:auto;white-space:pre-wrap;
      word-break:break-word;display:none;"></pre>
  </div>

  <button class="btn btn-red btn-sm" style="margin-top:12px;" id="cancelBtn" onclick="cancelRunningTask()">Cancel</button>
</div>
<script>
(function() {{
  const es = new EventSource('/api/tasks/{esc(task.id)}/events');
  const statusEl = document.getElementById('liveStatus');
  const outputEl = document.getElementById('liveOutput');
  const titleEl = document.getElementById('liveTitle');
  const thinkingPanel = document.getElementById('thinkingPanel');
  const thinkingOutput = document.getElementById('thinkingOutput');
  const thinkingBadge = document.getElementById('thinkingBadge');
  const thinkingDot = document.getElementById('thinkingDot');
  const thinkingIcon = document.getElementById('thinkingIcon');
  const outputLabel = document.getElementById('outputLabel');
  let stageCount = {len(task.stages)};
  let thinkingLines = 0;
  let thinkingExpanded = false;

  window.toggleThinking = function() {{
    thinkingExpanded = !thinkingExpanded;
    thinkingOutput.style.display = thinkingExpanded ? 'block' : 'none';
    thinkingIcon.style.transform = thinkingExpanded ? 'rotate(90deg)' : 'rotate(0deg)';
    if (thinkingExpanded) thinkingOutput.scrollTop = thinkingOutput.scrollHeight;
  }};

  // Track current stage output for timeline
  let currentStageOutput = '';
  let currentStageAgentId = '';
  let currentStageNumber = 0;

  function addTimelineItem(stageNum, agentId, gateResult, outputText, isRunning) {{
    const timeline = document.getElementById('liveTimeline');
    if (!timeline) return;
    const emptyState = timeline.querySelector('.empty-state');
    if (emptyState) emptyState.remove();

    // Remove existing running item for this stage
    const existingId = 'timeline-stage-' + stageNum;
    const existing = document.getElementById(existingId);
    if (existing) existing.remove();

    const statusClass = gateResult === 'approved' ? 'approved'
      : gateResult === 'rejected' ? 'rejected'
      : isRunning ? 'running' : '';
    const gateHtml = gateResult
      ? ' · Gate: <span class="badge badge-' + gateResult + '">' + gateResult + '</span>'
      : '';
    const preview = (outputText || '').substring(0, 200) + (outputText && outputText.length > 200 ? '...' : '');
    const now = new Date().toISOString().replace('T',' ').substring(0,19);

    const item = document.createElement('div');
    item.className = 'timeline-item ' + statusClass;
    item.id = existingId;
    item.innerHTML = '<div><strong>Stage ' + stageNum + '</strong> · '
      + '<span style="color:var(--accent);">' + agentId + '</span>'
      + gateHtml + '</div>'
      + '<div style="font-size:12px;color:var(--text-dim);">' + now + (isRunning ? ' · running...' : '') + '</div>'
      + '<div style="font-size:13px;margin-top:4px;color:var(--text-dim);">' + preview.replace(/</g,'&lt;') + '</div>'
      + (outputText && !isRunning ? '<details><summary>Full Output</summary><pre>' + outputText.replace(/</g,'&lt;') + '</pre></details>' : '');
    timeline.appendChild(item);
    item.scrollIntoView({{ behavior: 'smooth', block: 'nearest' }});
  }}

  es.addEventListener('stage_start', (e) => {{
    const d = JSON.parse(e.data);
    currentStageOutput = '';
    currentStageAgentId = d.agent_id;
    currentStageNumber = d.stage_number;
    titleEl.innerHTML = '<span class="live-dot"></span>Pipeline Running';
    statusEl.innerHTML = '<span class="live-dot"></span>Stage ' + d.stage_number + ': <strong>' + d.agent_id + '</strong> running...';
    outputEl.textContent = '';
    outputEl.style.display = 'block';
    outputLabel.style.display = 'block';
    thinkingOutput.textContent = '';
    thinkingLines = 0;
    thinkingPanel.style.display = 'none';
    thinkingDot.style.display = 'inline-block';
    // Add running indicator to timeline
    addTimelineItem(d.stage_number, d.agent_id, null, '', true);
  }});
  es.addEventListener('stage_thinking', (e) => {{
    const d = JSON.parse(e.data);
    thinkingPanel.style.display = 'block';
    thinkingLines++;
    thinkingBadge.textContent = thinkingLines + ' chunks';
    thinkingOutput.textContent += d.text + '\\n';
    if (thinkingExpanded) thinkingOutput.scrollTop = thinkingOutput.scrollHeight;
  }});
  es.addEventListener('stage_output', (e) => {{
    const d = JSON.parse(e.data);
    currentStageOutput += d.text + '\\n';
    outputEl.style.display = 'block';
    outputLabel.style.display = 'block';
    thinkingDot.style.display = 'none';
    outputEl.textContent += d.text + '\\n';
    outputEl.scrollTop = outputEl.scrollHeight;
  }});
  es.addEventListener('stage_complete', (e) => {{
    const d = JSON.parse(e.data);
    stageCount++;
    statusEl.innerHTML = 'Stage ' + d.stage_number + ': <strong>' + d.agent_id + '</strong> — ' + (d.gate_result || 'done');
    thinkingDot.style.display = 'none';
    // Update timeline with completed stage
    addTimelineItem(d.stage_number, d.agent_id, d.gate_result, d.output_preview || currentStageOutput, false);
  }});
  es.addEventListener('pipeline_resuming', (e) => {{
    titleEl.innerHTML = '<span class="live-dot"></span>Pipeline Resuming...';
    statusEl.innerHTML = 'Loading next agent...';
    outputEl.textContent = '';
    outputEl.style.display = 'block';
    thinkingOutput.textContent = '';
    thinkingPanel.style.display = 'none';
    thinkingLines = 0;
  }});
  es.addEventListener('human_input_waiting', (e) => {{
    es.close();
    showToast('Agent needs your input');
    setTimeout(() => location.reload(), 800);
  }});
  es.addEventListener('gate_waiting', (e) => {{
    es.close();
    showToast('Awaiting gate approval');
    setTimeout(() => location.reload(), 800);
  }});
  es.addEventListener('task_complete', (e) => {{
    es.close();
    showToast('Pipeline completed');
    titleEl.innerHTML = '<span style="color:var(--green);">&#10003;</span> Pipeline Completed';
    statusEl.innerHTML = 'All stages finished — ' + stageCount + ' stages total';
    document.getElementById('cancelBtn').style.display = 'none';
  }});
  es.addEventListener('task_cancelled', (e) => {{
    es.close();
    showToast('Pipeline cancelled');
    setTimeout(() => location.reload(), 800);
  }});
  es.addEventListener('task_failed', (e) => {{
    es.close();
    const d = JSON.parse(e.data);
    showToast('Pipeline failed: ' + (d.error||''), 'error');
    titleEl.innerHTML = '<span style="color:var(--red);">&#10007;</span> Pipeline Failed';
    statusEl.innerHTML = d.error || 'Unknown error';
    document.getElementById('cancelBtn').style.display = 'none';
  }});
  es.onerror = () => {{ es.close(); }};
}})();
async function cancelRunningTask() {{
  if (!confirm('Cancel this running pipeline?')) return;
  try {{
    await apiFetch('/api/tasks/{esc(task.id)}/cancel', {{method:'POST', body:'{{}}'}});
    showToast('Cancellation requested');
    setTimeout(() => location.reload(), 1000);
  }} catch(e) {{}}
}}
</script>"""

    # Stage timeline
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
        timeline = f'<div class="timeline" id="liveTimeline">{"".join(items)}</div>'

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
        f'{meta}\n{gate_actions}\n{human_input_panel}\n{live_panel}\n'
        f'<h2 style="margin-top:24px;">Stage Timeline</h2>\n{timeline}\n'
        f'{context_section}\n{fix_section}',
        active="tasks",
    )
