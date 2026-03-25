# aqm

An orchestration framework where multiple AI agents pass tasks through **explicit queues** — or **discuss in real-time sessions** until consensus.

Build pipelines in YAML. Share them with anyone. Run them locally.

```
      [planner] ──► [reviewer] ──approve──► [design_session] ──► [implementer]
          ▲              │                    ┌──┬──┬──┐
          └──── reject ──┘                    ▼  ▼  ▼  ▼  round-robin
                                           [arch][sec][fe]  until consensus
```

## Install

```bash
pip install aqm
```

> Requires Python 3.11+. At least one LLM CLI must be installed (see Multi-LLM below).

## Quick Start

```bash
cd my-project
aqm init                              # Interactive setup wizard
aqm run "Add JWT authentication"       # Run pipeline
aqm serve                              # Web dashboard at localhost:8000
```

## Features

### Multi-LLM Runtimes

Mix providers per agent. All use CLI subprocesses — no API keys or SDK setup needed.

| Runtime | Provider | Install |
|---|---|---|
| `claude` | Anthropic | `npm i -g @anthropic-ai/claude-code && claude login` |
| `gemini` | Google | `npm i -g @google/gemini-cli` |
| `codex` | OpenAI | `npm i -g @openai/codex` |

Claude auto-selects **Code mode** (with MCP/tools) vs **text-only mode** based on agent config.

```yaml
agents:
  - id: planner
    runtime: gemini
    model: gemini-2.5-flash
    system_prompt: "Plan: {{ input }}"
    handoffs: [{ to: developer }]

  - id: developer
    runtime: claude
    mcp: [{ server: github }]         # Auto Code mode
    system_prompt: "Implement: {{ input }}"
```

### Conversational Sessions

Session nodes let multiple agents **discuss in rounds** until consensus — like a meeting.

```yaml
agents:
  - id: design_review
    type: session
    participants: [architect, frontend, security]
    turn_order: round_robin           # or: moderator
    max_rounds: 5
    consensus:
      method: vote                    # or: moderator_decides
      keyword: "VOTE: AGREE"
      require: all                    # or: majority
    summary_agent: architect
    handoffs: [{ to: implementer }]
```

**Consensus methods:**

| Method | How It Works |
|---|---|
| `vote` | Each agent includes the keyword in their output. Consensus when `all` or `majority` agree. |
| `moderator_decides` | Only the `summary_agent` can declare consensus. |

**CLI output:**
```
── Round 1 ──
  [architect] I favor JWT for stateless scaling...
  [security] Token revocation concerns...
── Round 2 ──
  [architect] Hybrid approach. VOTE: AGREE  ✓
  [security] VOTE: AGREE  ✓
✓ Consensus reached (round 2)
```

Produces `transcript.md` meeting minutes. Mix freely: `batch → session → batch`.

### Chunk Decomposition

Break tasks into trackable work units. Agents manage chunks via output directives.

```yaml
- id: build_session
  type: session
  participants: [pm, dev]
  consensus:
    require_chunks_done: true         # All chunks must be done
  chunks:
    enabled: true
    initial:
      - "Set up project structure"
      - "Implement auth flow"
      - "Add unit tests"
```

**Agent directives:**
```
CHUNK_ADD: Implement drag-and-drop     → adds new chunk
CHUNK_DONE: C-001                      → marks chunk complete
CHUNK_REMOVE: C-003                    → removes chunk
```

Template variable `{{ chunks }}` injects a status table into prompts. Stored in `chunks.json`.

**CLI:**
```bash
aqm chunks list T-ABC123
aqm chunks add T-ABC123 "New feature"
aqm chunks done T-ABC123 C-001
aqm chunks remove T-ABC123 C-002
```

**Web API:** CRUD at `/api/tasks/{id}/chunks` with SSE `chunk_update` events.

### Context Strategy (Token Optimization)

Each agent has a `context_strategy` that controls what `{{ context }}` contains. Saves tokens by avoiding redundant context injection.

```yaml
agents:
  - id: architect
    runtime: claude
    context_strategy: own             # Only reads own notes
    system_prompt: |
      My notes: {{ context }}
      Discussion: {{ transcript }}

  - id: reviewer
    runtime: claude
    context_strategy: shared          # Only reads shared context.md

  - id: developer
    runtime: claude
    context_strategy: both            # Reads shared + own (default)
```

| Strategy | `{{ context }}` Contains | Use Case |
|---|---|---|
| `both` (default) | Shared context.md + agent's private notes | Full visibility, backward-compatible |
| `shared` | Shared context.md only | Agents that need full pipeline history |
| `own` | Agent's private `agent_{id}.md` only | Token-efficient for focused agents |

**File structure per task:**
```
.aqm/tasks/{task_id}/
├── context.md              # Shared (all stages, read by 'shared'/'both')
├── agent_architect.md      # Architect's private notes (read by 'own'/'both')
├── agent_developer.md      # Developer's private notes
├── transcript.md           # Session meeting minutes
├── chunks.json             # Chunk tracking
└── current_payload.md      # Last handoff payload
```

Every agent's output is written to **both** the shared `context.md` and their private `agent_{id}.md`. The `context_strategy` only controls what they **read**.

### Handoff Routing

Three strategies for task flow:

```yaml
# Static — fixed target
handoffs:
  - to: reviewer
    condition: always

# Fan-out — multiple targets in parallel
handoffs:
  - to: qa, docs, deploy
    condition: on_approve

# Agent-decided — agent picks target at runtime
handoffs:
  - to: "*"
    condition: auto    # Agent includes HANDOFF: <id> in output
```

**Conditions:** `always`, `on_approve`, `on_reject`, `on_pass`, `auto`, or expressions (`severity == critical`)

**Payload variables:** `{{ output }}`, `{{ input }}`, `{{ reject_reason }}`, `{{ gate_result }}`

### Gates (Quality Control)

```yaml
gate:
  type: llm              # LLM auto-evaluates → approved/rejected
  prompt: "Is this production-ready?"

gate:
  type: human            # Pauses pipeline → aqm approve/reject
```

### MCP Servers

Give agents real-world capabilities via [Model Context Protocol](https://modelcontextprotocol.io/).

```yaml
mcp:
  - server: github
  - server: filesystem
    args: ["/path/to/dir"]
  - server: custom-db
    command: node
    args: ["./mcp-server.js"]
    env: { DATABASE_URL: "postgres://..." }
```

### Params (Portable Pipelines)

```yaml
params:
  model: claude-sonnet-4-20250514
  project_path:
    type: string
    required: true
    prompt: "Project root path?"
    auto_detect: "Read package.json name"

agents:
  - id: dev
    model: ${{ params.model }}
```

**Override:** `aqm run "task" --param model=claude-opus-4-6`

**Priority:** CLI flags > params.yaml > interactive prompt > defaults

### Imports / Extends

```yaml
imports:
  - from: ./shared/reviewers.yaml
    agents: [security_reviewer]

agents:
  - id: base_reviewer
    abstract: true
    runtime: claude
    gate: { type: llm }

  - id: code_reviewer
    extends: base_reviewer
    system_prompt: "Review code: {{ input }}"
```

## CLI Reference

| Command | Description |
|---|---|
| `aqm init` | Interactive project setup (AI-generate, template, or pull) |
| `aqm run "task"` | Run pipeline (`--agent`, `--param`, `--priority`, `--parallel`, `--pipeline`) |
| `aqm fix <task_id> "text"` | Follow-up task with parent context |
| `aqm status [task_id]` | Task status (summary or detail) |
| `aqm list [--filter status]` | List tasks |
| `aqm approve <task_id>` | Approve human gate |
| `aqm reject <task_id> -r "reason"` | Reject human gate |
| `aqm cancel <task_id>` | Cancel task |
| `aqm priority <task_id> level` | Change priority |
| `aqm agents` | Show agent graph |
| `aqm context <task_id>` | View context.md |
| `aqm chunks list/add/done/remove` | Manage chunks |
| `aqm pipeline list/create/edit/default/delete` | Manage pipelines |
| `aqm serve` | Web dashboard (requires `pip install aqm[serve]`) |
| `aqm pull/publish/search` | Registry operations |
| `aqm validate` | Validate YAML against schema |

## agents.yaml Reference

### Agent Definition

| Field | Type | Default | Description |
|---|---|---|---|
| `id` | `string` | — | Unique identifier (required) |
| `type` | `"agent"` \| `"session"` | `"agent"` | Node type |
| `runtime` | `"claude"` \| `"gemini"` \| `"codex"` | — | Required for `type: agent` |
| `model` | `string` | CLI default | Model override |
| `system_prompt` | `string` | `""` | Jinja2 template: `{{ input }}`, `{{ context }}`, `{{ transcript }}`, `{{ chunks }}` |
| `context_strategy` | `"own"` \| `"shared"` \| `"both"` | `"both"` | What context to inject (token optimization) |
| `handoffs` | `list` | `[]` | Routing rules |
| `gate` | `object` | `null` | Quality gate (`llm` or `human`) |
| `mcp` | `list` | `[]` | MCP server connections |
| `claude_code_flags` | `list[string]` | `null` | Extra CLI flags for Claude |
| `abstract` | `boolean` | `false` | Template-only agent |
| `extends` | `string` | `null` | Parent agent ID |

### Session Fields (type: session)

| Field | Type | Default | Description |
|---|---|---|---|
| `participants` | `list[string]` | — | Agent IDs (required) |
| `turn_order` | `"round_robin"` \| `"moderator"` | `"round_robin"` | Turn ordering |
| `max_rounds` | `int` | `10` | Hard limit |
| `consensus.method` | `"vote"` \| `"moderator_decides"` | `"vote"` | How to detect agreement |
| `consensus.keyword` | `string` | `"VOTE: AGREE"` | Agreement signal |
| `consensus.require` | `"all"` \| `"majority"` | `"all"` | Threshold |
| `consensus.require_chunks_done` | `boolean` | `false` | Gate on chunk completion |
| `summary_agent` | `string` | `null` | Final summary producer |
| `chunks.enabled` | `boolean` | `true` | Enable chunk tracking |
| `chunks.initial` | `list[string]` | `[]` | Seed chunks |

## Architecture

```
aqm/
├── core/
│   ├── agent.py          # AgentDefinition, ConsensusConfig, ChunksConfig
│   ├── pipeline.py       # Pipeline loop + _run_session() + context strategy
│   ├── chunks.py         # Chunk model, ChunkManager, directive parser
│   ├── task.py           # Task, StageRecord, TaskStatus
│   ├── gate.py           # LLMGate / HumanGate
│   ├── context_file.py   # context.md + agent_{id}.md + transcript.md
│   ├── context.py        # Jinja2 prompt builder
│   └── project.py        # Project root detection
├── queue/
│   ├── sqlite.py         # SQLiteQueue (production)
│   └── file.py           # FileQueue (testing)
├── runtime/
│   ├── text.py           # Claude text-only
│   ├── claude_code.py    # Claude Code (with MCP)
│   ├── gemini.py         # Gemini CLI
│   └── codex.py          # Codex CLI
├── web/
│   ├── app.py            # FastAPI + SSE
│   └── api/              # REST + chunk + SSE endpoints
├── registry.py           # GitHub pipeline registry
└── cli.py                # Click CLI
```

## Comparison

| | LangGraph | CrewAI | OpenSWE | aqm |
|---|---|---|---|---|
| Pipeline definition | Python | Python | Code | **YAML** |
| Pipeline sharing | ❌ | Paid | ❌ | **Open registry** |
| Multi-agent discussion | ❌ | ❌ | ❌ | **Session nodes** |
| Task decomposition | ❌ | ❌ | ❌ | **Chunk tracking** |
| Context optimization | ❌ | ❌ | ❌ | **Per-agent context strategy** |
| Multi-LLM | Manual | Limited | ❌ | **Claude + Gemini + Codex** |
| Approve/Reject gate | Interrupt | ❌ | ❌ | **First-class** |
| Fan-out parallel | Manual | ❌ | ❌ | **Declarative** |
| File-based context | ❌ | ❌ | ❌ | **context.md + agent files** |
| Web dashboard | ❌ | Paid | ❌ | **Built-in** |

## Community

**[Discord](https://discord.gg/798f3rED)** | **[Registry](https://github.com/aqm-framework/registry)** | **[JSON Schema](schema/agents-schema.json)**

## Contributing

```bash
git clone https://github.com/aqm-framework/aqm
cd aqm
pip install -e ".[dev,serve]"
pytest tests/
```

Pipeline contributions are valued equally to code contributions. See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT
