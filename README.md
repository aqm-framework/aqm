# agent-queue

An orchestration framework where multiple AI agents pass tasks through **explicit queues** in sequence.

Build pipelines in YAML. Share them with anyone. Run them locally.

```
[planner] ──► [reviewer] ──approve──► [developer] ──► [qa]
                  │                                     │
                  └──reject──► [planner]    critical──► [planner]
```

## Why agent-queue?

Multi-agent frameworks have come in two flavors:

- **Code-based** (LangGraph, AutoGen) — hard to share pipelines
- **Cloud platforms** (CrewAI Studio, Vertex AI) — costly, vendor lock-in

agent-queue takes a different path:

**Declare pipelines in a single YAML file. Anyone can create them, anyone can use them — an open ecosystem.**

```bash
# Use a community-built pipeline instantly
agent-queue pull software-dev-pipeline

# Share your pipeline with the ecosystem
agent-queue publish my-pipeline
```

| Existing approach | agent-queue |
|---|---|
| Single agent does everything | Role separation per agent, handoff via queues |
| Direct calls between agents | Queue is the contract — loose coupling |
| Pipelines defined in code | Declared in YAML, shareable |
| Context lives only in memory | File-based context (`context.md`) — human-inspectable |
| Results always accepted | approve/reject gate is a first-class feature |
| External infrastructure required | SQLite default, runs after `pip install` |

## Install

```bash
pip install agent-queue
```

> Requires Python 3.11+.

## Quick Start

```bash
# Initialize in an existing project
cd my-project
agent-queue init

# Run a pipeline
agent-queue run "Add JWT authentication to login"

# Open the dashboard
agent-queue serve
# → http://localhost:8000
```

No Redis. No Docker. No cloud account. **Just SQLite.**

## How It Works

### 1. Declare agents and connections in YAML

```yaml
# .agent-queue/agents.yaml

agents:
  - id: planner
    name: Planning Agent
    model: claude-opus-4-6
    runtime: api
    mcp:
      - server: github
      - server: filesystem
    system_prompt: |
      You are a software planner.
      Analyze the requirements and write a detailed specification.
    handoffs:
      - to: reviewer
        task: review_spec
        condition: always

  - id: reviewer
    name: Review Agent
    model: claude-opus-4-6
    runtime: api
    system_prompt: |
      Review the specification. Decide approve or reject.
      If rejecting, always include the reason.
    handoffs:
      - to: developer
        task: implement
        condition: on_approve
      - to: planner
        task: revise_spec
        condition: on_reject
        payload: "{{ output }}\nREJECT_REASON: {{ reject_reason }}"

  - id: developer
    name: Development Agent
    runtime: claude_code
    mcp:
      - server: github
      - server: postgres
    handoffs:
      - to: qa
        task: test_implementation
        condition: always

  - id: qa
    name: QA Agent
    runtime: claude_code
    mcp:
      - server: browsertools
      - server: sentry
    handoffs:
      - to: planner
        task: rethink_spec
        condition: "severity == critical"
      - to: developer
        task: fix_bugs
        condition: "severity in [major, minor]"
```

### 2. Tasks move between agents through queues

Every task gets a unique ID (`T-A3F2B1`). Each agent picks up its assigned task from the queue, processes it, and passes the result to the next agent.

```
T-A3F2B1 created
  → [planner] writes specification
  → [reviewer] REJECTED — "missing security requirements"
  → [planner] revises spec (with reject reason)
  → [reviewer] APPROVED
  → [developer] implements code
  → [qa] tests pass → done
```

### 3. Context accumulates in files

Each time a task passes through an agent, the result is appended to `context.md`. The next agent reads this file to understand the full history.

```
.agent-queue/tasks/T-A3F2B1/
├── context.md           ← Full history (human-readable)
├── stage_01_planner.md
├── stage_02_reviewer.md
└── current_payload.md
```

### 4. MCP connects agents to the outside world

Attach MCP servers to any agent so it can **act**, not just generate text.

| Agent | MCP Connection | Capabilities |
|---|---|---|
| Developer | github, filesystem | Read/write files, create PRs |
| Designer | figma, slack | Read designs, send notifications |
| QA | browsertools, sentry | E2E browser tests, error lookup |
| Analyst | postgres, bigquery | Direct DB queries |

## CLI Reference

### `agent-queue init`

Initialize `.agent-queue/` in the current project directory.

```bash
agent-queue init
agent-queue init --path /path/to/project
```

Creates:
```
.agent-queue/
├── agents.yaml    ← Pipeline configuration (edit this)
├── tasks/         ← Task context directories (auto-generated)
└── queue.db       ← SQLite queue (auto-generated)
```

### `agent-queue run`

Create a task and run it through the pipeline.

```bash
# Basic usage
agent-queue run "Build a login feature"

# Specify starting agent
agent-queue run "Fix the payment bug" --agent developer

# With verbose logging
agent-queue -v run "Add user registration"
```

Output:
```
✓ Task created: T-A3F2B1
  Starting agent: planner

  stage 1 planner → Feature specification written...
  stage 2 reviewer → APPROVED...
  stage 3 developer → Code implemented...

✓ Completed T-A3F2B1
```

If a human gate is encountered:
```
⏸ Awaiting gate T-A3F2B1
  Proceed with 'agent-queue approve T-A3F2B1' or
  'agent-queue reject T-A3F2B1 -r "reason"'.
```

### `agent-queue status`

View task status.

```bash
# Summary of all tasks
agent-queue status

# Detailed view of a specific task
agent-queue status T-A3F2B1
```

Detailed output:
```
T-A3F2B1  Add JWT authentication
  Status: awaiting_gate
  Current agent: reviewer
  Created: 2026-03-24 10:22
  Stages: 2

  stage 1: planner
  stage 2: reviewer [rejected] (missing security requirements)
```

### `agent-queue list`

List tasks with optional status filtering.

```bash
# All tasks
agent-queue list

# Filter by status
agent-queue list --filter completed
agent-queue list --filter failed
agent-queue list --filter awaiting_gate
agent-queue list --filter pending
```

### `agent-queue approve`

Approve a task waiting at a human gate.

```bash
agent-queue approve T-A3F2B1
agent-queue approve T-A3F2B1 -r "Looks good, proceed with implementation"
```

### `agent-queue reject`

Reject a task waiting at a human gate. Reason is required.

```bash
agent-queue reject T-A3F2B1 -r "Missing error handling for edge cases"
```

### `agent-queue agents`

Display all agents and their handoff graph.

```bash
agent-queue agents
```

Output:
```
Agent Pipeline

  planner (Planning Agent) [api] (MCP: github, filesystem)
    → reviewer (always)

  reviewer (Review Agent) [api]
    gate: llm
    → developer (on_approve)
    → planner (on_reject)

  developer (Development Agent) [claude_code] (MCP: github)
    → qa (always)
```

### `agent-queue context`

Print the full `context.md` for a task.

```bash
agent-queue context T-A3F2B1
```

### `agent-queue serve`

Launch the local web dashboard.

```bash
agent-queue serve
agent-queue serve --port 3000
agent-queue serve --host 0.0.0.0 --port 8080
```

Requires: `pip install agent-queue[serve]`

Dashboard features:
- **Task list** — status, current agent, elapsed time
- **Agent diagram** — auto-generated connection graph from YAML
- **Task detail** — stage-by-stage input/output, gate results
- **Gate actions** — approve/reject buttons directly in the UI

### `agent-queue pull`

Pull a pipeline from the community registry.

```bash
agent-queue pull software-dev-pipeline
agent-queue pull legal-document-review
```

> Registry feature coming in v0.3.

### `agent-queue publish`

Share your pipeline to the registry.

```bash
agent-queue publish
agent-queue publish --name "my-pipeline" --description "Custom workflow"
```

> Registry feature coming in v0.3.

### `agent-queue search`

Search the pipeline registry.

```bash
agent-queue search "code review"
agent-queue search "content creation"
```

> Registry feature coming in v0.3.

## agents.yaml Reference

### Agent Definition

```yaml
agents:
  - id: planner                      # Unique ID (required)
    name: Planning Agent             # Display name (required)
    runtime: api                     # api | claude_code
    model: claude-sonnet-4-20250514  # Optional
    system_prompt: "..."             # Jinja2 template
    handoffs: [...]                  # Handoff rules
    gate: {...}                      # Gate config (optional)
    mcp:                             # MCP servers (optional)
      - server: github
      - server: filesystem
    claude_code_flags: [...]         # claude_code runtime only
```

### Runtime

| Value | Description |
|---|---|
| `api` | Calls Claude via the Anthropic API. Text input/output only. |
| `claude_code` | Runs Claude Code CLI as a subprocess. Can read/write files, execute code, use MCP tools. |

### Handoff

```yaml
handoffs:
  - to: reviewer
    task: review_spec
    condition: always          # always | on_approve | on_reject | on_pass | expression
    payload: "{{ output }}"    # Jinja2 template
```

**Condition options:**
- `always` — Always hand off
- `on_approve` — Only when gate approves
- `on_reject` — Only when gate rejects
- `on_pass` — When there is no gate, or when approved
- Expression — `"severity == critical"`, `"severity in [major, minor]"`

**Payload template variables:**
- `{{ output }}` — Current agent's output
- `{{ input }}` — Current agent's input
- `{{ reject_reason }}` — Gate rejection reason
- `{{ gate_result }}` — `approved` or `rejected`

### Gate

```yaml
gate:
  type: llm              # llm | human
  prompt: "Criteria..."  # Additional prompt for LLM gate
  model: claude-sonnet-4-20250514
```

| Type | Description |
|---|---|
| `llm` | Claude automatically evaluates output quality. Returns `approved`/`rejected` as JSON. |
| `human` | Pipeline pauses until a human approves/rejects via CLI or web UI. |

### MCP Servers

```yaml
mcp:
  - server: github             # Simple format — name only
  - server: filesystem
    args: ["/path/to/dir"]     # Additional arguments
  - server: custom             # Custom server
    command: node
    args: ["./my-server.js"]
    env:
      API_KEY: "..."
```

When only `server` name is provided, it auto-resolves to:
`npx -y @modelcontextprotocol/server-{name}`

## Pipelines for Any Domain

| Domain | Pipeline Example |
|---|---|
| Software Dev | Planning → Review → Implementation → QA → PR |
| Content Creation | Research → Draft → Edit → Publish |
| Legal Documents | Extract → Summarize → Risk Flag → Approval |
| Data Analysis | Collect → Clean → Analyze → Report |
| Customer Support | Classify → Lookup → Respond → Escalate |

## Comparison with Existing Frameworks

| | LangGraph | CrewAI | OpenSWE | agent-queue |
|---|---|---|---|---|
| Pipeline definition | Python code | Python code | Code | **YAML** |
| Pipeline sharing | ❌ | Paid platform | ❌ | **Open registry** |
| Explicit queue | ❌ | ❌ | ❌ | **SQLite default** |
| Approve/Reject gate | Interrupt pattern | ❌ | ❌ | **First-class** |
| Reverse feedback loop | Manual | Limited | ❌ | **Built-in** |
| File-based context | ❌ | ❌ | ❌ | **context.md** |
| MCP agent connection | Manual | ❌ | ❌ | **Declarative** |
| Local/offline | ❌ | ❌ | ❌ | **Default** |

## Design Principles

- **The ecosystem is the product** — The YAML sharing ecosystem is the core value, not the execution engine
- **Queue is the contract** — No direct calls between agents. Communication only through queues
- **Context is a file** — Accumulated in human-readable `context.md`
- **Local first** — SQLite default. Runs without external infrastructure
- **Declarative first** — Define in YAML, code is the escape hatch
- **Gate is first-class** — approve/reject is a core feature

## Architecture

```
agent-queue/
├── agent_queue/
│   ├── core/
│   │   ├── task.py           # Task, StageRecord, TaskStatus
│   │   ├── agent.py          # AgentDefinition, agents.yaml parsing
│   │   ├── pipeline.py       # Pipeline execution loop
│   │   ├── gate.py           # LLMGate / HumanGate
│   │   ├── context_file.py   # File-based context accumulation
│   │   ├── context.py        # Prompt builder
│   │   └── project.py        # Project root detection
│   ├── queue/
│   │   ├── base.py           # AbstractQueue interface
│   │   ├── sqlite.py         # SQLiteQueue (default)
│   │   └── file.py           # FileQueue (testing)
│   ├── runtime/
│   │   ├── base.py           # AbstractRuntime interface
│   │   ├── api.py            # Claude API runtime
│   │   └── claude_code.py    # Claude Code CLI runtime
│   ├── web/
│   │   └── app.py            # FastAPI web dashboard
│   └── cli.py                # Click CLI
├── examples/
│   ├── software-pipeline/
│   ├── content-pipeline/
│   └── data-analysis-pipeline/
└── tests/
    └── test_pipeline.py
```

## Roadmap

### v0.1 — Core
- [x] SQLite-based task queue
- [x] YAML agent declarations
- [x] Handoff routing (with conditions)
- [x] LLM / Human approval gates
- [x] File-based context accumulation (context.md)
- [x] Claude Code runtime with MCP support
- [x] Local web UI dashboard

### v0.2 — Connections
- [ ] Enhanced per-agent MCP server support
- [ ] GitHub / Slack webhook triggers
- [ ] Context summarization (prevent token explosion)

### v0.3 — Ecosystem
- [ ] Pipeline registry (`agent-queue publish / pull / search`)
- [ ] registry.agent-queue.dev launch
- [ ] YAML version control and forking

### v1.0 — Stabilization
- [ ] Task dependencies (DAG)
- [ ] Redis / Postgres queue backends
- [ ] Full web dashboard with real-time updates

## Contributing

```bash
git clone https://github.com/smoveth/agent-queue
cd agent-queue
pip install -e ".[dev]"
pytest tests/
```

Creating and sharing pipelines is also a great contribution.

## License

MIT
