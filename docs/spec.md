# agents.yaml Specification v0.1

## Overview

This document defines the **agents.yaml** configuration format used by aqm (aqm) pipelines. It specifies the structure, fields, types, defaults, and processing semantics for pipeline definition files.

**This specification is independent of any particular runtime.** While the reference implementation is the `aqm` CLI and Python SDK, any conforming runtime can load and execute an agents.yaml file. The JSON Schema at `schema/agents-schema.json` provides machine-readable validation.

### Goals

- Declarative agent pipeline definitions in YAML
- Parameterization and composition via `params`, `imports`, and `extends`
- Runtime-agnostic: the spec describes *what* to run, not *how*
- Human-readable and version-control friendly

---

## apiVersion

| Property | Value |
|----------|-------|
| **Field** | `apiVersion` |
| **Type** | string |
| **Required** | Yes |
| **Value** | `"aqm/v0.1"` |

The `apiVersion` field is required at the top level of every agents.yaml file. It identifies which version of this specification the file conforms to.

**Versioning strategy:** The spec follows semver. The version in `apiVersion` tracks the spec version, not the runtime version.

```yaml
apiVersion: aqm/v0.1
```

---

## Top-Level Structure

An agents.yaml file has four top-level fields:

```yaml
apiVersion: aqm/v0.1       # Required
params:                      # Optional — parameter declarations
  model: claude-sonnet-4-20250514
imports:                     # Optional — import agents from other files
  - from: ./shared/qa.yaml
agents:                      # Required — at least one agent
  - id: my_agent
    system_prompt: "You are helpful."
```

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `apiVersion` | string | Yes | — | Spec version. Must be `"aqm/v0.1"`. |
| `params` | object | No | `{}` | Parameter declarations. Keys are param names, values are `ParamDefinition` or shorthand. |
| `imports` | array of ImportSpec | No | `[]` | External YAML files to import agents from. |
| `agents` | array of AgentDefinition | Yes | — | Agent definitions. Must contain at least one entry. |

---

## Field Reference

### params

The `params` section declares named parameters that can be referenced anywhere in the YAML via `${{ params.<name> }}` syntax.

#### Shorthand format

A bare value (string, number, or boolean) is treated as a `ParamDefinition` with only a default:

```yaml
params:
  model: claude-sonnet-4-20250514       # shorthand for { default: "claude-sonnet-4-20250514" }
  max_tokens: 4096              # shorthand for { default: 4096 }
```

#### Full ParamDefinition

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `type` | `"string"` \| `"number"` \| `"boolean"` | No | `"string"` | Parameter type. Used for coercion when resolving CLI overrides. |
| `default` | any | No | `null` | Default value. Type should match the declared `type`. |
| `required` | boolean | No | `false` | If `true`, the parameter must be provided via CLI `--param` or a `params.yaml` override file. |
| `description` | string | No | `""` | Human-readable description. Shown in error messages. |

```yaml
params:
  github_token:
    type: string
    required: true
    description: "GitHub personal access token for API access"
  model:
    type: string
    default: claude-sonnet-4-20250514
  verbose:
    type: boolean
    default: false
```

**Resolution priority** (highest first):
1. CLI overrides (`--param key=value`)
2. Override file (`.aqm/params.yaml`)
3. Default values from param definitions

---

### imports

The `imports` section pulls agent definitions from external YAML files.

#### ImportSpec

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `from` | string | Yes | — | Path to the external YAML file, relative to the importing file's directory. |
| `agents` | array of string | No | `[]` | Agent IDs to import. If empty, all agents from the file are imported. |

```yaml
imports:
  - from: ./shared/qa-agents.yaml
    agents: [qa_reviewer]
  - from: ./shared/infra.yaml        # imports all agents from this file
```

The external file can be either:
- A full agents.yaml (with an `agents:` key)
- A bare YAML list of agent definition objects

Imported agents are prepended to the agent list, making them available as `extends` targets.

---

### agents

The `agents` section is a list of `AgentDefinition` objects. At least one agent is required.

#### AgentDefinition

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `id` | string | Yes | — | Unique identifier. Pattern: `^[a-zA-Z_][a-zA-Z0-9_-]*$`. Used in handoff targets and CLI. |
| `name` | string | No | `""` | Human-readable display name. Auto-generated from `id` if omitted (e.g. `code_reviewer` becomes `"Code Reviewer"`). |
| `runtime` | `"api"` \| `"claude_code"` | No | `"api"` | Execution runtime. `api` = Anthropic Messages API. `claude_code` = Claude Code CLI subprocess. |
| `model` | string \| null | No | `null` | Model identifier (e.g. `"claude-sonnet-4-20250514"`). If null, runtime default is used. |
| `system_prompt` | string | No | `""` | System prompt. Supports `${{ params.X }}` substitution. |
| `handoffs` | array of Handoff | No | `[]` | Handoff rules for routing output to other agents. |
| `gate` | GateConfig \| null | No | `null` | Optional gate (LLM or human review) before handoffs execute. |
| `mcp` | array of MCPServerConfig \| string | No | `[]` | MCP servers available to this agent. Supports shorthand strings. |
| `claude_code_flags` | array of string \| null | No | `null` | Extra CLI flags for `claude_code` runtime. |
| `abstract` | boolean | No | `false` | If `true`, agent is a template only and is removed before execution. |
| `extends` | string \| null | No | `null` | ID of parent agent. Child inherits parent fields via shallow merge; child fields win. |

```yaml
agents:
  - id: base_reviewer
    abstract: true
    runtime: api
    model: ${{ params.model }}

  - id: code_reviewer
    extends: base_reviewer
    system_prompt: "Review code for bugs and security issues."
    gate:
      type: llm
      prompt: "Is this review thorough? APPROVE or REJECT."
    handoffs:
      - to: qa
        condition: on_approve
```

---

### handoffs

Handoff rules define how output flows between agents.

#### Handoff

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `to` | string | Yes | — | Target agent ID, or comma-separated IDs for fan-out (e.g. `"qa, docs"`). |
| `task` | string | No | `""` | Task description passed to the target agent. |
| `condition` | string | No | `"always"` | When this handoff triggers. See condition presets below. |
| `payload` | string | No | `"{{ output }}"` | Jinja2 template for the payload. `{{ output }}` contains the current agent's output. |

#### Condition presets

| Condition | Description |
|-----------|-------------|
| `always` | Unconditional. Handoff always fires after agent completes. |
| `on_approve` | Fires only if the gate result is "approved". |
| `on_reject` | Fires only if the gate result is "rejected". |
| `on_pass` | Fires only if the gate result is "passed". |
| `auto` | The agent decides the target at runtime by including `HANDOFF: <agent_id>` in its output. The `to` field is ignored for target validation. |

Custom expression strings are also allowed for advanced routing logic.

```yaml
handoffs:
  - to: qa_reviewer
    condition: on_approve
    task: "Review the implementation"
  - to: code_reviewer
    condition: on_reject
    task: "Revise based on feedback"
    payload: "Previous output:\n{{ output }}\n\nFeedback: revision needed"
  - to: "qa, docs"
    condition: always          # fan-out to both agents
```

---

### gate

A gate pauses the pipeline to evaluate the agent's output before proceeding to handoffs.

#### GateConfig

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `type` | `"llm"` \| `"human"` | No | `"llm"` | `llm` = automatic LLM-based review. `human` = pauses for manual CLI approval. |
| `prompt` | string | No | `""` | Evaluation prompt for LLM gates. Ignored for human gates. |
| `model` | string \| null | No | `null` | Model for LLM gate evaluation. If null, uses the default model. |

```yaml
gate:
  type: human

# or

gate:
  type: llm
  prompt: "Does this output meet quality standards? Reply APPROVE or REJECT with reason."
  model: claude-sonnet-4-20250514
```

---

### mcp

MCP (Model Context Protocol) servers provide tools to agents.

#### MCPServerConfig

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `server` | string | Yes | — | MCP server name. E.g. `"github"`, `"filesystem"`. |
| `command` | string \| null | No | `null` | Command to start the server. E.g. `"npx"`. |
| `args` | array of string | No | `[]` | Arguments to the server command. |
| `env` | object \| null | No | `null` | Environment variables for the server process. Values are strings. |

**Shorthand:** A bare string in the `mcp` array is equivalent to `{ server: "<string>" }`.

```yaml
mcp:
  - github                                    # shorthand
  - server: filesystem
    args: ["/path/to/project"]
  - server: postgres
    command: npx
    args: ["@modelcontextprotocol/server-postgres"]
    env:
      DATABASE_URL: ${{ params.database_url }}
```

---

## Processing Order

A conforming runtime MUST process an agents.yaml file in the following order:

1. **Parse YAML** -- Load the raw YAML into a dictionary structure.
2. **Resolve params** -- Build final parameter values using resolution priority: CLI overrides > override file > defaults. Validate that all required params have values.
3. **Substitute** -- Replace all `${{ params.X }}` references in every string field throughout the entire raw dict.
4. **Resolve imports** -- Load external YAML files specified in `imports`. Prepend imported agents to the agent list.
5. **Resolve extends** -- For agents with `extends`, shallow-merge parent fields into child. Child fields override parent. Remove the `extends` key from the resolved definition.
6. **Filter abstract** -- Remove all agents with `abstract: true` from the final list.
7. **Validate** -- Validate the resolved structure against the schema. Auto-fill `name` from `id` if empty.
8. **Validate handoff targets** -- Verify that all handoff `to` targets (except `condition: auto`) reference existing agent IDs. Comma-separated targets are checked individually.

```
  agents.yaml
       |
       v
  [1] Parse YAML
       |
       v
  [2] Resolve params (CLI > file > defaults)
       |
       v
  [3] Substitute ${{ params.X }}
       |
       v
  [4] Resolve imports (load external files)
       |
       v
  [5] Resolve extends (shallow merge parent -> child)
       |
       v
  [6] Filter abstract agents
       |
       v
  [7] Validate schema + auto-fill name
       |
       v
  [8] Validate handoff targets
       |
       v
  Ready for execution
```

---

## Breaking Change Policy

### v0.x (current)

Breaking changes are allowed between minor versions during the v0.x phase. Each breaking change will be accompanied by:

- A migration guide documenting what changed and how to update
- A changelog entry clearly marked as BREAKING

### v1.0+

Once v1.0 is released:

- **No breaking changes within a major version.** A file valid under v1.0 must remain valid under v1.x.
- New optional fields may be added in minor versions.
- Deprecated fields will have **at least one minor version** of deprecation warnings before removal in the next major version.

### Deprecation process

1. Field is marked deprecated in the spec and JSON Schema (`deprecated: true`).
2. Runtimes emit a warning when the deprecated field is used.
3. The field is removed in the next major version.

---

## Full Example

```yaml
apiVersion: aqm/v0.1

params:
  model: claude-sonnet-4-20250514
  github_token:
    type: string
    required: true
    description: "GitHub token for MCP server"

imports:
  - from: ./shared/qa-agents.yaml
    agents: [qa_reviewer]

agents:
  - id: planner
    name: "Task Planner"
    model: ${{ params.model }}
    system_prompt: |
      You are a senior software architect. Break down the task
      into implementation steps.
    handoffs:
      - to: developer
        task: "Implement the plan"

  - id: developer
    runtime: claude_code
    model: ${{ params.model }}
    system_prompt: "Implement the described changes."
    mcp:
      - github
      - server: filesystem
        args: ["."]
    gate:
      type: llm
      prompt: "Does the implementation match the plan? APPROVE or REJECT."
    handoffs:
      - to: qa_reviewer
        condition: on_approve
      - to: developer
        condition: on_reject
        task: "Revise based on gate feedback"
```
