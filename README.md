# aqm

An orchestration framework where multiple AI agents pass tasks through **explicit queues** — or **discuss in real-time sessions** until consensus.

Build pipelines in YAML. Share them with anyone. Run them locally.

**[한국어 문서 (Korean)](docs/README.ko.md)**

```
  [user] ──input──► [planner] ──► [reviewer] ──approve──► [design_session] ──► [implementer]
                        ▲              │                    ┌──┬──┬──┐
                        └── reject ────┘                    ▼  ▼  ▼  ▼  round-robin
                        └── ask user ──►[user]             [arch][sec][fe]  until consensus
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

Claude always runs in **Code mode** — MCP servers and tools are available when configured.

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

### Human Input (Human-in-the-Loop)

Agents can request input from humans during pipeline execution — for clarifying requirements, gathering feedback, or making decisions that need human judgment.

```yaml
agents:
  - id: planner
    runtime: claude
    human_input:
      enabled: true
      mode: before           # Ask before agent runs
      prompt: "What specific features do you want? Any design preferences?"
    system_prompt: |
      Plan the project based on the user's requirements.
      {{ input }}

  - id: developer
    runtime: claude
    human_input: true        # Shorthand for on_demand mode
    system_prompt: |
      Implement the plan. If you need clarification, use:
      HUMAN_INPUT: <your question here>
      {{ input }}
```

**Modes:**

| Mode | Behavior |
|---|---|
| `before` | Always pause and ask the user before the agent runs. Good for requirements gathering. |
| `on_demand` | Agent requests input via `HUMAN_INPUT: <question>` directives in output. Good for mid-execution clarification. |
| `both` | Combines both modes. |

**Shorthand formats:**
```yaml
human_input: true              # Same as { enabled: true, mode: on_demand }
human_input: "before"          # Same as { enabled: true, mode: before }
human_input:
  enabled: true
  mode: before
  prompt: "Custom question"    # Shown to user in 'before' mode
```

Human responses are recorded in both `context.md` (shared) and `agent_{id}.md` (private), so all agents can see what the user said.

**Web dashboard** shows a cyan input panel when an agent needs input. **CLI:** responses via `aqm human-input <task_id> "response"`.

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

### Project Setup

```bash
aqm init                          # Interactive: [1] AI-generate [2] Template [3] Pull from registry
aqm init --path ./my-project      # Initialize in a specific directory
aqm validate                      # Validate agents.yaml against schema
aqm validate --pipeline review    # Validate a specific pipeline
aqm agents                        # Show agent graph and connections
```

### Running Pipelines

```bash
aqm run "Add JWT auth"                          # Run default pipeline
aqm run "Fix login bug" --agent bug_fixer       # Start from a specific agent
aqm run "Build API" --pipeline backend          # Run a named pipeline
aqm run "Deploy" --priority critical            # Set priority (critical|high|normal|low)
aqm run "Test" --param model=claude-opus-4-6    # Override pipeline parameters
aqm run "Task" --parallel                       # Run in parallel with other tasks
```

### Task Management

```bash
aqm list                          # List all tasks
aqm list --filter completed       # Filter by status (pending|in_progress|completed|failed|cancelled)
aqm status T-ABC123               # Detailed task status with stage history
aqm context T-ABC123              # View full context.md for a task
aqm priority T-ABC123 high        # Change priority (critical|high|normal|low)
aqm cancel T-ABC123               # Cancel a running or pending task
aqm fix T-ABC123 "Fix the color"  # Follow-up task with parent context carried over
```

### Gates & Human Input

```bash
aqm approve T-ABC123              # Approve human gate (resumes pipeline)
aqm approve T-ABC123 -r "LGTM"   # Approve with reason
aqm reject T-ABC123 -r "Needs tests"   # Reject human gate (reason required)
aqm human-input T-ABC123 "Use PostgreSQL and dark mode"   # Respond to agent's question
```

### Chunks (Work Units)

```bash
aqm chunks list T-ABC123          # Show chunk status table
aqm chunks add T-ABC123 "Add error handling"    # Add a new chunk
aqm chunks done T-ABC123 C-001    # Mark chunk as done
aqm chunks remove T-ABC123 C-002  # Remove a chunk
```

### Pipeline Management

```bash
aqm pipeline list                 # List all pipelines (shows ★ for default)
aqm pipeline create review        # Create new pipeline (interactive)
aqm pipeline create review --ai   # AI-generate pipeline
aqm pipeline create review --template   # Create from template
aqm pipeline edit review          # Edit pipeline with AI assistance
aqm pipeline default review       # Set default pipeline
aqm pipeline delete review        # Delete a pipeline
```

### Registry (Share & Discover)

```bash
aqm search                        # List all available pipelines
aqm search "code review"          # Search by keyword
aqm search --offline              # Search local registry only
aqm pull code-review-pipeline     # Install pipeline from registry
aqm pull my-pipeline --repo org/registry   # Pull from custom registry
aqm publish --name my-pipeline    # Publish to GitHub registry (creates PR)
aqm publish --local               # Save to local registry only
```

### Web Dashboard

```bash
aqm serve                         # Start at localhost:8000
aqm serve --port 3000             # Custom port
aqm serve --host 0.0.0.0          # Allow remote access
```

## agents.yaml Reference

### Entry Point (Auto-Routing)

Control which agent receives the user's input first:

```yaml
entry_point: auto    # LLM picks the best agent based on user input
# entry_point: first  # (default) Always start with the first agent in the list
```

| Value | Behavior |
|---|---|
| `first` (default) | First agent in the YAML list receives the task. Backward-compatible. |
| `auto` | LLM analyzes the user input against all agents and picks the most appropriate one. |

**Example — Multi-domain pipeline with auto-routing:**
```yaml
entry_point: auto

agents:
  - id: code_reviewer
    runtime: claude
    system_prompt: "Review code: {{ input }}"
  - id: bug_fixer
    runtime: claude
    system_prompt: "Fix bug: {{ input }}"
  - id: feature_planner
    runtime: claude
    system_prompt: "Plan feature: {{ input }}"
```

```bash
aqm run "Review PR #42"          # → auto-selects code_reviewer
aqm run "Fix login crash"        # → auto-selects bug_fixer
aqm run "Add dark mode"          # → auto-selects feature_planner
aqm run "Fix bug" --agent planner  # → --agent flag overrides auto
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
| `context_strategy` | `"own"` \| `"shared"` \| `"both"` | `"both"` | What context to inject (token optimization) |
| `context_window` | `int` | `3` | Recent stages in full; older stages summarized (0 = all) |
| `human_input` | `boolean` \| `object` | `null` | Human-in-the-loop input (`before`, `on_demand`, `both`) |
| `handoffs` | `list[Handoff]` | `[]` | Routing rules (see below) |
| `gate` | `object` | `null` | Quality gate (see below) |
| `mcp` | `list[MCPServer]` | `[]` | MCP server connections (see below) |
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

### Gate Fields

| Field | Type | Default | Description |
|---|---|---|---|
| `type` | `"llm"` \| `"human"` | `"llm"` | LLM auto-evaluates or human manually approves |
| `prompt` | `string` | `""` | Custom evaluation prompt (Jinja2: `{{ output }}`, `{{ input }}`) |
| `model` | `string` | config default | Model override for LLM gate evaluation |
| `max_retries` | `int` | `3` | Max reject retries before pipeline fails (prevents infinite loops) |

### MCP Server Fields

| Field | Type | Default | Description |
|---|---|---|---|
| `server` | `string` | — | Server name (e.g., `"github"`, `"filesystem"`) |
| `command` | `string` | `"npx"` | Custom command (default: `npx -y @modelcontextprotocol/server-{name}`) |
| `args` | `list[string]` | `[]` | Command arguments |
| `env` | `object` | `null` | Environment variables |

### Human Input Fields

| Field | Type | Default | Description |
|---|---|---|---|
| `enabled` | `boolean` | `true` | Enable/disable human input |
| `mode` | `"before"` \| `"on_demand"` \| `"both"` | `"on_demand"` | When to request input |
| `prompt` | `string` | `""` | Custom question shown to user (for `before` mode) |

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

Project-level configuration at `.aqm/config.yaml`. All fields are optional — defaults match built-in values.

```yaml
# .aqm/config.yaml
pipeline:
  max_stages: 20              # Maximum pipeline stages before failure

gate:
  model: claude-sonnet-4-20250514   # Default model for LLM gate evaluation
  timeout: 120                # Gate evaluation timeout (seconds)
  system_prompt: |            # Custom gate evaluation prompt
    You are a quality gate evaluator...

timeouts:                     # Runtime subprocess timeouts (seconds)
  claude: 600
  gemini: 600
  codex: 600
```

| Field | Type | Default | Description |
|---|---|---|---|
| `pipeline.max_stages` | `int` | `20` | Maximum pipeline stages before auto-failure |
| `gate.model` | `string` | `"claude-sonnet-4-20250514"` | Default model for LLM gate evaluation |
| `gate.timeout` | `int` | `120` | Gate subprocess timeout (seconds) |
| `gate.system_prompt` | `string` | built-in | Custom gate system prompt |
| `timeouts.claude` | `int` | `600` | Claude runtime timeout |
| `timeouts.gemini` | `int` | `600` | Gemini CLI runtime timeout |
| `timeouts.codex` | `int` | `600` | Codex CLI runtime timeout |

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
│   ├── claude_code.py    # Claude Code (with MCP, token streaming)
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
| Human-in-the-loop | ❌ | ❌ | ❌ | **`human_input` per agent** |
| Approve/Reject gate | Interrupt | ❌ | ❌ | **First-class** |
| Auto entry routing | ❌ | ❌ | ❌ | **LLM-based `entry_point: auto`** |
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
