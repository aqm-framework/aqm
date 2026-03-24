# aqm Launch Playbook

This document is the operational plan for launching aqm (aqm) as an open-source project. It covers the phased rollout, channel-by-channel content strategy, and a starter set of issues designed to attract first-time contributors.

---

## 1. Launch Sequence

### Phase 1: Foundation (Week 1-2)

**Objective:** Establish credibility and make the project discoverable.

| Action | Owner | Deadline |
|---|---|---|
| Publish the spec document (agents.yaml schema reference) | Core team | Day 1 |
| Publish JSON Schema for agents.yaml validation | Core team | Day 2 |
| Seed the registry with 10 pipelines (software, content, legal, data, support, translation, research, onboarding, security-audit, data-analysis) | Core team | Day 7 |
| Write CONTRIBUTING.md and Good First Issue labels | Core team | Day 3 |
| Submit to Awesome-LLM and Awesome-Agents lists | Core team | Day 10 |

**Goal metrics:**
- 100 GitHub stars
- 5 external contributors (issues, PRs, or pipeline submissions)
- 10 seed pipelines merged and documented

---

### Phase 2: Community (Week 3-4)

**Objective:** Drive adoption and build an active contributor base.

| Action | Owner | Deadline |
|---|---|---|
| Launch registry.aqm.dev (browse, search, pull pipelines) | Core team | Week 3 |
| Hacker News "Show HN" submission | Core team | Week 3, Tuesday 9am ET |
| Reddit posts to r/MachineLearning and r/LocalLLaMA | Core team | Same day as HN |
| Twitter/X launch thread | Core team | Same day as HN |
| Dev.to introductory article | Core team | Week 3 + 2 days |
| Discord / GitHub Discussions community setup | Core team | Week 3 |

**Goal metrics:**
- 500 GitHub stars
- 50 pipeline pulls from the registry
- 10 community-authored pipelines submitted

---

### Phase 3: Ecosystem (Week 5-8)

**Objective:** Transition from a single-team project to a self-sustaining ecosystem.

| Action | Owner | Deadline |
|---|---|---|
| Third-party tool integrations (VS Code extension, Raycast plugin) | Community + core | Week 5-6 |
| Conference lightning talks (local meetups, AI Engineer, PyCon) | Core team | Week 5-8 |
| Blog posts: technical deep-dive series (3 posts) | Core team | Week 5, 6, 7 |
| Invite prominent AI/ML open-source maintainers to try aqm | Core team | Week 5 |
| Publish "Pipeline of the Week" community showcase | Core team | Weekly from Week 5 |

**Goal metrics:**
- 1,000 GitHub stars
- 20 unique contributors
- 100+ pipelines in the registry
- At least 2 third-party integrations

---

## 2. Channel Strategy

### Hacker News

**Submission title:**

> Show HN: aqm -- YAML pipelines for multi-agent AI, shareable like npm packages

**First comment (author post):**

Hi HN -- I built aqm because I was frustrated with how hard it is to share multi-agent workflows. Every existing framework (LangGraph, CrewAI, AutoGen) requires you to define pipelines in code, which means sharing a workflow is basically "clone my repo and figure out my codebase." Nobody does that.

aqm takes a different approach: you declare your entire multi-agent pipeline in a single YAML file. Agents pass tasks through explicit queues. An approve/reject gate is a first-class concept, not a hack. The whole thing runs on SQLite -- no Redis, no Docker, no cloud account.

Here is what a real pipeline looks like:

```yaml
agents:
  - id: planner
    name: Planning Agent
    runtime: api
    system_prompt: |
      Analyze the requirements and write a specification.
      Requirements: {{ input }}
    handoffs:
      - to: reviewer
        condition: always

  - id: reviewer
    name: Review Agent
    runtime: api
    gate:
      type: llm
      prompt: "Is this spec complete and actionable?"
    handoffs:
      - to: developer
        condition: on_approve
      - to: planner
        condition: on_reject
        payload: "REJECTED: {{ reject_reason }}\n{{ output }}"

  - id: developer
    name: Dev Agent
    runtime: claude_code
    mcp:
      - server: github
      - server: filesystem
```

That is the entire pipeline. Run it with `aqm run "Add JWT auth"` and watch the agents collaborate -- the planner writes a spec, the reviewer evaluates it (and can reject it back), and the developer implements the approved plan with full file access via MCP.

The part I am most excited about is the sharing model. Pipelines are just YAML files, so you can pull one from the registry (`aqm pull software-dev-pipeline`), override a few parameters in params.yaml, and run it immediately. The goal is to build an npm-like ecosystem where domain experts create pipelines and everyone else benefits -- a content team shares their editorial pipeline, a legal team shares their contract review pipeline, and so on. The registry launches next month at registry.aqm.dev. Everything is MIT licensed.

---

### Reddit

#### r/MachineLearning

**Title:** [P] aqm: Declarative multi-agent orchestration in YAML with explicit queues, approval gates, and agent-decided routing

**Body:**

I have been working on aqm, an open-source framework for orchestrating multiple AI agents through explicit task queues.

**The core technical ideas:**

1. **Queue-mediated communication.** Agents do not call each other directly. Every handoff goes through a SQLite-backed queue with a unique task ID. This gives you full auditability and the ability to pause, inspect, and resume at any point.

2. **Declarative routing with three strategies.** Static routing (fixed target), fan-out (parallel dispatch to multiple agents), and agent-decided routing where the LLM itself outputs a `HANDOFF: <agent_id>` directive to choose the next step at runtime.

3. **First-class approval gates.** Gates (LLM-evaluated or human-in-the-loop) sit between agents and control flow. A reviewer agent with an LLM gate can reject a plan back to the planner in a loop until quality is sufficient. This is built into the YAML spec, not bolted on.

4. **File-based context accumulation.** Each stage appends to a `context.md` file that the next agent reads. This means context is human-inspectable, version-controllable, and does not depend on in-memory state.

5. **Parameterization and inheritance.** Pipelines support `${{ params.X }}` variables for portability, and agents can use `extends` to inherit from abstract base definitions. This makes it practical to publish reusable pipelines.

The framework uses Claude Code CLI as the runtime (both text-only and full tool-access modes). Pipelines are pure YAML, shareable via a registry (`aqm pull <name>`).

Repo: github.com/smoveth/aqm

Looking for feedback on the routing model and the gate mechanism in particular. Are there patterns from your multi-agent work that this does not cover?

---

#### r/LocalLLaMA

**Title:** aqm: Run multi-agent YAML pipelines locally with SQLite -- no cloud, no Docker, no API keys beyond your local LLM

**Body:**

I wanted to share aqm, a framework I built for running multi-agent pipelines entirely locally.

**What makes it local-first:**

- The task queue is SQLite. No Redis, no RabbitMQ, no external services.
- Pipelines are defined in a single YAML file. No cloud platform needed.
- Context between agents is stored in plain markdown files on disk. You can read them with `cat`.
- `pip install aqm` and you are running. No Docker, no Kubernetes.

**How it works:** You define agents in YAML with roles (planner, reviewer, developer, QA) and connect them with handoff rules. Agents pass tasks through queues. An optional approval gate lets an LLM (or a human) accept or reject output before it moves forward. If rejected, the task loops back to the previous agent with the rejection reason attached.

Currently it uses Claude Code CLI as the runtime, but the architecture has a clean runtime abstraction (`AbstractRuntime`) -- plugging in a local model backend (llama.cpp, Ollama) is a matter of implementing one class.

The real value proposition: pipelines are YAML files, so you can share them. I am building a registry (think npm for agent pipelines) where you can `aqm pull data-analysis-pipeline` and run it immediately with your own local model.

```bash
pip install aqm
cd my-project
aqm init
aqm run "Analyze sales data and generate a report"
```

Repo: github.com/smoveth/aqm

Interested in hearing from folks who have tried multi-agent setups with local models. What runtime integration would be most useful -- Ollama, llama-cpp-python, vLLM?

---

### Twitter/X Launch Thread

**Tweet 1 (Hook / Announcement):**

Introducing aqm: multi-agent AI pipelines defined in YAML, shared like npm packages.

No cloud. No Docker. Just `pip install` and a single YAML file.

Open source, MIT licensed.

github.com/smoveth/aqm

**Tweet 2 (Problem Statement):**

The problem with multi-agent AI today:

- LangGraph, AutoGen, CrewAI -- all require pipelines in code
- Sharing a workflow means "clone my repo and read my code"
- Most need Redis, Docker, or a cloud account just to start

There is no npm for agent workflows. Until now.

**Tweet 3 (How aqm Solves It):**

aqm in one diagram:

```
[planner] --> [reviewer] --> [developer] --> [qa]
                 |  ^              |
              reject|              |
                 +--+         fix_bugs
```

- Agents communicate through explicit queues
- Approve/reject gates control flow
- Fan-out sends tasks to multiple agents in parallel
- Agents can decide their own routing at runtime

All declared in YAML. All shareable.

**Tweet 4 (Demo / Example):**

Here is the entire pipeline for a software dev team:

```yaml
agents:
  - id: planner
    runtime: api
    handoffs:
      - to: reviewer
  - id: reviewer
    gate: { type: llm }
    handoffs:
      - to: developer
        condition: on_approve
      - to: planner
        condition: on_reject
  - id: developer
    runtime: claude_code
    mcp:
      - server: github
```

Run it:
```
aqm run "Add login feature"
```

That is it. Planner writes a spec, reviewer gates it, developer implements with full GitHub access via MCP.

**Tweet 5 (Call to Action):**

We are building the ecosystem for shareable agent pipelines.

How you can help:
- Star the repo: github.com/smoveth/aqm
- Submit a pipeline (YAML is a contribution!)
- Pick up a Good First Issue
- Tell us what pipeline you want to exist

Registry launching soon at registry.aqm.dev

---

### Dev.to

#### Article 1: Introduction / Tutorial

**Title:** "Build Your First Multi-Agent Pipeline in 5 Minutes with aqm"

**Outline:** This article walks readers through installing aqm and creating a three-agent content pipeline (researcher, writer, editor) from scratch. It covers the YAML syntax for agents, handoffs, and gates, culminating in a working pipeline that takes a topic and produces an edited article. The reader will have a running pipeline by the end and understand the core mental model of queue-mediated agent communication.

#### Article 2: Technical Deep-Dive

**Title:** "How aqm Routes Tasks: Static, Fan-Out, and Agent-Decided Handoffs Explained"

**Outline:** A deep dive into the three routing strategies in aqm and the engineering decisions behind them. The article explains how static routing maps to simple linear pipelines, how fan-out enables parallel execution with child tasks, and how agent-decided routing lets an LLM dynamically choose its successor using the `HANDOFF:` directive. Each strategy is illustrated with a real YAML pipeline and a trace of the resulting task flow.

#### Article 3: Comparison Post

**Title:** "aqm vs LangGraph vs CrewAI: When You Need Declarative Multi-Agent Pipelines"

**Outline:** An honest comparison of aqm against LangGraph and CrewAI across five dimensions: pipeline definition (YAML vs code), shareability (registry vs none), infrastructure requirements (SQLite vs Redis/cloud), approval mechanisms (first-class gates vs manual interrupts), and context management (files vs memory). The article does not claim aqm is universally better -- it identifies the specific use cases where a declarative, shareable approach wins and where code-first frameworks remain the right choice.

---

## 3. First External Contributors

### Good First Issues

Below are five issues designed to be approachable for newcomers while producing real value for the project.

---

#### Issue 1: Add Translation Pipeline to Seed Registry

**Title:** `[pipeline] Add multi-language translation pipeline`

**Description:** Create a new seed pipeline in `examples/translation-pipeline/` that translates text through a three-agent flow: a translator agent that produces the initial translation, a native-speaker reviewer agent that checks fluency and cultural accuracy, and a finalizer agent that produces the polished output. The pipeline should use `params` for source and target languages so it is reusable across language pairs.

**Labels:** `good-first-issue`, `pipeline`

**Expected time:** 1-2 hours

---

#### Issue 2: Add Docstrings to Core Module

**Title:** `[docs] Add docstrings to aqm/core/ modules`

**Description:** Several modules in `aqm/core/` (task.py, agent.py, pipeline.py, gate.py) are missing docstrings on public classes and methods. Add Google-style docstrings covering the purpose, parameters, and return values for each public API. This does not require understanding the full codebase -- each module is self-contained and the existing type hints provide strong guidance.

**Labels:** `good-first-issue`, `documentation`

**Expected time:** 2-3 hours

---

#### Issue 3: Add `--dry-run` Flag to CLI

**Title:** `[feature] Add --dry-run flag to `aqm run``

**Description:** Add a `--dry-run` flag to the `aqm run` command that validates the pipeline YAML, prints the agent graph and routing paths, and exits without actually executing any agents. This helps users verify their pipeline configuration before running it. The flag should be added to `cli.py` using Click and should call the existing YAML parsing logic without invoking the runtime.

**Labels:** `good-first-issue`, `enhancement`

**Expected time:** 2-4 hours

---

#### Issue 4: Increase Test Coverage for Gate Logic

**Title:** `[test] Add unit tests for LLM gate approve/reject flow`

**Description:** The gate module (`aqm/core/gate.py`) needs more test coverage, particularly around edge cases: what happens when the LLM returns an ambiguous response, when the gate prompt template has variables, and when a rejected task includes a multi-line reason. Add pytest tests that mock the Claude CLI call and verify the gate correctly parses approve/reject decisions under these scenarios.

**Labels:** `good-first-issue`, `testing`

**Expected time:** 2-3 hours

---

#### Issue 5: Create Pipeline Showcase Page for README

**Title:** `[community] Add "Community Pipelines" showcase section to README`

**Description:** Add a "Community Pipelines" section to the README that showcases interesting pipelines built by contributors. Create a simple table format (pipeline name, author, description, link) and populate it with the existing seed pipelines from `examples/`. Also add a short paragraph explaining how community members can submit their own pipelines for inclusion. This sets the stage for the registry launch and signals that pipeline contributions are valued equally to code.

**Labels:** `good-first-issue`, `community`

**Expected time:** 1-2 hours
