"""Registry page — search, pull, publish pipelines."""

from __future__ import annotations

from aqm.web.templates import layout


def render_registry() -> str:
    body = """\
<h1>Pipeline Registry</h1>

<div class="card">
  <h3>Search Pipelines</h3>
  <div style="display:flex;gap:8px;margin-top:12px;align-items:flex-end;">
    <div class="form-group" style="flex:1;margin-bottom:0;">
      <label for="searchQuery">Keyword</label>
      <input id="searchQuery" type="text" placeholder="e.g. code review, content, data...">
    </div>
    <button class="btn btn-primary" onclick="searchRegistry()" style="height:38px;">Search</button>
  </div>
  <div style="margin-top:8px;">
    <label style="font-size:13px;color:var(--text-dim);cursor:pointer;">
      <input type="checkbox" id="offlineMode" style="margin-right:4px;"> Offline only (local + bundled)
    </label>
  </div>
</div>

<div id="searchResults" style="margin-top:16px;"></div>

<div class="card" style="margin-top:24px;">
  <h3>Publish Current Pipeline</h3>
  <p style="font-size:13px;color:var(--text-dim);margin-bottom:12px;">
    Publish your .aqm/agents.yaml to the registry.
  </p>
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;">
    <div class="form-group">
      <label for="pubName">Pipeline name</label>
      <input id="pubName" type="text" placeholder="my-pipeline">
    </div>
    <div class="form-group">
      <label for="pubDesc">Description</label>
      <input id="pubDesc" type="text" placeholder="What this pipeline does...">
    </div>
  </div>
  <button class="btn btn-primary" onclick="publishPipeline(false)">Publish to GitHub</button>
  <button class="btn" onclick="publishPipeline(true)" style="margin-left:8px;">Save Locally</button>
</div>

<script>
async function searchRegistry() {
  const query = document.getElementById('searchQuery').value.trim();
  const offline = document.getElementById('offlineMode').checked;
  const params = new URLSearchParams();
  if (query) params.set('query', query);
  if (offline) params.set('offline', 'true');

  document.getElementById('searchResults').innerHTML = '<div style="text-align:center;color:var(--text-dim);padding:24px;">Searching...</div>';

  try {
    const data = await apiFetch('/api/registry/search?' + params.toString());
    if (!data.length) {
      document.getElementById('searchResults').innerHTML = '<div class="empty-state">No pipelines found.</div>';
      return;
    }
    let html = '<table><thead><tr><th>Name</th><th>Source</th><th>Description</th><th>Agents</th><th>Actions</th></tr></thead><tbody>';
    data.forEach(p => {
      const sourceColors = {github:'var(--purple)', bundled:'var(--accent)', local:'var(--green)'};
      const sourceColor = sourceColors[p.source] || 'var(--text-dim)';
      html += `<tr>
        <td><strong>${p.name}</strong></td>
        <td><span style="color:${sourceColor}">${p.source}</span></td>
        <td>${p.description || '<span style="color:var(--text-dim)">-</span>'}</td>
        <td>${p.agents_count || '-'}</td>
        <td><button class="btn btn-sm btn-primary" onclick="pullPipeline('${p.name}')">Pull</button></td>
      </tr>`;
    });
    html += '</tbody></table>';
    document.getElementById('searchResults').innerHTML = html;
  } catch(e) {
    document.getElementById('searchResults').innerHTML = '<div class="empty-state">Search failed.</div>';
  }
}

async function pullPipeline(name) {
  if (!confirm('Pull "' + name + '" and overwrite .aqm/agents.yaml?')) return;
  const offline = document.getElementById('offlineMode').checked;
  try {
    const data = await apiFetch('/api/registry/pull', {
      method: 'POST',
      body: JSON.stringify({pipeline_name: name, offline: offline})
    });
    showToast('Pulled ' + name + ' (' + data.agents_count + ' agents)');
  } catch(e) {}
}

async function publishPipeline(localOnly) {
  const name = document.getElementById('pubName').value.trim();
  const desc = document.getElementById('pubDesc').value.trim();
  if (!name) { showToast('Pipeline name is required', 'error'); return; }
  try {
    const data = await apiFetch('/api/registry/publish', {
      method: 'POST',
      body: JSON.stringify({name: name, description: desc, local_only: localOnly})
    });
    if (data.pr_url) {
      showToast('PR created: ' + data.pr_url);
    } else {
      showToast('Published locally');
    }
  } catch(e) {}
}

// Auto-search on load (defer to ensure apiFetch is defined)
window.addEventListener('DOMContentLoaded', () => setTimeout(searchRegistry, 50));
</script>"""

    return layout("Registry", body, active="registry")
