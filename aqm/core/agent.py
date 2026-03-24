"""AgentDefinition — parsing agents.yaml and agent definitions.

MCP configuration supports a simplified format:
  mcp:
    - server: github
    - server: filesystem
      args: ["/path/to/dir"]

Supports parameterization via ${{ params.var_name }} syntax,
pipeline composition via `extends` and `abstract` agents,
and imports from external YAML files.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal, Optional, Union

import yaml
from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Parameter definitions
# ---------------------------------------------------------------------------

class ParamDefinition(BaseModel):
    """A single parameter declaration in the params section."""

    type: Literal["string", "number", "boolean"] = "string"
    default: Optional[Any] = None
    required: bool = False
    description: str = ""
    prompt: Optional[str] = None
    auto_detect: Optional[str] = None

    @field_validator("default", mode="before")
    @classmethod
    def _coerce_default(cls, v: Any, info) -> Any:
        """Leave default as-is; type coercion happens at resolution time."""
        return v


# ---------------------------------------------------------------------------
# Agent models
# ---------------------------------------------------------------------------

class Handoff(BaseModel):
    """Handoff rules between agents.

    The ``to`` field can be a single agent ID or a comma-separated list for
    fan-out (e.g. ``"qa, docs"``).  When ``condition`` is ``"auto"``, the
    agent itself decides the target by including ``HANDOFF: <agent_id>`` in
    its output.
    """

    to: str  # single ID or comma-separated list for fan-out
    task: str = ""
    condition: str = "always"  # always | on_approve | on_reject | on_pass | auto | expression
    payload: str = "{{ output }}"


class GateConfig(BaseModel):
    """Gate configuration — LLM or Human."""

    type: Literal["llm", "human"] = "llm"
    prompt: str = ""
    model: Optional[str] = None


class MCPServerConfig(BaseModel):
    """MCP server configuration.

    Simplified format:  { server: "github" }
    Detailed format:    { server: "filesystem", command: "npx", args: [...], env: {...} }
    """

    server: str
    command: Optional[str] = None
    args: list[str] = Field(default_factory=list)
    env: Optional[dict[str, str]] = None


class ImportSpec(BaseModel):
    """An import directive that pulls agents from an external YAML file."""

    from_path: str = Field(..., alias="from")
    agents: list[str] = Field(default_factory=list)

    model_config = {"populate_by_name": True}


class AgentDefinition(BaseModel):
    """Complete definition of a single agent."""

    id: str
    name: str = ""
    runtime: Literal["api", "claude_code"] = "api"
    model: Optional[str] = None
    system_prompt: str = ""
    handoffs: list[Handoff] = Field(default_factory=list)
    gate: Optional[GateConfig] = None
    mcp: list[MCPServerConfig] = Field(default_factory=list)
    claude_code_flags: Optional[list[str]] = None
    abstract: bool = False
    extends: Optional[str] = None

    @field_validator("mcp", mode="before")
    @classmethod
    def _normalize_mcp(cls, v: Any) -> list[dict]:
        """Allow string-format MCP configurations as well."""
        if not v:
            return []
        result = []
        for item in v:
            if isinstance(item, str):
                result.append({"server": item})
            elif isinstance(item, dict):
                result.append(item)
            else:
                result.append(item)
        return result


class AgentsConfig(BaseModel):
    """Top-level structure of agents.yaml."""

    params: dict[str, ParamDefinition] = Field(default_factory=dict)
    imports: list[ImportSpec] = Field(default_factory=list)
    agents: list[AgentDefinition]

    @field_validator("params", mode="before")
    @classmethod
    def _normalize_params(cls, v: Any) -> dict:
        """Allow shorthand param declarations (just a default value)."""
        if not v:
            return {}
        result = {}
        for key, val in v.items():
            if isinstance(val, dict):
                result[key] = val
            else:
                # Shorthand: params: { model: "claude-sonnet-4-20250514" }
                result[key] = {"default": val}
        return result

    @field_validator("imports", mode="before")
    @classmethod
    def _normalize_imports(cls, v: Any) -> list:
        if not v:
            return []
        return v


# ---------------------------------------------------------------------------
# Param resolution
# ---------------------------------------------------------------------------

_PARAM_PATTERN = re.compile(r"\$\{\{\s*params\.(\w+)\s*\}\}")


def _coerce_param_value(value: str, param_def: ParamDefinition) -> Any:
    """Coerce a string value to the declared param type."""
    if param_def.type == "number":
        try:
            return int(value)
        except ValueError:
            return float(value)
    if param_def.type == "boolean":
        return value.lower() in ("true", "1", "yes")
    return value


def resolve_params(
    raw: dict[str, Any],
    param_defs: dict[str, ParamDefinition],
    cli_overrides: dict[str, str] | None = None,
    overrides_file: Path | None = None,
) -> dict[str, Any]:
    """Build the final resolved parameter values.

    Priority (highest first):
      1. CLI overrides (--param key=value)
      2. Overrides file (.aqm/params.yaml)
      3. Default values from param definitions

    Returns a dict of {param_name: resolved_value}.
    Raises ValueError for required params without values.
    """
    # Load overrides file if it exists
    file_overrides: dict[str, Any] = {}
    if overrides_file and overrides_file.exists():
        with open(overrides_file, encoding="utf-8") as f:
            file_overrides = yaml.safe_load(f) or {}

    resolved: dict[str, Any] = {}
    for name, param_def in param_defs.items():
        if cli_overrides and name in cli_overrides:
            resolved[name] = _coerce_param_value(cli_overrides[name], param_def)
        elif name in file_overrides:
            resolved[name] = file_overrides[name]
        elif param_def.default is not None:
            resolved[name] = param_def.default
        elif param_def.required:
            raise ValueError(
                f"Required parameter '{name}' is not set. "
                f"Provide it via --param {name}=<value> or in "
                f".aqm/params.yaml.\n"
                f"  Description: {param_def.description}"
            )
        else:
            resolved[name] = None

    return resolved


def _substitute_params_in_value(value: Any, params: dict[str, Any]) -> Any:
    """Recursively substitute ${{ params.X }} references in a value."""
    if isinstance(value, str):
        def _replacer(m: re.Match) -> str:
            param_name = m.group(1)
            if param_name not in params:
                raise ValueError(
                    f"Unknown parameter reference: ${{{{ params.{param_name} }}}}. "
                    f"Available params: {', '.join(params.keys()) or '(none)'}"
                )
            resolved = params[param_name]
            if resolved is None:
                return ""
            return str(resolved)

        return _PARAM_PATTERN.sub(_replacer, value)
    elif isinstance(value, dict):
        return {k: _substitute_params_in_value(v, params) for k, v in value.items()}
    elif isinstance(value, list):
        return [_substitute_params_in_value(item, params) for item in value]
    return value


def substitute_params(raw: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    """Substitute all ${{ params.X }} references in the raw YAML dict.

    Operates on the full raw dict BEFORE Pydantic model validation,
    so every string field in agents/imports/etc. gets resolved.
    """
    return _substitute_params_in_value(raw, params)


# ---------------------------------------------------------------------------
# Extends resolution
# ---------------------------------------------------------------------------

def _resolve_extends(agents_raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Resolve `extends` references by shallow-merging parent into child.

    Child fields override parent fields. The `extends` and `abstract`
    fields are consumed and removed during resolution.
    """
    # Build lookup by id
    by_id: dict[str, dict[str, Any]] = {}
    for agent_raw in agents_raw:
        aid = agent_raw.get("id")
        if aid:
            by_id[aid] = agent_raw

    resolved: list[dict[str, Any]] = []
    for agent_raw in agents_raw:
        extends = agent_raw.get("extends")
        if extends:
            if extends not in by_id:
                raise ValueError(
                    f"Agent '{agent_raw.get('id')}' extends '{extends}', "
                    f"but no agent with that ID exists."
                )
            parent = by_id[extends]
            # Shallow merge: start with parent, overlay child
            merged = {**parent, **agent_raw}
            # Remove the extends key from the final definition
            merged.pop("extends", None)
            # Child is NOT abstract unless it explicitly declares itself as such
            if "abstract" not in agent_raw:
                merged.pop("abstract", None)
            # The child keeps its own id, not the parent's
            resolved.append(merged)
        else:
            resolved.append(agent_raw)

    return resolved


def _filter_abstract(agents_raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove agents marked as abstract: true."""
    return [a for a in agents_raw if not a.get("abstract", False)]


# ---------------------------------------------------------------------------
# Imports resolution
# ---------------------------------------------------------------------------

def _resolve_imports(
    imports_raw: list[dict[str, Any]],
    base_dir: Path,
) -> list[dict[str, Any]]:
    """Load agents from imported YAML files.

    Each import specifies a ``from`` path (relative to the importing file's
    directory) and an optional list of agent IDs to import. If the agent
    list is empty, all agents from the file are imported.

    Returns a list of raw agent dicts ready to be merged into the main list.
    """
    imported_agents: list[dict[str, Any]] = []

    for imp in imports_raw:
        from_path = imp.get("from")
        if not from_path:
            continue

        file_path = (base_dir / from_path).resolve()
        if not file_path.exists():
            raise FileNotFoundError(
                f"Import file not found: {file_path} "
                f"(referenced from agents.yaml)"
            )

        with open(file_path, encoding="utf-8") as f:
            imported_raw = yaml.safe_load(f) or {}

        # The imported file can be a full agents.yaml (with `agents:` key)
        # or a bare list of agent dicts.
        if "agents" in imported_raw:
            agents_list = imported_raw["agents"]
        elif isinstance(imported_raw, list):
            agents_list = imported_raw
        else:
            agents_list = []

        requested_ids = set(imp.get("agents", []))
        for agent_raw in agents_list:
            if requested_ids and agent_raw.get("id") not in requested_ids:
                continue
            imported_agents.append(agent_raw)

    return imported_agents


# ---------------------------------------------------------------------------
# Main loader
# ---------------------------------------------------------------------------

def load_agents(
    path: Path,
    cli_params: dict[str, str] | None = None,
) -> dict[str, AgentDefinition]:
    """Parse agents.yaml and return an {agent_id: AgentDefinition} dictionary.

    Processing order:
      1. Parse raw YAML
      2. Resolve params (CLI > overrides file > defaults)
      3. Substitute ${{ params.X }} references in all string fields
      4. Resolve imports (load external agent files)
      5. Resolve extends (shallow merge parent -> child)
      6. Filter out abstract agents
      7. Validate with Pydantic models
      8. Validate handoff targets
    """
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    base_dir = path.parent

    # --- Step 1-2: Resolve params ---
    param_defs_raw = raw.get("params", {})
    # Normalize param defs before creating ParamDefinition models
    param_defs: dict[str, ParamDefinition] = {}
    if param_defs_raw:
        for name, val in param_defs_raw.items():
            if isinstance(val, dict):
                param_defs[name] = ParamDefinition.model_validate(val)
            else:
                param_defs[name] = ParamDefinition(default=val)

    # Look for overrides file
    overrides_file = base_dir / "params.yaml"
    if not overrides_file.exists():
        # Also check .aqm/params.yaml from project root
        project_params = base_dir.parent / ".aqm" / "params.yaml"
        if project_params.exists():
            overrides_file = project_params

    resolved_params = resolve_params(
        raw, param_defs, cli_overrides=cli_params, overrides_file=overrides_file
    )

    # --- Step 3: Substitute params in the entire raw dict ---
    if resolved_params:
        raw = substitute_params(raw, resolved_params)

    # --- Step 4: Resolve imports ---
    imports_raw = raw.get("imports", [])
    if imports_raw:
        imported_agents = _resolve_imports(imports_raw, base_dir)
        raw.setdefault("agents", [])
        # Prepend imported agents so they are available as extends targets
        raw["agents"] = imported_agents + raw["agents"]

    # --- Step 5-6: Resolve extends and filter abstract ---
    raw["agents"] = _resolve_extends(raw.get("agents", []))
    raw["agents"] = _filter_abstract(raw["agents"])

    # --- Step 7: Validate ---
    config = AgentsConfig.model_validate(raw)
    agents: dict[str, AgentDefinition] = {}
    for agent in config.agents:
        # Auto-fill name from id if not provided
        if not agent.name:
            agent.name = agent.id.replace("_", " ").replace("-", " ").title()
        if agent.id in agents:
            raise ValueError(f"Duplicate agent ID: {agent.id}")
        agents[agent.id] = agent

    # --- Step 8: Validate handoff targets ---
    all_ids = set(agents.keys())
    for agent in agents.values():
        for handoff in agent.handoffs:
            if handoff.condition == "auto":
                continue
            targets = [t.strip() for t in handoff.to.split(",")]
            for target in targets:
                if target not in all_ids:
                    raise ValueError(
                        f"Handoff target '{target}' of agent '{agent.id}' "
                        f"does not exist."
                    )

    return agents


def get_first_agent_id(path: Path, cli_params: dict[str, str] | None = None) -> str:
    """Return the first agent ID from agents.yaml (after full resolution)."""
    agents = load_agents(path, cli_params=cli_params)
    if not agents:
        raise ValueError("No agents are defined in agents.yaml.")
    return next(iter(agents))
