"""Gate — LLM automatic evaluation or Human manual approval/rejection.

LLMGate uses the Claude CLI (not the Anthropic SDK directly) so that
authentication is handled by the CLI's own login session.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

from aqm.core.agent import GateConfig
from aqm.core.context import render_template
from aqm.core.task import Task

logger = logging.getLogger(__name__)


@dataclass
class GateResult:
    """Gate evaluation result."""

    decision: str  # "approved" or "rejected"
    reason: str = ""


class AbstractGate(ABC):
    @abstractmethod
    def evaluate(self, task: Task, agent_output: str) -> Optional[GateResult]:
        """Evaluate the task output. HumanGate returns None to wait."""
        ...


class LLMGate(AbstractGate):
    """Automatic evaluation via the Claude CLI."""

    # Fallback when no project config is provided
    _DEFAULT_SYSTEM_PROMPT = (
        "You are a quality gate evaluator. Evaluate the agent output below.\n\n"
        "You must respond only in the following JSON format:\n"
        '{"decision": "approved" or "rejected", "reason": "basis for the decision"}'
    )

    def __init__(self, config: GateConfig, anthropic_client=None, gate_defaults=None) -> None:
        self.config = config
        self._defaults = gate_defaults  # ProjectConfig.gate (GateDefaults)

    def _get_system_prompt(self) -> str:
        if self._defaults:
            return self._defaults.system_prompt
        return self._DEFAULT_SYSTEM_PROMPT

    def _get_model(self) -> str:
        if self.config.model:
            return self.config.model
        if self._defaults:
            return self._defaults.model
        return "claude-sonnet-4-20250514"

    def _get_timeout(self) -> int:
        if self._defaults:
            return self._defaults.timeout
        return 120

    def evaluate(self, task: Task, agent_output: str) -> GateResult:
        extra_prompt = ""
        if self.config.prompt:
            extra_prompt = render_template(
                self.config.prompt,
                output=agent_output,
                input=task.description,
            )

        user_message = f"Agent output:\n{agent_output}"
        if extra_prompt:
            user_message = f"{extra_prompt}\n\n{user_message}"

        model = self._get_model()

        if shutil.which("claude") is None:
            raise FileNotFoundError(
                "The 'claude' CLI was not found on PATH. "
                "Please install Claude Code CLI first: "
                "https://docs.anthropic.com/en/docs/claude-code"
            )

        cmd = [
            "claude", "-p", user_message, "--print",
            "--system-prompt", self._get_system_prompt(),
            "--model", model,
        ]

        logger.info(
            "[LLMGate] Evaluating gate (model=%s)", model
        )

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=self._get_timeout(),
        )

        if result.returncode != 0:
            error_msg = result.stderr.strip() or f"Exit code: {result.returncode}"
            logger.error("[LLMGate] CLI failed: %s", error_msg)
            return GateResult(
                decision="rejected",
                reason=f"Gate evaluation failed: {error_msg}",
            )

        return self._parse_response(result.stdout.strip())

    def _parse_response(self, text: str) -> GateResult:
        """Parse the decision from the LLM response."""
        try:
            json_match = re.search(r"\{[^}]+\}", text, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group())
                decision = data.get("decision", "").lower()
                if decision in ("approved", "rejected"):
                    return GateResult(
                        decision=decision,
                        reason=data.get("reason", ""),
                    )
        except (json.JSONDecodeError, AttributeError):
            pass

        # Fallback: search for keywords in text
        lower = text.lower()
        if "approved" in lower or "approve" in lower:
            return GateResult(decision="approved", reason=text.strip())
        if "rejected" in lower or "reject" in lower:
            return GateResult(decision="rejected", reason=text.strip())

        return GateResult(decision="rejected", reason=f"Unable to determine: {text[:200]}")


class HumanGate(AbstractGate):
    """Waits for manual human approval/rejection."""

    def evaluate(self, task: Task, agent_output: str) -> None:
        """Return None to pause the pipeline."""
        return None
