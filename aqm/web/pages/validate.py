"""Validate page — agents.yaml validation with error display."""

from __future__ import annotations

from aqm.web.templates import layout


def render_validate() -> str:
    body = """\
<h1>Validate Pipeline</h1>

<div class="card">
  <p style="font-size:14px;color:var(--text-dim);margin-bottom:12px;">
    Validate your .aqm/agents.yaml against the JSON Schema specification.
  </p>
  <button class="btn btn-primary" onclick="runValidation()">Validate Now</button>
</div>

<div id="validationResult" style="margin-top:16px;"></div>

<script>
async function runValidation() {
  document.getElementById('validationResult').innerHTML = '<div style="text-align:center;color:var(--text-dim);padding:24px;">Validating...</div>';
  try {
    const data = await apiFetch('/api/validate', {method:'POST'});
    let html = '';

    if (data.valid) {
      html += '<div class="card" style="border-color:var(--green);">';
      html += '<h3 style="color:var(--green);">&#10003; Valid</h3>';
      html += '<div style="margin-top:8px;">';
      if (data.summary.agent_count !== undefined) html += '<div>Agents: <strong>' + data.summary.agent_count + '</strong></div>';
      if (data.summary.features && data.summary.features.length) html += '<div>Features: ' + data.summary.features.join(', ') + '</div>';
      html += '</div></div>';
    } else {
      html += '<div class="card" style="border-color:var(--red);">';
      html += '<h3 style="color:var(--red);">&#10007; ' + data.errors.length + ' error(s) found</h3>';
      html += '<div style="margin-top:12px;">';
      data.errors.forEach((err, i) => {
        html += '<div style="margin-bottom:12px;padding:12px;background:var(--surface2);border-radius:6px;">';
        html += '<div><strong style="color:var(--red);">' + (i+1) + '.</strong> <code>' + err.path + '</code></div>';
        html += '<div style="margin-top:4px;">' + err.message + '</div>';
        if (err.fix) html += '<div style="margin-top:4px;color:var(--text-dim);font-size:13px;">Fix: ' + err.fix + '</div>';
        html += '</div>';
      });
      html += '</div></div>';
    }

    if (data.yaml_content) {
      html += '<div class="card" style="margin-top:16px;"><details>';
      html += '<summary style="font-size:15px;font-weight:600;color:var(--text);">Raw YAML</summary>';
      html += '<pre style="margin-top:12px;">' + data.yaml_content.replace(/</g,'&lt;').replace(/>/g,'&gt;') + '</pre>';
      html += '</details></div>';
    }

    document.getElementById('validationResult').innerHTML = html;
  } catch(e) {
    document.getElementById('validationResult').innerHTML = '<div class="card" style="border-color:var(--red);"><h3 style="color:var(--red);">Validation failed</h3><p>' + e.message + '</p></div>';
  }
}

// Auto-validate on load
runValidation();
</script>"""

    return layout("Validate", body, active="validate")
