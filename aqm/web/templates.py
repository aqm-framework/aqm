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
  --sidebar-w: 240px;
}
[data-theme="light"] {
  --bg: #ffffff; --surface: #f6f8fa; --surface2: #eaeef2; --border: #d0d7de;
  --text: #1f2328; --text-dim: #656d76;
  --accent: #0969da; --green: #1a7f37; --red: #cf222e; --orange: #9a6700;
  --purple: #8250df; --cyan: #0e7c6b;
}
* { margin:0; padding:0; box-sizing:border-box; }
body { background:var(--bg); color:var(--text); font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif; line-height:1.6; }
a { color:var(--accent); text-decoration:none; } a:hover { text-decoration:underline; }
:focus-visible { outline:2px solid var(--accent); outline-offset:2px; }
.sr-only { position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);border:0; }

/* ── App Layout ─────────────────────────────── */
.app-layout { display:flex; min-height:100vh; }

/* ── Sidebar ────────────────────────────────── */
.sidebar { background:var(--surface); border-right:1px solid var(--border); position:sticky; top:0; width:var(--sidebar-w); min-width:var(--sidebar-w); height:100vh; display:flex; flex-direction:column; z-index:100; overflow-y:auto; }
.sidebar-logo { padding:20px 20px 16px; font-size:18px; font-weight:700; border-bottom:1px solid var(--border); }
.sidebar-logo span { color:var(--accent); }
.sidebar-nav { padding:12px 0; flex:1; }
.nav-section { padding:0 8px; margin-bottom:8px; }
.nav-section-title { font-size:11px; font-weight:600; color:var(--text-dim); text-transform:uppercase; letter-spacing:.5px; padding:8px 12px 4px; }
.nav-item { display:flex !important; align-items:center; gap:10px; padding:8px 12px; margin:2px 0; border-radius:6px; color:var(--text-dim); font-size:14px; transition:.15s; text-decoration:none !important; line-height:1.4; min-height:36px; }
.nav-item:hover { color:var(--text); background:var(--surface2); text-decoration:none; }
.nav-item.active { color:var(--accent); background:rgba(88,166,255,.12); font-weight:600; }
.nav-item svg { width:16px; height:16px; min-width:16px; min-height:16px; flex-shrink:0; }
.nav-item span { white-space:nowrap; }
.nav-item .kbd { margin-left:auto; font-size:10px; padding:1px 5px; border-radius:3px; background:var(--surface2); color:var(--text-dim); border:1px solid var(--border); font-family:monospace; }
.sidebar-footer { padding:12px 16px; border-top:1px solid var(--border); font-size:12px; color:var(--text-dim); }

/* ── Main Content ───────────────────────────── */
.main-content { flex:1; min-height:100vh; display:flex; flex-direction:column; min-width:0; }
.topbar { display:flex; align-items:center; justify-content:space-between; padding:10px 24px; border-bottom:1px solid var(--border); background:var(--surface); position:sticky; top:0; z-index:50; }
.topbar-left { display:flex; align-items:center; gap:8px; }
.topbar-right { display:flex; align-items:center; gap:6px; }
.topbar-right .btn { display:inline-flex; align-items:center; gap:4px; }
.topbar-right .btn svg { width:14px; height:14px; }
.breadcrumb { display:flex; align-items:center; gap:6px; font-size:14px; color:var(--text-dim); }
.breadcrumb a { color:var(--text-dim); } .breadcrumb a:hover { color:var(--text); text-decoration:none; }
.breadcrumb .sep { color:var(--border); }
.breadcrumb .current { color:var(--text); font-weight:600; }
.container { max-width:1200px; margin:0 auto; padding:24px; flex:1; }

/* ── Old top-nav (hidden, replaced by sidebar) ── */
body > nav { display:none; }

/* ── Stats ──────────────────────────────────── */
.stats { display:grid; grid-template-columns:repeat(auto-fit,minmax(140px,1fr)); gap:12px; margin-bottom:24px; }
.stat-card { background:var(--surface); border:1px solid var(--border); border-radius:var(--radius); padding:16px; text-align:center; transition:.2s; }
.stat-card:hover { border-color:var(--accent); transform:translateY(-1px); }
.stat-card .value { font-size:32px; font-weight:700; color:var(--accent); }
.stat-card .label { font-size:13px; color:var(--text-dim); margin-top:4px; }
.stat-card.green .value { color:var(--green); }
.stat-card.red .value { color:var(--red); }
.stat-card.orange .value { color:var(--orange); }
.stat-card.blue .value { color:var(--accent); }

/* ── Tables ─────────────────────────────────── */
.table-wrap { overflow-x:auto; }
table { width:100%; border-collapse:collapse; background:var(--surface); border:1px solid var(--border); border-radius:var(--radius); overflow:hidden; }
thead { background:var(--surface2); }
th,td { padding:10px 14px; text-align:left; font-size:14px; border-bottom:1px solid var(--border); }
th { font-weight:600; color:var(--text-dim); font-size:12px; text-transform:uppercase; letter-spacing:.5px; cursor:default; }
th[data-sort] { cursor:pointer; user-select:none; }
th[data-sort]:hover { color:var(--text); }
th .sort-icon { font-size:10px; margin-left:4px; }
tr:last-child td { border-bottom:none; }
tr:hover td { background:rgba(88,166,255,.04); }

/* ── Badges ─────────────────────────────────── */
.badge { display:inline-block; padding:2px 10px; border-radius:12px; font-size:12px; font-weight:600; }
.badge-pending { background:#30363d; color:#8b949e; }
.badge-in_progress { background:#0d419d; color:#58a6ff; }
.badge-awaiting_gate { background:#462c08; color:#d29922; }
.badge-approved,.badge-completed { background:#0f2d16; color:#3fb950; }
.badge-rejected,.badge-failed { background:#3d1214; color:#f85149; }
.badge-cancelled { background:#30363d; color:#d29922; }
.badge-awaiting_human_input { background:#0a2e2a; color:#39d2c0; }
.badge-stalled { background:#462c08; color:#d29922; }
.badge-started { background:#0d419d; color:#58a6ff; }

/* ── Buttons ────────────────────────────────── */
.btn { display:inline-block; padding:8px 16px; border-radius:6px; font-size:14px; font-weight:600; border:1px solid var(--border); cursor:pointer; transition:.15s; background:var(--surface2); color:var(--text); }
.btn:hover { border-color:var(--accent); }
.btn-primary { background:var(--accent); color:#fff; border-color:var(--accent); } .btn-primary:hover { opacity:.9; }
.btn-green { background:var(--green); color:#fff; border-color:var(--green); } .btn-green:hover { opacity:.9; }
.btn-red { background:var(--red); color:#fff; border-color:var(--red); } .btn-red:hover { opacity:.9; }
.btn-sm { padding:4px 10px; font-size:12px; }
.btn-icon { padding:6px 8px; line-height:1; }
.btn-icon svg, .btn svg { width:16px; height:16px; vertical-align:middle; }
.btn-sm svg { width:14px; height:14px; vertical-align:middle; }

/* ── Cards ──────────────────────────────────── */
.card { background:var(--surface); border:1px solid var(--border); border-radius:var(--radius); padding:20px; margin-bottom:16px; }

/* ── Timeline ───────────────────────────────── */
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

/* ── Details & Pre ──────────────────────────── */
details { margin-top:8px; } details summary { cursor:pointer; color:var(--text-dim); font-size:13px; } details summary:hover { color:var(--text); }
pre { background:var(--surface2); border:1px solid var(--border); border-radius:6px; padding:12px; overflow-x:auto; font-size:13px; color:var(--text); white-space:pre-wrap; word-break:break-word; max-height:400px; overflow-y:auto; }
.pre-wrap { position:relative; }
.pre-wrap .copy-btn { position:absolute; top:8px; right:8px; padding:4px 8px; font-size:11px; background:var(--surface); border:1px solid var(--border); border-radius:4px; color:var(--text-dim); cursor:pointer; opacity:0; transition:.15s; }
.pre-wrap:hover .copy-btn { opacity:1; }
.pre-wrap .copy-btn:hover { color:var(--text); border-color:var(--accent); }

/* ── Meta ───────────────────────────────────── */
.meta-row { display:flex; gap:24px; flex-wrap:wrap; margin-bottom:8px; }
.meta-row .meta-item { font-size:14px; } .meta-row .meta-label { color:var(--text-dim); margin-right:6px; }

/* ── Forms ──────────────────────────────────── */
.form-group { margin-bottom:12px; }
.form-group label { display:block; font-size:13px; color:var(--text-dim); margin-bottom:4px; }
.form-group input,.form-group textarea,.form-group select { width:100%; background:var(--surface2); border:1px solid var(--border); border-radius:6px; padding:8px 12px; color:var(--text); font-size:14px; font-family:inherit; }
.form-group input:focus,.form-group textarea:focus,.form-group select:focus { border-color:var(--accent); outline:none; }
.form-group textarea { min-height:80px; resize:vertical; }
.form-group select { appearance:none; cursor:pointer; }
.filter-bar { display:flex; gap:12px; flex-wrap:wrap; align-items:flex-end; margin-bottom:16px; }
.filter-bar .form-group { margin-bottom:0; }
.filter-bar .form-group.flex-1 { flex:1; min-width:160px; }

/* ── Agent diagram ──────────────────────────── */
.graph-container { width:100%; overflow:auto; background:var(--surface); border:1px solid var(--border); border-radius:var(--radius); padding:20px; position:relative; }
.graph-controls { display:flex; gap:8px; margin-bottom:12px; }
.agent-node-html { background:var(--surface2); border:2px solid var(--border); border-radius:var(--radius); padding:12px 16px; min-width:180px; cursor:pointer; transition:.2s; }
.agent-node-html:hover { border-color:var(--accent); }
.agent-node-html .agent-title { font-weight:700; font-size:14px; color:var(--accent); margin-bottom:4px; }
.agent-node-html .agent-id { font-size:11px; color:var(--text-dim); }
.agent-node-html .mcp-item { display:inline-block; background:var(--surface); border:1px solid var(--border); border-radius:4px; padding:1px 6px; font-size:10px; color:var(--cyan); margin:2px 2px 0 0; }
.agent-node-html .gate-badge { display:inline-block; margin-top:6px; padding:1px 6px; border-radius:4px; font-size:10px; font-weight:600; }
.gate-llm { background:#1a1040; color:var(--purple); border:1px solid #3b2d6b; }
.gate-human { background:#462c08; color:var(--orange); border:1px solid #6b4f1d; }

/* ── Agent detail sidebar ───────────────────── */
.agent-sidebar { position:fixed; right:-420px; top:0; bottom:0; width:400px; background:var(--surface); border-left:1px solid var(--border); transition:right .3s ease; z-index:150; padding:24px; overflow-y:auto; box-shadow:-4px 0 24px rgba(0,0,0,.3); }
.agent-sidebar.open { right:0; }
.agent-sidebar .close-btn { position:absolute; top:12px; right:12px; }

/* ── Agent accordion ────────────────────────── */
.agent-accordion { margin-top:24px; }
.agent-accordion-item { background:var(--surface); border:1px solid var(--border); border-radius:var(--radius); margin-bottom:8px; overflow:hidden; }
.agent-accordion-item summary { padding:12px 16px; cursor:pointer; font-weight:600; font-size:14px; display:flex; align-items:center; gap:8px; }
.agent-accordion-item summary:hover { background:var(--surface2); }
.agent-accordion-content { padding:0 16px 16px; font-size:13px; }

/* ── Tabs (CSS-only + JS enhanced) ──────────── */
.tabs { display:flex; gap:0; border-bottom:1px solid var(--border); margin-bottom:16px; overflow-x:auto; }
.tabs input[type="radio"] { display:none; }
.tabs label.tab { padding:10px 20px; font-size:14px; color:var(--text-dim); cursor:pointer; border-bottom:2px solid transparent; transition:.15s; white-space:nowrap; user-select:none; }
.tabs label.tab:hover { color:var(--text); }
.tab-panels .tab-panel { display:none; }
/* CSS-only tab switching */
#tab-1:checked ~ .tab-panels #panel-1, #tab-2:checked ~ .tab-panels #panel-2,
#tab-3:checked ~ .tab-panels #panel-3, #tab-4:checked ~ .tab-panels #panel-4,
#tab-5:checked ~ .tab-panels #panel-5 { display:block; }
#tab-1:checked ~ .tabs label[for="tab-1"], #tab-2:checked ~ .tabs label[for="tab-2"],
#tab-3:checked ~ .tabs label[for="tab-3"], #tab-4:checked ~ .tabs label[for="tab-4"],
#tab-5:checked ~ .tabs label[for="tab-5"] { color:var(--accent); border-bottom-color:var(--accent); }

/* ── Toast / notification ───────────────────── */
.toast-container { position:fixed; top:16px; right:24px; z-index:1000; display:flex; flex-direction:column; gap:8px; }
.toast { padding:12px 20px; border-radius:8px; font-size:14px; animation:slideIn .3s ease; transition:opacity .3s; max-width:420px; }
.toast-success { background:#0f2d16; color:var(--green); border:1px solid #1a4025; }
.toast-error { background:#3d1214; color:var(--red); border:1px solid #5a1d20; }
.toast-info { background:#0d2d6d; color:var(--accent); border:1px solid #1a4090; }
.toast .toast-action { margin-left:12px; font-weight:600; text-decoration:underline; cursor:pointer; }
@keyframes slideIn { from{transform:translateX(100%);opacity:0} to{transform:translateX(0);opacity:1} }

/* ── Dropdown ───────────────────────────────── */
.dropdown { position:relative; display:inline-block; }
.dropdown-menu { display:none; position:absolute; right:0; top:100%; margin-top:4px; background:var(--surface); border:1px solid var(--border); border-radius:6px; min-width:160px; z-index:50; box-shadow:0 4px 16px rgba(0,0,0,.3); overflow:hidden; }
.dropdown-menu.show { display:block; }
.dropdown-menu a,.dropdown-menu button { display:block; width:100%; padding:8px 14px; font-size:13px; color:var(--text); border:none; background:none; cursor:pointer; text-align:left; text-decoration:none; }
.dropdown-menu a:hover,.dropdown-menu button:hover { background:var(--surface2); text-decoration:none; }
.dropdown-menu .divider { height:1px; background:var(--border); margin:4px 0; }
.dropdown-menu .danger { color:var(--red); }

/* ── Skeleton loading ───────────────────────── */
.skeleton { background:linear-gradient(90deg,var(--surface2) 25%,var(--surface) 50%,var(--surface2) 75%); background-size:200% 100%; animation:shimmer 1.5s infinite; border-radius:4px; }
@keyframes shimmer { 0%{background-position:200% 0} 100%{background-position:-200% 0} }

/* ── Empty state ────────────────────────────── */
.empty-state { text-align:center; padding:48px 24px; color:var(--text-dim); }
.empty-state .icon { font-size:48px; margin-bottom:16px; opacity:.5; }
.empty-state .title { font-size:16px; font-weight:600; color:var(--text); margin-bottom:8px; }
.empty-state .desc { font-size:14px; margin-bottom:20px; }

/* ── Progress bar ───────────────────────────── */
.progress-bar { width:100%; height:4px; background:var(--surface2); border-radius:2px; overflow:hidden; }
.progress-bar .fill { height:100%; background:var(--accent); border-radius:2px; transition:width .5s; }

/* ── Live indicator ─────────────────────────── */
.live-dot { display:inline-block; width:8px; height:8px; border-radius:50%; background:var(--green); margin-right:6px; animation:pulse 1.5s infinite; }

/* ── Duration badge ─────────────────────────── */
.duration { font-size:11px; color:var(--text-dim); background:var(--surface2); padding:1px 6px; border-radius:4px; margin-left:6px; }

/* ── Agent tooltip ──────────────────────────── */
.agent-tip { position:relative; cursor:help; border-bottom:1px dotted var(--text-dim); }
.agent-tip::after { content:attr(data-tip); position:absolute; bottom:calc(100% + 6px); left:50%; transform:translateX(-50%); background:var(--surface2); border:1px solid var(--border); padding:8px 12px; border-radius:6px; font-size:12px; white-space:pre-line; display:none; z-index:10; min-width:180px; max-width:300px; color:var(--text); pointer-events:none; box-shadow:0 4px 12px rgba(0,0,0,.3); }
.agent-tip:hover::after { display:block; }

/* ── Modal ──────────────────────────────────── */
.modal-overlay { display:none; position:fixed; inset:0; background:rgba(0,0,0,.6); z-index:200; align-items:center; justify-content:center; }
.modal-overlay.show { display:flex; }
.modal { background:var(--surface); border:1px solid var(--border); border-radius:12px; padding:24px; max-width:600px; width:90%; max-height:80vh; overflow-y:auto; }
.modal-sm { max-width:400px; }
.modal-lg { max-width:900px; }
.modal h3 { margin-bottom:16px; }

/* ── Command Palette ────────────────────────── */
.cmd-overlay { display:none; position:fixed; inset:0; background:rgba(0,0,0,.5); z-index:300; align-items:flex-start; justify-content:center; padding-top:20vh; }
.cmd-overlay.show { display:flex; }
.cmd-box { background:var(--surface); border:1px solid var(--border); border-radius:12px; width:560px; max-width:95%; box-shadow:0 8px 32px rgba(0,0,0,.5); overflow:hidden; }
.cmd-input { width:100%; padding:16px 20px; background:transparent; border:none; border-bottom:1px solid var(--border); color:var(--text); font-size:16px; outline:none; }
.cmd-results { max-height:300px; overflow-y:auto; }
.cmd-item { padding:10px 20px; display:flex; align-items:center; gap:12px; cursor:pointer; font-size:14px; color:var(--text-dim); }
.cmd-item:hover,.cmd-item.selected { background:var(--surface2); color:var(--text); }
.cmd-item .cmd-label { flex:1; }
.cmd-item .cmd-shortcut { font-size:11px; color:var(--text-dim); font-family:monospace; }

/* ── Batch action bar ───────────────────────── */
.batch-bar { display:none; align-items:center; gap:12px; padding:12px 16px; background:var(--surface); border:1px solid var(--accent); border-radius:var(--radius); margin-bottom:12px; }
.batch-bar.show { display:flex; }
.batch-bar .count { font-size:14px; font-weight:600; color:var(--accent); }

/* ── Pagination ─────────────────────────────── */
.pagination { display:flex; align-items:center; justify-content:center; gap:8px; margin-top:16px; }
.pagination button { padding:6px 12px; border-radius:4px; border:1px solid var(--border); background:var(--surface2); color:var(--text); cursor:pointer; font-size:13px; }
.pagination button:hover { border-color:var(--accent); }
.pagination button.active { background:var(--accent); color:#fff; border-color:var(--accent); }
.pagination button:disabled { opacity:.4; cursor:default; }
.pagination .info { font-size:13px; color:var(--text-dim); }

/* ── YAML Editor ────────────────────────────── */
.yaml-editor { font-family:'SF Mono','Monaco','Menlo','Consolas',monospace; font-size:13px; line-height:1.6; min-height:300px; resize:vertical; tab-size:2; background:var(--bg); color:var(--text); border:1px solid var(--border); border-radius:6px; padding:12px; }

/* ── Chunk list ─────────────────────────────── */
.chunk-item { display:flex; align-items:center; gap:12px; padding:10px 0; border-bottom:1px solid var(--border); }
.chunk-item:last-child { border-bottom:none; }
.chunk-item .chunk-desc { flex:1; font-size:14px; }
.chunk-item select { width:auto; padding:4px 8px; font-size:12px; }

@media (max-width:768px) {
  .sidebar { position:fixed; top:0; bottom:0; left:0; transform:translateX(-100%); transition:transform .3s ease; }
  .sidebar.open { transform:translateX(0); }
  .main-content { width:100%; }
  .topbar { padding:10px 16px; }
  .hamburger { display:flex !important; }
  .stats { grid-template-columns:repeat(2,1fr); }
  .meta-row { flex-direction:column; gap:4px; }
  th,td { padding:8px 10px; font-size:13px; }
  .modal { width:95%; max-height:90vh; }
  .cmd-box { width:95%; }
  .filter-bar { flex-direction:column; }
  .agent-sidebar { width:100%; right:-100%; }
}
"""


# ---------------------------------------------------------------------------
# SVG Icons (inline, no external deps)
# ---------------------------------------------------------------------------

ICONS = {
    "dashboard": '<svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor"><path d="M6.5 1.75a.75.75 0 00-1.5 0V5H1.75a.75.75 0 000 1.5H5v3.25a.75.75 0 001.5 0V6.5h3.25a.75.75 0 000-1.5H6.5V1.75z"/><path d="M0 1.75C0 .784.784 0 1.75 0h12.5C15.216 0 16 .784 16 1.75v12.5A1.75 1.75 0 0114.25 16H1.75A1.75 1.75 0 010 14.25V1.75zm1.75-.25a.25.25 0 00-.25.25v12.5c0 .138.112.25.25.25h12.5a.25.25 0 00.25-.25V1.75a.25.25 0 00-.25-.25H1.75z"/></svg>',
    "tasks": '<svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor"><path d="M1.5 3.25c0-.966.784-1.75 1.75-1.75h9.5c.966 0 1.75.784 1.75 1.75v9.5a1.75 1.75 0 01-1.75 1.75h-9.5a1.75 1.75 0 01-1.75-1.75v-9.5zm1.75-.25a.25.25 0 00-.25.25v9.5c0 .138.112.25.25.25h9.5a.25.25 0 00.25-.25v-9.5a.25.25 0 00-.25-.25h-9.5z"/><path d="M11.28 6.28a.75.75 0 00-1.06-1.06L7 8.44 5.78 7.22a.75.75 0 00-1.06 1.06l1.75 1.75a.75.75 0 001.06 0l3.75-3.75z"/></svg>',
    "agents": '<svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor"><path d="M2 2.5A2.5 2.5 0 014.5 0h7A2.5 2.5 0 0114 2.5v2.382a1 1 0 01-.447.894l-2 1.25A1 1 0 0011 7.92v.08h3a1 1 0 011 1v4.5a2.5 2.5 0 01-2.5 2.5h-9A2.5 2.5 0 011 13.5V9a1 1 0 011-1h3v-.08a1 1 0 01-.553-.894l-2-1.25A1 1 0 012 4.882V2.5z"/></svg>',
    "pipelines": '<svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor"><path d="M8.5 1.75a.75.75 0 00-1.5 0v1.5H5.75a.75.75 0 000 1.5H7v1.984l-3.21 2.14A1.5 1.5 0 003 10.136V13.5a1.5 1.5 0 001.5 1.5h7a1.5 1.5 0 001.5-1.5v-3.364a1.5 1.5 0 00-.79-1.312L8.5 6.734V4.75h1.25a.75.75 0 000-1.5H8.5v-1.5z"/></svg>',
    "registry": '<svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor"><path d="M2 2.5A2.5 2.5 0 014.5 0h7A2.5 2.5 0 0114 2.5v11a2.5 2.5 0 01-2.5 2.5h-7A2.5 2.5 0 012 13.5v-11zM4.5 1.5a1 1 0 00-1 1v11a1 1 0 001 1h7a1 1 0 001-1v-11a1 1 0 00-1-1h-7z"/><path d="M5 4h6v1.5H5V4zm0 3h6v1.5H5V7zm0 3h4v1.5H5V10z"/></svg>',
    "validate": '<svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor"><path d="M8 16A8 8 0 108 0a8 8 0 000 16zm3.78-9.72a.75.75 0 00-1.06-1.06L7 8.94 5.28 7.22a.75.75 0 00-1.06 1.06l2.25 2.25a.75.75 0 001.06 0l4.25-4.25z"/></svg>',
    "sun": '<svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor"><path d="M8 12a4 4 0 100-8 4 4 0 000 8zm0 1.5a5.5 5.5 0 100-11 5.5 5.5 0 000 11zm5.657-9.157a.75.75 0 00-1.06-1.06l-.354.353a.75.75 0 001.06 1.061l.354-.354zM8 .75a.75.75 0 01.75.75v.5a.75.75 0 01-1.5 0v-.5A.75.75 0 018 .75z"/></svg>',
    "moon": '<svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor"><path d="M9.598 1.591a.75.75 0 01.785-.175 7 7 0 11-8.967 8.967.75.75 0 01.961-.96 5.5 5.5 0 007.046-7.046.75.75 0 01.175-.786z"/></svg>',
    "search": '<svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor"><path d="M11.5 7a4.5 4.5 0 11-9 0 4.5 4.5 0 019 0zm-.82 4.74a6 6 0 111.06-1.06l3.04 3.04a.75.75 0 11-1.06 1.06l-3.04-3.04z"/></svg>',
    "command": '<svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor"><path d="M3.5 2A1.5 1.5 0 002 3.5V5h2.5a1.5 1.5 0 000-3H3.5zM6 5V3.5A3 3 0 003.5 .5 3 3 0 00.5 3.5V6h5.5V5zM5 7.5H.5V12.5A3 3 0 003.5 15.5 3 3 0 006 12.5V7.5H5zm1.5 0V12.5a3 3 0 003 3 3 3 0 003-3V7.5h-6zm7.5 0h-1v5a1.5 1.5 0 003 0V8.5h-2z"/></svg>',
    "hamburger": '<svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor"><path d="M1 2.75A.75.75 0 011.75 2h12.5a.75.75 0 110 1.5H1.75A.75.75 0 011 2.75zm0 5A.75.75 0 011.75 7h12.5a.75.75 0 110 1.5H1.75A.75.75 0 011 7.75zM1.75 12a.75.75 0 100 1.5h12.5a.75.75 0 100-1.5H1.75z"/></svg>',
}


# ---------------------------------------------------------------------------
# Shared JS Utilities
# ---------------------------------------------------------------------------

JS_UTILS = """\
/* ── Toast ─────────────────────────────────── */
function showToast(msg, type='success', action) {
  const c = document.getElementById('toastContainer');
  if (!c) return;
  const t = document.createElement('div');
  t.className = 'toast toast-' + type;
  t.innerHTML = msg;
  if (action) {
    const a = document.createElement('a');
    a.className = 'toast-action';
    a.textContent = action.label;
    a.href = action.href || '#';
    if (action.onclick) a.onclick = action.onclick;
    t.appendChild(a);
  }
  c.appendChild(t);
  setTimeout(() => { t.style.opacity = '0'; setTimeout(() => t.remove(), 300); }, 4000);
}

/* ── API Fetch ─────────────────────────────── */
async function apiFetch(url, opts={}) {
  try {
    const res = await fetch(url, {headers:{'Content-Type':'application/json'}, ...opts});
    if (res.headers.get('content-type')?.includes('text/plain')) return await res.text();
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Request failed');
    return data;
  } catch(e) { showToast(e.message, 'error'); throw e; }
}

/* ── Theme Toggle ──────────────────────────── */
(function initTheme() {
  const saved = localStorage.getItem('aqm-theme');
  if (saved) document.documentElement.setAttribute('data-theme', saved);
})();
function toggleTheme() {
  const current = document.documentElement.getAttribute('data-theme') || 'dark';
  const next = current === 'dark' ? 'light' : 'dark';
  document.documentElement.setAttribute('data-theme', next);
  localStorage.setItem('aqm-theme', next);
  const icon = document.getElementById('themeIcon');
  if (icon) icon.innerHTML = next === 'dark' ? '""" + ICONS["sun"].replace("'", "\\'") + """' : '""" + ICONS["moon"].replace("'", "\\'") + """';
}

/* ── Escape key ────────────────────────────── */
document.addEventListener('keydown', function(e) {
  if (e.key === 'Escape') closeSidebarMobile();
});

/* ── Dropdown ──────────────────────────────── */
function toggleDropdown(el) {
  const menu = el.nextElementSibling;
  document.querySelectorAll('.dropdown-menu.show').forEach(m => { if(m!==menu) m.classList.remove('show'); });
  menu.classList.toggle('show');
}
document.addEventListener('click', function(e) {
  if (!e.target.closest('.dropdown')) document.querySelectorAll('.dropdown-menu.show').forEach(m => m.classList.remove('show'));
});

/* ── Mobile sidebar ────────────────────────── */
function toggleSidebar() {
  document.querySelector('.sidebar').classList.toggle('open');
}
function closeSidebarMobile() {
  document.querySelector('.sidebar')?.classList.remove('open');
}

/* ── Copy to clipboard ─────────────────────── */
function copyText(btn) {
  const pre = btn.closest('.pre-wrap')?.querySelector('pre');
  if (!pre) return;
  navigator.clipboard.writeText(pre.textContent).then(() => {
    btn.textContent = 'Copied!';
    setTimeout(() => { btn.textContent = 'Copy'; }, 1500);
  });
}

/* ── Global error handler ──────────────────── */
window.addEventListener('unhandledrejection', function(e) {
  if (e.reason && e.reason.message) showToast(e.reason.message, 'error');
});
"""


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

def sidebar_html(active: str = "") -> str:
    links = [
        ("/", "Dashboard", "tasks", "tasks"),
        ("/agents", "Agents", "agents", "agents"),
        ("/pipelines", "Pipelines", "pipelines", "pipelines"),
        ("/registry", "Registry", "registry", "registry"),
        ("/validate", "Validate", "validate", "validate"),
    ]
    items = []
    for href, label, key, icon_key in links:
        cls = "nav-item active" if key == active else "nav-item"
        icon = ICONS.get(icon_key, "")
        items.append(f'<a class="{cls}" href="{href}">{icon}<span>{label}</span></a>')

    return f"""\
<aside class="sidebar" role="navigation" aria-label="Main navigation">
  <div class="sidebar-logo"><span>aqm</span> Dashboard</div>
  <nav class="sidebar-nav">
    <div class="nav-section">
      <div class="nav-section-title">Navigation</div>
      {"".join(items)}
    </div>
  </nav>
  <div class="sidebar-footer">aqm v1.3.0</div>
</aside>"""


# ---------------------------------------------------------------------------
# Topbar
# ---------------------------------------------------------------------------

def topbar_html(breadcrumbs: list[tuple[str, str | None]] | None = None) -> str:
    bc = ""
    if breadcrumbs:
        parts = []
        for i, (label, href) in enumerate(breadcrumbs):
            if href:
                parts.append(f'<a href="{href}">{esc(label)}</a>')
            else:
                parts.append(f'<span class="current">{esc(label)}</span>')
            if i < len(breadcrumbs) - 1:
                parts.append('<span class="sep">/</span>')
        bc = f'<div class="breadcrumb">{"".join(parts)}</div>'
    else:
        bc = '<div class="breadcrumb"></div>'

    return f"""\
<div class="topbar">
  <div class="topbar-left">
    <button class="btn btn-icon hamburger" style="display:none;" onclick="toggleSidebar()" aria-label="Toggle menu">{ICONS["hamburger"]}</button>
    {bc}
  </div>
  <div class="topbar-right">
    <button class="btn btn-icon" onclick="toggleTheme()" title="Toggle theme" aria-label="Toggle theme"><span id="themeIcon">{ICONS["sun"]}</span></button>
  </div>
</div>"""


# ---------------------------------------------------------------------------
# Command Palette HTML
# ---------------------------------------------------------------------------

CMD_PALETTE_HTML = ""


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------

def layout(
    title: str,
    body: str,
    active: str = "",
    extra_head: str = "",
    breadcrumbs: list[tuple[str, str | None]] | None = None,
) -> str:
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
<a href="#main" class="sr-only">Skip to main content</a>
<div class="app-layout">
{sidebar_html(active)}
<div class="main-content" role="main" id="main">
{topbar_html(breadcrumbs)}
<div class="container">
{body}
</div>
</div>
</div>
<div class="toast-container" id="toastContainer" aria-live="polite"></div>
{CMD_PALETTE_HTML}
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


def fmt_duration(started_at: Optional[datetime], finished_at: Optional[datetime]) -> str:
    """Format elapsed time between two datetimes as a human-readable string."""
    if not started_at or not finished_at:
        return ""
    delta = finished_at - started_at
    secs = int(delta.total_seconds())
    if secs < 0:
        return ""
    if secs >= 3600:
        return f"{secs // 3600}h {(secs % 3600) // 60}m {secs % 60}s"
    if secs >= 60:
        return f"{secs // 60}m {secs % 60}s"
    return f"{secs}s"


def copy_pre(content: str) -> str:
    """Wrap content in a <pre> with a copy button overlay."""
    return f'<div class="pre-wrap"><button class="copy-btn" onclick="copyText(this)">Copy</button><pre>{esc(content)}</pre></div>'
