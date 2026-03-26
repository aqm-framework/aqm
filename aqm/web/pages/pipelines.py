"""Pipeline management page — create, edit (visual + YAML), duplicate, delete."""

from __future__ import annotations

from aqm.web.templates import esc, layout


def render_pipelines(
    pipelines: list[dict],
    current_default: str,
    edit_name: str | None = None,
    edit_content: str | None = None,
) -> str:
    # Pipeline table
    if pipelines:
        rows = []
        for p in pipelines:
            name = p["name"]
            default_badge = '<span class="badge badge-completed">default</span>' if p.get("is_default") else ""
            rows.append(f"""\
<tr>
  <td><a href="/pipelines?edit={esc(name)}">{esc(name)}</a></td>
  <td>{p.get('agent_count', 0)}</td>
  <td>{default_badge}</td>
  <td>
    <div class="dropdown">
      <button class="btn btn-sm" onclick="toggleDropdown(this)">Actions</button>
      <div class="dropdown-menu">
        <a href="/pipelines?edit={esc(name)}">Edit</a>
        <button onclick="duplicatePipeline('{esc(name)}')">Duplicate</button>
        <button onclick="setDefault('{esc(name)}')">Set as Default</button>
        <a href="/api/pipelines/{esc(name)}/yaml" download>Download YAML</a>
        <div class="divider"></div>
        <button class="danger" onclick="deletePipeline('{esc(name)}')">Delete</button>
      </div>
    </div>
  </td>
</tr>""")
        table = f"""\
<div class="table-wrap">
<table>
  <thead><tr><th>Name</th><th>Agents</th><th>Status</th><th>Actions</th></tr></thead>
  <tbody>{"".join(rows)}</tbody>
</table>
</div>"""
    else:
        table = """\
<div class="empty-state">
  <div class="title">No pipelines yet</div>
  <div class="desc">Create your first pipeline to get started.</div>
</div>"""

    # Create section
    create_section = """\
<div class="card">
  <h3>Create Pipeline</h3>
  <div style="display:flex;gap:12px;margin-top:12px;flex-wrap:wrap;">
    <div class="form-group" style="flex:1;min-width:200px;">
      <label for="newName">Pipeline name</label>
      <input id="newName" type="text" placeholder="my-pipeline"
             onkeydown="if(event.key==='Enter')createPipeline('template')">
    </div>
    <div style="display:flex;gap:8px;align-items:flex-end;">
      <button class="btn btn-primary" onclick="createPipeline('template')">From Template</button>
      <button class="btn" onclick="createPipeline('blank')">Blank</button>
    </div>
  </div>
</div>"""

    # Editor section (Visual + YAML tabs)
    editor_section = ""
    if edit_name and edit_content is not None:
        editor_section = f"""\
<div class="card" style="border-color:var(--accent);">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
    <h3>Editing: {esc(edit_name)}</h3>
    <div style="display:flex;gap:8px;">
      <button class="btn btn-sm" onclick="validateYaml()">Validate</button>
      <button class="btn btn-sm btn-primary" onclick="saveFromCurrentTab()">Save</button>
      <a class="btn btn-sm" href="/pipelines">Close</a>
    </div>
  </div>
  <div id="validationResult" style="margin-bottom:8px;"></div>

  <!-- Editor Tabs -->
  <div class="tabs" style="margin-bottom:0;">
    <label class="tab" style="padding:8px 20px;cursor:pointer;border-bottom:2px solid var(--accent);color:var(--accent);"
           id="tabVisual" onclick="switchEditorTab('visual')">Visual Editor</label>
    <label class="tab" style="padding:8px 20px;cursor:pointer;"
           id="tabYaml" onclick="switchEditorTab('yaml')">YAML Editor</label>
  </div>

  <!-- Visual Editor Panel -->
  <div id="panelVisual" style="margin-top:16px;">
    <div id="agentCards">
      <div style="text-align:center;color:var(--text-dim);padding:24px;">Loading agents...</div>
    </div>
    <button class="btn btn-primary" onclick="showAgentForm(null)" style="margin-top:12px;">+ Add Agent</button>
  </div>

  <!-- YAML Editor Panel -->
  <div id="panelYaml" style="display:none;margin-top:16px;">
    <textarea id="yamlEditor" class="yaml-editor" spellcheck="false">{esc(edit_content)}</textarea>
  </div>
</div>

<!-- Agent Edit Modal -->
<div class="modal-overlay" id="agentModal">
  <div class="modal modal-lg">
    <h3 id="agentModalTitle">Add Agent</h3>
    <form id="agentForm" onsubmit="return false;">
      <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;">
        <div class="form-group">
          <label>Agent ID *</label>
          <input id="af_id" type="text" placeholder="my-agent" required>
        </div>
        <div class="form-group">
          <label>Name</label>
          <input id="af_name" type="text" placeholder="My Agent">
        </div>
        <div class="form-group">
          <label>Runtime</label>
          <select id="af_runtime">
            <option value="claude">Claude</option>
            <option value="gemini">Gemini</option>
            <option value="codex">Codex</option>
          </select>
        </div>
      </div>
      <div class="form-group">
        <label>System Prompt</label>
        <textarea id="af_prompt" rows="4" placeholder="You are a helpful agent. Task: {{{{ input }}}}"></textarea>
      </div>

      <!-- Handoffs -->
      <div style="margin-top:12px;">
        <div style="display:flex;justify-content:space-between;align-items:center;">
          <label style="font-weight:600;font-size:13px;color:var(--text-dim);">Handoffs</label>
          <button type="button" class="btn btn-sm" onclick="addHandoffRow()">+ Add</button>
        </div>
        <div id="handoffRows" style="margin-top:8px;"></div>
      </div>

      <!-- Gate -->
      <div style="margin-top:16px;">
        <label style="font-size:13px;color:var(--text-dim);cursor:pointer;">
          <input type="checkbox" id="af_gateEnabled" onchange="document.getElementById('gateFields').style.display=this.checked?'block':'none'"> Enable Gate
        </label>
        <div id="gateFields" style="display:none;margin-top:8px;">
          <div style="display:grid;grid-template-columns:1fr 2fr;gap:12px;">
            <div class="form-group">
              <label>Type</label>
              <select id="af_gateType"><option value="llm">LLM</option><option value="human">Human</option></select>
            </div>
            <div class="form-group">
              <label>Prompt</label>
              <input id="af_gatePrompt" type="text" placeholder="Is this output production-ready?">
            </div>
          </div>
        </div>
      </div>

      <div style="display:flex;gap:8px;margin-top:20px;">
        <button type="button" class="btn btn-primary" onclick="saveAgent()">Save Agent</button>
        <button type="button" class="btn" onclick="closeAgentModal()">Cancel</button>
        <button type="button" class="btn btn-red" id="deleteAgentBtn" style="margin-left:auto;display:none;"
                onclick="deleteAgent()">Delete</button>
      </div>
    </form>
  </div>
</div>"""

    js = f"""\
<script>
const EDIT_PIPELINE = {f"'{esc(edit_name)}'" if edit_name else "null"};
let currentTab = 'visual';
let editingAgentId = null;

/* ── Tab switching ─────────────────────────── */
function switchEditorTab(tab) {{
  currentTab = tab;
  document.getElementById('panelVisual').style.display = tab === 'visual' ? 'block' : 'none';
  document.getElementById('panelYaml').style.display = tab === 'yaml' ? 'block' : 'none';
  document.getElementById('tabVisual').style.borderBottomColor = tab === 'visual' ? 'var(--accent)' : 'transparent';
  document.getElementById('tabVisual').style.color = tab === 'visual' ? 'var(--accent)' : 'var(--text-dim)';
  document.getElementById('tabYaml').style.borderBottomColor = tab === 'yaml' ? 'var(--accent)' : 'transparent';
  document.getElementById('tabYaml').style.color = tab === 'yaml' ? 'var(--accent)' : 'var(--text-dim)';
  if (tab === 'visual') loadAgentCards();
  if (tab === 'yaml') loadYamlContent();
}}

/* ── Save from current tab ─────────────────── */
async function saveFromCurrentTab() {{
  if (currentTab === 'yaml') {{
    await savePipelineYaml();
  }}
  showToast('Pipeline saved');
}}

/* ── Load agent cards ──────────────────────── */
async function loadAgentCards() {{
  if (!EDIT_PIPELINE) return;
  try {{
    const agents = await apiFetch('/api/pipelines/' + encodeURIComponent(EDIT_PIPELINE) + '/agents');
    renderAgentCards(agents);
  }} catch(e) {{
    document.getElementById('agentCards').innerHTML = '<div style="color:var(--red);padding:12px;">Failed to load agents</div>';
  }}
}}

function renderAgentCards(agents) {{
  const container = document.getElementById('agentCards');
  if (!agents.length) {{
    container.innerHTML = '<div class="empty-state" style="padding:24px;"><div class="title">No agents</div><div class="desc">Add your first agent to get started.</div></div>';
    return;
  }}
  container.innerHTML = agents.map((a, i) => {{
    const runtimeColors = {{claude:'var(--accent)', gemini:'var(--green)', codex:'var(--purple)'}};
    const rc = runtimeColors[a.runtime] || 'var(--text-dim)';
    const handoffs = (a.handoffs || []).map(h =>
      '<span style="display:inline-block;background:var(--surface);border:1px solid var(--border);border-radius:4px;padding:1px 8px;font-size:11px;margin:2px;">' +
      '→ ' + (h.to||'') + (h.condition && h.condition !== 'always' ? ' [' + h.condition + ']' : '') + '</span>'
    ).join('');
    const gateHtml = a.gate ? '<span class="badge" style="background:#1a1040;color:var(--purple);border:1px solid #3b2d6b;font-size:10px;margin-left:6px;">' + a.gate.type + ' gate</span>' : '';
    const promptPreview = (a.system_prompt || '').substring(0, 100).replace(/</g, '&lt;');

    return `<div class="card" style="padding:14px;margin-bottom:8px;border-left:3px solid ${{rc}};">
      <div style="display:flex;justify-content:space-between;align-items:flex-start;">
        <div>
          <div style="font-weight:700;color:var(--accent);">${{a.name || a.id}}
            <span style="font-size:11px;color:var(--text-dim);font-weight:400;margin-left:6px;">${{a.id}}</span>
            <span style="font-size:11px;color:${{rc}};margin-left:6px;">${{a.runtime}}</span>
            ${{gateHtml}}
          </div>
          ${{promptPreview ? '<div style="font-size:12px;color:var(--text-dim);margin-top:4px;">' + promptPreview + (a.system_prompt && a.system_prompt.length > 100 ? '...' : '') + '</div>' : ''}}
          ${{handoffs ? '<div style="margin-top:6px;">' + handoffs + '</div>' : ''}}
        </div>
        <div style="display:flex;gap:4px;flex-shrink:0;">
          <button class="btn btn-sm" onclick="editAgent('${{a.id}}')">Edit</button>
          <button class="btn btn-sm" style="color:var(--red);" onclick="confirmDeleteAgent('${{a.id}}')">Delete</button>
        </div>
      </div>
    </div>`;
  }}).join('');
}}

/* ── Load YAML content ─────────────────────── */
async function loadYamlContent() {{
  if (!EDIT_PIPELINE) return;
  try {{
    const data = await apiFetch('/api/pipelines/' + encodeURIComponent(EDIT_PIPELINE));
    document.getElementById('yamlEditor').value = data.content;
  }} catch(e) {{}}
}}

/* ── Agent form ────────────────────────────── */
function showAgentForm(agentId) {{
  editingAgentId = agentId;
  document.getElementById('agentModalTitle').textContent = agentId ? 'Edit Agent' : 'Add Agent';
  document.getElementById('deleteAgentBtn').style.display = agentId ? 'inline-block' : 'none';
  // Clear form
  document.getElementById('af_id').value = '';
  document.getElementById('af_name').value = '';
  document.getElementById('af_runtime').value = 'claude';
  document.getElementById('af_prompt').value = '';
  document.getElementById('af_gateEnabled').checked = false;
  document.getElementById('gateFields').style.display = 'none';
  document.getElementById('af_gateType').value = 'llm';
  document.getElementById('af_gatePrompt').value = '';
  document.getElementById('handoffRows').innerHTML = '';
  document.getElementById('af_id').disabled = false;

  if (agentId) {{
    // Populate from API
    apiFetch('/api/pipelines/' + encodeURIComponent(EDIT_PIPELINE) + '/agents').then(agents => {{
      const a = agents.find(x => x.id === agentId);
      if (!a) return;
      document.getElementById('af_id').value = a.id;
      document.getElementById('af_id').disabled = true;
      document.getElementById('af_name').value = a.name || '';
      document.getElementById('af_runtime').value = a.runtime || 'claude';
      document.getElementById('af_prompt').value = a.system_prompt || '';
      if (a.gate) {{
        document.getElementById('af_gateEnabled').checked = true;
        document.getElementById('gateFields').style.display = 'block';
        document.getElementById('af_gateType').value = a.gate.type || 'llm';
        document.getElementById('af_gatePrompt').value = a.gate.prompt || '';
      }}
      (a.handoffs || []).forEach(h => addHandoffRow(h.to, h.condition || 'always'));
    }});
  }}

  document.getElementById('agentModal').classList.add('show');
}}

function editAgent(agentId) {{ showAgentForm(agentId); }}
function closeAgentModal() {{ document.getElementById('agentModal').classList.remove('show'); }}

document.getElementById('agentModal')?.addEventListener('click', function(e) {{
  if (e.target === this) closeAgentModal();
}});

/* ── Handoff rows ──────────────────────────── */
function addHandoffRow(to, condition) {{
  const container = document.getElementById('handoffRows');
  const row = document.createElement('div');
  row.style.cssText = 'display:flex;gap:8px;margin-bottom:6px;align-items:center;';
  row.innerHTML = `
    <input type="text" class="ho-to" value="${{to||''}}" placeholder="Target agent ID" style="flex:1;padding:6px 8px;font-size:13px;border:1px solid var(--border);border-radius:4px;background:var(--surface2);color:var(--text);">
    <select class="ho-cond" style="padding:6px 8px;font-size:13px;border:1px solid var(--border);border-radius:4px;background:var(--surface2);color:var(--text);">
      <option value="always" ${{(condition||'always')==='always'?'selected':''}}>always</option>
      <option value="on_approve" ${{condition==='on_approve'?'selected':''}}>on_approve</option>
      <option value="on_reject" ${{condition==='on_reject'?'selected':''}}>on_reject</option>
      <option value="auto" ${{condition==='auto'?'selected':''}}>auto</option>
    </select>
    <button type="button" class="btn btn-sm" onclick="this.parentElement.remove()" style="color:var(--red);">✕</button>
  `;
  container.appendChild(row);
}}

/* ── Save agent ────────────────────────────── */
async function saveAgent() {{
  const id = document.getElementById('af_id').value.trim();
  if (!id) {{ showToast('Agent ID is required', 'error'); return; }}

  const body = {{
    id: id,
    name: document.getElementById('af_name').value.trim(),
    runtime: document.getElementById('af_runtime').value,
    system_prompt: document.getElementById('af_prompt').value,
    handoffs: [],
  }};

  // Collect handoffs
  document.querySelectorAll('#handoffRows > div').forEach(row => {{
    const to = row.querySelector('.ho-to').value.trim();
    const cond = row.querySelector('.ho-cond').value;
    if (to) body.handoffs.push({{to, condition: cond}});
  }});

  // Gate
  if (document.getElementById('af_gateEnabled').checked) {{
    body.gate = {{
      type: document.getElementById('af_gateType').value,
      prompt: document.getElementById('af_gatePrompt').value,
    }};
  }}

  const url = '/api/pipelines/' + encodeURIComponent(EDIT_PIPELINE) + '/agents' + (editingAgentId ? '/' + encodeURIComponent(editingAgentId) : '');
  const method = editingAgentId ? 'PUT' : 'POST';

  try {{
    await apiFetch(url, {{method, body:JSON.stringify(body)}});
    showToast(editingAgentId ? 'Agent updated' : 'Agent added');
    closeAgentModal();
    loadAgentCards();
  }} catch(e) {{}}
}}

/* ── Delete agent ──────────────────────────── */
async function deleteAgent() {{
  if (!editingAgentId || !confirm('Delete agent "' + editingAgentId + '"?')) return;
  try {{
    await apiFetch('/api/pipelines/' + encodeURIComponent(EDIT_PIPELINE) + '/agents/' + encodeURIComponent(editingAgentId), {{method:'DELETE'}});
    showToast('Agent deleted');
    closeAgentModal();
    loadAgentCards();
  }} catch(e) {{}}
}}
async function confirmDeleteAgent(agentId) {{
  if (!confirm('Delete agent "' + agentId + '"?')) return;
  try {{
    await apiFetch('/api/pipelines/' + encodeURIComponent(EDIT_PIPELINE) + '/agents/' + encodeURIComponent(agentId), {{method:'DELETE'}});
    showToast('Agent deleted');
    loadAgentCards();
  }} catch(e) {{}}
}}

/* ── Pipeline CRUD ─────────────────────────── */
async function createPipeline(mode) {{
  const name = document.getElementById('newName').value.trim();
  if (!name) {{ showToast('Enter a pipeline name', 'error'); return; }}
  const content = mode === 'template'
    ? 'agents:\\n  - id: planner\\n    name: Planner\\n    runtime: claude\\n    system_prompt: "Plan: {{{{ input }}}}"\\n    handoffs:\\n      - to: developer\\n\\n  - id: developer\\n    name: Developer\\n    runtime: claude\\n    system_prompt: "Build: {{{{ input }}}}"\\n'
    : 'agents:\\n  - id: agent_1\\n    name: Agent 1\\n    runtime: claude\\n    system_prompt: "{{{{ input }}}}"\\n';
  try {{
    await apiFetch('/api/pipelines', {{method:'POST', body:JSON.stringify({{name, content}})}});
    showToast('Pipeline created');
    location.href = '/pipelines?edit=' + encodeURIComponent(name);
  }} catch(e) {{}}
}}
async function savePipelineYaml() {{
  const content = document.getElementById('yamlEditor')?.value;
  if (!EDIT_PIPELINE || !content) return;
  try {{
    await apiFetch('/api/pipelines/' + encodeURIComponent(EDIT_PIPELINE), {{method:'PUT', body:JSON.stringify({{content}})}});
  }} catch(e) {{}}
}}
async function validateYaml() {{
  let content;
  if (currentTab === 'yaml') {{
    content = document.getElementById('yamlEditor').value;
  }} else {{
    const data = await apiFetch('/api/pipelines/' + encodeURIComponent(EDIT_PIPELINE));
    content = data.content;
  }}
  try {{
    const r = await apiFetch('/api/validate', {{method:'POST', body:JSON.stringify({{yaml_content:content}})}});
    const el = document.getElementById('validationResult');
    if (r.valid) {{
      el.innerHTML = '<span style="color:var(--green);">\\u2713 Valid (' + (r.agent_count||0) + ' agents)</span>';
    }} else {{
      el.innerHTML = '<span style="color:var(--red);">\\u2717 ' + (r.errors||[]).map(e=>e.message||e).join(', ') + '</span>';
    }}
  }} catch(e) {{}}
}}
async function deletePipeline(name) {{
  if (!confirm('Delete pipeline "' + name + '"?')) return;
  try {{
    await apiFetch('/api/pipelines/' + encodeURIComponent(name), {{method:'DELETE'}});
    showToast('Pipeline deleted');
    location.reload();
  }} catch(e) {{}}
}}
async function duplicatePipeline(name) {{
  const newName = prompt('New pipeline name:');
  if (!newName) return;
  try {{
    await apiFetch('/api/pipelines/' + encodeURIComponent(name) + '/duplicate', {{method:'POST', body:JSON.stringify({{new_name:newName}})}});
    showToast('Pipeline duplicated');
    location.reload();
  }} catch(e) {{}}
}}
async function setDefault(name) {{
  try {{
    await apiFetch('/api/pipelines/default', {{method:'POST', body:JSON.stringify({{name}})}});
    showToast('Default set to ' + name);
    location.reload();
  }} catch(e) {{}}
}}

/* Init: load agent cards if editing */
if (EDIT_PIPELINE) setTimeout(loadAgentCards, 50);
</script>"""

    body = (
        f"<h1>Pipelines</h1>\n"
        f"{create_section}\n"
        f"{editor_section}\n"
        f"{table}\n"
        f"{js}"
    )

    return layout("Pipelines", body, active="pipelines")
