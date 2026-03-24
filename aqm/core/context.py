"""Prompt builder — constructs agent prompts using Jinja2 templates."""

from __future__ import annotations

from typing import Any

from jinja2 import BaseLoader, Undefined
from jinja2.sandbox import SandboxedEnvironment


class _LenientUndefined(Undefined):
    """Treat undefined variables as empty strings."""

    def __str__(self) -> str:
        return ""

    def __bool__(self) -> bool:
        return False

    def __iter__(self):
        return iter([])


def _create_env() -> SandboxedEnvironment:
    return SandboxedEnvironment(
        loader=BaseLoader(),
        undefined=_LenientUndefined,
    )


def render_template(template_str: str, **variables: Any) -> str:
    """Render a Jinja2 template string."""
    env = _create_env()
    template = env.from_string(template_str)
    return template.render(**variables)


def build_prompt(
    *,
    system_prompt_template: str,
    input_text: str,
    context: str = "",
    output: str = "",
    reject_reason: str = "",
    gate_result: str = "",
    **extra: Any,
) -> str:
    """Render an agent system prompt."""
    return render_template(
        system_prompt_template,
        input=input_text,
        context=context,
        output=output,
        reject_reason=reject_reason,
        gate_result=gate_result,
        **extra,
    )


def build_payload(
    payload_template: str,
    *,
    output: str = "",
    input_text: str = "",
    reject_reason: str = "",
    gate_result: str = "",
    **extra: Any,
) -> str:
    """Render a handoff payload."""
    return render_template(
        payload_template,
        output=output,
        input=input_text,
        reject_reason=reject_reason,
        gate_result=gate_result,
        **extra,
    )
