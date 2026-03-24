# aqm

An orchestration framework where multiple AI agents pass tasks through **explicit queues** in sequence.

Build pipelines in YAML. Share them with anyone. Run them locally.

```
                 reject                          critical
          ┌──────────────────────┐       ┌──────────────────┐
          ▼                      │       ▼                  │
      [planner] ──► [reviewer] ──┴─approve──► [developer] ──► [qa]
```

## Powered by Claude Code

aqm uses **[Claude Code](https://docs.anthropic.com/en/docs/claude-code)** (Anthropic's CLI) as the underlying LLM runtime. Both `api` and `claude_code` runtimes invoke the `claude` CLI as a subprocess — no API key configuration or SDK setup required.

- **`api` runtime** — Calls `claude -p <prompt> --print` for pure text generation (planning, reviewing, summarizing)
- **`claude_code` runtime** — Runs Claude Code CLI with full tool access (file read/write, code execution, MCP tools)
- **LLM Gate** — Also uses the Claude CLI for automatic approve/reject evaluation

> **Prerequisite:** Install Claude Code CLI and authenticate before using aqm.
> ```bash
> npm install -g @anthropic-ai/claude-code
> claude login
> ```

## Why aqm?

Multi-agent frameworks have come in two flavors:

- **Code-based** (LangGraph, AutoGen) — hard to share pipelines
- **Cloud platforms** (CrewAI Studio, Vertex AI) — costly, vendor lock-in

aqm takes a different path:

**Declare pipelines in a single YAML file. Anyone can create them, anyone can use them — an open ecosystem.**

```bash
# Use a community-built pipeline instantly
aqm pull software-dev-pipeline

# Share your pipeline with the ecosystem
aqm publish my-pipeline
```

| Existing approach | aqm |
|---|---|
| Single agent does everything | Role separation per agent, handoff via queues |
| Direct calls between agents | Queue is the contract — loose coupling |
| Pipelines defined in code | Declared in YAML, shareable |
| Context lives only in memory | File-based context (`context.md`) — human-inspectable |
| Results always accepted | approve/reject gate is a first-class feature |
| External infrastructure required | SQLite default, runs after `pip install` |

## Install

```bash
pip install aqm
```

> Requires Python 3.11+.

## Quick Start

```bash
# Initialize in an existing project
cd my-project
aqm init

# Choose your setup method:
#   [1] Create default template — basic planner→executor pipeline
#   [2] Pull from registry — install a community pipeline
#   [3] AI-generate — describe what you want, Claude builds the YAML

# Run a pipeline
aqm run "Add JWT authentication to login"

# Open the dashboard
aqm serve
# → http://localhost:8000
```

No Redis. No Docker. No cloud account. **Just SQLite.**

## How It Works

### 1. Declare agents and connections in YAML

```yaml
# .aqm/agents.yaml

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

Handoffs support three routing strategies:
- **Static** — fixed target (`to: reviewer`)
- **Fan-out** — multiple targets in parallel (`to: qa, docs, deploy`)
- **Agent-decided** — agent chooses at runtime (`condition: auto` + `HANDOFF: agent_id` in output)

### 3. Context accumulates in files

Each time a task passes through an agent, the result is appended to `context.md`. The next agent reads this file to understand the full history.

```
.aqm/tasks/T-A3F2B1/
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

### `aqm init`

Initialize `.aqm/` in the current project directory with an interactive setup wizard.

```bash
aqm init
aqm init --path /path/to/project
```

The wizard offers three setup methods:

| Method | Description |
|--------|-------------|
| **[1] Default template** | Start with a basic planner→executor pipeline. Edit `agents.yaml` to customize. |
| **[2] Pull from registry** | Browse and install pipelines from bundled examples or local registry. |
| **[3] AI-generate** | Describe your desired pipeline in plain language and Claude generates the YAML — always referencing the full [YAML spec](docs/spec.md) for correctness. |

**AI-generate example:**

```bash
$ aqm init
How would you like to set up your pipeline?

  [1] Create default template
  [2] Pull from registry
  [3] AI-generate from description

  Choice: 3

  Pipeline description: Code review pipeline with planning, review gate,
  implementation, and automated QA testing

  Generating agents.yaml with Claude (referencing YAML spec)...

  Generated agents.yaml:
  ─────────────────────────────────────
  apiVersion: aqm/v0.1
  agents:
    - id: planner
      ...
  ─────────────────────────────────────

  [1] Use this pipeline  [2] Regenerate  [3] Use default template
  Choice: 1

✓ .aqm/ initialized with AI-generated pipeline
```

Creates:
```
.aqm/
├── agents.yaml    ← Pipeline configuration (edit this)
├── tasks/         ← Task context directories (auto-generated)
└── queue.db       ← SQLite queue (auto-generated)
```

### `aqm run`

Create a task and run it through the pipeline.

```bash
# Basic usage
aqm run "Build a login feature"

# Specify starting agent
aqm run "Fix the payment bug" --agent developer

# With verbose logging
aqm -v run "Add user registration"
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
  Proceed with 'aqm approve T-A3F2B1' or
  'aqm reject T-A3F2B1 -r "reason"'.
```

### `aqm fix`

Follow-up on a previous task. Carries over the full `context.md` so agents understand the previous work and can make targeted corrections.

```bash
aqm fix T-A3F2B1 "The login button color should be blue, not red"
aqm fix T-A3F2B1 "Authentication fails on mobile"
aqm fix T-A3F2B1 "Update API endpoint to use v2" --agent developer
```

Output:
```
✓ Fix task created: T-C4D5E6 (from T-A3F2B1)
  Starting agent: planner

  stage 1 planner → Analyzed previous context, fixing color...
  stage 2 developer → Updated button color to blue...

✓ Completed T-C4D5E6
```

Use `fix` for any follow-up — bug reports, corrections, refinements. One command covers all iteration.

### `aqm status`

View task status.

```bash
# Summary of all tasks
aqm status

# Detailed view of a specific task
aqm status T-A3F2B1
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

### `aqm list`

List tasks with optional status filtering.

```bash
# All tasks
aqm list

# Filter by status
aqm list --filter completed
aqm list --filter failed
aqm list --filter awaiting_gate
aqm list --filter pending
```

### `aqm approve`

Approve a task waiting at a human gate.

```bash
aqm approve T-A3F2B1
aqm approve T-A3F2B1 -r "Looks good, proceed with implementation"
```

### `aqm reject`

Reject a task waiting at a human gate. Reason is required.

```bash
aqm reject T-A3F2B1 -r "Missing error handling for edge cases"
```

### `aqm agents`

Display all agents and their handoff graph.

```bash
aqm agents
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

### `aqm context`

Print the full `context.md` for a task.

```bash
aqm context T-A3F2B1
```

### `aqm serve`

Launch the local web dashboard.

```bash
aqm serve
aqm serve --port 3000
aqm serve --host 0.0.0.0 --port 8080
```

Requires: `pip install aqm[serve]`

Dashboard features:
- **Task list** — status, current agent, elapsed time
- **Agent diagram** — auto-generated connection graph from YAML
- **Task detail** — stage-by-stage input/output, gate results
- **Gate actions** — approve/reject buttons directly in the UI

### `aqm pull`

Pull a pipeline from the community registry.

```bash
aqm pull software-dev-pipeline
aqm pull legal-document-review
```

> Registry feature coming in v0.3.

### `aqm publish`

Share your pipeline to the registry.

```bash
aqm publish
aqm publish --name "my-pipeline" --description "Custom workflow"
```

> Registry feature coming in v0.3.

### `aqm search`

Search the pipeline registry.

```bash
aqm search "code review"
aqm search "content creation"
```

> Registry feature coming in v0.3.

## agents.yaml Complete Reference

The `agents.yaml` file is the single source of truth for your pipeline. It defines all agents, their roles, how they connect, and how tasks flow between them.

### Top-Level Structure

```yaml
# .aqm/agents.yaml
params:          # (optional) Parameterization for reusability
  model: claude-sonnet-4-20250514
  project_path:
    type: string
    required: true
    description: "Path to the project root"

imports:         # (optional) Import agents from external files
  - from: ./shared/reviewers.yaml
    agents: [code_reviewer]

agents:
  - id: ...      # First agent (pipeline entry point)
  - id: ...      # Second agent
  - id: ...      # ...
```

The file has three top-level keys: `params` (variable declarations), `imports` (external agent files), and `agents` (the pipeline definition). The **first non-abstract agent** is used as the default starting point.

---

### Params — Parameterization for Reusability

Params make pipelines portable. Declare variables with types and defaults, then reference them anywhere using `${{ params.var_name }}` syntax. Add `prompt` and `auto_detect` to create interactive setup flows.

```yaml
params:
  # Shorthand — just a default value
  model: claude-sonnet-4-20250514

  # Full declaration
  project_path:
    type: string        # string | number | boolean
    required: true
    description: "Path to the project root"

  # Interactive param with auto-detection
  primary_color:
    type: string
    required: true
    description: "Primary brand color hex code"
    prompt: "What is the primary brand color?"                    # question shown during aqm run
    auto_detect: "Analyze CSS/config files for primary color"     # LLM instruction for auto-fill

  max_retries:
    type: number
    default: 3

agents:
  - id: developer
    model: ${{ params.model }}
    mcp:
      - server: filesystem
        args: ["${{ params.project_path }}"]
```

**Interactive setup with `prompt` + `auto_detect`:**

When you pull a shared pipeline, params can ask questions interactively:

```yaml
params:
  primary_color:
    type: string
    required: true
    prompt: "What is the primary brand color?"
    auto_detect: "Analyze the project's CSS/tailwind config and extract the primary color"

  project_name:
    type: string
    required: true
    prompt: "What is the project name?"
    auto_detect: "Read package.json or pyproject.toml and extract the project name"
```

When you run `aqm run`, each unresolved param with a `prompt` shows an interactive setup:

```
? What is the primary brand color?
  [1] Enter manually
  [2] Auto-detect from project
  Choice [1]: 2
  Detecting...
  Detected: #3B82F6
  Use this value? [Y/n]: Y
```

This makes pipelines truly portable — pull a design pipeline, and it asks the right questions for your project.

**Override params at runtime:**

```bash
# Via CLI flags (skips interactive prompts)
aqm run "Build feature" --param model=claude-opus-4-6 --param project_path=/my/project

# Via overrides file (.aqm/params.yaml)
echo "model: claude-opus-4-6" > .aqm/params.yaml
echo "project_path: /my/project" >> .aqm/params.yaml
aqm run "Build feature"
```

**Resolution priority:** CLI flags > params.yaml file > interactive prompt > default values.

This is what makes the "pull and customize" workflow possible:
```bash
aqm pull software-dev-pipeline
# Interactive prompts fill in project-specific values
aqm run "Build login feature"
```

---

### Imports — Reuse Agents Across Pipelines

Import agent definitions from external YAML files to avoid duplication:

```yaml
# .aqm/agents.yaml
imports:
  - from: ./shared/reviewer.yaml          # relative path
    agents: [security_reviewer]            # import specific agents (optional — omit to import all)

agents:
  - id: planner
    runtime: api
    handoffs:
      - to: security_reviewer
        condition: always
```

```yaml
# .aqm/shared/reviewer.yaml
agents:
  - id: security_reviewer
    runtime: api
    system_prompt: "Review for security vulnerabilities: {{ input }}"
    gate:
      type: llm
      prompt: "Are there any security issues?"
```

---

### Extends — Agent Inheritance

Define a base agent and extend it to create specialized variants:

```yaml
agents:
  - id: base_reviewer
    abstract: true          # Not instantiated — only used as a base
    runtime: api
    gate:
      type: llm
    system_prompt: "Review: {{ input }}"

  - id: code_reviewer
    extends: base_reviewer  # Inherits runtime, gate from parent
    system_prompt: "Review this CODE for bugs and style: {{ input }}"

  - id: security_reviewer
    extends: base_reviewer
    system_prompt: "Review for security vulnerabilities: {{ input }}"
```

- `abstract: true` agents are excluded from the pipeline (base-only)
- Child fields **override** parent fields (shallow merge)
- Works with imports: import a base, extend it locally

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
| `abstract` | `boolean` | No | `false` | If `true`, agent is a base template only — excluded from pipeline execution. |
| `extends` | `string` | No | `null` | ID of a parent agent to inherit fields from (shallow merge). |

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
| `to` | `string` | **Yes** | — | Target agent `id`. Comma-separated for fan-out (e.g. `"qa, docs"`). Ignored when `condition: auto`. |
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
| `auto` | **Agent decides at runtime** — parses `HANDOFF: <id>` from agent output |
| Custom expression | Expression evaluates to true (e.g. `"severity == critical"`, `"severity in [major, minor]"`) |

#### Three Routing Strategies

**1. Static routing** — fixed target, simplest form:
```yaml
handoffs:
  - to: reviewer
    condition: always
```

**2. Fan-out** — send to multiple agents simultaneously:
```yaml
handoffs:
  - to: qa, docs, deploy       # all three run in parallel as child tasks
    condition: on_approve
```
The first target continues in the current task; additional targets spawn independent child tasks that run concurrently.

**3. Agent-decided routing (`auto`)** — the agent itself chooses where to route:
```yaml
# Triage agent analyzes the input and decides which specialist to hand off to
- id: triage
  name: Triage Agent
  runtime: api
  system_prompt: |
    Analyze the request and decide which agent should handle it.
    End your response with: HANDOFF: <agent_id>
    Available agents: developer, designer, analyst
  handoffs:
    - to: "*"                   # 'to' is ignored when condition is auto
      condition: auto
```
The agent includes a `HANDOFF:` directive in its output:
```
This request requires code changes to the payment module.
HANDOFF: developer
```
Multiple targets are also supported:
```
This needs both code changes and documentation updates.
HANDOFF: developer, docs
```

> **Note:** When using `auto`, the agent must include `HANDOFF: <agent_id>` in its output. If the directive is missing, the handoff is skipped and a warning is logged.

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

**Example — Intelligent triage with fan-out:**
```yaml
agents:
  - id: triage
    name: Triage Agent
    runtime: api
    system_prompt: |
      Analyze this customer request. Determine which teams should handle it.
      If multiple teams are needed, list them all.
      End with: HANDOFF: team1, team2
    handoffs:
      - to: "*"
        condition: auto

  - id: billing
    name: Billing Agent
    runtime: api
    system_prompt: "Handle billing issues: {{ input }}"

  - id: technical
    name: Technical Agent
    runtime: claude_code
    system_prompt: "Investigate technical issues: {{ input }}"

  - id: account
    name: Account Agent
    runtime: api
    system_prompt: "Handle account issues: {{ input }}"
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
| `human` | Pipeline **pauses** and waits for manual approval. Resume with `aqm approve <task-id>` or `aqm reject <task-id> -r "reason"`. |

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
# .aqm/agents.yaml
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
    system_prompt: |
      Run tests and evaluate quality. If issues are found,
      decide severity and route accordingly.
      End with: HANDOFF: <agent_id>
    handoffs:
      # Agent decides based on analysis — no static condition needed
      - to: "*"
        condition: auto
```

In this example, the QA agent analyzes test results and autonomously decides:
- `HANDOFF: developer` for minor bugs
- `HANDOFF: planner` for critical design issues
- `HANDOFF: developer, docs` if both code and docs need updates

## Pipelines for Any Domain

| Domain | Pipeline Example |
|---|---|
| Software Dev | Planning → Review → Implementation → QA → PR |
| Content Creation | Research → Draft → Edit → Publish |
| Legal Documents | Extract → Summarize → Risk Flag → Approval |
| Data Analysis | Collect → Clean → Analyze → Report |
| Customer Support | Classify → Lookup → Respond → Escalate |

## Comparison with Existing Frameworks

| | LangGraph | CrewAI | OpenSWE | aqm |
|---|---|---|---|---|
| Pipeline definition | Python code | Python code | Code | **YAML** |
| Pipeline sharing | ❌ | Paid platform | ❌ | **Open registry** |
| Explicit queue | ❌ | ❌ | ❌ | **SQLite default** |
| Approve/Reject gate | Interrupt pattern | ❌ | ❌ | **First-class** |
| Reverse feedback loop | Manual | Limited | ❌ | **Built-in** |
| Fan-out (parallel branches) | Manual | ❌ | ❌ | **Declarative** |
| Agent-decided routing | Manual | ❌ | ❌ | **`condition: auto`** |
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

## Documentation

| Document | Description |
|---|---|
| [Core Concepts](docs/concepts.md) | Task, Queue, Handoff, Gate, Condition, Context, Pipeline — with LangGraph/CrewAI comparison tables |
| [YAML Specification](docs/spec.md) | Independent `agents.yaml` format spec (`apiVersion: aqm/v0.1`), field reference, processing order, versioning policy |
| [JSON Schema](schema/agents-schema.json) | Machine-readable schema for validation and IDE autocomplete |
| [Competitive Analysis](docs/competitive-analysis.md) | Positioning vs. LangGraph, CrewAI, AutoGen, OpenSWE, Copilot, Vertex AI |
| [Seed Pipelines](examples/README.md) | 10 ready-to-use pipelines with feature matrix |
| [Contributing](CONTRIBUTING.md) | How to contribute pipelines (equal to code!), submission template, review process |

Validate your pipeline against the spec:
```bash
aqm validate .aqm/agents.yaml
```

## Architecture

```
aqm/
├── aqm/
│   ├── core/
│   │   ├── task.py           # Task, StageRecord, TaskStatus
│   │   ├── agent.py          # AgentDefinition, params, extends, imports
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
│   │   ├── api.py            # Claude CLI runtime (text-only)
│   │   └── claude_code.py    # Claude Code CLI runtime (tools + MCP)
│   ├── web/
│   │   └── app.py            # FastAPI web dashboard
│   └── cli.py                # Click CLI
├── schema/
│   └── agents-schema.json    # JSON Schema for agents.yaml
├── docs/
│   ├── concepts.md           # Core concepts guide
│   ├── spec.md               # YAML format specification
│   └── competitive-analysis.md
├── examples/                  # 10 seed pipelines
└── tests/
```

## Roadmap

### v0.1 — Core
- [x] SQLite-based task queue
- [x] YAML agent declarations
- [x] Handoff routing (static, fan-out, agent-decided)
- [x] LLM / Human approval gates
- [x] File-based context accumulation (context.md)
- [x] Claude Code runtime with MCP support
- [x] Local web UI dashboard
- [x] Fan-out: parallel child tasks from comma-separated targets
- [x] `condition: auto`: agent-decided routing via `HANDOFF:` directive
- [x] `params`: parameterization with `${{ params.X }}` for portable pipelines
- [x] `extends` / `abstract`: agent inheritance for DRY definitions
- [x] `imports`: reuse agents across pipelines from external files
- [x] 10 seed pipelines covering software, content, legal, data, and more
- [x] Interactive params: `prompt` + `auto_detect` for guided pipeline setup
- [x] Follow-up tasks: `aqm fix` for multi-turn iteration with context carry-over

### v0.2 — Connections
- [ ] Enhanced per-agent MCP server support
- [ ] GitHub / Slack webhook triggers
- [ ] Context summarization (prevent token explosion)

### v0.3 — Ecosystem
- [ ] Pipeline registry (`aqm publish / pull / search`)
- [ ] registry.aqm.dev launch
- [ ] YAML version control and forking

### v1.0 — Stabilization
- [ ] Task dependencies (DAG)
- [ ] Redis / Postgres queue backends
- [ ] Full web dashboard with real-time updates

## Contributing

```bash
git clone https://github.com/aqm-framework/aqm
cd aqm
pip install -e ".[dev]"
pytest tests/
```

Creating and sharing pipelines is also a great contribution.

## License

MIT
