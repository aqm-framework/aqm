"""Project root detection and initialization."""

from __future__ import annotations

from pathlib import Path

AQM_DIR = ".aqm"
AGENTS_YAML = "agents.yaml"

DEFAULT_AGENTS_YAML = """\
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


def init_project(path: Path | None = None) -> Path:
    """Create the .aqm/ directory and default agents.yaml."""
    root = (path or Path.cwd()).resolve()
    aq_dir = root / AQM_DIR

    aq_dir.mkdir(exist_ok=True)
    (aq_dir / "tasks").mkdir(exist_ok=True)

    agents_yaml = aq_dir / AGENTS_YAML
    if not agents_yaml.exists():
        agents_yaml.write_text(DEFAULT_AGENTS_YAML, encoding="utf-8")

    return root


def get_agents_yaml_path(root: Path) -> Path:
    return root / AQM_DIR / AGENTS_YAML


def get_tasks_dir(root: Path) -> Path:
    return root / AQM_DIR / "tasks"


def get_db_path(root: Path) -> Path:
    return root / AQM_DIR / "queue.db"
