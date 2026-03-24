"""AgentDefinition — parsing agents.yaml and agent definitions.

MCP configuration supports a simplified format:
  mcp:
    - server: github
    - server: filesystem
      args: ["/path/to/dir"]
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal, Optional, Union

import yaml
from pydantic import BaseModel, Field, field_validator


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


class AgentDefinition(BaseModel):
    """Complete definition of a single agent."""

    id: str
    name: str
    runtime: Literal["api", "claude_code"] = "api"
    model: Optional[str] = None
    system_prompt: str = ""
    handoffs: list[Handoff] = Field(default_factory=list)
    gate: Optional[GateConfig] = None
    mcp: list[MCPServerConfig] = Field(default_factory=list)
    claude_code_flags: Optional[list[str]] = None

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

    agents: list[AgentDefinition]


def load_agents(path: Path) -> dict[str, AgentDefinition]:
    """Parse agents.yaml and return an {agent_id: AgentDefinition} dictionary."""
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    config = AgentsConfig.model_validate(raw)
    agents: dict[str, AgentDefinition] = {}
    for agent in config.agents:
        if agent.id in agents:
            raise ValueError(f"Duplicate agent ID: {agent.id}")
        agents[agent.id] = agent

    # Validate that handoff targets exist
    all_ids = set(agents.keys())
    for agent in agents.values():
        for handoff in agent.handoffs:
            # "auto" condition means the agent decides at runtime — skip
            # static target validation since the target comes from output.
            if handoff.condition == "auto":
                continue
            # Support comma-separated fan-out targets (e.g. "qa, docs")
            targets = [t.strip() for t in handoff.to.split(",")]
            for target in targets:
                if target not in all_ids:
                    raise ValueError(
                        f"Handoff target '{target}' of agent '{agent.id}' "
                        f"does not exist."
                    )

    return agents


def get_first_agent_id(path: Path) -> str:
    """Return the first agent ID from agents.yaml."""
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    config = AgentsConfig.model_validate(raw)
    if not config.agents:
        raise ValueError("No agents are defined in agents.yaml.")
    return config.agents[0].id
