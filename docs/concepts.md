# Core Concepts

This document describes the eight foundational concepts of **aqm (aqm)**. Each concept maps to a concrete data structure or mechanism in the codebase, and every concept is illustrated with a real `agents.yaml` snippet you can copy into your own pipelines.

---

### Task

**Definition:** The unit of work that flows through the pipeline, carrying its description, status, stage history, and metadata from the first agent to the last.

**How it works:** A Task is a Pydantic model identified by a short hash ID in `T-XXXXXX` format. When you run `aqm run --input "..."`, the system creates a Task whose `description` field holds your input text. As the task moves through agents, each execution is recorded as a `StageRecord` appended to the task's `stages` list. The task tracks which agent currently owns it (`current_agent_id`) and which queue it sits on (`current_queue`).

A task's `status` field follows a well-defined lifecycle: `pending` -> `in_progress` -> (optionally) `awaiting_gate` -> `completed` or `failed`. The `awaiting_gate` state is special -- it means the pipeline has paused because a human gate requires manual approval. Once a human issues `aqm approve` or `aqm reject`, the pipeline resumes from where it left off.

Each StageRecord captures the stage number, the agent that ran, the input and output text, the gate decision (if any), the reject reason, and start/finish timestamps. This gives you a complete audit trail of every decision the pipeline made. The task also stores a `context_dir` path pointing to the directory where the context file and individual stage files live on disk.

Tasks also carry a free-form `metadata` dictionary. The pipeline uses this for internal bookkeeping (e.g., storing a `parent_task_id` for fan-out child tasks or an `error` message when the task exceeds the maximum stage limit), but you can attach any additional data your agents need.

**Example:**
```yaml
# A task is created implicitly when you run the CLI:
#   aqm run --input "Add OAuth2 authentication"
#
# Internally, the Task object looks like:
#   Task(
#     id="T-3A7F2B",
#     description="Add OAuth2 authentication",
#     status="pending",
#     stages=[],
#     metadata={}
#   )
#
# After the first agent completes, a StageRecord is appended:
#   StageRecord(stage_number=1, agent_id="planner", output_text="...")
```

**Comparison:**
- **LangGraph equivalent:** LangGraph has no explicit task object; instead, the "state" dictionary carries data between nodes. In aqm, the Task is a first-class entity with its own lifecycle, ID, and persistent stage history. LangGraph state is ephemeral within a single graph execution; aqm Tasks persist to disk and can be resumed across process restarts.
- **CrewAI equivalent:** CrewAI's `Task` is a unit of work assigned to a single agent with a description and expected output. aqm's Task is broader -- it flows across multiple agents, accumulates a full stage history, and supports pause/resume via gates. CrewAI tasks do not carry execution history; aqm tasks are self-documenting audit trails.

---

### Queue

**Definition:** The message bus that holds tasks between agent executions, providing FIFO delivery and status tracking.

**How it works:** The queue is defined by the `AbstractQueue` interface, which declares seven operations: `push`, `pop`, `peek`, `update`, `get`, `list_tasks`, and `list_queues`. Each agent conceptually has its own named queue. When a handoff sends a task to the next agent, the pipeline calls `queue.push(task, agent_id)` to place the task on that agent's queue. The pipeline then immediately pops and processes it.

The queue serves two purposes. First, it is the persistent store for all tasks -- you can query tasks by status or by queue name at any time using `list_tasks`. Second, it provides the `awaiting_gate()` convenience method that returns all tasks currently paused at a human gate, which the CLI uses to show you what needs your attention.

The abstraction is deliberately simple. The built-in implementation uses a local file-based store, but because `AbstractQueue` is an abstract base class, you can swap in Redis, SQS, or any other backing store by implementing the seven required methods. The queue does not enforce ordering constraints beyond FIFO within a single named queue -- cross-queue coordination is the pipeline's responsibility.

The queue also acts as the single source of truth for task state. Every time an agent finishes, a gate fires, or a handoff occurs, the pipeline calls `queue.update(task)` to persist the latest status and stage records. This means you can inspect any task's full history at any point, even if the pipeline process has exited.

**Example:**
```yaml
# The queue is configured at the infrastructure level, not in agents.yaml.
# The pipeline creates per-agent queues automatically based on agent IDs:
#
#   queue.push(task, "planner")   # task enters planner's queue
#   queue.push(task, "reviewer")  # after handoff, task enters reviewer's queue
#
# To see tasks waiting for human approval:
#   aqm list --status awaiting_gate
```

**Comparison:**
- **LangGraph equivalent:** LangGraph does not expose a queue abstraction. Nodes are invoked synchronously by the graph runtime, and there is no intermediate storage between node executions. aqm's queue makes inter-agent communication explicit and inspectable, and it enables pause/resume across process boundaries.
- **CrewAI equivalent:** CrewAI has no queue concept. Tasks are dispatched to agents directly by the crew orchestrator in memory. aqm queues decouple agents from each other, allow external systems to inject tasks, and make the system observable (you can list and filter tasks by queue or status at any time).

---

### Handoff

**Definition:** The routing rule that determines which agent receives a task next, what payload it gets, and under what conditions the transfer occurs.

**How it works:** Each agent declares zero or more `handoffs` in its definition. A Handoff has four fields: `to` (the target agent ID or comma-separated list for fan-out), `task` (a human-readable label for what the next agent should do), `condition` (when this handoff fires), and `payload` (a template that constructs the input text for the next agent).

When an agent finishes execution and its gate (if any) has been evaluated, the pipeline iterates through the agent's handoff list and evaluates each condition. All matching handoffs contribute targets. If no handoff matches, the task is marked `completed` and the pipeline stops. If multiple targets are produced (either from fan-out syntax or from multiple matching rules), the first target continues the current task and additional targets spawn independent child tasks.

The `payload` field supports template variables: `{{ output }}` (the current agent's output), `{{ input }}` (the current agent's input), `{{ reject_reason }}` (the gate's rejection reason), and `{{ gate_result }}` (the gate decision string). This is how reverse feedback loops work -- a rejected handoff can include the rejection reason in the payload so the receiving agent knows exactly what to fix.

Handoffs are the edges of the agent graph. Unlike static DAG edges, they are conditional and data-dependent. A single agent can have handoffs that branch on gate results (on_approve goes forward, on_reject loops back), fan out to parallel reviewers, or let the agent itself decide the target at runtime via the `auto` condition.

**Example:**
```yaml
agents:
  - id: reviewer
    gate:
      type: llm
      prompt: "Is this specification ready for implementation?"
    handoffs:
      # Approved -> move forward to development
      - to: developer
        task: implement
        condition: on_approve
        payload: "{{ output }}"
      # Rejected -> reverse feedback loop back to planner
      - to: planner
        task: revise_spec
        condition: on_reject
        payload: "{{ output }}\n\nREJECT_REASON: {{ reject_reason }}\nPlease revise the specification."
```

**Comparison:**
- **LangGraph equivalent:** LangGraph uses "edges" (including conditional edges) to connect nodes. The key difference is that LangGraph edges are defined externally from the nodes in the graph builder, whereas aqm handoffs are declared inside each agent's definition. LangGraph conditional edges require a Python function that inspects the state; aqm conditions are declarative strings (`on_approve`, `on_reject`, `auto`, or expressions) evaluated by the pipeline engine.
- **CrewAI equivalent:** CrewAI does not have an explicit handoff concept. Task ordering is determined by the `Process` type (sequential or hierarchical). In sequential mode, tasks execute in list order; in hierarchical mode, a manager agent delegates. aqm handoffs give you fine-grained control over routing, branching, fan-out, and feedback loops that CrewAI's process abstraction does not support.

---

### Gate

**Definition:** An approve/reject checkpoint inserted after an agent's execution that determines whether the output meets quality standards before the pipeline proceeds.

**How it works:** Gates are optional. An agent declares a gate in its definition with a `type` (either `llm` or `human`) and an optional `prompt` that provides evaluation criteria. After the agent produces its output, the pipeline passes that output to the gate for evaluation. The gate returns a `GateResult` containing a `decision` ("approved" or "rejected") and a `reason` string.

An **LLM gate** evaluates the output automatically by calling the Claude CLI with a system prompt that instructs it to respond with a JSON decision. The LLM gate's `prompt` field is rendered with template variables and prepended to the evaluation request, so you can express domain-specific quality criteria like "Is this specification complete?" or "Is this fix safe to deploy to production?" The LLM gate always returns a decision immediately.

A **human gate** pauses the pipeline entirely. When the pipeline encounters a human gate, it sets the task status to `awaiting_gate` and returns control to the caller. The task remains in this state until a human runs `aqm approve T-XXXXXX` or `aqm reject T-XXXXXX --reason "..."`. The `resume_task` method on the Pipeline then picks up where it left off, evaluating handoff conditions based on the human's decision.

The gate result feeds directly into handoff condition evaluation. An `on_approve` handoff fires only when the gate approved; `on_reject` fires only when the gate rejected. The `on_pass` condition fires when either no gate exists or the gate approved, making it useful for optional-gate scenarios. This tight coupling between gates and handoffs is what enables reverse feedback loops: reject the output, and the task loops back to a previous agent with the rejection reason embedded in the payload.

**Example:**
```yaml
agents:
  - id: qa
    name: QA Agent
    runtime: claude
    system_prompt: |
      Verify the implemented code. Run the test suite.
      Implementation result: {{ input }}
    gate:
      type: human
    handoffs:
      - to: developer
        task: fix_bugs
        condition: on_reject
        payload: "{{ output }}\n\nBUGS_FOUND: {{ reject_reason }}\nPlease fix the issues."
```

**Comparison:**
- **LangGraph equivalent:** LangGraph's closest analogue is the "interrupt" mechanism, which pauses graph execution for human input. However, LangGraph interrupts are a control-flow mechanism, not a quality gate -- they do not produce structured approve/reject decisions that feed into conditional routing. aqm's LLM gate has no LangGraph equivalent at all; automatic quality evaluation is not a built-in LangGraph concept.
- **CrewAI equivalent:** CrewAI has no gate concept. There is no built-in mechanism to pause a crew for human approval or to automatically evaluate agent output quality before proceeding to the next task. Quality control in CrewAI must be implemented as custom logic inside task descriptions or agent instructions.

---

### Condition

**Definition:** The routing logic attached to each handoff that determines whether the handoff fires, supporting fixed rules, gate-dependent branching, agent-decided routing, and expression-based matching.

**How it works:** Every handoff carries a `condition` field that defaults to `"always"`. The pipeline evaluates each condition after the agent runs and the gate (if any) has produced its decision. The built-in condition types are:

- **`always`** -- the handoff fires unconditionally. Use this for straightforward linear chains where every output goes to the same next agent.
- **`on_approve`** -- fires only when the gate decision is `"approved"`. This is the forward path in a gate-guarded pipeline.
- **`on_reject`** -- fires only when the gate decision is `"rejected"`. This is the reverse feedback loop path.
- **`on_pass`** -- fires when there is no gate or when the gate approved. This is useful for agents that may or may not have a gate configured.
- **`auto`** -- the agent itself decides the target at runtime. The pipeline scans the agent's output for `HANDOFF: <agent_id>` directives and routes accordingly. If no directive is found, the handoff is skipped with a warning. The `to` field is ignored for auto conditions since the agent's output determines the destination.
- **Expressions** -- simple pattern-matching expressions like `severity == critical` or `type in [bug, security]`. The pipeline evaluates these by checking whether the right-hand value appears in the agent's output text (case-insensitive). This is intentionally simple -- complex routing logic belongs in an auto-routing agent, not in expression syntax.

Conditions are evaluated in order. All matching handoffs contribute their targets, which enables multi-path fan-out when more than one condition is satisfied. The deduplication logic ensures that the same target agent is not dispatched twice even if multiple handoff rules resolve to it.

The design philosophy is progressive complexity: start with `always` for simple chains, add `on_approve`/`on_reject` when you introduce gates, use `auto` when routing decisions require LLM reasoning, and fall back to expressions for keyword-based branching.

**Example:**
```yaml
agents:
  - id: severity_router
    name: Severity Router
    system_prompt: |
      Assess incident severity and route accordingly.
      Include exactly one line: HANDOFF: <agent_id>
      - critical_fix: SEV1/SEV2 incidents
      - standard_fix: SEV3/SEV4 incidents
      Incident analysis: {{ input }}
    handoffs:
      - to: severity_router
        task: route
        condition: auto
```

**Comparison:**
- **LangGraph equivalent:** LangGraph's conditional edges accept a Python function that inspects the graph state and returns the name of the next node. This is more flexible (arbitrary Python logic) but less declarative and harder to audit from a YAML file. aqm's condition system trades some flexibility for transparency -- you can understand the entire routing topology by reading the YAML without tracing through Python functions. The `auto` condition adds LLM-level flexibility when declarative rules are not enough.
- **CrewAI equivalent:** CrewAI's `Process` enum (`sequential`, `hierarchical`) is the closest analogue. Sequential process is equivalent to a chain of `always` conditions. Hierarchical process, where a manager agent delegates to workers, is loosely similar to `auto` routing. However, CrewAI does not support branching, conditional routing, or fan-out within a single process definition.

---

### Context

**Definition:** A file-based accumulation mechanism that records the full history of a task's execution in a human-readable `context.md` file, providing shared memory across agents.

**How it works:** Each task gets its own directory under `.aqm/tasks/<task-id>/`. Inside this directory, the `ContextFile` manager maintains a `context.md` file that grows with every stage. Each time an agent completes (or fails, or hits a gate), a new section is appended to `context.md` with the stage number, agent ID, task name, timestamp, status, input text, and output text. If the gate rejected the output, the rejection reason is included as well.

In addition to the cumulative `context.md`, each stage is also saved as an individual file (`stage_01_planner.md`, `stage_02_reviewer.md`, etc.) for targeted access. A `current_payload.md` file holds the most recent handoff payload, giving agents quick access to their immediate input without parsing the full context.

The context file is injected into every agent's prompt. When the pipeline prepares the prompt for the next agent, it reads the full contents of `context.md` and includes it alongside the agent's system prompt and the current input. This means every agent in the chain can see what all previous agents did -- their inputs, outputs, and gate decisions. This is the shared memory mechanism that allows a postmortem writer to summarize an entire incident response, or a summarizer to synthesize results from three parallel code reviewers.

The deliberate choice to use Markdown files rather than an in-memory state object has several advantages. Humans can open `context.md` in any editor and read the full execution history. Agents that use the `filesystem` MCP server can read and reference the context file directly. The context persists across process restarts, which is essential for human-gated workflows where hours or days may pass between the gate pause and the human decision.

**Example:**
```yaml
# The context file is managed automatically. No YAML configuration is needed.
# After a 3-stage pipeline, the context.md file looks like:
#
# ## [stage 1] planner -- plan_feature
# **Time**: 2026-03-24T10:00:00+00:00
# **Status**: completed
# ### Input
# Add OAuth2 authentication
# ### Output
# Feature specification: ...
# ---
#
# ## [stage 2] reviewer -- review_spec
# **Time**: 2026-03-24T10:01:30+00:00
# **Status**: approved
# ### Input
# Feature specification: ...
# ### Output
# Review passed. Specification is complete.
# ---
#
# ## [stage 3] developer -- implement
# **Time**: 2026-03-24T10:05:00+00:00
# **Status**: completed
# ### Input
# Review passed. Specification is complete.
# ### Output
# Implementation complete. Created 4 files...
# ---
```

**Comparison:**
- **LangGraph equivalent:** LangGraph uses an in-memory "state" dictionary (typically a `TypedDict` or Pydantic model) that is passed between nodes and can be checkpointed. The state is structured and typed, while aqm's context is unstructured Markdown. LangGraph's approach is better for programmatic access; aqm's approach is better for human readability, agent consumption (LLMs process Markdown naturally), and debugging. LangGraph checkpoints are opaque binary stores; aqm context files are plain text you can `cat` from your terminal.
- **CrewAI equivalent:** CrewAI has no equivalent. Agent outputs are passed directly to the next task's input with no accumulated history. If a later agent needs to reference an earlier agent's work, you must manually wire that into the task description. aqm's context file provides automatic, cumulative memory that every agent in the chain can access without explicit configuration.

---

### Pipeline

**Definition:** The orchestration engine that executes agents in sequence, evaluates gates, resolves handoff conditions, manages fan-out, and writes the context file -- tying all other concepts together into a running system.

**How it works:** The Pipeline class is initialized with a dictionary of agent definitions (loaded from `agents.yaml`), a queue implementation, and the project root path. When you call `run_task`, the pipeline enters a loop: it looks up the current agent, builds a prompt from the agent's system prompt template plus the accumulated context, dispatches the prompt to the appropriate runtime (API or Claude Code), evaluates the gate if one is configured, records the stage, resolves handoff conditions, and advances to the next agent. The loop continues until either no handoffs match (task completed), a human gate pauses execution (task awaiting_gate), an error occurs (task failed), or the stage count exceeds the safety limit of 20 stages.

The pipeline supports three runtime backends. The `claude` runtime invokes Claude Code CLI with MCP server and tool support. The `gemini` runtime invokes Google Gemini CLI. The `codex` runtime invokes OpenAI Codex CLI. Each agent declares its runtime in the YAML, so you can mix providers in the same pipeline.

Fan-out is handled by spawning child tasks. When handoff resolution produces multiple targets, the first target continues the current task (preserving the stage history) while each additional target gets a fresh child Task with a `parent_task_id` in its metadata. The child tasks are executed inline via recursive `run_task` calls. This means fan-out is synchronous in the current implementation -- parallel branches execute sequentially but produce independent output in the shared context file.

The `resume_task` method handles the human gate continuation path. It looks up the paused task, records the human's decision on the latest stage, re-evaluates handoff conditions with the gate result, and re-enters the `run_task` loop from the next agent. This is what makes human-in-the-loop workflows possible without any special infrastructure -- the task's full state is persisted in the queue and on disk, so the pipeline process can exit and restart between the gate pause and the human decision.

**Example:**
```yaml
# A complete pipeline definition in agents.yaml:
params:
  tone: "professional"
  audience: "software developers"

agents:
  - id: researcher
    name: Research Agent
    runtime: claude
    model: claude-sonnet-4-20250514
    system_prompt: |
      Research the topic for ${{ params.audience }}.
      Topic: {{ input }}
    handoffs:
      - to: writer
        task: draft_content
        condition: always

  - id: writer
    name: Writing Agent
    runtime: claude
    model: claude-sonnet-4-20250514
    system_prompt: |
      Write a compelling article. Tone: ${{ params.tone }}.
      Research material: {{ input }}
    handoffs:
      - to: editor
        task: review_content
        condition: always

  - id: editor
    name: Editing Agent
    runtime: claude
    model: claude-sonnet-4-20250514
    system_prompt: |
      Edit this article for quality.
      Draft: {{ input }}
    gate:
      type: llm
      prompt: "Is this article ready for publication?"
    handoffs:
      - to: publisher
        task: publish
        condition: on_approve
      - to: writer
        task: revise_content
        condition: on_reject
        payload: "{{ output }}\n\nFEEDBACK: {{ reject_reason }}"

  - id: publisher
    name: Publishing Agent
    runtime: claude
    model: claude-sonnet-4-20250514
    system_prompt: |
      Finalize and format the approved article.
      Content: {{ input }}
```

**Comparison:**
- **LangGraph equivalent:** LangGraph's `StateGraph` is the orchestration engine. Both systems compile a graph of agents/nodes and execute them in order. Key differences: LangGraph requires Python code to define the graph (add_node, add_edge, add_conditional_edges); aqm defines everything in YAML. LangGraph supports async execution natively; aqm's fan-out is currently synchronous. LangGraph has built-in streaming and time-travel debugging; aqm has file-based context that serves a similar debugging purpose with simpler tooling.
- **CrewAI equivalent:** CrewAI's `Crew` is the pipeline orchestrator. A Crew takes a list of agents and tasks and executes them according to a process type. The main difference is that CrewAI's Crew is configured in Python code, while aqm's Pipeline is driven by declarative YAML. CrewAI supports sequential and hierarchical processes; aqm supports arbitrary graph topologies including branches, loops, fan-out, and conditional routing, all declared in a single YAML file.

---

### Registry

**Definition:** A sharing ecosystem that allows users to publish, discover, and install reusable agent definitions and pipeline templates from a community repository.

**How it works:** The Registry is fully implemented and available for use.

The Registry builds on the composability features in the codebase.

The workflow is: `aqm search "code review"` to find published pipelines, `aqm pull code-review-pipeline` to download the YAML into your project, and then override parameters via `--param` flags or a local `params.yaml` file. Authors publish with `aqm publish`, and the Registry handles discoverability via GitHub.

The Registry is designed to address a gap in the current agent framework landscape. While LangChain Hub exists for prompt templates and CrewAI has some community examples, no framework offers a first-class package manager for complete multi-agent pipeline definitions. The combination of YAML-only definitions, parameterization, and imports makes aqm pipelines uniquely suited to this kind of sharing.

**Example:**
```yaml
# Usage:
#
#   aqm pull code-review-pipeline
#
# This downloads agents.yaml with:
imports:
  - from: .aqm/registry/code-review-pipeline/agents.yaml
    agents: [diff_loader, security, performance, style, summarizer]

params:
  repo: "my-org/my-repo"
  language: "Python"

agents:
  # You can extend imported agents to customize them:
  - id: security_custom
    extends: security
    system_prompt: |
      Additional context: we use Django and PostgreSQL.
      Review: {{ input }}
```

**Comparison:**
- **LangGraph equivalent:** LangGraph does not have a registry for graph definitions. LangChain Hub stores individual prompts and chains, but not complete multi-agent graphs. Sharing a LangGraph workflow requires sharing Python code, which is inherently more complex than sharing a YAML file with parameter overrides.
- **CrewAI equivalent:** CrewAI does not have a registry. Community examples exist as GitHub repositories, but there is no install/publish workflow. Like LangGraph, sharing a CrewAI setup requires sharing Python code. aqm's YAML-first approach makes registry sharing feasible because the entire pipeline definition is a single declarative file.

---

## Mapping Tables

### LangGraph -> aqm Concept Mapping

| LangGraph | aqm | Notes |
|---|---|---|
| Node | Agent | Both are units of execution. LangGraph nodes are Python functions; aqm agents are YAML-declared with a runtime (API or Claude Code). |
| Edge | Handoff | Both connect execution units. LangGraph edges are defined in the graph builder; aqm handoffs are declared inside each agent definition. |
| State | Context (context.md) | LangGraph state is a typed in-memory dict; aqm context is an append-only Markdown file on disk. aqm's format is human-readable and LLM-friendly. |
| Checkpoint | Stage Record | LangGraph checkpoints snapshot the full state for replay; aqm stage records capture per-agent input/output/gate results as an audit trail. |
| Conditional Edge | Condition | LangGraph uses Python functions; aqm uses declarative strings (`always`, `on_approve`, `on_reject`, `on_pass`, `auto`, expressions). |
| Interrupt | Human Gate | LangGraph interrupts pause execution for external input; aqm human gates pause for approve/reject decisions that feed back into handoff conditions. |
| (none) | LLM Gate | LangGraph has no built-in automatic quality evaluation. aqm LLM gates use a second LLM call to approve or reject agent output before proceeding. |
| (none) | Registry | LangGraph has no pipeline-level package manager. LangChain Hub covers prompts but not full graph definitions. |
| (none) | Fan-out | LangGraph supports parallel node execution via `Send`, but does not have declarative comma-separated fan-out in the graph definition. aqm fan-out is declared in YAML with comma-separated `to` fields. |
| (none) | Auto routing | LangGraph has no equivalent of agent-decided routing via output directives. Conditional edges are statically defined; aqm `auto` conditions let the agent choose its successor at runtime. |

### CrewAI -> aqm Concept Mapping

| CrewAI | aqm | Notes |
|---|---|---|
| Crew | Pipeline | Both orchestrate multi-agent execution. CrewAI Crews are defined in Python; aqm Pipelines are driven by declarative YAML. |
| Agent | Agent | Both represent an LLM-powered worker with a role and instructions. aqm agents additionally declare their runtime, handoffs, gates, and MCP server connections. |
| Task | Task + Handoff | A CrewAI Task combines "what to do" with "who does it next." aqm separates these concerns: the Task is the work item, and Handoffs define the routing. |
| Process (sequential) | Handoff chain | CrewAI's sequential process runs tasks in list order. aqm achieves the same with a chain of `condition: always` handoffs, but also supports branching and loops. |
| Process (hierarchical) | Agent-decided routing | CrewAI's hierarchical process uses a manager agent to delegate. aqm's `condition: auto` with `HANDOFF:` directives is similar but more explicit -- the routing agent states its decision in its output text. |
| (none) | Gate | CrewAI has no built-in approve/reject checkpoint mechanism. Quality control must be implemented inside agent instructions or as custom Python code. |
| (none) | Queue | CrewAI has no inter-agent message bus. Task handoff is implicit and in-memory. aqm queues are explicit, persistent, and queryable. |
| (none) | Context file | CrewAI has no cumulative execution history. Each task receives only the previous task's output. aqm's context.md gives every agent access to the full pipeline history. |
| (none) | Fan-out | CrewAI does not support parallel task execution within a single crew. aqm supports fan-out via comma-separated `to` fields, spawning child tasks for parallel branches. |
| (none) | Registry | CrewAI has no package manager for crew/agent definitions. Sharing requires copying Python code. aqm's YAML-first design makes registry-based sharing feasible. |
