# agent-queue

An orchestration framework where multiple AI agents pass tasks through **explicit queues** in sequence.

Build pipelines in YAML. Share them with anyone. Run them locally.

```
                 reject                          critical
          ┌──────────────────────┐       ┌──────────────────┐
          ▼                      │       ▼                  │
      [planner] ──► [reviewer] ──┴─approve──► [developer] ──► [qa]
```

## Powered by Claude Code

agent-queue uses **[Claude Code](https://docs.anthropic.com/en/docs/claude-code)** (Anthropic's CLI) as the underlying LLM runtime. Both `api` and `claude_code` runtimes invoke the `claude` CLI as a subprocess — no API key configuration or SDK setup required.

- **`api` runtime** — Calls `claude -p <prompt> --print` for pure text generation (planning, reviewing, summarizing)
- **`claude_code` runtime** — Runs Claude Code CLI with full tool access (file read/write, code execution, MCP tools)
- **LLM Gate** — Also uses the Claude CLI for automatic approve/reject evaluation

> **Prerequisite:** Install Claude Code CLI and authenticate before using agent-queue.
> ```bash
> npm install -g @anthropic-ai/claude-code
> claude login
> ```

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

## agents.yaml Complete Reference

The `agents.yaml` file is the single source of truth for your pipeline. It defines all agents, their roles, how they connect, and how tasks flow between them.

### Top-Level Structure

```yaml
# .agent-queue/agents.yaml
agents:
  - id: ...      # First agent (pipeline entry point)
  - id: ...      # Second agent
  - id: ...      # ...
```

The file has a single top-level key `agents` containing a list of agent definitions. The **first agent** in the list is used as the default starting point when running `agent-queue run`.

---

### Agent Definition

Each agent supports the following fields:

```yaml
agents:
  - id: planner                      # (required) Unique identifier, used in handoff routing
    name: Planning Agent             # (required) Display name for CLI output and dashboard
    runtime: api                     # (optional) api | claude_code — default: api
    model: claude-sonnet-4-20250514  # (optional) Model to use, omit for CLI default
    system_prompt: |                 # (optional) Jinja2 template for the system prompt
      You are a software planner.
      Analyze: {{ input }}
    handoffs:                        # (optional) List of handoff rules
      - to: reviewer
        task: review_spec
        condition: always
    gate:                            # (optional) Quality gate configuration
      type: llm
      prompt: "Is this ready?"
    mcp:                             # (optional) MCP server connections
      - server: github
    claude_code_flags:               # (optional) Extra CLI flags, claude_code runtime only
      - "--allowedTools"
      - "Edit,Write,Bash,Read"
```

#### Field Reference

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `id` | `string` | **Yes** | — | Unique identifier. Used as handoff target. Must not duplicate. |
| `name` | `string` | **Yes** | — | Human-readable display name. |
| `runtime` | `"api"` \| `"claude_code"` | No | `"api"` | Execution runtime. See [Runtime](#runtime) section. |
| `model` | `string` | No | CLI default | Claude model ID (e.g. `claude-opus-4-6`, `claude-sonnet-4-20250514`). |
| `system_prompt` | `string` | No | `""` | Jinja2 template. Available variables: `{{ input }}`, `{{ output }}`. |
| `handoffs` | `list[Handoff]` | No | `[]` | Where to send results after this agent completes. |
| `gate` | `GateConfig` | No | `null` | Quality gate evaluated before handoff routing. |
| `mcp` | `list[MCPServer]` | No | `[]` | MCP servers to attach to this agent. |
| `claude_code_flags` | `list[string]` | No | `null` | Additional CLI flags passed to `claude`. Only used with `claude_code` runtime. |

---

### Runtime

| Value | Description | Use Case |
|---|---|---|
| `api` | Runs `claude -p <prompt> --print`. Text-only, no tool access. | Planning, reviewing, summarizing, analysis |
| `claude_code` | Runs Claude Code CLI with full tool access. Can read/write files, execute shell commands, use MCP tools. | Implementation, testing, file manipulation |

Both runtimes invoke the `claude` CLI as a subprocess. The difference is that `api` mode disables tool use, while `claude_code` mode enables full Claude Code capabilities.

**model values** — Any valid Claude model ID:
- `claude-opus-4-6` — Most capable, best for complex reasoning
- `claude-sonnet-4-20250514` — Balanced speed and quality (recommended default)
- `claude-haiku-4-5-20251001` — Fastest, best for simple tasks

---

### Handoff

Handoffs define how tasks flow from one agent to the next.

```yaml
handoffs:
  - to: reviewer             # (required) Target agent ID
    task: review_spec        # (optional) Task label, default: ""
    condition: always        # (optional) When to trigger, default: "always"
    payload: "{{ output }}"  # (optional) Data to pass, default: "{{ output }}"
```

#### Handoff Fields

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `to` | `string` | **Yes** | — | Target agent `id`. Must exist in the agents list. |
| `task` | `string` | No | `""` | Label describing what the next agent should do. |
| `condition` | `string` | No | `"always"` | When this handoff triggers. See conditions below. |
| `payload` | `string` | No | `"{{ output }}"` | Jinja2 template for data passed to the next agent. |

#### Condition Values

| Condition | Triggers When |
|---|---|
| `always` | Always triggers (no gate needed) |
| `on_approve` | Gate decision is `approved` |
| `on_reject` | Gate decision is `rejected` |
| `on_pass` | No gate exists, or gate approved |
| Custom expression | Expression evaluates to true (e.g. `"severity == critical"`, `"severity in [major, minor]"`) |

#### Payload Template Variables

| Variable | Description |
|---|---|
| `{{ output }}` | Current agent's output text |
| `{{ input }}` | Current agent's input text |
| `{{ reject_reason }}` | Gate rejection reason (empty if approved) |
| `{{ gate_result }}` | Gate decision: `"approved"` or `"rejected"` |

**Example — Passing reject reason back to planner:**
```yaml
handoffs:
  - to: planner
    task: revise_spec
    condition: on_reject
    payload: "REJECTED: {{ reject_reason }}\nOriginal plan: {{ output }}"
```

---

### Gate

Gates evaluate an agent's output before handoff routing occurs. If a gate exists, handoff conditions like `on_approve` / `on_reject` are resolved based on the gate result.

```yaml
gate:
  type: llm                          # (required) llm | human
  prompt: "Is this plan actionable?" # (optional) Extra evaluation criteria for LLM gate
  model: claude-sonnet-4-20250514    # (optional) Model for LLM gate evaluation
```

#### Gate Fields

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `type` | `"llm"` \| `"human"` | No | `"llm"` | Gate type. |
| `prompt` | `string` | No | `""` | Additional evaluation criteria. Jinja2 template (variables: `{{ output }}`, `{{ input }}`). |
| `model` | `string` | No | `claude-sonnet-4-20250514` | Model used for LLM gate evaluation. |

#### Gate Types

| Type | Behavior |
|---|---|
| `llm` | Claude CLI automatically evaluates the output and returns `{"decision": "approved"/"rejected", "reason": "..."}`. Pipeline continues immediately. |
| `human` | Pipeline **pauses** and waits for manual approval. Resume with `agent-queue approve <task-id>` or `agent-queue reject <task-id> -r "reason"`. |

---

### MCP Servers

Attach [Model Context Protocol](https://modelcontextprotocol.io/) servers to give agents real-world capabilities.

```yaml
mcp:
  # Simple format — auto-resolves to npx -y @modelcontextprotocol/server-{name}
  - server: github

  # With arguments
  - server: filesystem
    args: ["/path/to/dir"]

  # Custom server with full configuration
  - server: custom-db
    command: node
    args: ["./my-mcp-server.js"]
    env:
      DATABASE_URL: "postgres://..."
      API_KEY: "sk-..."
```

#### MCP Server Fields

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `server` | `string` | **Yes** | — | Server name. Used as identifier and for auto-resolution. |
| `command` | `string` | No | `"npx"` | Command to launch the server. |
| `args` | `list[string]` | No | `[]` | Arguments passed to the command. |
| `env` | `dict[string, string]` | No | `null` | Environment variables for the server process. |

**Auto-resolution:** When only `server` name is provided (no `command`), it resolves to:
```
npx -y @modelcontextprotocol/server-{name} [args...]
```

**Common MCP servers:**

| Server Name | Capabilities |
|---|---|
| `github` | Read/write repos, create PRs, manage issues |
| `filesystem` | Read/write local files and directories |
| `postgres` | Execute SQL queries against PostgreSQL |
| `slack` | Send messages, read channels |
| `browsertools` | Browser automation, E2E testing |
| `sentry` | Error tracking and lookup |

---

### claude_code_flags

Extra CLI flags passed directly to the `claude` command. Only applies when `runtime: claude_code`.

```yaml
claude_code_flags:
  - "--allowedTools"
  - "Edit,Write,Bash,Read"
```

This is useful for restricting which tools an agent can use, or passing other Claude Code CLI options.

---

### Complete Example

```yaml
# .agent-queue/agents.yaml
agents:
  - id: planner
    name: Planning Agent
    runtime: api
    model: claude-sonnet-4-20250514
    system_prompt: |
      You are a software planner.
      Analyze the requirements and write a detailed specification.
      Requirements: {{ input }}
    handoffs:
      - to: reviewer
        task: review_spec
        condition: always
        payload: "{{ output }}"

  - id: reviewer
    name: Review Agent
    runtime: api
    model: claude-sonnet-4-20250514
    system_prompt: |
      Review this specification. Decide approve or reject.
      If rejecting, always include the reason.
      Spec: {{ input }}
    gate:
      type: llm
      prompt: "Is this spec clear, complete, and ready for implementation?"
      model: claude-sonnet-4-20250514
    handoffs:
      - to: developer
        task: implement
        condition: on_approve
        payload: "{{ output }}"
      - to: planner
        task: revise_spec
        condition: on_reject
        payload: "REJECTED: {{ reject_reason }}\nOriginal: {{ output }}"

  - id: developer
    name: Development Agent
    runtime: claude_code
    model: claude-sonnet-4-20250514
    mcp:
      - server: filesystem
        args: ["/path/to/project"]
    system_prompt: |
      Implement the approved specification.
      Plan: {{ input }}
    claude_code_flags:
      - "--allowedTools"
      - "Edit,Write,Bash,Read"
    handoffs:
      - to: qa
        task: test_implementation
        condition: always

  - id: qa
    name: QA Agent
    runtime: claude_code
    mcp:
      - server: browsertools
    gate:
      type: human
    handoffs:
      - to: planner
        task: rethink_spec
        condition: "severity == critical"
      - to: developer
        task: fix_bugs
        condition: "severity in [major, minor]"
```

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
