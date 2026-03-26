"""Agent diagram page — D3.js + dagre directed graph with zoom/pan, detail sidebar, path highlighting."""

from __future__ import annotations

import json

from aqm.core.agent import AgentDefinition
from aqm.core.task import Task
from aqm.web.templates import esc, layout


def _build_graph_data(agents: dict[str, AgentDefinition]) -> str:
    """Build JSON graph data for D3.js rendering."""
    nodes = []
    edges = []
    for agent in agents.values():
        nodes.append({
            "id": agent.id,
            "name": agent.name,
            "runtime": agent.runtime,
            "type": agent.type,
            "gate": {"type": agent.gate.type, "prompt": agent.gate.prompt} if agent.gate else None,
            "mcp": [m.server for m in agent.mcp],
            "context_strategy": agent.context_strategy,
            "human_input": {"mode": agent.human_input.mode} if agent.human_input and agent.human_input.enabled else None,
            "system_prompt_preview": (agent.system_prompt[:200] + "...") if len(agent.system_prompt) > 200 else agent.system_prompt,
            "handoffs_raw": [{"to": h.to, "condition": h.condition, "task": h.task} for h in agent.handoffs],
        })
        for h in agent.handoffs:
            targets = [t.strip() for t in h.to.split(",")]
            for target in targets:
                edges.append({
                    "source": agent.id,
                    "target": target,
                    "condition": h.condition,
                    "task": h.task or "",
                })
    return json.dumps({"nodes": nodes, "edges": edges})


D3_DAGRE_CDN = """\
<script src="https://cdn.jsdelivr.net/npm/d3@7"></script>
<script src="https://cdn.jsdelivr.net/npm/dagre@0.8.5/dist/dagre.min.js"></script>"""

GRAPH_JS = """\
<script>
(function() {
  const data = GRAPH_DATA_PLACEHOLDER;
  if (!data.nodes.length) return;

  const g = new dagre.graphlib.Graph();
  g.setGraph({ rankdir:'TB', nodesep:80, ranksep:100, marginx:40, marginy:40 });
  g.setDefaultEdgeLabel(() => ({}));

  data.nodes.forEach(n => {
    let h = 70;
    if (n.gate) h += 28;
    if (n.mcp.length > 0) h += 24 + Math.ceil(n.mcp.length / 3) * 24;
    g.setNode(n.id, { label:n.name, width:220, height:h, data:n });
  });

  data.edges.forEach(e => {
    g.setEdge(e.source, e.target, { condition:e.condition, task:e.task });
  });

  dagre.layout(g);

  const graphInfo = g.graph();
  const svgW = graphInfo.width + 80;
  const svgH = graphInfo.height + 80;

  const svg = d3.select('#agentGraph')
    .append('svg')
    .attr('width', '100%')
    .attr('viewBox', `0 0 ${svgW} ${svgH}`)
    .attr('preserveAspectRatio', 'xMidYMid meet');

  // Enable zoom/pan
  const zoomBehavior = d3.zoom()
    .scaleExtent([0.3, 3])
    .on('zoom', (event) => {
      container.attr('transform', event.transform);
    });
  svg.call(zoomBehavior);

  // Arrow markers
  const defs = svg.append('defs');
  const colors = {
    always:'#3fb950', on_approve:'#3fb950', on_reject:'#f85149',
    on_pass:'#3fb950', auto:'#bc8cff', _default:'#58a6ff'
  };
  Object.entries(colors).forEach(([key, color]) => {
    defs.append('marker').attr('id','arrow-'+key)
      .attr('viewBox','0 0 10 10').attr('refX',10).attr('refY',5)
      .attr('markerWidth',8).attr('markerHeight',8).attr('orient','auto')
      .append('path').attr('d','M 0 0 L 10 5 L 0 10 z').attr('fill',color);
  });

  const container = svg.append('g').attr('transform','translate(40,40)');

  // Draw edges
  g.edges().forEach(e => {
    const edge = g.edge(e);
    const cond = edge.condition || 'always';
    const color = colors[cond] || colors._default;
    const isDashed = cond === 'on_approve' || cond === 'on_reject' || cond === 'auto';
    const markerKey = colors[cond] ? cond : '_default';

    const line = d3.line().x(d=>d.x).y(d=>d.y).curve(d3.curveBasis);
    container.append('path')
      .attr('class', 'edge-path')
      .attr('data-source', e.v).attr('data-target', e.w)
      .attr('d', line(edge.points))
      .attr('fill','none')
      .attr('stroke', color)
      .attr('stroke-width', 2)
      .attr('stroke-dasharray', isDashed ? '6,3' : 'none')
      .attr('marker-end', `url(#arrow-${markerKey})`);

    const mid = edge.points[Math.floor(edge.points.length/2)];
    const label = cond === 'always' ? '' : cond;
    if (label) {
      container.append('rect')
        .attr('x', mid.x - label.length*3.5 - 4).attr('y', mid.y - 9)
        .attr('width', label.length*7 + 8).attr('height', 18).attr('rx', 4)
        .attr('fill', '#161b22').attr('stroke', '#30363d');
      container.append('text')
        .attr('x', mid.x).attr('y', mid.y + 4)
        .attr('text-anchor','middle').attr('fill', color)
        .attr('font-size','11px').attr('font-weight','600').text(label);
    }
  });

  // Draw nodes
  g.nodes().forEach(nid => {
    const node = g.node(nid);
    const n = node.data;
    const x = node.x - node.width/2;
    const y = node.y - node.height/2;

    const fo = container.append('foreignObject')
      .attr('x', x).attr('y', y)
      .attr('width', node.width).attr('height', node.height)
      .attr('style', 'overflow:visible');

    let html = `<div class="agent-node-html" id="node-${n.id}" onclick="showAgentDetail('${n.id}')">`;
    html += `<div class="agent-title">${n.name}</div>`;
    html += `<div class="agent-id">${n.id} · ${n.runtime || n.type}</div>`;
    if (n.gate) {
      const cls = n.gate.type === 'llm' ? 'gate-llm' : 'gate-human';
      html += `<span class="gate-badge ${cls}">${n.gate.type} gate</span>`;
    }
    if (n.mcp.length > 0) {
      html += `<div>` + n.mcp.map(m => `<span class="mcp-item">${m}</span>`).join('') + `</div>`;
    }
    html += `</div>`;

    fo.append('xhtml:div').html(html);
  });

  // ── Zoom Controls ──
  window.zoomIn = () => svg.transition().call(zoomBehavior.scaleBy, 1.3);
  window.zoomOut = () => svg.transition().call(zoomBehavior.scaleBy, 0.7);
  window.zoomReset = () => svg.transition().call(zoomBehavior.transform, d3.zoomIdentity.translate(40,40));
  window.zoomFit = () => {
    const bounds = container.node().getBBox();
    const parent = svg.node().parentElement;
    const pw = parent.clientWidth;
    const ph = parent.clientHeight || 500;
    const scale = Math.min(pw / (bounds.width + 80), ph / (bounds.height + 80), 1.5);
    const tx = (pw - bounds.width * scale) / 2 - bounds.x * scale;
    const ty = 20;
    svg.transition().call(zoomBehavior.transform, d3.zoomIdentity.translate(tx, ty).scale(scale));
  };

  // ── Agent Detail Sidebar ──
  window._nodeData = {};
  data.nodes.forEach(n => { window._nodeData[n.id] = n; });

  window.showAgentDetail = function(agentId) {
    const n = window._nodeData[agentId];
    if (!n) return;
    const sidebar = document.getElementById('agentSidebar');
    const detail = document.getElementById('agentDetailContent');
    if (!sidebar || !detail) return;

    let html = `<h3 style="color:var(--accent);margin-bottom:16px;">${n.name}</h3>`;
    html += `<div class="meta-row"><div class="meta-item"><span class="meta-label">ID:</span> ${n.id}</div></div>`;
    html += `<div class="meta-row"><div class="meta-item"><span class="meta-label">Runtime:</span> ${n.runtime || n.type}</div></div>`;
    html += `<div class="meta-row"><div class="meta-item"><span class="meta-label">Context:</span> ${n.context_strategy}</div></div>`;
    if (n.gate) html += `<div class="meta-row"><div class="meta-item"><span class="meta-label">Gate:</span> ${n.gate.type} — ${(n.gate.prompt||'').substring(0,100)}</div></div>`;
    if (n.human_input) html += `<div class="meta-row"><div class="meta-item"><span class="meta-label">Human Input:</span> ${n.human_input.mode}</div></div>`;
    if (n.mcp.length) html += `<div class="meta-row"><div class="meta-item"><span class="meta-label">MCP:</span> ${n.mcp.join(', ')}</div></div>`;
    if (n.handoffs_raw.length) {
      html += `<h4 style="margin-top:16px;margin-bottom:8px;">Handoffs</h4>`;
      n.handoffs_raw.forEach(h => {
        html += `<div style="font-size:13px;padding:4px 0;color:var(--text-dim);">→ ${h.to} <span class="badge badge-${h.condition}">${h.condition}</span></div>`;
      });
    }
    if (n.system_prompt_preview) {
      html += `<h4 style="margin-top:16px;margin-bottom:8px;">System Prompt</h4>`;
      html += `<pre style="font-size:11px;max-height:200px;">${n.system_prompt_preview.replace(/</g,'&lt;')}</pre>`;
    }

    detail.innerHTML = html;
    sidebar.classList.add('open');

    // Highlight this node
    d3.selectAll('.agent-node-html').style('opacity', 0.4);
    d3.select('#node-' + agentId).style('opacity', 1).style('border-color', 'var(--accent)');
  };

  window.closeAgentSidebar = function() {
    document.getElementById('agentSidebar')?.classList.remove('open');
    d3.selectAll('.agent-node-html').style('opacity', 1).style('border-color', '');
    d3.selectAll('.edge-path').style('opacity', 1);
  };

  // ── Execution Path Highlighting ──
  window.highlightPath = async function(taskId) {
    if (!taskId) {
      d3.selectAll('.agent-node-html').style('opacity', 1).style('border-color', '');
      d3.selectAll('.edge-path').style('opacity', 1);
      return;
    }
    try {
      const task = await apiFetch('/api/tasks/' + taskId);
      const path = task.stages.map(s => s.agent_id);
      d3.selectAll('.agent-node-html').style('opacity', 0.2).style('border-color', '');
      d3.selectAll('.edge-path').style('opacity', 0.1);
      path.forEach((id, i) => {
        d3.select('#node-' + id).style('opacity', 1).style('border-color', 'var(--green)');
        if (i > 0) {
          d3.selectAll('.edge-path').filter(function() {
            return this.dataset.source === path[i-1] && this.dataset.target === id;
          }).style('opacity', 1);
        }
      });
    } catch(e) {}
  };
})();
</script>"""


def render_agents(
    agents: dict[str, AgentDefinition],
    pipelines: list[str] | None = None,
    current_pipeline: str = "default",
    agent_error: str | None = None,
    recent_tasks: list[Task] | None = None,
) -> str:
    # Pipeline selector
    pipeline_selector = ""
    if pipelines and len(pipelines) > 1:
        pipe_options = "".join(
            f'<option value="{esc(p)}"{"selected" if p == current_pipeline else ""}>{esc(p)}</option>'
            for p in pipelines
        )
        pipeline_selector = (
            f'<div style="margin-bottom:16px;display:flex;align-items:center;gap:12px;">'
            f'<label style="font-weight:600;">Pipeline:</label>'
            f'<select onchange="location.href=\'/agents?pipeline=\'+this.value"'
            f' style="max-width:300px;">{pipe_options}</select></div>'
        )

    error_banner = ""
    if agent_error:
        error_banner = (
            f'<div style="background:var(--surface2);border-left:4px solid var(--orange);'
            f'padding:12px 16px;margin-bottom:16px;border-radius:6px;">'
            f'<strong style="color:var(--orange);">Pipeline configuration error</strong>'
            f'<p style="margin:6px 0 0;font-size:13px;opacity:.85;">{esc(agent_error)}</p>'
            f'</div>'
        )

    if not agents:
        body = error_banner or """\
<div class="empty-state">
  <div class="icon">🤖</div>
  <div class="title">No agents defined</div>
  <div class="desc">Create a pipeline with agents to see the visualization.</div>
  <a href="/pipelines" class="btn btn-primary">Go to Pipelines</a>
</div>"""
        return layout("Agents", f"<h1>Agent Pipeline</h1>\n{pipeline_selector}{body}", active="agents")

    graph_data = _build_graph_data(agents)
    graph_js = GRAPH_JS.replace("GRAPH_DATA_PLACEHOLDER", graph_data)

    # Task selector for path highlighting
    task_selector = ""
    if recent_tasks:
        task_options = "".join(
            f'<option value="{esc(t.id)}">{esc(t.id)} — {esc(t.description[:40])}</option>'
            for t in recent_tasks[:20]
        )
        task_selector = f"""\
<div style="display:flex;align-items:center;gap:8px;">
  <label style="font-size:13px;color:var(--text-dim);">Highlight path:</label>
  <select onchange="highlightPath(this.value)" style="max-width:300px;font-size:13px;">
    <option value="">None</option>
    {task_options}
  </select>
</div>"""

    graph_section = f"""\
<div class="graph-controls">
  <button class="btn btn-sm" onclick="zoomIn()" title="Zoom In">+</button>
  <button class="btn btn-sm" onclick="zoomOut()" title="Zoom Out">-</button>
  <button class="btn btn-sm" onclick="zoomReset()" title="Reset">Reset</button>
  <button class="btn btn-sm" onclick="zoomFit()" title="Fit to View">Fit</button>
  {task_selector}
</div>
<div class="graph-container" id="agentGraph" style="min-height:400px;"></div>
{graph_js}"""

    # Agent detail sidebar
    sidebar = """\
<div class="agent-sidebar" id="agentSidebar">
  <button class="btn btn-sm close-btn" onclick="closeAgentSidebar()">Close</button>
  <div id="agentDetailContent"></div>
</div>"""

    return layout(
        "Agents",
        f"<h1>Agent Pipeline</h1>\n{pipeline_selector}{error_banner}{graph_section}\n{sidebar}",
        active="agents",
        extra_head=D3_DAGRE_CDN,
    )
