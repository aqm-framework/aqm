"""CLI — Click-based command-line interface.

aqm init       Initialize project
aqm run        Run pipeline
aqm fix        Follow-up on a previous task (carries over context)
aqm status     Query task status
aqm list       List tasks
aqm approve    Approve human gate
aqm reject     Reject human gate
aqm agents     List agents
aqm context    View task context
aqm validate   Validate agents.yaml against JSON Schema
aqm serve      Run web dashboard
aqm pull       Pull pipeline from registry
aqm publish    Publish pipeline to registry
aqm search     Search registry
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from aqm.core.agent import load_agents
from aqm.core.project import (
    find_project_root,
    get_agents_yaml_path,
    get_db_path,
    get_tasks_dir,
    init_project,
)
from aqm.core.task import Task, TaskStatus

console = Console()


def _prompt_for_params(
    param_defs: dict[str, "ParamDefinition"],
    cli_overrides: dict[str, str],
    overrides_file: Path | None = None,
) -> dict[str, str]:
    """Interactively prompt for unresolved params that have `prompt` set.

    For each param that:
      - has no value from CLI overrides or overrides file
      - has a `prompt` field defined
    Shows an interactive prompt with options:
      [1] Enter manually
      [2] Auto-detect from project (if auto_detect is set)

    Returns additional overrides to merge into cli_overrides.
    """
    import yaml as _yaml

    # Load existing file overrides
    file_overrides: dict[str, Any] = {}
    if overrides_file and overrides_file.exists():
        with open(overrides_file, encoding="utf-8") as f:
            file_overrides = _yaml.safe_load(f) or {}

    extra: dict[str, str] = {}
    for name, param_def in param_defs.items():
        # Skip if already resolved
        if name in cli_overrides:
            continue
        if name in file_overrides:
            continue
        if param_def.default is not None and not param_def.prompt:
            continue
        if not param_def.prompt:
            continue

        # Show the interactive prompt
        console.print(f"\n[bold cyan]?[/] [bold]{param_def.prompt}[/]")
        if param_def.description:
            console.print(f"  [dim]{param_def.description}[/]")

        has_auto = bool(param_def.auto_detect)

        console.print(f"  [green][1][/] Enter manually")
        if has_auto:
            console.print(f"  [blue][2][/] Auto-detect from project")
        if param_def.default is not None:
            console.print(f"  [dim][3][/] Use default: {param_def.default}")

        choice = click.prompt(
            "  Choice",
            type=str,
            default="1",
        )

        if choice == "1":
            value = click.prompt(f"  Value", type=str)
            extra[name] = value
        elif choice == "2" and has_auto:
            console.print(f"  [dim]Auto-detecting...[/]")
            try:
                import subprocess

                result = subprocess.run(
                    ["claude", "-p", param_def.auto_detect, "--print"],
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                detected = result.stdout.strip()
                if detected:
                    console.print(f"  [green]Detected:[/] {detected}")
                    if click.confirm("  Use this value?", default=True):
                        extra[name] = detected
                    else:
                        value = click.prompt(f"  Enter manually", type=str)
                        extra[name] = value
                else:
                    console.print(f"  [yellow]Could not auto-detect.[/]")
                    value = click.prompt(f"  Enter manually", type=str)
                    extra[name] = value
            except Exception as e:
                console.print(f"  [yellow]Auto-detect failed: {e}[/]")
                value = click.prompt(f"  Enter manually", type=str)
                extra[name] = value
        elif choice == "3" and param_def.default is not None:
            extra[name] = str(param_def.default)
        else:
            if param_def.default is not None:
                extra[name] = str(param_def.default)
            else:
                value = click.prompt(f"  Value", type=str)
                extra[name] = value

    return extra


def _require_project() -> Path:
    """Find the project root, or error if not found."""
    root = find_project_root()
    if root is None:
        console.print(
            "[red]Error:[/] Cannot find .aqm/ directory.\n"
            "Please run 'aqm init' first.",
        )
        sys.exit(1)
    return root


def _get_queue(root: Path):
    from aqm.queue.sqlite import SQLiteQueue

    return SQLiteQueue(get_db_path(root))


@click.group()
@click.option("-v", "--verbose", is_flag=True, help="Enable debug logging")
def cli(verbose: bool) -> None:
    """aqm — AI agent orchestration framework"""
    level = logging.DEBUG if verbose else logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(name)s] %(message)s",
        datefmt="%H:%M:%S",
    )


# ── init ────────────────────────────────────────────────────────────────


@cli.command()
@click.option(
    "--path",
    type=click.Path(),
    default=None,
    help="Directory to initialize (default: current directory)",
)
def init(path: str | None) -> None:
    """Initialize .aqm/ in the current project."""
    target = Path(path) if path else None
    root = init_project(target)
    agents_yaml = get_agents_yaml_path(root)
    console.print(f"[green]✓[/] .aqm/ initialization complete")
    console.print(f"  Config file: {agents_yaml}")
    console.print(
        f"\n[dim]Edit agents.yaml to configure your pipeline.[/]"
    )


# ── run ─────────────────────────────────────────────────────────────────


@cli.command()
@click.argument("input_text")
@click.option("--agent", default=None, help="Starting agent ID (default: first)")
@click.option(
    "--param", "-p",
    "params",
    multiple=True,
    help="Parameter override in key=value format (repeatable)",
)
def run(input_text: str, agent: str | None, params: tuple[str, ...]) -> None:
    """Run pipeline. Example: aqm run 'Build a login feature'"""
    root = _require_project()

    # Parse --param key=value pairs into a dict
    cli_params: dict[str, str] = {}
    for p in params:
        if "=" not in p:
            console.print(
                f"[red]Error:[/] Invalid --param format: '{p}'. "
                f"Expected key=value."
            )
            sys.exit(1)
        key, value = p.split("=", 1)
        cli_params[key.strip()] = value.strip()

    # Interactive param prompts for params with `prompt` field
    try:
        import yaml as _yaml

        agents_yaml_path = get_agents_yaml_path(root)
        with open(agents_yaml_path, encoding="utf-8") as f:
            raw_yaml = _yaml.safe_load(f)

        param_defs_raw = raw_yaml.get("params", {})
        if param_defs_raw:
            from aqm.core.agent import ParamDefinition

            param_defs: dict[str, ParamDefinition] = {}
            for pname, pval in param_defs_raw.items():
                if isinstance(pval, dict):
                    param_defs[pname] = ParamDefinition.model_validate(pval)
                else:
                    param_defs[pname] = ParamDefinition(default=pval)

            # Check if any params need interactive prompts
            overrides_file = agents_yaml_path.parent / "params.yaml"
            interactive_params = _prompt_for_params(
                param_defs, cli_params, overrides_file
            )
            cli_params.update(interactive_params)
    except Exception:
        pass  # Fall through to normal loading which will report errors

    try:
        agents = load_agents(get_agents_yaml_path(root), cli_params=cli_params or None)
    except (ValueError, FileNotFoundError) as e:
        console.print(f"[red]Error:[/] {e}")
        sys.exit(1)

    queue = _get_queue(root)

    start_agent = agent or next(iter(agents))

    from aqm.core.pipeline import Pipeline

    pipeline = Pipeline(agents, queue, root)

    task = Task(description=input_text)
    queue.push(task, start_agent)

    console.print(f"[green]✓[/] Task created: [bold]{task.id}[/]")
    console.print(f"  Starting agent: {start_agent}\n")

    def _on_stage(t: Task, stage) -> None:
        status_color = {
            "completed": "green",
            "approved": "green",
            "rejected": "red",
            "failed": "red",
        }.get(stage.gate_result or "completed", "blue")

        console.print(
            f"  [{status_color}]stage {stage.stage_number}[/] "
            f"[bold]{stage.agent_id}[/] → "
            f"{(stage.output_text[:80] + '...') if len(stage.output_text) > 80 else stage.output_text}"
        )

    result = pipeline.run_task(
        task,
        start_agent,
        on_stage_complete=_on_stage,
    )

    console.print()
    if result.status == TaskStatus.completed:
        console.print(f"[green]✓ Completed[/] {result.id}")
    elif result.status == TaskStatus.awaiting_gate:
        console.print(
            f"[yellow]⏸ Awaiting gate[/] {result.id}\n"
            f"  Proceed with 'aqm approve {result.id}' or "
            f"'aqm reject {result.id} -r \"reason\"'."
        )
    elif result.status == TaskStatus.failed:
        console.print(f"[red]✗ Failed[/] {result.id}")
    else:
        console.print(f"[dim]Status: {result.status.value}[/] {result.id}")


# ── task (alias for run, backward compat) ───────────────────────────────


@cli.command(hidden=True)
@click.argument("input_text")
@click.option("--agent", default=None)
@click.option("--param", "-p", "params", multiple=True)
@click.pass_context
def task(ctx, input_text: str, agent: str | None, params: tuple[str, ...]) -> None:
    """Create and run a task (alias for run)."""
    ctx.invoke(run, input_text=input_text, agent=agent, params=params)


# ── status ──────────────────────────────────────────────────────────────


@cli.command()
@click.argument("task_id", required=False)
def status(task_id: str | None) -> None:
    """Query task status."""
    root = _require_project()
    queue = _get_queue(root)

    if task_id:
        t = queue.get(task_id)
        if not t:
            console.print(f"[red]Task '{task_id}' not found.[/]")
            return

        console.print(f"[bold]{t.id}[/]  {t.description}")
        console.print(f"  Status: {t.status.value}")
        console.print(f"  Current agent: {t.current_agent_id or '-'}")
        console.print(f"  Created: {t.created_at.strftime('%Y-%m-%d %H:%M')}")
        console.print(f"  Stages: {len(t.stages)}\n")

        for s in t.stages:
            gate_info = ""
            if s.gate_result:
                gate_info = f" [{s.gate_result}]"
                if s.reject_reason:
                    gate_info += f" ({s.reject_reason})"
            console.print(
                f"  stage {s.stage_number}: {s.agent_id}"
                f"{gate_info}"
            )
    else:
        tasks = queue.list_tasks()
        if not tasks:
            console.print("[dim]No tasks found.[/]")
            return

        table = Table(title="Task List")
        table.add_column("ID", style="bold")
        table.add_column("Status")
        table.add_column("Agent")
        table.add_column("Description")
        table.add_column("Stages")

        for t in tasks[:20]:
            status_style = {
                "completed": "green",
                "failed": "red",
                "awaiting_gate": "yellow",
                "in_progress": "blue",
            }.get(t.status.value, "dim")

            table.add_row(
                t.id,
                f"[{status_style}]{t.status.value}[/]",
                t.current_agent_id or "-",
                (t.description[:40] + "...")
                if len(t.description) > 40
                else t.description,
                str(len(t.stages)),
            )
        console.print(table)


# ── list ────────────────────────────────────────────────────────────────


@cli.command(name="list")
@click.option(
    "--filter",
    "status_filter",
    default=None,
    help="Status filter (pending, completed, failed, awaiting_gate)",
)
def list_tasks(status_filter: str | None) -> None:
    """List tasks (filter by status)."""
    root = _require_project()
    queue = _get_queue(root)

    task_status = None
    if status_filter:
        try:
            task_status = TaskStatus(status_filter)
        except ValueError:
            console.print(f"[red]Unknown status: {status_filter}[/]")
            return

    tasks = queue.list_tasks(status=task_status)
    if not tasks:
        console.print("[dim]No tasks found.[/]")
        return

    for t in tasks:
        console.print(
            f"  {t.id}  [{t.status.value:15}]  {t.description[:50]}"
        )


# ── approve / reject ────────────────────────────────────────────────────


@cli.command()
@click.argument("task_id")
@click.option("-r", "--reason", default="", help="Approval reason")
def approve(task_id: str, reason: str) -> None:
    """Approve human gate."""
    root = _require_project()
    agents = load_agents(get_agents_yaml_path(root))
    queue = _get_queue(root)

    from aqm.core.pipeline import Pipeline

    pipeline = Pipeline(agents, queue, root)

    try:
        result = pipeline.resume_task(task_id, "approved", reason)
        console.print(f"[green]✓ Approved[/] {task_id} → {result.status.value}")
    except ValueError as e:
        console.print(f"[red]Error:[/] {e}")


@cli.command()
@click.argument("task_id")
@click.option("-r", "--reason", required=True, help="Rejection reason")
def reject(task_id: str, reason: str) -> None:
    """Reject human gate."""
    root = _require_project()
    agents = load_agents(get_agents_yaml_path(root))
    queue = _get_queue(root)

    from aqm.core.pipeline import Pipeline

    pipeline = Pipeline(agents, queue, root)

    try:
        result = pipeline.resume_task(task_id, "rejected", reason)
        console.print(f"[yellow]✗ Rejected[/] {task_id} → {result.status.value}")
    except ValueError as e:
        console.print(f"[red]Error:[/] {e}")


# ── agents ──────────────────────────────────────────────────────────────


@cli.command()
def agents() -> None:
    """List agents and print handoff graph."""
    root = _require_project()
    agent_defs = load_agents(get_agents_yaml_path(root))

    console.print("[bold]Agent Pipeline[/]\n")

    for agent in agent_defs.values():
        mcp_info = ""
        if agent.mcp:
            servers = ", ".join(m.server for m in agent.mcp)
            mcp_info = f" [dim](MCP: {servers})[/]"

        console.print(
            f"  [bold]{agent.id}[/] ({agent.name}) "
            f"[{agent.runtime}]{mcp_info}"
        )

        if agent.gate:
            console.print(
                f"    gate: {agent.gate.type}"
            )

        for h in agent.handoffs:
            console.print(
                f"    → {h.to} [dim]({h.condition})[/]"
            )

        console.print()


# ── context ─────────────────────────────────────────────────────────────


@cli.command()
@click.argument("task_id")
def context(task_id: str) -> None:
    """Print the context.md content for a task."""
    root = _require_project()
    tasks_dir = get_tasks_dir(root)
    context_path = tasks_dir / task_id / "context.md"

    if not context_path.exists():
        console.print(f"[red]Context file not found: {task_id}[/]")
        return

    console.print(context_path.read_text(encoding="utf-8"))


# ── validate ─────────────────────────────────────────────────────────────


@cli.command()
@click.argument(
    "path",
    type=click.Path(exists=True),
    default=".aqm/agents.yaml",
    required=False,
)
def validate(path: str) -> None:
    """Validate agents.yaml against the JSON Schema."""
    import json

    try:
        from jsonschema import Draft7Validator, ValidationError
    except ImportError:
        console.print(
            "[red]Error:[/] jsonschema package is required.\n"
            "  pip install 'jsonschema>=4.0'"
        )
        sys.exit(1)

    import yaml as _yaml

    # Load the YAML file
    yaml_path = Path(path)
    try:
        with open(yaml_path, encoding="utf-8") as f:
            data = _yaml.safe_load(f)
    except Exception as e:
        console.print(f"[red]Error:[/] Failed to parse YAML: {e}")
        sys.exit(1)

    if not isinstance(data, dict):
        console.print(
            "[red]Error:[/] agents.yaml must be a YAML mapping (object), "
            f"got {type(data).__name__}."
        )
        sys.exit(1)

    # Load the JSON Schema from the package
    schema_path = Path(__file__).resolve().parent.parent / "schema" / "agents-schema.json"
    if not schema_path.exists():
        console.print(
            f"[red]Error:[/] JSON Schema not found at {schema_path}.\n"
            "  Ensure the schema/ directory is installed with the package."
        )
        sys.exit(1)

    with open(schema_path, encoding="utf-8") as f:
        schema = json.load(f)

    # Validate
    validator = Draft7Validator(schema)
    errors = sorted(validator.iter_errors(data), key=lambda e: list(e.absolute_path))

    if errors:
        console.print(
            f"[red]Validation failed[/] — {len(errors)} error(s) in {yaml_path}\n"
        )
        for i, error in enumerate(errors, 1):
            field_path = " -> ".join(str(p) for p in error.absolute_path) or "(root)"
            console.print(f"  [red]{i}.[/] [bold]{field_path}[/]")
            console.print(f"     {error.message}")

            # Provide fix suggestions for common issues
            if "is a required property" in error.message:
                prop = error.message.split("'")[1]
                console.print(
                    f"     [dim]Fix: Add the '{prop}' field to your agents.yaml.[/]"
                )
            elif "is not valid under any of the given schemas" in error.message:
                console.print(
                    f"     [dim]Fix: Check the field type and format. "
                    f"See docs/spec.md for allowed values.[/]"
                )
            elif "is not one of" in error.message:
                console.print(
                    f"     [dim]Fix: Use one of the allowed values listed above.[/]"
                )
            elif "Additional properties are not allowed" in error.message:
                console.print(
                    f"     [dim]Fix: Remove the unrecognized field(s) or check spelling.[/]"
                )
            console.print()

        sys.exit(1)

    # Success — collect summary stats
    agents_list = data.get("agents", [])
    agent_count = len(agents_list)
    param_count = len(data.get("params", {}))
    import_count = len(data.get("imports", []))

    features = []
    has_gates = any(a.get("gate") for a in agents_list)
    has_mcp = any(a.get("mcp") for a in agents_list)
    has_handoffs = any(a.get("handoffs") for a in agents_list)
    has_extends = any(a.get("extends") for a in agents_list)

    if has_gates:
        gate_types = set()
        for a in agents_list:
            g = a.get("gate")
            if g:
                gate_types.add(g.get("type", "llm") if isinstance(g, dict) else "llm")
        features.append(f"gates ({', '.join(sorted(gate_types))})")
    if has_mcp:
        features.append("MCP servers")
    if has_handoffs:
        features.append("handoffs")
    if has_extends:
        features.append("extends/composition")
    if param_count > 0:
        features.append(f"{param_count} param(s)")
    if import_count > 0:
        features.append(f"{import_count} import(s)")

    console.print(
        f"[green]Valid[/] — {yaml_path}\n"
        f"  Agents: {agent_count}"
    )
    if features:
        console.print(f"  Features: {', '.join(features)}")


# ── serve ───────────────────────────────────────────────────────────────


@cli.command()
@click.option("--port", default=8000, help="Port number")
@click.option("--host", default="127.0.0.1", help="Host")
def serve(port: int, host: str) -> None:
    """Run web dashboard."""
    root = _require_project()

    console.print(
        f"[green]aqm dashboard[/] → http://{host}:{port}\n"
        f"[dim]Press Ctrl+C to stop[/]"
    )

    try:
        from aqm.web.app import create_app

        app = create_app(root)

        import uvicorn

        uvicorn.run(app, host=host, port=port, log_level="info")
    except ImportError:
        console.print(
            "[yellow]Additional packages are required to run the web dashboard:[/]\n"
            "  pip install aqm[serve]"
        )


# ── fix (follow-up task) ───────────────────────────────────────────────


@cli.command()
@click.argument("task_id")
@click.argument("input_text")
@click.option("--agent", default=None, help="Starting agent ID (default: first)")
@click.option(
    "--param", "-p",
    "params",
    multiple=True,
    help="Parameter override in key=value format (repeatable)",
)
def fix(task_id: str, input_text: str, agent: str | None, params: tuple[str, ...]) -> None:
    """Follow-up on a previous task. Carries over context.

    Example: aqm fix T-A3F2B1 "The login button color is wrong"
    """
    root = _require_project()

    # Parse --param key=value pairs
    cli_params: dict[str, str] = {}
    for p in params:
        if "=" not in p:
            console.print(
                f"[red]Error:[/] Invalid --param format: '{p}'. "
                f"Expected key=value."
            )
            sys.exit(1)
        key, value = p.split("=", 1)
        cli_params[key.strip()] = value.strip()

    # Verify parent task exists and load its context
    queue = _get_queue(root)
    parent_task = queue.get(task_id)
    if not parent_task:
        console.print(f"[red]Error:[/] Task '{task_id}' not found.")
        sys.exit(1)

    tasks_dir = get_tasks_dir(root)
    context_path = tasks_dir / task_id / "context.md"
    parent_context = ""
    if context_path.exists():
        parent_context = context_path.read_text(encoding="utf-8")

    try:
        agents = load_agents(get_agents_yaml_path(root), cli_params=cli_params or None)
    except (ValueError, FileNotFoundError) as e:
        console.print(f"[red]Error:[/] {e}")
        sys.exit(1)

    start_agent = agent or next(iter(agents))

    from aqm.core.pipeline import Pipeline

    pipeline = Pipeline(agents, queue, root)

    # Build the follow-up input with parent context
    followup_input = (
        f"[FIX — follow-up from {task_id}]\n"
        f"Description: {parent_task.description}\n\n"
        f"--- Previous context ---\n{parent_context}\n"
        f"--- Fix request ---\n{input_text}"
    )

    task = Task(
        description=f"[fix] {input_text}",
        parent_task_id=task_id,
        metadata={"kind": "fix", "parent_task_id": task_id},
    )
    queue.push(task, start_agent)

    console.print(
        f"[green]✓[/] Fix task created: [bold]{task.id}[/]"
        f" (from {task_id})"
    )
    console.print(f"  Starting agent: {start_agent}\n")

    def _on_stage(t: Task, stage) -> None:
        status_color = {
            "completed": "green",
            "approved": "green",
            "rejected": "red",
            "failed": "red",
        }.get(stage.gate_result or "completed", "blue")

        console.print(
            f"  [{status_color}]stage {stage.stage_number}[/] "
            f"[bold]{stage.agent_id}[/] → "
            f"{(stage.output_text[:80] + '...') if len(stage.output_text) > 80 else stage.output_text}"
        )

    result = pipeline.run_task(
        task,
        start_agent,
        input_text=followup_input,
        on_stage_complete=_on_stage,
    )

    console.print()
    if result.status == TaskStatus.completed:
        console.print(f"[green]✓ Completed[/] {result.id}")
    elif result.status == TaskStatus.awaiting_gate:
        console.print(
            f"[yellow]⏸ Awaiting gate[/] {result.id}\n"
            f"  Proceed with 'aqm approve {result.id}' or "
            f"'aqm reject {result.id} -r \"reason\"'."
        )
    elif result.status == TaskStatus.failed:
        console.print(f"[red]✗ Failed[/] {result.id}")
    else:
        console.print(f"[dim]Status: {result.status.value}[/] {result.id}")


# ── pull / publish / search (registry) ──────────────────────────────────


REGISTRY_URL = "https://registry.aqm.dev"


@cli.command()
@click.argument("pipeline_name")
def pull(pipeline_name: str) -> None:
    """Pull a pipeline from the registry."""
    root = _require_project()

    console.print(
        f"[dim]Searching for '{pipeline_name}' in registry...[/]"
    )

    # TODO: Integrate with actual registry API
    console.print(
        f"[yellow]Registry feature will be available in v0.3.[/]\n"
        f"  For now, please copy the agents.yaml file manually."
    )


@cli.command()
@click.option("--name", default=None, help="Pipeline name")
@click.option("--description", default=None, help="Pipeline description")
def publish(name: str | None, description: str | None) -> None:
    """Publish a pipeline to the registry."""
    root = _require_project()
    agents_yaml = get_agents_yaml_path(root)

    if not agents_yaml.exists():
        console.print("[red]Cannot find agents.yaml.[/]")
        return

    console.print(
        f"[yellow]Registry feature will be available in v0.3.[/]\n"
        f"  For now, please share the agents.yaml file directly.\n"
        f"  File location: {agents_yaml}"
    )


@cli.command()
@click.argument("query")
def search(query: str) -> None:
    """Search for pipelines in the registry."""
    console.print(
        f"[dim]Searching for '{query}'...[/]"
    )

    # TODO: Integrate with actual registry API
    console.print(
        f"[yellow]Registry feature will be available in v0.3.[/]\n"
        f"  Community pipelines: https://github.com/topics/aqm-pipeline"
    )


if __name__ == "__main__":
    cli()
