"""Project root detection and initialization."""

from __future__ import annotations

import subprocess
from pathlib import Path

AQM_DIR = ".aqm"
AGENTS_YAML = "agents.yaml"

# YAML spec reference for AI generation — loaded from docs/spec.md at runtime
SPEC_PATH = Path(__file__).resolve().parent.parent.parent / "docs" / "spec.md"

DEFAULT_AGENTS_YAML = """\
apiVersion: aqm/v0.1

agents:
  - id: planner
    name: Planning Agent
    runtime: api
    system_prompt: |
      You are a versatile planner.
      Analyze the user's requirements and create a detailed execution plan.

      Requirements: {{ input }}
    handoffs:
      - to: executor
        task: execute
        condition: always
        payload: "{{ output }}"

  - id: executor
    name: Execution Agent
    runtime: claude_code
    system_prompt: |
      Execute the task based on the plan.

      Plan: {{ input }}
    claude_code_flags:
      - "--allowedTools"
      - "Edit,Write,Bash,Read"
"""


def find_project_root(start: Path | None = None) -> Path | None:
    """Traverse parent directories to find the .aqm/ directory."""
    current = (start or Path.cwd()).resolve()
    for parent in [current, *current.parents]:
        if (parent / AQM_DIR).is_dir():
            return parent
    return None


def init_project(path: Path | None = None, yaml_content: str | None = None) -> Path:
    """Create the .aqm/ directory and agents.yaml.

    Args:
        path: Target directory. Defaults to cwd.
        yaml_content: Custom YAML content. Uses DEFAULT_AGENTS_YAML if None.
    """
    root = (path or Path.cwd()).resolve()
    aq_dir = root / AQM_DIR

    aq_dir.mkdir(exist_ok=True)
    (aq_dir / "tasks").mkdir(exist_ok=True)

    agents_yaml = aq_dir / AGENTS_YAML
    if not agents_yaml.exists():
        agents_yaml.write_text(yaml_content or DEFAULT_AGENTS_YAML, encoding="utf-8")

    return root


def _load_spec() -> str:
    """Load the YAML spec document for AI-assisted generation."""
    if SPEC_PATH.exists():
        return SPEC_PATH.read_text(encoding="utf-8")
    # Fallback: minimal spec summary
    return (
        "agents.yaml format: apiVersion: aqm/v0.1, agents list with id, name, "
        "runtime (api|claude_code), system_prompt, handoffs (to, task, condition, payload), "
        "gate (type: llm|human, prompt), mcp servers, params section."
    )


def generate_agents_yaml(description: str) -> str:
    """Use Claude CLI to generate agents.yaml from a natural language description.

    Args:
        description: User's description of the desired pipeline/automation.

    Returns:
        Generated YAML content string.

    Raises:
        RuntimeError: If generation fails.
    """
    spec = _load_spec()

    prompt = f"""You are an expert at creating aqm pipeline configurations.

Based on the user's description, generate a valid agents.yaml file.

## YAML Specification Reference
{spec}

## User's Pipeline Description
{description}

## Instructions
- Output ONLY the raw YAML content, no markdown fences, no explanation
- Must start with `apiVersion: aqm/v0.1`
- Include appropriate agents with clear system_prompts
- Use proper handoff conditions (always, on_approve, on_reject, auto)
- Choose runtime wisely: `api` for planning/reviewing, `claude_code` for execution
- Add gates where quality checks make sense
- Add MCP servers where agents need external tool access
- Use params for configurable values when appropriate
"""

    result = subprocess.run(
        ["claude", "-p", prompt, "--print"],
        capture_output=True,
        text=True,
        timeout=120,
    )

    if result.returncode != 0:
        raise RuntimeError(f"Claude CLI failed: {result.stderr.strip()}")

    generated = result.stdout.strip()
    if not generated:
        raise RuntimeError("Claude returned empty output")

    # Strip markdown fences if present
    if generated.startswith("```"):
        lines = generated.split("\n")
        # Remove first and last fence lines
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        generated = "\n".join(lines)

    return generated + "\n"


def get_agents_yaml_path(root: Path) -> Path:
    return root / AQM_DIR / AGENTS_YAML


def get_tasks_dir(root: Path) -> Path:
    return root / AQM_DIR / "tasks"


def get_db_path(root: Path) -> Path:
    return root / AQM_DIR / "queue.db"
