"""Shared HTML templates, CSS, and helper functions for the web dashboard."""

from __future__ import annotations

import html
from datetime import datetime
from typing import Optional


# ---------------------------------------------------------------------------
# CSS Design System
# ---------------------------------------------------------------------------

CSS = """\
:root {
  --bg: #0d1117; --surface: #161b22; --surface2: #21262d; --border: #30363d;
  --text: #e6edf3; --text-dim: #8b949e;
  --accent: #58a6ff; --green: #3fb950; --red: #f85149; --orange: #d29922;
  --purple: #bc8cff; --cyan: #39d2c0; --radius: 8px;
}
* { margin:0; padding:0; box-sizing:border-box; }
body { background:var(--bg); color:var(--text); font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif; line-height:1.6; }
a { color:var(--accent); text-decoration:none; } a:hover { text-decoration:underline; }
.container { max-width:1200px; margin:0 auto; padding:24px 16px; }

/* Nav */
nav { background:var(--surface); border-bottom:1px solid var(--border); padding:12px 0; position:sticky; top:0; z-index:100; }
nav .inner { max-width:1200px; margin:0 auto; padding:0 16px; display:flex; align-items:center; gap:24px; }
nav .logo { font-size:18px; font-weight:700; color:var(--text); }
nav .logo span { color:var(--accent); }
nav a.nav-link { color:var(--text-dim); font-size:14px; padding:4px 10px; border-radius:6px; transition:.15s; }
nav a.nav-link:hover { color:var(--text); background:var(--surface2); text-decoration:none; }
nav a.nav-link.active { color:var(--accent); background:rgba(88,166,255,.1); }

/* Typography */
h1 { font-size:24px; margin-bottom:16px; } h2 { font-size:20px; margin-bottom:12px; } h3 { font-size:16px; margin-bottom:8px; }

/* Stats */
.stats { display:grid; grid-template-columns:repeat(auto-fit,minmax(140px,1fr)); gap:12px; margin-bottom:24px; }
.stat-card { background:var(--surface); border:1px solid var(--border); border-radius:var(--radius); padding:16px; text-align:center; transition:.2s; }
.stat-card:hover { border-color:var(--accent); transform:translateY(-1px); }
.stat-card .value { font-size:32px; font-weight:700; color:var(--accent); }
.stat-card .label { font-size:13px; color:var(--text-dim); margin-top:4px; }
.stat-card.green .value { color:var(--green); }
.stat-card.red .value { color:var(--red); }
.stat-card.orange .value { color:var(--orange); }
.stat-card.blue .value { color:var(--accent); }

/* Tables */
table { width:100%; border-collapse:collapse; background:var(--surface); border:1px solid var(--border); border-radius:var(--radius); overflow:hidden; }
thead { background:var(--surface2); }
th,td { padding:10px 14px; text-align:left; font-size:14px; border-bottom:1px solid var(--border); }
th { font-weight:600; color:var(--text-dim); font-size:12px; text-transform:uppercase; letter-spacing:.5px; }
tr:last-child td { border-bottom:none; }
tr:hover td { background:rgba(88,166,255,.04); }

/* Badges */
.badge { display:inline-block; padding:2px 10px; border-radius:12px; font-size:12px; font-weight:600; }
.badge-pending { background:#30363d; color:#8b949e; }
.badge-in_progress { background:#0d419d; color:#58a6ff; }
.badge-awaiting_gate { background:#462c08; color:#d29922; }
.badge-approved,.badge-completed { background:#0f2d16; color:#3fb950; }
.badge-rejected,.badge-failed { background:#3d1214; color:#f85149; }
.badge-started { background:#0d419d; color:#58a6ff; }

/* Buttons */
.btn { display:inline-block; padding:8px 16px; border-radius:6px; font-size:14px; font-weight:600; border:1px solid var(--border); cursor:pointer; transition:.15s; background:var(--surface2); color:var(--text); }
.btn:hover { border-color:var(--accent); }
.btn-primary { background:var(--accent); color:#fff; border-color:var(--accent); } .btn-primary:hover { opacity:.9; }
.btn-green { background:var(--green); color:#fff; border-color:var(--green); } .btn-green:hover { opacity:.9; }
.btn-red { background:var(--red); color:#fff; border-color:var(--red); } .btn-red:hover { opacity:.9; }
.btn-sm { padding:4px 10px; font-size:12px; }

/* Cards */
.card { background:var(--surface); border:1px solid var(--border); border-radius:var(--radius); padding:20px; margin-bottom:16px; }

/* Timeline */
.timeline { position:relative; padding-left:28px; }
.timeline::before { content:''; position:absolute; left:10px; top:0; bottom:0; width:2px; background:var(--border); }
.timeline-item { position:relative; margin-bottom:20px; }
.timeline-item::before { content:''; position:absolute; left:-22px; top:6px; width:12px; height:12px; border-radius:50%; background:var(--accent); border:2px solid var(--bg); }
.timeline-item.approved::before { background:var(--green); }
.timeline-item.rejected::before { background:var(--red); }
.timeline-item.awaiting::before { background:var(--orange); animation:pulse 1.5s infinite; }
.timeline-item.running::before { background:var(--accent); animation:pulse 1.5s infinite; }
.timeline-item.failed::before { background:var(--red); }
@keyframes pulse { 0%,100%{opacity:1;transform:scale(1)} 50%{opacity:.6;transform:scale(1.2)} }

/* Details & Pre */
details { margin-top:8px; } details summary { cursor:pointer; color:var(--text-dim); font-size:13px; } details summary:hover { color:var(--text); }
pre { background:var(--surface2); border:1px solid var(--border); border-radius:6px; padding:12px; overflow-x:auto; font-size:13px; color:var(--text); white-space:pre-wrap; word-break:break-word; max-height:400px; overflow-y:auto; }

/* Meta */
.meta-row { display:flex; gap:24px; flex-wrap:wrap; margin-bottom:8px; }
.meta-row .meta-item { font-size:14px; } .meta-row .meta-label { color:var(--text-dim); margin-right:6px; }

/* Forms */
.form-group { margin-bottom:12px; }
.form-group label { display:block; font-size:13px; color:var(--text-dim); margin-bottom:4px; }
.form-group input,.form-group textarea,.form-group select { width:100%; background:var(--surface2); border:1px solid var(--border); border-radius:6px; padding:8px 12px; color:var(--text); font-size:14px; font-family:inherit; }
.form-group input:focus,.form-group textarea:focus,.form-group select:focus { border-color:var(--accent); outline:none; }
.form-group textarea { min-height:80px; resize:vertical; }
.form-group select { appearance:none; cursor:pointer; }

/* Agent diagram */
.graph-container { width:100%; overflow:auto; background:var(--surface); border:1px solid var(--border); border-radius:var(--radius); padding:20px; position:relative; }
.graph-controls { display:flex; gap:8px; margin-bottom:12px; }
.agent-node-html { background:var(--surface2); border:2px solid var(--border); border-radius:var(--radius); padding:12px 16px; min-width:180px; cursor:default; transition:.2s; }
.agent-node-html:hover { border-color:var(--accent); }
.agent-node-html .agent-title { font-weight:700; font-size:14px; color:var(--accent); margin-bottom:4px; }
.agent-node-html .agent-id { font-size:11px; color:var(--text-dim); }
.agent-node-html .mcp-item { display:inline-block; background:var(--surface); border:1px solid var(--border); border-radius:4px; padding:1px 6px; font-size:10px; color:var(--cyan); margin:2px 2px 0 0; }
.agent-node-html .gate-badge { display:inline-block; margin-top:6px; padding:1px 6px; border-radius:4px; font-size:10px; font-weight:600; }
.gate-llm { background:#1a1040; color:var(--purple); border:1px solid #3b2d6b; }
.gate-human { background:#462c08; color:var(--orange); border:1px solid #6b4f1d; }

/* Agent detail accordion */
.agent-accordion { margin-top:24px; }
.agent-accordion-item { background:var(--surface); border:1px solid var(--border); border-radius:var(--radius); margin-bottom:8px; overflow:hidden; }
.agent-accordion-item summary { padding:12px 16px; cursor:pointer; font-weight:600; font-size:14px; display:flex; align-items:center; gap:8px; }
.agent-accordion-item summary:hover { background:var(--surface2); }
.agent-accordion-content { padding:0 16px 16px; font-size:13px; }

/* Tabs */
.tabs { display:flex; gap:0; border-bottom:1px solid var(--border); margin-bottom:16px; }
.tab { padding:8px 16px; font-size:14px; color:var(--text-dim); cursor:pointer; border-bottom:2px solid transparent; transition:.15s; }
.tab:hover { color:var(--text); } .tab.active { color:var(--accent); border-bottom-color:var(--accent); }

/* Toast / notification */
.toast { position:fixed; top:80px; right:24px; padding:12px 20px; border-radius:8px; font-size:14px; z-index:1000; animation:slideIn .3s ease; }
.toast-success { background:#0f2d16; color:var(--green); border:1px solid #1a4025; }
.toast-error { background:#3d1214; color:var(--red); border:1px solid #5a1d20; }
@keyframes slideIn { from{transform:translateX(100%);opacity:0} to{transform:translateX(0);opacity:1} }

/* Empty state */
.empty-state { text-align:center; padding:48px; color:var(--text-dim); }

/* Progress bar */
.progress-bar { width:100%; height:4px; background:var(--surface2); border-radius:2px; overflow:hidden; }
.progress-bar .fill { height:100%; background:var(--accent); border-radius:2px; transition:width .5s; }

/* Live indicator */
.live-dot { display:inline-block; width:8px; height:8px; border-radius:50%; background:var(--green); margin-right:6px; animation:pulse 1.5s infinite; }

/* Modal */
.modal-overlay { display:none; position:fixed; inset:0; background:rgba(0,0,0,.6); z-index:200; align-items:center; justify-content:center; }
.modal-overlay.show { display:flex; }
.modal { background:var(--surface); border:1px solid var(--border); border-radius:12px; padding:24px; max-width:600px; width:90%; max-height:80vh; overflow-y:auto; }
.modal h3 { margin-bottom:16px; }

@media (max-width:768px) {
  .stats { grid-template-columns:repeat(2,1fr); }
  .meta-row { flex-direction:column; gap:4px; }
  th,td { padding:8px 10px; font-size:13px; }
  nav .inner { gap:12px; flex-wrap:wrap; }
}
"""


# ---------------------------------------------------------------------------
# Shared JS Utilities
# ---------------------------------------------------------------------------

JS_UTILS = """\
function showToast(msg, type='success') {
  const t = document.createElement('div');
  t.className = 'toast toast-' + type;
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 3000);
}
async function apiFetch(url, opts={}) {
  try {
    const res = await fetch(url, {headers:{'Content-Type':'application/json'}, ...opts});
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Request failed');
    return data;
  } catch(e) { showToast(e.message, 'error'); throw e; }
}
"""


# ---------------------------------------------------------------------------
# Navigation
# ---------------------------------------------------------------------------

def nav_html(active: str = "") -> str:
    links = [
        ("/", "Tasks", "tasks"),
        ("/agents", "Agents", "agents"),
        ("/registry", "Registry", "registry"),
        ("/validate", "Validate", "validate"),
    ]
    items = []
    for href, label, key in links:
        cls = ' class="nav-link active"' if key == active else ' class="nav-link"'
        items.append(f'<a{cls} href="{href}">{label}</a>')
    return f"""\
<nav>
  <div class="inner">
    <div class="logo"><span>aqm</span> Dashboard</div>
    {"".join(items)}
  </div>
</nav>"""


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------

def layout(title: str, body: str, active: str = "", extra_head: str = "") -> str:
    return f"""\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{title} - aqm</title>
  <style>{CSS}</style>
  {extra_head}
</head>
<body>
{nav_html(active)}
<div class="container">
{body}
</div>
<script>{JS_UTILS}</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def esc(text: str) -> str:
    return html.escape(str(text)) if text else ""


def fmt_time(dt: Optional[datetime]) -> str:
    if dt is None:
        return "-"
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def badge(status: str) -> str:
    return f'<span class="badge badge-{esc(status)}">{esc(status)}</span>'
