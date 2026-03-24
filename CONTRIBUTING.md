# Contributing to aqm

Thank you for your interest in contributing to aqm. This project values **pipeline contributions equally to code contributions**. If you create a useful YAML pipeline, that is just as meaningful as a pull request that touches Python source.

This guide covers everything you need to get started.

---

## Getting Started

### Prerequisites

- Python 3.11 or higher
- Claude Code CLI installed and authenticated (`npm install -g @anthropic-ai/claude-code && claude login`)
- Git

### Setup

```bash
# Clone the repository
git clone https://github.com/smoveth/aqm.git
cd aqm

# Install in development mode with dev dependencies
pip install -e ".[dev]"

# Verify everything works
pytest tests/
```

### Running the project locally

```bash
# Initialize in a test project
mkdir /tmp/test-project && cd /tmp/test-project
aqm init

# Run a pipeline
aqm run "Hello world test"

# Launch the web dashboard (requires serve extras)
pip install -e ".[serve]"
aqm serve
```

---

## Types of Contributions

### Pipeline Contributions

Pipelines are the core of the aqm ecosystem. A well-designed YAML pipeline that solves a real problem is a first-class contribution.

#### How to create a pipeline

1. Create a new directory under `examples/`:
   ```
   examples/your-pipeline-name/
   ├── agents.yaml      # The pipeline definition
   ├── README.md        # What it does, how to use it
   └── params.yaml      # (optional) Example parameter overrides
   ```

2. Define your agents in `agents.yaml`. Use the full feature set where appropriate:
   - `params` for values that users will want to customize
   - `gate` for quality control between stages
   - `extends` / `abstract` for DRY agent definitions
   - `condition: auto` when the agent should decide routing dynamically

3. Test your pipeline end-to-end with `aqm run`.

4. Write a README that explains what the pipeline does, what domain it serves, and how to customize it via params.

#### Pipeline submission template

Include this header block at the top of your `agents.yaml`:

```yaml
# Pipeline name: [name]
# Description: [one-line description of what this pipeline does]
# Author: [your GitHub handle]
# Features: [params, gate, fan-out, auto-routing, extends, imports, etc.]
# Domain: [software, content, data, legal, customer-support, research, etc.]
```

**Example:**

```yaml
# Pipeline name: contract-review
# Description: Three-stage legal contract review with risk flagging and human approval
# Author: @janedoe
# Features: params, gate (llm + human), fan-out
# Domain: legal

params:
  jurisdiction:
    type: string
    default: "US"
    description: "Legal jurisdiction for compliance checks"

agents:
  - id: extractor
    name: Contract Extractor
    runtime: api
    system_prompt: |
      Extract key clauses, dates, and obligations from this contract.
      Jurisdiction: ${{ params.jurisdiction }}
      Contract: {{ input }}
    handoffs:
      - to: risk_assessor
        condition: always

  - id: risk_assessor
    name: Risk Assessment Agent
    runtime: api
    gate:
      type: llm
      prompt: "Are there any high-risk clauses that require human review?"
    handoffs:
      - to: summarizer
        condition: on_approve
      - to: human_review
        condition: on_reject

  - id: human_review
    name: Human Review Gate
    runtime: api
    gate:
      type: human
    handoffs:
      - to: summarizer
        condition: on_approve

  - id: summarizer
    name: Summary Agent
    runtime: api
    system_prompt: |
      Produce a final contract summary with risk assessment.
      Input: {{ input }}
```

#### Pipeline quality checklist

Before submitting a pipeline PR, verify:

- [ ] Pipeline runs end-to-end without errors
- [ ] `agents.yaml` includes the submission template header
- [ ] A README.md explains the pipeline purpose, agents, and usage
- [ ] Parameterized values use `params` (not hardcoded)
- [ ] Agent IDs are descriptive and use snake_case
- [ ] System prompts include `{{ input }}` to receive task data
- [ ] Handoff conditions are appropriate (not everything should be `always`)

---

### Code Contributions

#### Architecture overview

```
aqm/
├── core/
│   ├── task.py           # Task, StageRecord, TaskStatus models
│   ├── agent.py          # AgentDefinition, YAML parsing
│   ├── pipeline.py       # Pipeline execution loop
│   ├── gate.py           # LLMGate / HumanGate evaluation
│   ├── context_file.py   # File-based context accumulation
│   ├── context.py        # Prompt builder
│   └── project.py        # Project root detection
├── queue/
│   ├── base.py           # AbstractQueue interface
│   ├── sqlite.py         # SQLiteQueue (default backend)
│   └── file.py           # FileQueue (for testing)
├── runtime/
│   ├── base.py           # AbstractRuntime interface
│   ├── api.py            # Claude API runtime (text-only)
│   └── claude_code.py    # Claude Code CLI runtime (full tools)
├── web/
│   └── app.py            # FastAPI web dashboard
└── cli.py                # Click CLI entry point
```

Key design decisions:
- **Pydantic models** for all data structures (Task, AgentDefinition, Handoff, GateConfig)
- **Abstract base classes** for Queue and Runtime, making backends swappable
- **Jinja2 templates** for system prompts and payload interpolation
- **SQLite** as the default queue backend, requiring zero configuration

#### Pull request process

1. **Fork the repo** and create a feature branch from `main`.
2. **Write your code** following the style guide below.
3. **Add tests** for any new functionality.
4. **Run the test suite** to make sure nothing is broken:
   ```bash
   pytest tests/
   ```
5. **Open a PR** against `main` with a clear description of what changed and why.

---

### Documentation Contributions

Documentation improvements are always welcome. Areas that currently need help:

- **Docstrings:** Many public classes and methods in `aqm/core/` lack Google-style docstrings. Adding them is a great first contribution.
- **Examples:** More real-world pipeline examples with detailed READMEs.
- **Tutorials:** Step-by-step guides for common use cases (content pipelines, data pipelines, support workflows).
- **Architecture docs:** Deeper explanations of the routing engine, gate evaluation, and context accumulation system.

To contribute docs, follow the same PR process as code contributions.

---

## Code Style

### Python

- **Python 3.11+** -- use modern syntax (match statements, type union `X | Y`, etc.)
- **Pydantic v2 models** for all structured data
- **Type hints** on all function signatures and return values
- **Google-style docstrings** for public classes and methods:
  ```python
  def enqueue(self, task: Task, agent_id: str) -> None:
      """Add a task to the specified agent's queue.

      Args:
          task: The task to enqueue.
          agent_id: Target agent identifier.

      Raises:
          QueueFullError: If the agent's queue has reached capacity.
      """
  ```
- **snake_case** for functions and variables, **PascalCase** for classes
- No wildcard imports (`from module import *`)
- Keep functions short and focused. If a function exceeds 40 lines, consider splitting it.

### YAML (pipelines)

- 2-space indentation
- Agent IDs in snake_case
- Include comments for non-obvious configuration
- Always use `|` (literal block scalar) for multi-line system prompts

### Tests

- Tests are required for all new features and bug fixes
- Use `pytest` with fixtures
- Mock external calls (Claude CLI) rather than making real API requests
- Place tests in `tests/` mirroring the source structure:
  ```
  tests/
  ├── test_pipeline.py
  ├── test_gate.py
  ├── test_task.py
  └── ...
  ```

---

## Review Process

### Pipeline PRs

- **Target review time: 24 hours.** Pipeline contributions should have a fast feedback loop to keep contributors engaged.
- Reviewers check: does the pipeline run, is the YAML well-structured, is the README clear, are params used appropriately.
- Minor style feedback will be given as suggestions, not blockers. We merge and iterate.

### Code PRs

- Standard code review process. At least one maintainer approval required.
- CI must pass (tests, linting).
- For larger changes, open an issue first to discuss the approach before writing code.

### What to expect

- We aim to respond to all PRs within 48 hours.
- If your PR needs changes, we will explain why clearly. Do not hesitate to ask questions.
- Once merged, you will be added to the Contributors section. Pipeline authors are credited in the registry.

---

## Questions?

- Open a GitHub Issue for bugs or feature requests
- Start a GitHub Discussion for questions or ideas
- Tag your issue with `question` if you need help getting started

We are glad to have you here. Every contribution -- whether a pipeline, a bug fix, a test, or a typo correction -- makes aqm better for everyone.
