"""APIRuntime — LLM invocation via the Anthropic API."""

from __future__ import annotations

import logging
from typing import Any

from agent_queue.core.agent import AgentDefinition
from agent_queue.core.task import Task
from agent_queue.runtime.base import AbstractRuntime

logger = logging.getLogger(__name__)


class APIRuntime(AbstractRuntime):
    """Handles text input/output through the Anthropic Messages API."""

    def __init__(self, anthropic_client=None) -> None:
        self._client = anthropic_client

    @property
    def name(self) -> str:
        return "api"

    @property
    def client(self):
        if self._client is None:
            import anthropic

            self._client = anthropic.Anthropic()
        return self._client

    def run(self, prompt: str, agent: AgentDefinition, task: Task) -> str:
        model = agent.model or "claude-sonnet-4-20250514"

        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": 8192,
            "system": agent.system_prompt,
            "messages": [{"role": "user", "content": prompt}],
        }

        logger.info(
            f"[APIRuntime] Running agent '{agent.id}' (model={model})"
        )

        response = self.client.messages.create(**kwargs)

        output_parts = []
        for block in response.content:
            if block.type == "text":
                output_parts.append(block.text)

        output = "\n".join(output_parts)
        logger.info(
            f"[APIRuntime] Agent '{agent.id}' completed ({len(output)} chars)"
        )
        return output
