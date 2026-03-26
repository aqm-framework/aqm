# aqm &nbsp;|&nbsp; [한국어](docs/README.ko.md)

**Build AI agent teams in YAML. No code. No API keys. Just pipelines.**

An orchestration framework where multiple AI agents pass tasks through **explicit queues** — or **discuss in real-time sessions** until consensus. Define once, run anywhere, share with anyone.

```
  [user] ──input──► [planner] ──► [reviewer] ──approve──► [design_session] ──► [implementer]
                        ▲              │                    ┌──┬──┬──┐
                        └── reject ────┘                    ▼  ▼  ▼  ▼  round-robin
                        └── ask user ──►[user]             [arch][sec][fe]  until consensus
```

## Why aqm?

A single AI agent writes code and reviews it with the **same bias**. It can't catch its own blind spots.

aqm gives you a **team** — each agent has a dedicated role, a separate prompt, and optionally a different LLM. A quality gate rejects bad output automatically. A session lets agents debate before deciding.

```yaml
# One YAML file. That's the entire pipeline.
agents:
  - id: developer
    runtime: claude
    system_prompt: "Implement: {{ input }}"
    handoffs: [{ to: reviewer }]

  - id: reviewer
    runtime: gemini                    # Different LLM catches different bugs
    system_prompt: "Review for security: {{ input }}"
    gate:
      type: llm
      prompt: "Is this production-ready?"
      max_retries: 3                   # Auto-reject → retry up to 3 times
    handoffs:
      - { to: deployer, condition: on_approve }
      - { to: developer, condition: on_reject }

  - id: deployer
    runtime: claude
    context_strategy: none             # 85% token savings — no context needed
    system_prompt: "Deploy: {{ input }}"
```

```bash
pip install aqm && aqm init && aqm run "Add JWT authentication"
```

### What makes aqm different

| Problem | Single Agent | aqm |
|---|---|---|
| Same LLM reviews its own code | One bias, one perspective | **Cross-LLM verification** (Claude writes, Gemini reviews) |
| No forced quality checks | Agent says "looks good" to itself | **Quality gates** auto-reject and retry |
| Context window explodes at scale | Everything in one conversation | **5 context strategies** — 55-85% token savings |
| Can't standardize team processes | Every run is ad-hoc | **YAML pipelines** — version-controlled, shareable |
| Expensive API costs | Per-token API billing adds up | **CLI-based** — uses your existing CLI subscriptions, no extra API fees |
| Setup overhead | API keys, SDKs, env configs | **Zero config** — uses CLI tools you already have |

## Install

```bash
pip install aqm
```

> Requires Python 3.11+. At least one LLM CLI must be installed:

| Runtime | Provider | Install |
|---|---|---|
| `claude` | Anthropic | `npm i -g @anthropic-ai/claude-code && claude login` |
| `gemini` | Google | `npm i -g @google/gemini-cli` |
| `codex` | OpenAI | `npm i -g @openai/codex` |

No API keys or SDK setup needed — aqm runs CLI tools as subprocesses. You pay for the CLI subscriptions you already have, not per-token API fees.

## Quick Start

```bash
cd my-project
aqm init                              # Interactive setup wizard
aqm run "Add JWT authentication"       # Run pipeline
aqm serve                              # Web dashboard at localhost:8000
```

## Real-World Examples

### Example 1: Code Review Pipeline

Every PR goes through planning, implementation, review, and testing — automatically.

```yaml
agents:
  - id: planner
    runtime: gemini
    system_prompt: "Break this into implementation steps: {{ input }}"
    handoffs: [{ to: developer }]

  - id: developer
    runtime: claude
    mcp: [{ server: github }]
    system_prompt: "Implement the plan: {{ input }}"
    handoffs: [{ to: reviewer }]

  - id: reviewer
    runtime: gemini                    # Different LLM = different perspective
    system_prompt: "Review for bugs and security issues: {{ input }}"
    gate:
      type: llm
      prompt: "Is this code production-ready? Check OWASP Top 10."
      max_retries: 3
    handoffs:
      - { to: qa, condition: on_approve }
      - { to: developer, condition: on_reject }

  - id: qa
    runtime: claude
    context_strategy: last_only        # Only needs reviewer's output → 55% fewer tokens
    system_prompt: "Write tests for: {{ input }}"
```

```bash
aqm run "Add user preferences with database, API, and frontend"
```

### Example 2: Architecture Decision Session

Multiple experts debate until they agree — like a real design meeting.

```yaml
agents:
  - id: architect
    runtime: claude
    system_prompt: |
      You are a software architect. Discuss: {{ input }}
      Previous discussion: {{ transcript }}

  - id: security
    runtime: gemini
    system_prompt: |
      You are a security expert. Focus on threats: {{ input }}
      Previous discussion: {{ transcript }}

  - id: design_session
    type: session
    participants: [architect, security]
    max_rounds: 5
    consensus:
      method: vote
      keyword: "VOTE: AGREE"
      require: all
    summary_agent: architect
    handoffs: [{ to: developer }]
```

```
── Round 1 ──
  [architect] JWT for stateless scaling. Token rotation every 15min...
  [security] Token revocation is the weak point. Consider hybrid...
── Round 2 ──
  [architect] Agreed — hybrid with Redis blacklist. VOTE: AGREE  ✓
  [security] Redis approach works. VOTE: AGREE  ✓
✓ Consensus reached (round 2)
```

### Example 3: Human-in-the-Loop Deployment

AI does the work, but humans approve the critical steps.

```yaml
agents:
  - id: developer
    runtime: claude
    human_input:
      mode: before
      prompt: "What features do you want? Any constraints?"
    system_prompt: "Build: {{ input }}"
    handoffs: [{ to: deployer }]

  - id: deployer
    runtime: claude
    gate: { type: human }              # Pipeline pauses for manual approval
    system_prompt: "Deploy: {{ input }}"
```

```bash
aqm run "Refactor auth module"
# → Developer asks for your input first
# → After coding, pipeline pauses at deployer
aqm approve T-ABC123 -r "LGTM, deploy to staging"
```

## Features

### Multi-LLM Runtimes

Mix providers per agent. Claude writes code, Gemini reviews it, Codex tests it.

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
  - id: planner
    context_strategy: both            # Full visibility (default)

  - id: developer
    context_strategy: last_only       # Only previous stage → 55% savings
    context_window: 1

  - id: deployer
    context_strategy: none            # No context → 85% savings
```

| Strategy | `{{ context }}` Contains | Token Savings | Use Case |
|---|---|---|---|
| `both` (default) | Shared context.md + agent's private notes | — | Full visibility, backward-compatible |
| `shared` | Smart-windowed shared context.md | ~same | Agents that need pipeline history |
| `last_only` | Only the most recent stage output | **~55%** | Agents that only need the previous step |
| `own` | Agent's private `agent_{id}.md` only | **~85%** | Focused agents with their own notes |
| `none` | Empty (no context injected) | **~85%** | Self-contained agents with no context needed |

Benchmarked on a 10-agent pipeline (see `tests/bench_token_efficiency.py`):
```
Strategy      Total Tokens   Savings
both              12,233        0%
last_only          5,504       55%
none               1,873       85%
```

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

### Human Input (Human-in-the-Loop)

```yaml
agents:
  - id: planner
    human_input:
      mode: before           # Ask before agent runs
      prompt: "What specific features do you want?"

  - id: developer
    human_input: true        # Shorthand: agent can ask mid-execution via HUMAN_INPUT: <question>
```

**Modes:**

| Mode | Behavior |
|---|---|
| `before` | Always pause and ask the user before the agent runs. |
| `on_demand` | Agent requests input via `HUMAN_INPUT: <question>` directives in output. |
| `both` | Combines both modes. |

### Gates (Quality Control)

```yaml
gate:
  type: llm              # LLM auto-evaluates → approved/rejected
  prompt: "Is this production-ready?"
  max_retries: 3         # Reject → retry up to 3 times, then fail

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

agents:
  - id: dev
    model: ${{ params.model }}
```

**Override:** `aqm run "task" --param model=claude-opus-4-6`

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

### Pipeline Registry (Share & Discover)

```bash
aqm search "code review"              # Find community pipelines
aqm pull security-audit               # Install in one command
aqm publish --name my-pipeline        # Share yours
```

## CLI Reference

```bash
# Setup
aqm init                              # Interactive setup wizard
aqm validate                          # Validate agents.yaml
aqm agents                            # Show agent graph

# Run
aqm run "Add JWT auth"                # Run default pipeline
aqm run "Fix bug" --agent bug_fixer   # Start from specific agent
aqm run "Build API" --pipeline backend # Named pipeline
aqm run "Task" --param model=opus     # Override parameters

# Manage
aqm list                              # List all tasks
aqm status T-ABC123                   # Task details
aqm cancel T-ABC123                   # Cancel task
aqm fix T-ABC123 "Fix the color"      # Follow-up with context

# Gates & Human Input
aqm approve T-ABC123                  # Approve gate
aqm reject T-ABC123 -r "Needs tests" # Reject gate
aqm human-input T-ABC123 "response"   # Answer agent's question

# Chunks
aqm chunks list T-ABC123              # Status table
aqm chunks done T-ABC123 C-001        # Mark done

# Pipelines
aqm pipeline list                     # List pipelines
aqm pipeline create review --ai       # AI-generate
aqm pipeline default review           # Set default

# Registry
aqm search "code review"              # Search
aqm pull code-review-pipeline         # Install
aqm publish --name my-pipeline        # Share

# Dashboard
aqm serve                             # Web UI at localhost:8000
```

## agents.yaml Reference

### Entry Point (Auto-Routing)

```yaml
entry_point: auto    # LLM picks the best agent based on user input
# entry_point: first  # (default) Always start with the first agent
```

### Agent Definition

| Field | Type | Default | Description |
|---|---|---|---|
| `id` | `string` | — | Unique identifier (required) |
| `name` | `string` | `""` | Display name (auto-generated from id if empty) |
| `type` | `"agent"` \| `"session"` | `"agent"` | Node type |
| `runtime` | `"claude"` \| `"gemini"` \| `"codex"` | — | Required for `type: agent` |
| `model` | `string` | CLI default | Model override |
| `system_prompt` | `string` | `""` | Jinja2 template: `{{ input }}`, `{{ context }}`, `{{ transcript }}`, `{{ chunks }}` |
| `context_strategy` | `"none"` \| `"last_only"` \| `"own"` \| `"shared"` \| `"both"` | `"both"` | What context to inject (token optimization) |
| `context_window` | `int` | `3` | Recent stages in full; older stages summarized (0 = all) |
| `human_input` | `boolean` \| `object` | `null` | Human-in-the-loop input (`before`, `on_demand`, `both`) |
| `handoffs` | `list[Handoff]` | `[]` | Routing rules |
| `gate` | `object` | `null` | Quality gate |
| `mcp` | `list[MCPServer]` | `[]` | MCP server connections |
| `claude_code_flags` | `list[string]` | `null` | Extra CLI flags for Claude |
| `abstract` | `boolean` | `false` | Template-only agent (not executed) |
| `extends` | `string` | `null` | Parent agent ID for inheritance |

### Handoff Fields

| Field | Type | Default | Description |
|---|---|---|---|
| `to` | `string` | — | Target agent ID, or comma-separated for fan-out (`"qa, docs"`) |
| `task` | `string` | `""` | Task name label |
| `condition` | `string` | `"always"` | `always`, `on_approve`, `on_reject`, `on_pass`, `auto`, or expression |
| `payload` | `string` | `"{{ output }}"` | Jinja2 template: `{{ output }}`, `{{ input }}`, `{{ reject_reason }}`, `{{ gate_result }}` |

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

### config.yaml Reference

Project-level configuration at `.aqm/config.yaml`. All fields are optional.

```yaml
pipeline:
  max_stages: 20
gate:
  model: claude-sonnet-4-20250514
  timeout: 120
timeouts:
  claude: 600
  gemini: 600
  codex: 600
```

## Comparison

| | LangGraph | CrewAI | AutoGen | aqm |
|---|---|---|---|---|
| Pipeline definition | Python | Python + YAML | Python | **YAML only** |
| Pipeline sharing | ❌ | Paid | ❌ | **Open registry** |
| Multi-agent discussion | ❌ | ❌ | Group chat | **Session nodes + consensus voting** |
| Task decomposition | ❌ | ❌ | ❌ | **Chunk tracking** |
| Context optimization | ❌ | Auto-summarize | ❌ | **5 strategies (55-85% savings)** |
| Multi-LLM | LangChain | LiteLLM | Multiple | **CLI subprocess (no API keys)** |
| Cost model | Per-token API | Per-token API | Per-token API | **CLI subscription (no extra fees)** |
| Human-in-the-loop | Middleware | Webhooks | HumanProxy | **First-class per-agent config** |
| Quality gates | ❌ | Callbacks | ❌ | **LLM + Human gates** |
| Auto entry routing | ❌ | ❌ | ❌ | **LLM-based `entry_point: auto`** |
| Fan-out parallel | Manual | Manual | ❌ | **Declarative** |
| Real-time streaming | ❌ | ❌ | ❌ | **Token-level SSE** |
| Web dashboard | Paid | Paid | ❌ | **Built-in (free)** |
| Stars | 27k | 44k | 54k | New |

## Architecture

```
aqm/
├── core/
│   ├── agent.py          # AgentDefinition, ConsensusConfig, ChunksConfig, HumanInputConfig
│   ├── pipeline.py       # Pipeline loop + _run_session() + context strategy
│   ├── chunks.py         # Chunk model, ChunkManager, directive parser
│   ├── task.py           # Task, StageRecord, TaskStatus
│   ├── gate.py           # LLMGate / HumanGate
│   ├── context_file.py   # context.md + agent_{id}.md + transcript.md + smart windowing
│   ├── context.py        # Jinja2 prompt builder
│   ├── config.py         # ProjectConfig (.aqm/config.yaml)
│   └── project.py        # Project root detection
├── queue/
│   ├── base.py           # AbstractQueue interface
│   ├── sqlite.py         # SQLiteQueue (production)
│   └── file.py           # FileQueue (testing)
├── runtime/
│   ├── base.py           # AbstractRuntime interface
│   ├── claude_code.py    # Claude Code (with MCP, token streaming)
│   ├── gemini.py         # Gemini CLI
│   └── codex.py          # Codex CLI
├── web/
│   ├── app.py            # FastAPI app factory
│   ├── templates.py      # Shared CSS/layout/helpers
│   ├── pages/            # Page renderers (dashboard, agents, registry, validate, task_detail)
│   └── api/              # REST + chunk + SSE + human input endpoints
├── registry.py           # GitHub pipeline registry
└── cli.py                # Click CLI
```

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
