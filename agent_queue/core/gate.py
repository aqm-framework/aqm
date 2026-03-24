"""Gate — LLM automatic evaluation or Human manual approval/rejection."""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

from agent_queue.core.agent import GateConfig
from agent_queue.core.context import render_template
from agent_queue.core.task import Task


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
    """Automatic evaluation via the Claude API."""

    EVAL_SYSTEM_PROMPT = """\
You are a quality gate evaluator. Evaluate the agent output below.

You must respond only in the following JSON format:
{"decision": "approved" or "rejected", "reason": "basis for the decision"}
"""

    def __init__(self, config: GateConfig, anthropic_client) -> None:
        self.config = config
        self.client = anthropic_client

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

        model = self.config.model or "claude-sonnet-4-20250514"

        response = self.client.messages.create(
            model=model,
            max_tokens=1024,
            system=self.EVAL_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )

        return self._parse_response(response.content[0].text)

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
