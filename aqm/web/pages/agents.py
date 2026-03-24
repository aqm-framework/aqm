"""Agent diagram page — D3.js + dagre directed graph visualization."""

from __future__ import annotations

import json

from aqm.core.agent import AgentDefinition
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
            "gate": {"type": agent.gate.type, "prompt": agent.gate.prompt} if agent.gate else None,
            "mcp": [m.server for m in agent.mcp],
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


def _build_agent_details(agents: dict[str, AgentDefinition]) -> str:
    """Build collapsible accordion with agent details."""
    items = []
    for agent in agents.values():
        mcp_html = ""
        if agent.mcp:
            servers = ", ".join(m.server for m in agent.mcp)
            mcp_html = f"<div><strong>MCP:</strong> {esc(servers)}</div>"

        gate_html = ""
        if agent.gate:
            gate_html = f"<div><strong>Gate:</strong> {esc(agent.gate.type)}"
            if agent.gate.prompt:
                gate_html += f" — {esc(agent.gate.prompt[:100])}"
            gate_html += "</div>"

        handoffs_html = ""
        if agent.handoffs:
            parts = []
            for h in agent.handoffs:
                parts.append(f"→ {esc(h.to)} [{esc(h.condition)}]")
            handoffs_html = f"<div><strong>Handoffs:</strong> {' | '.join(parts)}</div>"

        prompt_preview = ""
        if agent.system_prompt:
            preview = agent.system_prompt[:150].replace("\n", " ")
            prompt_preview = f'<div style="margin-top:8px;"><strong>Prompt:</strong> <span style="color:var(--text-dim)">{esc(preview)}...</span></div>'

        items.append(
            f'<details class="agent-accordion-item">'
            f'<summary><span style="color:var(--accent)">{esc(agent.name)}</span>'
            f' <span style="color:var(--text-dim);font-weight:400;font-size:12px">{esc(agent.id)} · {esc(agent.runtime)}</span></summary>'
            f'<div class="agent-accordion-content">'
            f'{gate_html}{mcp_html}{handoffs_html}{prompt_preview}'
            f'</div></details>'
        )
    return f'<div class="agent-accordion">{"".join(items)}</div>'


D3_DAGRE_CDN = """\
<script src="https://cdn.jsdelivr.net/npm/d3@7"></script>
<script src="https://cdn.jsdelivr.net/npm/dagre@0.8.5/dist/dagre.min.js"></script>"""

GRAPH_JS = """\
<script>
(function() {
  const data = GRAPH_DATA_PLACEHOLDER;
  if (!data.nodes.length) return;

  const g = new dagre.graphlib.Graph();
  g.setGraph({ rankdir:'TB', nodesep:60, ranksep:80, marginx:40, marginy:40 });
  g.setDefaultEdgeLabel(() => ({}));

  // Add nodes
  data.nodes.forEach(n => {
    const h = 60 + (n.mcp.length > 0 ? 20 : 0) + (n.gate ? 20 : 0);
    g.setNode(n.id, { label:n.name, width:200, height:h, data:n });
  });

  // Add edges
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
      .attr('d', line(edge.points))
      .attr('fill','none')
      .attr('stroke', color)
      .attr('stroke-width', 2)
      .attr('stroke-dasharray', isDashed ? '6,3' : 'none')
      .attr('marker-end', `url(#arrow-${markerKey})`);

    // Edge label
    const mid = edge.points[Math.floor(edge.points.length/2)];
    const label = cond === 'always' ? '' : cond;
    if (label) {
      container.append('rect')
        .attr('x', mid.x - label.length*3.5 - 4)
        .attr('y', mid.y - 9)
        .attr('width', label.length*7 + 8)
        .attr('height', 18)
        .attr('rx', 4)
        .attr('fill', '#161b22')
        .attr('stroke', '#30363d');
      container.append('text')
        .attr('x', mid.x).attr('y', mid.y + 4)
        .attr('text-anchor','middle')
        .attr('fill', color)
        .attr('font-size','11px')
        .attr('font-weight','600')
        .text(label);
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
      .attr('width', node.width).attr('height', node.height);

    let html = `<div class="agent-node-html">`;
    html += `<div class="agent-title">${n.name}</div>`;
    html += `<div class="agent-id">${n.id} · ${n.runtime}</div>`;
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
})();
</script>"""


def render_agents(agents: dict[str, AgentDefinition]) -> str:
    if not agents:
        body = '<div class="empty-state">No agents defined. Create .aqm/agents.yaml first.</div>'
        return layout("Agents", f"<h1>Agent Pipeline</h1>\n{body}", active="agents")

    graph_data = _build_graph_data(agents)
    graph_js = GRAPH_JS.replace("GRAPH_DATA_PLACEHOLDER", graph_data)

    graph_section = f"""\
<div class="graph-container" id="agentGraph"></div>
{graph_js}"""

    details = _build_agent_details(agents)

    return layout(
        "Agents",
        f"<h1>Agent Pipeline</h1>\n{graph_section}\n<h2 style='margin-top:24px;'>Agent Details</h2>\n{details}",
        active="agents",
        extra_head=D3_DAGRE_CDN,
    )
