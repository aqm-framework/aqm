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


def analyze_project(project_dir: Path) -> str:
    """Analyze the existing project to extract tech stack and structure.

    Uses Claude CLI to scan the project directory and produce a summary
    of languages, frameworks, tools, and directory structure.

    Args:
        project_dir: Root directory of the project to analyze.

    Returns:
        Project analysis summary string.
    """
    analysis_prompt = (
        "Analyze this project directory and produce a concise summary. Include:\n"
        "- Primary language(s) and frameworks\n"
        "- Build tools and package managers (package.json, pyproject.toml, etc.)\n"
        "- Project structure (key directories and their purpose)\n"
        "- Testing frameworks in use\n"
        "- CI/CD configuration if present\n"
        "- Any notable tools or services (databases, APIs, etc.)\n\n"
        "Be concise — bullet points only, no prose."
    )

    result = subprocess.run(
        ["claude", "-p", analysis_prompt, "--print"],
        capture_output=True,
        text=True,
        timeout=90,
        cwd=str(project_dir),
    )

    if result.returncode != 0 or not result.stdout.strip():
        return ""

    return result.stdout.strip()


def generate_agents_yaml(
    description: str,
    project_dir: Path | None = None,
) -> str:
    """Use Claude CLI to generate agents.yaml from a description and project context.

    When project_dir is provided, the project is analyzed first and the
    analysis is included in the generation prompt so the resulting pipeline
    is tailored to the actual tech stack.

    Args:
        description: User's description of the desired pipeline/automation.
        project_dir: Optional project directory to analyze for context.

    Returns:
        Generated YAML content string.

    Raises:
        RuntimeError: If generation fails.
    """
    spec = _load_spec()

    # Build project context section
    project_context = ""
    if project_dir:
        analysis = analyze_project(project_dir)
        if analysis:
            project_context = (
                f"\n## Existing Project Analysis\n"
                f"The pipeline will be used in a project with the following characteristics:\n"
                f"{analysis}\n\n"
                f"Tailor the pipeline to this project's tech stack. For example:\n"
                f"- Use appropriate MCP servers for the detected tools\n"
                f"- Reference the project's test framework in QA agents\n"
                f"- Match the project's language/framework in system prompts\n"
            )

    prompt = f"""You are an expert at creating aqm pipeline configurations.

Based on the user's description, generate a valid agents.yaml file.

## YAML Specification Reference
{spec}
{project_context}
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
