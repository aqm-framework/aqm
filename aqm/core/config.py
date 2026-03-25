"""Centralized project configuration — .aqm/config.yaml.

All hardcoded values live here as defaults.  When a config.yaml exists,
its values override defaults.  Missing fields keep their defaults,
so the file is entirely optional for backward compatibility.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

AQM_DIR = ".aqm"
CONFIG_YAML = "config.yaml"


# ---------------------------------------------------------------------------
# Sub-models
# ---------------------------------------------------------------------------

class PipelineConfig(BaseModel):
    max_stages: int = 20


class GateDefaults(BaseModel):
    """Project-level defaults for LLM gates."""

    model: str = "claude-sonnet-4-20250514"
    timeout: int = 120
    system_prompt: str = (
        "You are a quality gate evaluator. Evaluate the agent output below.\n\n"
        "You must respond only in the following JSON format:\n"
        '{"decision": "approved" or "rejected", "reason": "basis for the decision"}'
    )


class RuntimeTimeouts(BaseModel):
    claude: int = 600
    gemini: int = 600
    codex: int = 600


# ---------------------------------------------------------------------------
# Top-level config
# ---------------------------------------------------------------------------

class ProjectConfig(BaseModel):
    """Complete project configuration loaded from ``.aqm/config.yaml``."""

    default_pipeline: Optional[str] = None
    pipeline: PipelineConfig = Field(default_factory=PipelineConfig)
    gate: GateDefaults = Field(default_factory=GateDefaults)
    timeouts: RuntimeTimeouts = Field(default_factory=RuntimeTimeouts)


# ---------------------------------------------------------------------------
# Load / save
# ---------------------------------------------------------------------------

def load_project_config(root: Path) -> ProjectConfig:
    """Load ``.aqm/config.yaml`` and merge with defaults.

    Returns a default ``ProjectConfig`` when the file does not exist or
    is empty — guaranteed to never raise.
    """
    config_path = root / AQM_DIR / CONFIG_YAML
    if not config_path.exists():
        return ProjectConfig()
    try:
        with open(config_path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return ProjectConfig.model_validate(data)
    except Exception as exc:
        logger.warning("Failed to load config.yaml, using defaults: %s", exc)
        return ProjectConfig()


def save_project_config(root: Path, config: ProjectConfig) -> None:
    """Write config back to ``.aqm/config.yaml``.

    Only writes fields that differ from defaults to keep the file clean.
    """
    config_path = root / AQM_DIR / CONFIG_YAML
    config_path.parent.mkdir(parents=True, exist_ok=True)

    # Dump only non-default values for a clean file
    data = config.model_dump(mode="json", exclude_defaults=True)

    # Always include default_pipeline if set (even if it looks like a "default")
    if config.default_pipeline is not None:
        data["default_pipeline"] = config.default_pipeline

    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True)
