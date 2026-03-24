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
    AVAILABLE_MODELS,
    DEFAULT_MODEL,
    deep_analyze_project,
    delete_pipeline,
    edit_pipeline_yaml,
    find_project_root,
    generate_agents_yaml,
    generate_clarifying_questions,
    get_agents_yaml_path,
    get_db_path,
    get_default_pipeline,
    get_pipeline_path,
    get_tasks_dir,
    init_project,
    list_pipelines,
    save_pipeline,
    set_default_pipeline,
)
from aqm.core.task import Task, TaskStatus

console = Console()


def _pick_model() -> str:
    """Prompt user to select a Claude model for AI generation."""
    console.print("\n[bold]Select AI model:[/]")
    for i, (model_id, label) in enumerate(AVAILABLE_MODELS, 1):
        default_mark = " [green](default)[/]" if model_id == DEFAULT_MODEL else ""
        console.print(f"  [{i}] {label}{default_mark}")
    idx = click.prompt(
        "\n  Model",
        type=click.IntRange(1, len(AVAILABLE_MODELS)),
        default=1,
    )
    return AVAILABLE_MODELS[idx - 1][0]



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
    """Initialize .aqm/ in the current project.

    Offers three setup methods:
      [1] Create default template — start with a basic planner→executor pipeline
      [2] Pull from registry — install a community or local pipeline
      [3] AI-generate — describe your pipeline and let Claude create it
    """
    target = Path(path) if path else None
    project_dir = (target or Path.cwd()).resolve()

    # Check for existing pipelines (both legacy and new locations)
    existing_pipelines = []
    legacy_yaml = project_dir / ".aqm" / "agents.yaml"
    pipelines_dir = project_dir / ".aqm" / "pipelines"
    if legacy_yaml.exists():
        existing_pipelines.append(legacy_yaml)
    if pipelines_dir.exists():
        existing_pipelines.extend(pipelines_dir.glob("*.yaml"))

    if existing_pipelines:
        console.print(
            f"\n[yellow]Warning:[/] Existing pipeline(s) found in .aqm/:\n"
            f"  {', '.join(p.name for p in existing_pipelines)}\n"
        )
        overwrite = click.confirm(
            "  Delete all existing pipelines and start fresh?", default=False
        )
        if not overwrite:
            console.print("[dim]Cancelled. Existing pipelines unchanged.[/]")
            return
        for p in existing_pipelines:
            p.unlink()
        console.print("[dim]Existing pipelines removed.[/]\n")

    console.print("\n[bold]How would you like to set up your pipeline?[/]\n")
    console.print("  [magenta][1][/] AI-generate from description")
    console.print("  [green][2][/] Create default template")
    console.print("  [blue][3][/] Pull from registry")

    choice = click.prompt("\n  Choice", type=click.IntRange(1, 3), default=1)

    if choice == 1:
        # AI-generate from description
        _init_from_ai(target)

    elif choice == 2:
        # Default template
        root = init_project(target)
        agents_yaml = get_agents_yaml_path(root)
        console.print(f"\n[green]✓[/] .aqm/ initialization complete")
        console.print(f"  Config file: {agents_yaml}")
        console.print(
            f"\n  Run [bold]aqm run \"your task\"[/] to start the pipeline.\n"
            f"  Run [bold]aqm serve[/] to open the web dashboard."
        )

    elif choice == 3:
        # Pull from registry
        _init_from_registry(target)


def _init_from_registry(target: Path | None) -> None:
    """Interactive registry pull during init."""
    # Show available pipelines
    results: list[tuple[str, str]] = []

    registry_dir = _get_registry_dir()
    if registry_dir.is_dir():
        for d in sorted(registry_dir.iterdir()):
            if d.is_dir() and (d / "agents.yaml").exists():
                if not any(r[0] == d.name for r in results):
                    results.append((d.name, "local"))

    if not results:
        console.print("[yellow]No pipelines available in registry.[/]")
        console.print("[dim]Falling back to default template.[/]")
        root = init_project(target)
        console.print(f"[green]✓[/] .aqm/ initialized with default template")
        return

    console.print(f"\n[bold]Available pipelines:[/]\n")
    for i, (name, source) in enumerate(results, 1):
        src_tag = f"[blue]{source}[/]"
        console.print(f"  [green][{i}][/] {name}  {src_tag}")

    idx = click.prompt(
        f"\n  Select pipeline",
        type=click.IntRange(1, len(results)),
        default=1,
    )
    pipeline_name, _ = results[idx - 1]

    # Find source YAML
    source_yaml: Path | None = None
    local_path = registry_dir / pipeline_name / "agents.yaml"
    if local_path.exists():
        source_yaml = local_path

    if not source_yaml:
        console.print(f"[red]Pipeline '{pipeline_name}' not found.[/]")
        sys.exit(1)

    content = source_yaml.read_text(encoding="utf-8")
    root = init_project(target, yaml_content=content)
    agents_yaml = get_agents_yaml_path(root)

    import yaml as _yaml
    data = _yaml.safe_load(content)
    agent_count = len(data.get("agents", []))

    console.print(
        f"\n[green]✓[/] .aqm/ initialized with [bold]{pipeline_name}[/]\n"
        f"  Agents: {agent_count}\n"
        f"  Config file: {agents_yaml}\n"
        f"\n  Run [bold]aqm run \"your task\"[/] to start the pipeline.\n"
        f"  Run [bold]aqm serve[/] to open the web dashboard."
    )


def _init_from_ai(target: Path | None) -> None:
    """AI-generate agents.yaml from user description with project analysis."""
    project_dir = (Path(target) if target else Path.cwd()).resolve()

    # Step 0: Select model
    selected_model = _pick_model()
    console.print(f"  [dim]Using: {selected_model}[/]\n")

    # Step 1: Get pipeline description first
    console.print(
        "\n[bold]Describe the pipeline you want to create.[/]\n"
        "[dim]Examples:[/]\n"
        '  [dim]"Code review pipeline with planning, implementation, and QA stages"[/]\n'
        '  [dim]"Blog content pipeline: research → write → edit → SEO optimize"[/]\n'
        '  [dim]"Customer support triage that routes to technical or billing agents"[/]\n'
    )

    description = click.prompt("  Pipeline description", type=str)
    # Normalize multi-line pasted input to a single line and flush stdin
    # to prevent leftover newlines from leaking into subsequent prompts.
    description = " ".join(description.splitlines()).strip()
    try:
        import termios
        termios.tcflush(sys.stdin.fileno(), termios.TCIFLUSH)
    except (ImportError, termios.error, OSError):
        pass

    # Step 2: Analyze project (after description, so analysis can be contextual)
    analysis = ""
    has_project = any(
        p for p in project_dir.iterdir()
        if p.name not in {".git", ".aqm", "__pycache__", "node_modules", ".venv"}
    ) if project_dir.exists() else False

    if has_project:
        from aqm.core.project import analyze_project
        with console.status("[bold cyan]Analyzing project...[/]", spinner="dots"):
            analysis = analyze_project(project_dir, model=selected_model)
        if analysis:
            console.print(f"\n[bold]Project analysis:[/]\n")
            console.print(f"[dim]{analysis}[/]\n")
        else:
            console.print("[dim]Could not analyze project (continuing without context).[/]\n")
            has_project = False

    # Step 3: Generate clarifying questions and collect answers
    project_analysis_text = analysis if has_project else ""
    with console.status("[bold cyan]Preparing questions...[/]", spinner="dots"):
        questions = generate_clarifying_questions(description, project_analysis_text, model=selected_model)

    qa_context = ""
    if questions:
        console.print(
            f"\n[bold]A few questions to build a better pipeline[/] "
            f"[dim](press Enter to use default)[/]\n"
        )
        qa_pairs: list[str] = []
        for i, q in enumerate(questions, 1):
            question_text = q.get("question", "")
            why_text = q.get("why", "")
            default_text = q.get("default", "")

            if why_text:
                console.print(f"  [dim]{why_text}[/]")

            answer = click.prompt(
                f"  [bold]Q{i}.[/] {question_text}",
                default=default_text or "",
                show_default=bool(default_text),
            )
            if answer:
                qa_pairs.append(f"Q: {question_text}\nA: {answer}")
            console.print()

        qa_context = "\n\n".join(qa_pairs)
    else:
        console.print("[dim]No additional questions needed.[/]\n")

    # Step 4: Targeted re-analysis based on Q&A answers
    deep_analysis_text = ""
    if has_project and qa_context:
        with console.status("[bold cyan]Investigating project based on your answers...[/]", spinner="dots"):
            deep_analysis_text = deep_analyze_project(
                project_dir, qa_context, initial_analysis=analysis, model=selected_model,
            )
        if deep_analysis_text:
            console.print(f"\n[bold]Additional findings:[/]\n")
            console.print(f"[dim]{deep_analysis_text}[/]\n")
        else:
            console.print("[dim]No additional investigation needed.[/]\n")

    # Step 5: Generate YAML (with spinner)
    gen_msg = (
        "Generating agents.yaml (project analysis + your answers + YAML spec)..."
        if has_project
        else "Generating agents.yaml (your answers + YAML spec)..."
    )

    try:
        with console.status(f"[bold cyan]{gen_msg}[/]", spinner="dots") as status:
            def _update_status(msg: str) -> None:
                status.update(f"[bold cyan]{msg}[/]")

            generated = generate_agents_yaml(
                description,
                project_dir=project_dir if has_project else None,
                qa_context=qa_context,
                deep_analysis=deep_analysis_text,
                on_status=_update_status,
                model=selected_model,
            )
    except Exception as e:
        console.print(f"[red]Generation failed:[/] {e}")
        console.print("[dim]Falling back to default template.[/]")
        root = init_project(target)
        console.print(f"[green]✓[/] .aqm/ initialized with default template")
        return

    # Preview the generated YAML
    console.print("\n[bold]Generated agents.yaml:[/]\n")
    from rich.syntax import Syntax
    console.print(Syntax(generated, "yaml", theme="monokai", line_numbers=True))

    action = click.prompt(
        "\n  [1] Use this pipeline  [2] Regenerate  [3] Use default template\n  Choice",
        type=click.IntRange(1, 3),
        default=1,
    )

    if action == 2:
        refined = click.prompt("  Refined description (or press Enter to retry)", default=description)
        console.print(f"\n[dim]Regenerating...[/]")
        try:
            generated = generate_agents_yaml(
                refined,
                project_dir=project_dir if has_project else None,
                qa_context=qa_context,
                deep_analysis=deep_analysis_text,
                on_status=_print_status,
                model=selected_model,
            )
            console.print("\n[bold]Regenerated agents.yaml:[/]\n")
            console.print(Syntax(generated, "yaml", theme="monokai", line_numbers=True))
            if not click.confirm("\n  Use this pipeline?", default=True):
                console.print("[dim]Using default template instead.[/]")
                root = init_project(target)
                console.print(f"[green]✓[/] .aqm/ initialized with default template")
                return
        except Exception as e:
            console.print(f"[red]Regeneration failed:[/] {e}")
            console.print("[dim]Using default template.[/]")
            root = init_project(target)
            console.print(f"[green]✓[/] .aqm/ initialized with default template")
            return

    if action == 3:
        root = init_project(target)
        console.print(f"\n[green]✓[/] .aqm/ initialized with default template")
        return

    root = init_project(target, yaml_content=generated)
    agents_yaml = get_agents_yaml_path(root)

    console.print(
        f"\n[green]✓[/] .aqm/ initialized with AI-generated pipeline\n"
        f"  Config file: {agents_yaml}\n"
        f"\n  Run [bold]aqm validate[/] to check the configuration.\n"
        f"  Run [bold]aqm run \"your task\"[/] to start the pipeline.\n"
        f"  Run [bold]aqm serve[/] to open the web dashboard."
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
@click.option(
    "--priority",
    type=click.Choice(["critical", "high", "normal", "low"]),
    default="normal",
    help="Task priority (default: normal)",
)
@click.option(
    "--parallel",
    is_flag=True,
    help="Run in parallel with other tasks (default: sequential)",
)
@click.option(
    "--pipeline", "pipeline_name",
    default=None,
    help="Pipeline name to use (default: default pipeline)",
)
def run(input_text: str, agent: str | None, params: tuple[str, ...], priority: str, parallel: bool, pipeline_name: str | None) -> None:
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

        agents_yaml_path = get_agents_yaml_path(root, pipeline_name)
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
            # Look for params.yaml in pipeline dir, then .aqm/ dir
            overrides_file = agents_yaml_path.parent / "params.yaml"
            if not overrides_file.exists():
                aqm_params = root / ".aqm" / "params.yaml"
                if aqm_params.exists():
                    overrides_file = aqm_params
            interactive_params = _prompt_for_params(
                param_defs, cli_params, overrides_file
            )
            cli_params.update(interactive_params)
    except Exception:
        pass  # Fall through to normal loading which will report errors

    try:
        agents = load_agents(get_agents_yaml_path(root, pipeline_name), cli_params=cli_params or None)
    except (ValueError, FileNotFoundError) as e:
        console.print(f"[red]Error:[/] {e}")
        sys.exit(1)

    from aqm.core.task import TaskPriority

    queue = _get_queue(root)

    # Check for running tasks (sequential mode)
    if not parallel:
        running = queue.list_tasks(status=TaskStatus.in_progress)
        if running:
            console.print(
                f"[yellow]⏳ Waiting...[/] {len(running)} task(s) running "
                f"(use --parallel to skip waiting)"
            )
            import time as _time
            while True:
                _time.sleep(2)
                running = queue.list_tasks(status=TaskStatus.in_progress)
                if not running:
                    break

    start_agent = agent or next(iter(agents))

    from aqm.core.pipeline import Pipeline

    pipeline = Pipeline(agents, queue, root)

    task_priority = TaskPriority[priority]
    task_metadata = {"pipeline": pipeline_name} if pipeline_name else {}
    task = Task(description=input_text, priority=task_priority, metadata=task_metadata)
    queue.push(task, start_agent)

    priority_label = f" [{priority}]" if priority != "normal" else ""
    console.print(f"[green]✓[/] Task created: [bold]{task.id}[/]{priority_label}")
    console.print(f"  Starting agent: {start_agent}\n")

    if parallel:
        console.print(
            f"[yellow]⚠ Parallel mode:[/] Multiple agents may modify files simultaneously.\n"
            f"  Use [bold]git diff[/] to review changes if conflicts occur.\n"
        )

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

    # Stream output lines to terminal in real-time
    def _on_output(line: str) -> None:
        if line.strip():
            console.print(f"    [dim]{line}[/]")

    result = pipeline.run_task(
        task,
        start_agent,
        on_stage_complete=_on_stage,
        on_output=_on_output,
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
                "cancelled": "yellow",
                "stalled": "yellow",
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

    status_colors = {
        "pending": "dim",
        "in_progress": "blue",
        "completed": "green",
        "failed": "red",
        "awaiting_gate": "yellow",
        "approved": "green",
        "rejected": "red",
        "cancelled": "dim",
        "stalled": "yellow",
    }

    table = Table(show_header=True, header_style="bold")
    table.add_column("ID", style="bold")
    table.add_column("Status")
    table.add_column("Agent")
    table.add_column("Stages", justify="right")
    table.add_column("Description")

    for t in tasks:
        color = status_colors.get(t.status.value, "dim")
        table.add_row(
            t.id,
            f"[{color}]{t.status.value}[/{color}]",
            t.current_agent_id or "-",
            str(len(t.stages)),
            t.description[:60],
        )

    console.print(table)


# ── approve / reject ────────────────────────────────────────────────────


@cli.command()
@click.argument("task_id")
@click.option("-r", "--reason", default="", help="Approval reason")
def approve(task_id: str, reason: str) -> None:
    """Approve human gate."""
    root = _require_project()
    queue = _get_queue(root)
    task = queue.get(task_id)
    pipe_name = task.metadata.get("pipeline") if task else None
    agents = load_agents(get_agents_yaml_path(root, pipe_name))

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
    queue = _get_queue(root)
    task = queue.get(task_id)
    pipe_name = task.metadata.get("pipeline") if task else None
    agents = load_agents(get_agents_yaml_path(root, pipe_name))

    from aqm.core.pipeline import Pipeline

    pipeline = Pipeline(agents, queue, root)

    try:
        result = pipeline.resume_task(task_id, "rejected", reason)
        console.print(f"[yellow]✗ Rejected[/] {task_id} → {result.status.value}")
    except ValueError as e:
        console.print(f"[red]Error:[/] {e}")


# ── cancel ─────────────────────────────────────────────────────────────


@cli.command()
@click.argument("task_id")
@click.option("-r", "--reason", default="", help="Cancellation reason")
def cancel(task_id: str, reason: str) -> None:
    """Cancel a running or pending task.

    For in_progress tasks, signals the pipeline to stop at the next stage boundary.
    Any code changes made by completed stages are preserved (use git to review/revert).
    """
    root = _require_project()
    queue = _get_queue(root)

    task = queue.get(task_id)
    if not task:
        console.print(f"[red]Error:[/] Task '{task_id}' not found.")
        return

    if task.status in (TaskStatus.completed, TaskStatus.failed):
        console.print(
            f"[yellow]Task {task_id} is already {task.status.value}.[/]"
        )
        return

    from aqm.core.task import TaskStatus as TS

    if task.status == TS("cancelled"):
        console.print(f"[yellow]Task {task_id} is already cancelled.[/]")
        return

    # For in_progress tasks, also signal the pipeline loop to stop
    if task.status == TaskStatus.in_progress:
        from aqm.core.pipeline import cancel_task
        cancel_task(task_id)

    # Cancel immediately in DB for all states (including stalled)
    task.status = TS("cancelled")
    task.metadata["cancel_reason"] = reason or "Cancelled by user"
    task.touch()
    queue.update(task)

    console.print(f"[green]✓ Cancelled[/] {task_id}")
    if task.stages:
        console.print(
            f"  {len(task.stages)} stage(s) completed before cancellation.\n"
            f"  Use [bold]git diff[/] to review any code changes."
        )


# ── priority ───────────────────────────────────────────────────────────


@cli.command()
@click.argument("task_id")
@click.argument(
    "level",
    type=click.Choice(["critical", "high", "normal", "low"]),
)
def priority(task_id: str, level: str) -> None:
    """Change task priority. Example: aqm priority T-A3F2B1 high"""
    from aqm.core.task import TaskPriority

    root = _require_project()
    queue = _get_queue(root)

    task = queue.get(task_id)
    if not task:
        console.print(f"[red]Error:[/] Task '{task_id}' not found.")
        return

    old_priority = task.priority.name
    task.priority = TaskPriority[level]
    task.touch()
    queue.update(task)

    console.print(
        f"[green]✓[/] Priority changed: {task_id} "
        f"[dim]{old_priority}[/] → [bold]{level}[/]"
    )


# ── agents ──────────────────────────────────────────────────────────────


@cli.command()
@click.option("--pipeline", "pipeline_name", default=None, help="Pipeline name")
def agents(pipeline_name: str | None) -> None:
    """List agents and print handoff graph."""
    root = _require_project()
    agent_defs = load_agents(get_agents_yaml_path(root, pipeline_name))

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
    type=click.Path(),
    default=None,
    required=False,
)
@click.option("--pipeline", "pipeline_name", default=None, help="Pipeline name to validate")
def validate(path: str | None, pipeline_name: str | None) -> None:
    """Validate agents.yaml against the JSON Schema."""
    import json

    # Resolve path: explicit arg > --pipeline > default pipeline
    if path is None:
        root = find_project_root()
        if root:
            try:
                resolved = get_agents_yaml_path(root, pipeline_name)
                path = str(resolved)
            except FileNotFoundError:
                pass
        if path is None:
            path = ".aqm/agents.yaml"  # legacy fallback

    if not Path(path).exists():
        console.print(f"[red]Error:[/] File not found: {path}")
        sys.exit(1)

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

    # Load the JSON Schema — try package-internal path first, then project root
    schema_path = Path(__file__).resolve().parent / "schema" / "agents-schema.json"
    if not schema_path.exists():
        schema_path = Path(__file__).resolve().parent.parent / "schema" / "agents-schema.json"
    if not schema_path.exists():
        console.print(
            f"[red]Error:[/] JSON Schema not found.\n"
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
        pipe_name = parent_task.metadata.get("pipeline") if parent_task else None
        agents = load_agents(get_agents_yaml_path(root, pipe_name), cli_params=cli_params or None)
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


REGISTRY_DIR_NAME = "registry"


def _get_registry_dir() -> Path:
    """Get the global registry directory (~/.aqm/registry/)."""
    registry = Path.home() / ".aqm" / REGISTRY_DIR_NAME
    registry.mkdir(parents=True, exist_ok=True)
    return registry


@cli.command()
@click.argument("pipeline_name")
@click.option(
    "--repo",
    default=None,
    help="GitHub registry repo (default: aqm-framework/registry)",
)
@click.option(
    "--offline",
    is_flag=True,
    help="Skip GitHub, only search local registry",
)
def pull(pipeline_name: str, repo: str | None, offline: bool) -> None:
    """Pull a pipeline and install it into .aqm/pipelines/.

    Searches in order:
      1. GitHub registry (aqm-framework/registry)
      2. Local registry (~/.aqm/registry/)

    Example: aqm pull software-feature-pipeline
    """
    from aqm.registry import pull_from_github, DEFAULT_REGISTRY_REPO

    root = _require_project()
    registry_repo = repo or DEFAULT_REGISTRY_REPO

    console.print(f"[dim]Searching for '{pipeline_name}'...[/]")

    content: str | None = None
    source_label = ""

    # 1. GitHub registry
    if not offline:
        console.print(f"  [dim]Checking GitHub ({registry_repo})...[/]")
        result = pull_from_github(pipeline_name, repo=registry_repo)
        if result:
            content, meta = result
            source_label = f"github ({registry_repo})"
            console.print(f"  [green]Found on GitHub[/]")

    # 2. Local registry
    if content is None:
        registry_dir = _get_registry_dir()
        local_path = registry_dir / pipeline_name / "agents.yaml"
        if local_path.exists():
            content = local_path.read_text(encoding="utf-8")
            source_label = "local registry"

    if content is None:
        console.print(
            f"[red]Pipeline '{pipeline_name}' not found.[/]\n"
            f"  Searched:\n"
            f"    - GitHub: {registry_repo}\n"
            f"    - Local: {_get_registry_dir()}\n"
            f"\n  Use 'aqm search' to list available pipelines."
        )
        sys.exit(1)

    # Save to pipelines directory
    import yaml as _yaml

    existing = list_pipelines(root)
    if pipeline_name in existing:
        if not click.confirm(
            f"  Pipeline '{pipeline_name}' already exists. Overwrite?",
            default=False,
        ):
            console.print("[dim]Cancelled.[/]")
            return

    target = save_pipeline(root, pipeline_name, content)

    data = _yaml.safe_load(content)
    agent_count = len(data.get("agents", []))
    param_count = len(data.get("params", {}))

    console.print(
        f"[green]✓[/] Pulled [bold]{pipeline_name}[/] from {source_label}\n"
        f"  Agents: {agent_count}"
    )
    if param_count:
        console.print(f"  Params: {param_count}")
    console.print(
        f"  Installed to: {target}\n"
        f"\n  Run [bold]aqm run --pipeline {pipeline_name} \"your task\"[/] to start."
    )


@cli.command()
@click.option("--name", default=None, help="Pipeline name (default: directory name)")
@click.option("--description", default=None, help="Pipeline description")
@click.option(
    "--repo",
    default=None,
    help="GitHub registry repo (default: aqm-framework/registry)",
)
@click.option(
    "--local",
    is_flag=True,
    help="Publish to local registry only (skip GitHub PR)",
)
def publish(
    name: str | None,
    description: str | None,
    repo: str | None,
    local: bool,
) -> None:
    """Publish .aqm/agents.yaml to the registry.

    By default, creates a PR to the GitHub registry repo.
    Use --local to save only to ~/.aqm/registry/ without a PR.

    Example: aqm publish --name my-pipeline
    """
    from aqm.registry import publish_to_github, DEFAULT_REGISTRY_REPO

    root = _require_project()
    agents_yaml = get_agents_yaml_path(root)

    if not agents_yaml.exists():
        console.print("[red]Cannot find pipeline YAML file.[/]")
        return

    # Validate the YAML first
    import yaml as _yaml

    try:
        with open(agents_yaml, encoding="utf-8") as f:
            data = _yaml.safe_load(f)
    except Exception as e:
        console.print(f"[red]Error:[/] Failed to parse agents.yaml: {e}")
        return

    if not isinstance(data, dict) or "agents" not in data:
        console.print(
            "[red]Error:[/] agents.yaml must have an 'agents' key."
        )
        return

    pipeline_name = name or root.name
    agent_count = len(data.get("agents", []))

    # Always save to local registry
    registry_dir = _get_registry_dir()
    target_dir = registry_dir / pipeline_name
    target_dir.mkdir(parents=True, exist_ok=True)

    target_yaml = target_dir / "agents.yaml"
    content = agents_yaml.read_text(encoding="utf-8")
    target_yaml.write_text(content, encoding="utf-8")

    import json

    meta = {
        "name": pipeline_name,
        "description": description or "",
        "agents_count": agent_count,
        "params": len(data.get("params", {})),
        "source": str(root),
    }
    (target_dir / "meta.json").write_text(
        json.dumps(meta, indent=2) + "\n", encoding="utf-8"
    )

    console.print(
        f"[green]✓[/] Saved [bold]{pipeline_name}[/] to local registry\n"
        f"  Agents: {agent_count}\n"
        f"  Location: {target_dir}"
    )

    if local:
        console.print(
            f"\n  Pull from any project: [bold]aqm pull {pipeline_name} --offline[/]"
        )
        return

    # Publish to GitHub via PR
    registry_repo = repo or DEFAULT_REGISTRY_REPO
    console.print(
        f"\n[dim]Creating PR to {registry_repo}...[/]"
    )

    result = publish_to_github(
        agents_yaml_path=agents_yaml,
        pipeline_name=pipeline_name,
        description=description or "",
        repo=registry_repo,
    )

    if result.success:
        console.print(
            f"[green]✓[/] PR created: [bold]{result.pr_url}[/]\n"
            f"\n  Your pipeline will be available after the PR is reviewed and merged."
        )
    else:
        console.print(
            f"[yellow]⚠[/] GitHub publish failed: {result.error}\n"
            f"\n  Pipeline is still available locally: "
            f"[bold]aqm pull {pipeline_name} --offline[/]"
        )


@cli.command()
@click.argument("query", required=False, default=None)
@click.option(
    "--repo",
    default=None,
    help="GitHub registry repo (default: aqm-framework/registry)",
)
@click.option(
    "--offline",
    is_flag=True,
    help="Skip GitHub, only search local registry",
)
def search(query: str | None, repo: str | None, offline: bool) -> None:
    """Search for available pipelines.

    Lists pipelines from GitHub registry and local registry.
    Optionally filter by keyword.

    Example: aqm search code
    """
    from aqm.registry import search_github, DEFAULT_REGISTRY_REPO

    results: list[tuple[str, str, str]] = []  # (name, source, description)
    seen_names: set[str] = set()

    # 1. GitHub registry
    if not offline:
        registry_repo = repo or DEFAULT_REGISTRY_REPO
        console.print(f"[dim]Searching GitHub ({registry_repo})...[/]")
        github_results = search_github(query=query, repo=registry_repo)
        for meta in github_results:
            results.append((meta.name, "github", meta.description))
            seen_names.add(meta.name)

    # 2. Local registry
    registry_dir = _get_registry_dir()
    if registry_dir.is_dir():
        for d in sorted(registry_dir.iterdir()):
            if d.is_dir() and (d / "agents.yaml").exists():
                desc = ""
                meta_path = d / "meta.json"
                if meta_path.exists():
                    import json

                    meta_data = json.loads(
                        meta_path.read_text(encoding="utf-8")
                    )
                    desc = meta_data.get("description", "")
                if d.name in seen_names:
                    results = [
                        (n, f"{s}+local" if n == d.name else s, de or desc)
                        for n, s, de in results
                    ]
                else:
                    results.append((d.name, "local", desc))
                    seen_names.add(d.name)

    # Filter by query (for local/bundled that weren't pre-filtered)
    if query:
        q = query.lower()
        results = [
            (n, s, d)
            for n, s, d in results
            if q in n.lower() or q in d.lower()
        ]

    if not results:
        if query:
            console.print(f"[dim]No pipelines matching '{query}'.[/]")
        else:
            console.print("[dim]No pipelines found.[/]")
        return

    table = Table(title="Available Pipelines")
    table.add_column("Name", style="bold")
    table.add_column("Source")
    table.add_column("Description")

    source_styles = {
        "github": "[magenta]github[/]",
        "bundled": "[blue]bundled[/]",
        "local": "[green]local[/]",
    }

    for pipeline_name, source, desc in results:
        # Style composite sources like "github+bundled"
        styled_source = source
        for key, style in source_styles.items():
            styled_source = styled_source.replace(key, style)
        table.add_row(pipeline_name, styled_source, desc or "[dim]-[/]")

    console.print(table)
    console.print(
        f"\n  Pull a pipeline: [bold]aqm pull <pipeline-name>[/]"
    )


# ── pipeline management ────────────────────────────────────────────────


@cli.group(name="pipeline")
def pipeline_group() -> None:
    """Manage multiple pipelines in the project."""
    pass


@pipeline_group.command(name="list")
def pipeline_list_cmd() -> None:
    """List all pipelines in the project."""
    root = _require_project()
    pipelines = list_pipelines(root)
    default = get_default_pipeline(root) or "default"

    if not pipelines:
        console.print("[dim]No pipelines found. Run [bold]aqm init[/] to create one.[/]")
        return

    console.print("[bold]Pipelines[/]\n")
    for name in pipelines:
        is_default = " [green]★ default[/]" if name == default else ""
        try:
            path = get_pipeline_path(root, name)
            import yaml as _yaml
            with open(path, encoding="utf-8") as f:
                data = _yaml.safe_load(f)
            agent_count = len(data.get("agents", []))
            console.print(f"  [bold]{name}[/] ({agent_count} agents){is_default}")
        except Exception:
            console.print(f"  [bold]{name}[/]{is_default}")

    console.print(
        f"\n  Use [bold]aqm run --pipeline <name> \"task\"[/] to run a specific pipeline."
    )


@pipeline_group.command(name="create")
@click.argument("name")
@click.option("--ai", is_flag=True, help="AI-generate the pipeline")
@click.option("--template", is_flag=True, help="Use default template")
def pipeline_create_cmd(name: str, ai: bool, template: bool) -> None:
    """Create a new pipeline.  Example: aqm pipeline create code-review"""
    root = _require_project()
    pipelines = list_pipelines(root)

    if name in pipelines:
        console.print(f"[red]Error:[/] Pipeline '{name}' already exists.")
        sys.exit(1)

    if ai:
        # Use AI generation flow
        from aqm.core.project import DEFAULT_AGENTS_YAML
        _init_from_ai_for_pipeline(root, name)
        return

    if template:
        from aqm.core.project import DEFAULT_AGENTS_YAML
        save_pipeline(root, name, DEFAULT_AGENTS_YAML)
        console.print(f"[green]✓[/] Pipeline '{name}' created with default template.")
        return

    # Interactive choice
    console.print(f"\n[bold]Creating pipeline: {name}[/]\n")
    console.print("  [green][1][/] Default template")
    console.print("  [magenta][2][/] AI-generate from description")

    choice = click.prompt("\n  Choice", type=click.IntRange(1, 2), default=1)

    if choice == 1:
        from aqm.core.project import DEFAULT_AGENTS_YAML
        save_pipeline(root, name, DEFAULT_AGENTS_YAML)
        console.print(f"\n[green]✓[/] Pipeline '{name}' created with default template.")
    elif choice == 2:
        _init_from_ai_for_pipeline(root, name)


def _init_from_ai_for_pipeline(root: Path, name: str) -> None:
    """AI-generate a pipeline and save it with the given name."""
    selected_model = _pick_model()
    console.print(f"  [dim]Using: {selected_model}[/]\n")

    console.print(
        "\n[bold]Describe the pipeline you want to create.[/]\n"
    )
    description = click.prompt("  Pipeline description", type=str)
    description = " ".join(description.splitlines()).strip()

    project_dir = root

    # Analyze project
    analysis = ""
    has_project = any(
        p for p in project_dir.iterdir()
        if p.name not in {".git", ".aqm", "__pycache__", "node_modules", ".venv"}
    ) if project_dir.exists() else False

    if has_project:
        from aqm.core.project import analyze_project
        with console.status("[bold cyan]Analyzing project...[/]", spinner="dots"):
            analysis = analyze_project(project_dir, model=selected_model)
        if analysis:
            console.print(f"\n[bold]Project analysis:[/]\n")
            console.print(f"[dim]{analysis}[/]\n")

    # Clarifying questions
    project_analysis_text = analysis if has_project else ""
    with console.status("[bold cyan]Preparing questions...[/]", spinner="dots"):
        questions = generate_clarifying_questions(description, project_analysis_text, model=selected_model)

    qa_context = ""
    if questions:
        console.print(
            f"\n[bold]A few questions to build a better pipeline[/] "
            f"[dim](press Enter to use default)[/]\n"
        )
        qa_pairs: list[str] = []
        for i, q in enumerate(questions, 1):
            question_text = q.get("question", "")
            why_text = q.get("why", "")
            default_text = q.get("default", "")
            if why_text:
                console.print(f"  [dim]{why_text}[/]")
            answer = click.prompt(
                f"  [bold]Q{i}.[/] {question_text}",
                default=default_text or "",
                show_default=bool(default_text),
            )
            if answer:
                qa_pairs.append(f"Q: {question_text}\nA: {answer}")
            console.print()
        qa_context = "\n\n".join(qa_pairs)

    # Deep analysis
    deep_analysis_text = ""
    if has_project and qa_context:
        with console.status("[bold cyan]Investigating project based on your answers...[/]", spinner="dots"):
            deep_analysis_text = deep_analyze_project(
                project_dir, qa_context, initial_analysis=analysis, model=selected_model,
            )
        if deep_analysis_text:
            console.print(f"\n[bold]Additional findings:[/]\n")
            console.print(f"[dim]{deep_analysis_text}[/]\n")

    # Generate YAML
    try:
        with console.status("[bold cyan]Generating pipeline...[/]", spinner="dots") as status:
            def _update_status(msg: str) -> None:
                status.update(f"[bold cyan]{msg}[/]")
            generated = generate_agents_yaml(
                description,
                project_dir=project_dir if has_project else None,
                qa_context=qa_context,
                deep_analysis=deep_analysis_text,
                on_status=_update_status,
                model=selected_model,
            )
    except Exception as e:
        console.print(f"[red]Generation failed:[/] {e}")
        return

    from rich.syntax import Syntax
    console.print("\n[bold]Generated pipeline:[/]\n")
    console.print(Syntax(generated, "yaml", theme="monokai", line_numbers=True))

    if click.confirm("\n  Use this pipeline?", default=True):
        save_pipeline(root, name, generated)
        console.print(
            f"\n[green]✓[/] Pipeline '{name}' created.\n"
            f"  Run [bold]aqm run --pipeline {name} \"your task\"[/] to use it."
        )


@pipeline_group.command(name="delete")
@click.argument("name")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation")
def pipeline_delete_cmd(name: str, yes: bool) -> None:
    """Delete a pipeline.  Example: aqm pipeline delete old-pipeline"""
    root = _require_project()

    if not yes:
        if not click.confirm(f"  Delete pipeline '{name}'?", default=False):
            console.print("[dim]Cancelled.[/]")
            return

    try:
        delete_pipeline(root, name)
        console.print(f"[green]✓[/] Pipeline '{name}' deleted.")
    except (FileNotFoundError, ValueError) as e:
        console.print(f"[red]Error:[/] {e}")
        sys.exit(1)


@pipeline_group.command(name="default")
@click.argument("name", required=False)
def pipeline_default_cmd(name: str | None) -> None:
    """Get or set the default pipeline.  Example: aqm pipeline default code-review"""
    root = _require_project()

    if name is None:
        current = get_default_pipeline(root) or "default"
        console.print(f"Default pipeline: [bold]{current}[/]")
        return

    pipelines = list_pipelines(root)
    if name not in pipelines:
        console.print(f"[red]Error:[/] Pipeline '{name}' not found.")
        console.print(f"  Available: {', '.join(pipelines)}")
        sys.exit(1)

    set_default_pipeline(root, name)
    console.print(f"[green]✓[/] Default pipeline set to '{name}'.")


@pipeline_group.command(name="edit")
@click.argument("name", required=False)
def pipeline_edit_cmd(name: str | None) -> None:
    """Edit a pipeline with AI.  Example: aqm pipeline edit default"""
    root = _require_project()

    if name is None:
        name = get_default_pipeline(root) or "default"

    pipelines = list_pipelines(root)
    if name not in pipelines:
        console.print(f"[red]Error:[/] Pipeline '{name}' not found.")
        console.print(f"  Available: {', '.join(pipelines)}")
        sys.exit(1)

    # Read current YAML
    pipeline_path = get_pipeline_path(root, name)
    current_yaml = pipeline_path.read_text(encoding="utf-8")

    # Show current YAML
    from rich.syntax import Syntax
    console.print(f"\n[bold]Current pipeline: {name}[/]\n")
    console.print(Syntax(current_yaml, "yaml", theme="monokai", line_numbers=True))

    # Model selection
    selected_model = _pick_model()
    console.print(f"  [dim]Using: {selected_model}[/]\n")

    # Get edit instruction
    edit_instruction = click.prompt(
        "\n  What would you like to change?", type=str,
    )
    edit_instruction = " ".join(edit_instruction.splitlines()).strip()

    # Edit with AI
    try:
        with console.status("[bold cyan]Editing pipeline...[/]", spinner="dots") as status:
            def _update_status(msg: str) -> None:
                status.update(f"[bold cyan]{msg}[/]")
            modified = edit_pipeline_yaml(
                current_yaml, edit_instruction,
                on_status=_update_status,
                model=selected_model,
            )
    except Exception as e:
        console.print(f"[red]Edit failed:[/] {e}")
        return

    # Preview changes
    console.print(f"\n[bold]Modified pipeline:[/]\n")
    console.print(Syntax(modified, "yaml", theme="monokai", line_numbers=True))

    if click.confirm("\n  Apply these changes?", default=True):
        save_pipeline(root, name, modified)
        console.print(f"[green]✓[/] Pipeline '{name}' updated.")
    else:
        console.print("[dim]Changes discarded.[/]")


if __name__ == "__main__":
    cli()
