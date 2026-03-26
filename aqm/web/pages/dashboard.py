"""Dashboard page — task stats, run pipeline form, task list with filtering/sorting."""

from __future__ import annotations

from aqm.core.agent import AgentDefinition
from aqm.core.task import Task, TaskStatus
from aqm.web.templates import badge, esc, fmt_time, layout


def render_dashboard(
    tasks: list[Task],
    agents: dict[str, AgentDefinition],
    pipelines: list[str] | None = None,
    current_pipeline: str = "default",
    agent_error: str | None = None,
) -> str:
    total = len(tasks)
    completed = sum(1 for t in tasks if t.status == TaskStatus.completed)
    failed = sum(1 for t in tasks if t.status == TaskStatus.failed)
    awaiting = sum(1 for t in tasks if t.status == TaskStatus.awaiting_gate)
    running = sum(1 for t in tasks if t.status == TaskStatus.in_progress)

    # Pipeline selector
    pipeline_selector = ""
    if pipelines and len(pipelines) > 1:
        pipe_options = "".join(
            f'<option value="{esc(p)}"{"selected" if p == current_pipeline else ""}>{esc(p)}</option>'
            for p in pipelines
        )
        pipeline_selector = f"""\
<div class="card" style="margin-bottom:16px;padding:12px 16px;display:flex;align-items:center;gap:12px;">
  <label style="font-weight:600;white-space:nowrap;">Pipeline:</label>
  <select id="pipelineSelector" onchange="location.href='/?pipeline='+this.value"
          style="flex:1;max-width:300px;">{pipe_options}</select>
  <span style="font-size:12px;opacity:.6;">{len(pipelines)} pipeline(s)</span>
</div>"""

    error_banner = ""
    if agent_error:
        error_banner = (
            f'<div class="card" style="background:var(--surface2);border-left:4px solid var(--orange);'
            f'padding:12px 16px;margin-bottom:16px;">'
            f'<strong style="color:var(--orange);">Warning: Pipeline configuration error</strong>'
            f'<p style="margin:6px 0 0;font-size:13px;opacity:.85;">{esc(agent_error)}</p>'
            f'</div>'
        )

    stats = f"""\
{pipeline_selector}
{error_banner}
<div class="stats" id="statsCards">
  <div class="stat-card"><div class="value" id="statTotal">{total}</div><div class="label">Total</div></div>
  <div class="stat-card blue"><div class="value" id="statRunning">{running}</div><div class="label">Running</div></div>
  <div class="stat-card green"><div class="value" id="statCompleted">{completed}</div><div class="label">Completed</div></div>
  <div class="stat-card red"><div class="value" id="statFailed">{failed}</div><div class="label">Failed</div></div>
  <div class="stat-card orange"><div class="value" id="statAwaiting">{awaiting}</div><div class="label">Awaiting Gate</div></div>
</div>"""

    # Agent options for dropdown
    agent_options = "".join(
        f'<option value="{esc(a.id)}">{esc(a.name)} ({esc(a.id)})</option>'
        for a in agents.values()
    )

    # Unique agents from tasks for filter
    task_agents = sorted(set(t.current_agent_id for t in tasks if t.current_agent_id))
    agent_filter_options = "".join(f'<option value="{esc(a)}">{esc(a)}</option>' for a in task_agents)

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
  </form>
</div>"""

    # Filter bar
    filter_bar = f"""\
<div class="filter-bar">
  <div class="form-group">
    <label>Status</label>
    <select id="filterStatus" onchange="applyFilters()" style="width:140px;">
      <option value="">All</option>
      <option value="pending">Pending</option>
      <option value="in_progress">Running</option>
      <option value="completed">Completed</option>
      <option value="failed">Failed</option>
      <option value="awaiting_gate">Awaiting Gate</option>
      <option value="awaiting_human_input">Awaiting Input</option>
      <option value="cancelled">Cancelled</option>
      <option value="stalled">Stalled</option>
    </select>
  </div>
  <div class="form-group">
    <label>Agent</label>
    <select id="filterAgent" onchange="applyFilters()" style="width:140px;">
      <option value="">All</option>
      {agent_filter_options}
    </select>
  </div>
  <div class="form-group">
    <label>Priority</label>
    <select id="filterPriority" onchange="applyFilters()" style="width:120px;">
      <option value="">All</option>
      <option value="critical">Critical</option>
      <option value="high">High</option>
      <option value="normal">Normal</option>
      <option value="low">Low</option>
    </select>
  </div>
  <div class="form-group flex-1">
    <label>Search</label>
    <input id="filterSearch" type="text" placeholder="Search descriptions..." oninput="applyFilters()">
  </div>
</div>"""

    # Batch action bar
    batch_bar = """\
<div class="batch-bar" id="batchBar">
  <span class="count" id="batchCount">0 selected</span>
  <button class="btn btn-sm btn-red" onclick="batchCancel()">Cancel Selected</button>
  <button class="btn btn-sm" onclick="batchRestart()">Restart Selected</button>
  <button class="btn btn-sm" onclick="clearSelection()">Clear</button>
</div>"""

    # Task table
    if not tasks:
        table_body = """\
<div class="empty-state">
  <div class="icon">📋</div>
  <div class="title">No tasks yet</div>
  <div class="desc">Run a pipeline above to create your first task.</div>
</div>"""
        table = f'<h2 style="margin-top:24px;">Tasks</h2>\n{table_body}'
    else:
        row_list = []
        for t in tasks:
            desc = esc(t.description[:60])
            if len(t.description) > 60:
                desc += "..."
            agent = esc(t.current_agent_id or "-")
            p_name = t.priority.name

            # Quick actions dropdown
            actions_items = f'<a href="/tasks/{esc(t.id)}">View</a>'
            if t.status in (TaskStatus.completed, TaskStatus.failed):
                actions_items += f'<button onclick="showFixForm(\'{esc(t.id)}\')">Fix</button>'
            if t.status in (TaskStatus.failed, TaskStatus.completed, TaskStatus.stalled, TaskStatus.cancelled):
                actions_items += f'<button onclick="restartTask(\'{esc(t.id)}\')">Restart</button>'
            if t.status == TaskStatus.awaiting_gate:
                actions_items += f'<a href="/tasks/{esc(t.id)}">Approve/Reject</a>'
            if t.status in (TaskStatus.pending, TaskStatus.in_progress, TaskStatus.awaiting_gate, TaskStatus.awaiting_human_input):
                actions_items += f'<div class="divider"></div><button class="danger" onclick="showCancelModal(\'{esc(t.id)}\')">Cancel</button>'

            row_list.append(
                f'<tr data-status="{esc(t.status.value)}" data-agent="{agent}" '
                f'data-desc="{esc(t.description.lower())}" data-priority="{p_name}" '
                f'data-created="{t.created_at.isoformat() if t.created_at else ""}" data-id="{esc(t.id)}">'
                f'<td><input type="checkbox" class="row-check" onchange="updateBatch()"></td>'
                f'<td><a href="/tasks/{esc(t.id)}">{esc(t.id)}</a></td>'
                f'<td>{badge(t.status.value)}</td>'
                f'<td style="font-size:12px;color:var(--text-dim);">{p_name}</td>'
                f'<td>{agent}</td>'
                f'<td title="{esc(t.description)}">{desc}</td>'
                f'<td>{len(t.stages)}</td>'
                f'<td style="font-size:12px;">{fmt_time(t.created_at)}</td>'
                f'<td>'
                f'<div class="dropdown">'
                f'<button class="btn btn-sm" onclick="toggleDropdown(this)">Actions</button>'
                f'<div class="dropdown-menu">{actions_items}</div>'
                f'</div></td>'
                f'</tr>'
            )
        rows = "\n".join(row_list)

        table = f"""\
<h2 style="margin-top:24px;">Tasks</h2>
{filter_bar}
{batch_bar}
<div class="table-wrap">
<table id="taskTable">
<thead><tr>
  <th style="width:32px;"><input type="checkbox" id="selectAll" onchange="toggleSelectAll(this)"></th>
  <th data-sort="id">ID <span class="sort-icon"></span></th>
  <th data-sort="status">Status <span class="sort-icon"></span></th>
  <th data-sort="priority">Priority <span class="sort-icon"></span></th>
  <th data-sort="agent">Agent <span class="sort-icon"></span></th>
  <th>Description</th>
  <th data-sort="stages">Stages <span class="sort-icon"></span></th>
  <th data-sort="created">Created <span class="sort-icon"></span></th>
  <th>Actions</th>
</tr></thead>
<tbody id="taskBody">{rows}</tbody>
</table>
</div>
<div class="pagination" id="pagination"></div>"""

    # Cancel modal
    cancel_modal = """\
<div class="modal-overlay" id="cancelModal">
  <div class="modal modal-sm">
    <h3>Cancel Task</h3>
    <input type="hidden" id="cancelTaskId">
    <div class="form-group">
      <label for="cancelReason">Reason (optional)</label>
      <input id="cancelReason" type="text" placeholder="Why are you cancelling?">
    </div>
    <div style="display:flex;gap:8px;margin-top:12px;">
      <button class="btn btn-red" onclick="confirmCancel()">Cancel Task</button>
      <button class="btn" onclick="document.getElementById('cancelModal').classList.remove('show')">Close</button>
    </div>
  </div>
</div>"""

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
</div>"""

    js = """\
<script>
/* ── Run form ──────────────────────────────── */
document.getElementById('runForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const desc = document.getElementById('runDesc').value.trim();
  if (!desc) return;
  const agent = document.getElementById('runAgent').value;
  const priority = document.getElementById('runPriority').value;
  const pipelineSel = document.getElementById('pipelineSelector');
  const body = {description: desc, priority: priority};
  if (agent) body.agent_id = agent;
  if (pipelineSel) body.pipeline = pipelineSel.value;
  const btn = e.target.querySelector('button[type=submit]');
  btn.disabled = true; btn.textContent = 'Starting...';
  try {
    const data = await apiFetch('/api/run', {method:'POST', body:JSON.stringify(body)});
    showToast('Pipeline started: ' + data.task_id);
    setTimeout(() => location.href = '/tasks/' + data.task_id, 600);
  } catch(err) { btn.disabled = false; btn.textContent = 'Start Run'; }
});

/* ── Fix modal ─────────────────────────────── */
function showFixForm(taskId) {
  document.getElementById('fixParentId').value = taskId;
  document.getElementById('fixTaskId').textContent = taskId;
  document.getElementById('fixModal').classList.add('show');
}
document.getElementById('fixForm')?.addEventListener('submit', async (e) => {
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
document.querySelectorAll('.modal-overlay').forEach(m => {
  m.addEventListener('click', (e) => { if (e.target === m) m.classList.remove('show'); });
});

/* ── Cancel modal ──────────────────────────── */
function showCancelModal(taskId) {
  document.getElementById('cancelTaskId').value = taskId;
  document.getElementById('cancelReason').value = '';
  document.getElementById('cancelModal').classList.add('show');
}
async function confirmCancel() {
  const taskId = document.getElementById('cancelTaskId').value;
  const reason = document.getElementById('cancelReason').value.trim();
  const body = reason ? {reason} : {};
  try {
    await apiFetch('/api/tasks/' + taskId + '/cancel', {method:'POST', body:JSON.stringify(body)});
    showToast('Task cancelled');
    document.getElementById('cancelModal').classList.remove('show');
    setTimeout(() => location.reload(), 600);
  } catch(e) {}
}

/* ── Restart ───────────────────────────────── */
async function restartTask(taskId) {
  try {
    await apiFetch('/api/tasks/' + taskId + '/restart', {method:'POST', body:'{}'});
    showToast('Task restarting');
    setTimeout(() => location.href = '/tasks/' + taskId, 600);
  } catch(e) {}
}

/* ── Filtering ─────────────────────────────── */
function applyFilters() {
  const status = document.getElementById('filterStatus')?.value || '';
  const agent = document.getElementById('filterAgent')?.value || '';
  const priority = document.getElementById('filterPriority')?.value || '';
  const search = document.getElementById('filterSearch')?.value?.toLowerCase() || '';
  document.querySelectorAll('#taskBody tr').forEach(row => {
    const show = (!status || row.dataset.status === status)
      && (!agent || row.dataset.agent === agent)
      && (!priority || row.dataset.priority === priority)
      && (!search || row.dataset.desc?.includes(search));
    row.style.display = show ? '' : 'none';
  });
  paginate();
}

/* ── Sorting ───────────────────────────────── */
let sortCol = '', sortAsc = true;
document.querySelectorAll('th[data-sort]').forEach(th => {
  th.addEventListener('click', () => {
    const col = th.dataset.sort;
    if (sortCol === col) { sortAsc = !sortAsc; } else { sortCol = col; sortAsc = true; }
    const tbody = document.getElementById('taskBody');
    if (!tbody) return;
    const rows = Array.from(tbody.querySelectorAll('tr'));
    rows.sort((a, b) => {
      let av = a.dataset[col] || '';
      let bv = b.dataset[col] || '';
      if (col === 'stages') { av = a.children[6]?.textContent||'0'; bv = b.children[6]?.textContent||'0'; return sortAsc ? av-bv : bv-av; }
      return sortAsc ? av.localeCompare(bv) : bv.localeCompare(av);
    });
    rows.forEach(r => tbody.appendChild(r));
    // Update sort icons
    document.querySelectorAll('th[data-sort] .sort-icon').forEach(s => s.textContent = '');
    th.querySelector('.sort-icon').textContent = sortAsc ? ' ▲' : ' ▼';
    paginate();
  });
});

/* ── Pagination ────────────────────────────── */
const PAGE_SIZE = 25;
let currentPage = 1;
function paginate() {
  const rows = Array.from(document.querySelectorAll('#taskBody tr')).filter(r => r.style.display !== 'none');
  const totalPages = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
  if (currentPage > totalPages) currentPage = totalPages;
  rows.forEach((row, i) => {
    row.style.display = (i >= (currentPage-1)*PAGE_SIZE && i < currentPage*PAGE_SIZE) ? '' : 'none';
  });
  const pag = document.getElementById('pagination');
  if (!pag || rows.length <= PAGE_SIZE) { if(pag) pag.innerHTML = ''; return; }
  let html = '<button '+(currentPage<=1?'disabled':'')+' onclick="goPage('+(currentPage-1)+')">Prev</button>';
  for (let p = 1; p <= totalPages; p++) {
    html += '<button class="'+(p===currentPage?'active':'')+'" onclick="goPage('+p+')">'+p+'</button>';
  }
  html += '<button '+(currentPage>=totalPages?'disabled':'')+' onclick="goPage('+(currentPage+1)+')">Next</button>';
  html += '<span class="info">'+rows.length+' task(s)</span>';
  pag.innerHTML = html;
}
function goPage(p) { currentPage = p; applyFilters(); }

/* ── Batch selection ───────────────────────── */
function toggleSelectAll(el) {
  document.querySelectorAll('#taskBody .row-check').forEach(cb => {
    if (cb.closest('tr').style.display !== 'none') cb.checked = el.checked;
  });
  updateBatch();
}
function updateBatch() {
  const checked = document.querySelectorAll('#taskBody .row-check:checked');
  const bar = document.getElementById('batchBar');
  if (!bar) return;
  if (checked.length > 0) {
    bar.classList.add('show');
    document.getElementById('batchCount').textContent = checked.length + ' selected';
  } else {
    bar.classList.remove('show');
  }
}
function getSelectedIds() {
  return Array.from(document.querySelectorAll('#taskBody .row-check:checked'))
    .map(cb => cb.closest('tr').dataset.id);
}
function clearSelection() {
  document.querySelectorAll('#taskBody .row-check').forEach(cb => cb.checked = false);
  document.getElementById('selectAll').checked = false;
  updateBatch();
}
async function batchCancel() {
  const ids = getSelectedIds();
  if (!ids.length || !confirm('Cancel ' + ids.length + ' task(s)?')) return;
  for (const id of ids) {
    try { await apiFetch('/api/tasks/' + id + '/cancel', {method:'POST', body:'{}'}); } catch(e) {}
  }
  showToast(ids.length + ' task(s) cancelled');
  setTimeout(() => location.reload(), 600);
}
async function batchRestart() {
  const ids = getSelectedIds();
  if (!ids.length || !confirm('Restart ' + ids.length + ' task(s)?')) return;
  for (const id of ids) {
    try { await apiFetch('/api/tasks/' + id + '/restart', {method:'POST', body:'{}'}); } catch(e) {}
  }
  showToast(ids.length + ' task(s) restarting');
  setTimeout(() => location.reload(), 600);
}

/* ── SSE real-time counters ────────────────── */
try {
  const es = new EventSource('/api/events');
  es.addEventListener('task_count_update', (e) => {
    const d = JSON.parse(e.data);
    if (d.total !== undefined) document.getElementById('statTotal').textContent = d.total;
    if (d.running !== undefined) document.getElementById('statRunning').textContent = d.running;
    if (d.completed !== undefined) document.getElementById('statCompleted').textContent = d.completed;
    if (d.failed !== undefined) document.getElementById('statFailed').textContent = d.failed;
    if (d.awaiting !== undefined) document.getElementById('statAwaiting').textContent = d.awaiting;
  });
  es.onerror = () => {};
} catch(e) {}

/* Init pagination */
paginate();
</script>"""

    return layout(
        "Dashboard",
        f"<h1>Dashboard</h1>\n{stats}\n{form}\n{table}\n{cancel_modal}\n{fix_modal}\n{js}",
        active="tasks",
    )
