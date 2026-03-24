"""Project root detection and initialization."""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

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


def generate_clarifying_questions(
    description: str,
    project_analysis: str = "",
) -> list[dict[str, str]]:
    """Generate clarifying questions to ask the user before building the pipeline.

    Uses Claude CLI to analyze the pipeline description (and optional project
    context) and produce questions that would help create a better pipeline.

    Returns a list of dicts: [{"question": "...", "why": "...", "default": "..."}]
    An empty list means no questions are needed.
    """
    context_block = ""
    if project_analysis:
        context_block = f"\n\nPROJECT CONTEXT:\n{project_analysis}"

    prompt = f"""You are helping a user set up an automation pipeline. The pipeline is NOT limited to software development — it can be for any domain (marketing, legal, education, content creation, operations, etc.).

USER'S PIPELINE DESCRIPTION: {description}{context_block}

Based on the description above, generate 3-5 clarifying questions that would help you build a better pipeline YAML configuration. Focus on:

1. Ambiguous requirements that could go multiple ways
2. Domain-specific details that would improve agent system prompts (e.g., brand colors for design, tone for content, compliance rules for legal)
3. Quality criteria for gates (what does "good enough" mean?)
4. Handoff/routing preferences the user hasn't specified
5. Specific tools, services, or data sources the agents should use

Do NOT ask about:
- Technical YAML syntax or aqm configuration details
- Things already clear from the description or project analysis

Respond ONLY in JSON array format. Each element must have:
- "question": the question to ask (concise, 1-2 sentences)
- "why": brief reason this matters for the pipeline (shown to user as context)
- "default": a reasonable default answer if the user wants to skip (empty string if no good default)

Example response:
[
  {{"question": "What tone should the content use? (formal, casual, technical, friendly)", "why": "Affects the system prompt for writing agents", "default": "professional but approachable"}},
  {{"question": "Should QA rejection send the task back to the original author or a different reviewer?", "why": "Determines the reject handoff routing", "default": ""}}
]

Respond with ONLY the JSON array. No other text."""

    result = subprocess.run(
        ["claude", "-p", prompt, "--print", "--output-format", "text"],
        capture_output=True,
        text=True,
        timeout=60,
    )

    if result.returncode != 0 or not result.stdout.strip():
        logger.warning("Failed to generate clarifying questions: %s", result.stderr.strip())
        return []

    text = result.stdout.strip()

    # Strip markdown fences if present
    text = _strip_markdown_fences(text)

    try:
        questions = json.loads(text)
        if isinstance(questions, list):
            return [
                q for q in questions
                if isinstance(q, dict) and "question" in q
            ]
    except json.JSONDecodeError:
        # Try to find JSON array in the text
        import re
        match = re.search(r"\[.*\]", text, re.DOTALL)
        if match:
            try:
                questions = json.loads(match.group())
                if isinstance(questions, list):
                    return [
                        q for q in questions
                        if isinstance(q, dict) and "question" in q
                    ]
            except json.JSONDecodeError:
                pass

    logger.warning("Could not parse clarifying questions from AI response")
    return []


def generate_agents_yaml(
    description: str,
    project_dir: Path | None = None,
    qa_context: str = "",
    on_status: "Callable[[str], None] | None" = None,
) -> str:
    """Use Claude CLI to generate agents.yaml from a description and project context.

    When project_dir is provided, the project is analyzed first and the
    analysis is included in the generation prompt so the resulting pipeline
    is tailored to the actual tech stack.

    Args:
        description: User's description of the desired pipeline/automation.
        project_dir: Optional project directory to analyze for context.
        qa_context: Formatted Q&A string from clarifying questions.

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

    # Build Q&A context section
    qa_section = ""
    if qa_context:
        qa_section = (
            f"\n## User's Answers to Clarifying Questions\n"
            f"The user provided the following additional context. "
            f"Use these answers to tailor agent system prompts, gate criteria, "
            f"handoff routing, and params:\n\n{qa_context}\n"
        )

    prompt = f"""You are a YAML generator. You output ONLY valid YAML. No prose, no explanations, no markdown.

TASK: Generate an aqm agents.yaml pipeline configuration.

SPEC:
{spec}
{project_context}{qa_section}
USER REQUEST: {description}

RULES:
1. First line of output MUST be: apiVersion: aqm/v0.1
2. Output raw YAML only — no ```yaml fences, no comments explaining what you did, no introductory text
3. Every agent needs: id, name, runtime (api or claude_code), system_prompt
4. Use handoff conditions: always, on_approve, on_reject, auto
5. Use runtime: api for planning/reviewing, claude_code for code execution
6. Add gates (type: llm or human) where quality checks make sense
7. Add MCP servers where agents need external tools
8. Use params for configurable values
9. The "payload" field in handoffs MUST be a plain string (Jinja2 template), NEVER a dict/object.
   CORRECT:   payload: "Feature plan: {{{{ output }}}}\nFeature name: {{{{ params.feature_name }}}}"
   WRONG:     payload:
                feature_plan: "{{{{ output }}}}"
                feature_name: "{{{{ params.feature_name }}}}"
   Use newlines or markdown headings inside the string to separate sections:
     payload: "## Feature Plan\n{{{{ output }}}}\n\n## Design Report\n{{{{ agents.design_auditor.output }}}}"
10. Available Jinja2 template variables for payload: {{{{ output }}}}, {{{{ input }}}}, {{{{ reject_reason }}}}, {{{{ gate_result }}}}
    Do NOT use {{{{ params.X }}}} or {{{{ agents.X.output }}}} in payload — these are not supported. Only the four variables above are available.

IMPORTANT: Your entire response must be parseable as YAML. Do not write anything before or after the YAML."""

    result = subprocess.run(
        ["claude", "-p", prompt, "--print", "--output-format", "text"],
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
    generated = _strip_markdown_fences(generated)

    # Strip any leading non-YAML prose before apiVersion
    generated = _strip_leading_prose(generated)

    generated = generated + "\n"

    # Validate and auto-fix loop
    generated = _validate_and_fix(generated, max_retries=2, on_status=on_status)

    return generated


def _strip_markdown_fences(text: str) -> str:
    """Remove markdown code fences from generated output."""
    lines = text.split("\n")

    # Remove opening fence (```yaml, ```yml, ```)
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]

    # Remove closing fence
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]

    return "\n".join(lines)


def _strip_leading_prose(text: str) -> str:
    """Strip any non-YAML text before the actual YAML content.

    Looks for 'apiVersion:' line and discards everything before it.
    """
    lines = text.split("\n")

    for i, line in enumerate(lines):
        stripped = line.strip()
        # Found the start of actual YAML
        if stripped.startswith("apiVersion:"):
            return "\n".join(lines[i:])
        # Also accept lines starting with valid top-level keys
        if stripped.startswith(("agents:", "params:", "imports:")):
            return "\n".join(lines[i:])

    # No recognizable YAML start found — return as-is
    return text


def _validate_yaml(yaml_text: str) -> list[str]:
    """Validate YAML text against the agents-schema.json.

    Returns a list of human-readable error strings.  Empty list means valid.
    """
    import yaml as _yaml

    try:
        from jsonschema import Draft7Validator
    except ImportError:
        # Can't validate without jsonschema — treat as valid.
        logger.warning("jsonschema not installed; skipping validation")
        return []

    # Parse YAML
    try:
        data = _yaml.safe_load(yaml_text)
    except Exception as e:
        return [f"YAML parse error: {e}"]

    if not isinstance(data, dict):
        return [f"Root must be a mapping/object, got {type(data).__name__}"]

    # Load JSON Schema
    schema_path = Path(__file__).resolve().parent.parent.parent / "schema" / "agents-schema.json"
    if not schema_path.exists():
        logger.warning("JSON Schema not found at %s; skipping validation", schema_path)
        return []

    with open(schema_path, encoding="utf-8") as f:
        schema = json.load(f)

    validator = Draft7Validator(schema)
    errors = sorted(validator.iter_errors(data), key=lambda e: list(e.absolute_path))

    result: list[str] = []
    for error in errors:
        field_path = " -> ".join(str(p) for p in error.absolute_path) or "(root)"
        result.append(f"{field_path}: {error.message}")

    return result


def _validate_and_fix(
    yaml_text: str,
    max_retries: int = 2,
    on_status: "Callable[[str], None] | None" = None,
) -> str:
    """Validate generated YAML and ask Claude to fix errors if found.

    Runs up to *max_retries* fix attempts.  If all retries fail, the last
    best version is returned (caller can still show a warning).

    Args:
        yaml_text: The generated YAML string.
        max_retries: Maximum number of AI-powered fix attempts.
        on_status: Optional callback for status messages (e.g. console.print).

    Returns:
        Validated (or best-effort) YAML string.
    """
    def _status(msg: str) -> None:
        if on_status:
            on_status(msg)

    errors = _validate_yaml(yaml_text)
    if not errors:
        return yaml_text

    spec = _load_spec()

    for attempt in range(1, max_retries + 1):
        error_report = "\n".join(f"  {i}. {e}" for i, e in enumerate(errors, 1))
        _status(
            f"Validation found {len(errors)} error(s). Auto-fixing (attempt {attempt}/{max_retries})..."
        )
        logger.info(
            "[generate] Validation errors (attempt %d): %s", attempt, error_report
        )

        fix_prompt = f"""You are a YAML fixer. You receive an agents.yaml file that has validation errors.
Fix ALL errors and output the corrected YAML. Output ONLY valid YAML — no prose, no markdown fences.

SCHEMA SPEC (for reference):
{spec}

CURRENT YAML:
```
{yaml_text}
```

VALIDATION ERRORS:
{error_report}

COMMON FIXES:
- "payload" must be a plain string (Jinja2 template), NEVER a dict/object.
  WRONG: payload:
           key: "{{{{ output }}}}"
  CORRECT: payload: "key: {{{{ output }}}}"
- Available payload variables: {{{{ output }}}}, {{{{ input }}}}, {{{{ reject_reason }}}}, {{{{ gate_result }}}}
- "apiVersion" is required and must be "aqm/v0.1"
- "runtime" must be "api" or "claude_code"
- "gate.type" must be "llm" or "human"
- All handoffs need a "to" field (string)
- No additional properties beyond what the schema allows

Output the complete fixed YAML. First line MUST be: apiVersion: aqm/v0.1"""

        result = subprocess.run(
            ["claude", "-p", fix_prompt, "--print", "--output-format", "text"],
            capture_output=True,
            text=True,
            timeout=90,
        )

        if result.returncode != 0 or not result.stdout.strip():
            logger.warning("[generate] Fix attempt %d failed: %s", attempt, result.stderr.strip())
            continue

        fixed = result.stdout.strip()
        fixed = _strip_markdown_fences(fixed)
        fixed = _strip_leading_prose(fixed)
        fixed = fixed + "\n"

        new_errors = _validate_yaml(fixed)
        if not new_errors:
            _status("Validation passed.")
            return fixed

        # Fewer errors = progress; keep the better version
        if len(new_errors) < len(errors):
            yaml_text = fixed
            errors = new_errors
        else:
            # No improvement — still keep if it's valid YAML at least
            import yaml as _yaml
            try:
                _yaml.safe_load(fixed)
                yaml_text = fixed
                errors = new_errors
            except Exception:
                pass

    if errors:
        _status(
            f"Warning: {len(errors)} validation error(s) remain after {max_retries} fix attempts."
        )

    return yaml_text


def get_agents_yaml_path(root: Path) -> Path:
    return root / AQM_DIR / AGENTS_YAML


def get_tasks_dir(root: Path) -> Path:
    return root / AQM_DIR / "tasks"


def get_db_path(root: Path) -> Path:
    return root / AQM_DIR / "queue.db"
