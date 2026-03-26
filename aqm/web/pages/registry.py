"""Registry page — search, pull (with version), publish pipelines."""

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
      <input id="searchQuery" type="text" placeholder="e.g. code review, content, data..."
             onkeydown="if(event.key==='Enter')searchRegistry()">
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
    Publish your pipeline to the registry with semantic versioning.
  </p>
  <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;">
    <div class="form-group">
      <label for="pubName">Pipeline name</label>
      <input id="pubName" type="text" placeholder="my-pipeline">
    </div>
    <div class="form-group">
      <label for="pubDesc">Description</label>
      <input id="pubDesc" type="text" placeholder="What this pipeline does...">
    </div>
    <div class="form-group">
      <label for="pubVersion">Version (optional)</label>
      <input id="pubVersion" type="text" placeholder="auto-increment">
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

  document.getElementById('searchResults').innerHTML =
    '<div style="text-align:center;color:var(--text-dim);padding:24px;">Searching...</div>';

  try {
    const data = await apiFetch('/api/registry/search?' + params.toString());
    if (!data.length) {
      document.getElementById('searchResults').innerHTML =
        '<div class="empty-state"><div class="title">No pipelines found</div><div class="desc">Try a different keyword or check the registry.</div></div>';
      return;
    }
    let html = `<div class="table-wrap"><table>
      <thead><tr><th>Name</th><th>Source</th><th>Version</th><th>Description</th><th>Agents</th><th>Actions</th></tr></thead>
      <tbody>`;
    data.forEach(p => {
      const sourceColors = {github:'var(--purple)', bundled:'var(--accent)', local:'var(--green)'};
      const sourceColor = sourceColors[p.source] || 'var(--text-dim)';
      const latestVersion = p.latest || p.version || '-';
      const versions = p.versions || [];

      // Version selector + pull button
      let pullHtml;
      if (versions.length > 1) {
        const options = versions.map(v =>
          `<option value="${v}"${v === p.latest ? ' selected' : ''}>${v}</option>`
        ).join('');
        pullHtml = `
          <div style="display:flex;gap:4px;align-items:center;">
            <select id="ver-${p.name}" style="padding:4px 6px;font-size:12px;border:1px solid var(--border);
              border-radius:4px;background:var(--surface2);color:var(--text);width:80px;">
              ${options}
            </select>
            <button class="btn btn-sm btn-primary" onclick="pullPipeline('${p.name}',
              document.getElementById('ver-${p.name}').value)">Pull</button>
          </div>`;
      } else {
        pullHtml = `<button class="btn btn-sm btn-primary" onclick="pullPipeline('${p.name}','${latestVersion}')">Pull</button>`;
      }

      html += `<tr>
        <td><strong>${p.name}</strong></td>
        <td><span style="color:${sourceColor};font-size:12px;">${p.source}</span></td>
        <td><span style="font-family:monospace;font-size:12px;">${latestVersion}</span></td>
        <td style="max-width:250px;font-size:13px;">${p.description || '<span style="color:var(--text-dim)">-</span>'}</td>
        <td>${p.agents_count || '-'}</td>
        <td>${pullHtml}</td>
      </tr>`;
    });
    html += '</tbody></table></div>';
    document.getElementById('searchResults').innerHTML = html;
  } catch(e) {
    document.getElementById('searchResults').innerHTML =
      '<div class="empty-state"><div class="title">Search failed</div></div>';
  }
}

async function pullPipeline(name, version) {
  const label = version && version !== '-' ? name + '@' + version : name;
  if (!confirm('Pull "' + label + '" into your project?')) return;
  const offline = document.getElementById('offlineMode').checked;
  const body = {pipeline_name: name, offline: offline};
  if (version && version !== '-') body.version = version;
  try {
    const data = await apiFetch('/api/registry/pull', {method:'POST', body:JSON.stringify(body)});
    const vLabel = data.version ? ' v' + data.version : '';
    showToast('Pulled ' + name + vLabel + ' (' + data.agents_count + ' agents)');
  } catch(e) {}
}

async function publishPipeline(localOnly) {
  const name = document.getElementById('pubName').value.trim();
  const desc = document.getElementById('pubDesc').value.trim();
  const version = document.getElementById('pubVersion').value.trim();
  if (!name) { showToast('Pipeline name is required', 'error'); return; }
  const body = {name, description: desc, local_only: localOnly};
  if (version) body.version = version;
  try {
    const data = await apiFetch('/api/registry/publish', {method:'POST', body:JSON.stringify(body)});
    const vLabel = data.version ? ' v' + data.version : '';
    if (data.pr_url) {
      showToast('PR created for ' + name + vLabel);
    } else {
      showToast('Published ' + name + vLabel + ' locally');
    }
  } catch(e) {}
}

// Auto-search on load
window.addEventListener('DOMContentLoaded', () => setTimeout(searchRegistry, 50));
</script>"""

    return layout("Registry", body, active="registry")
