"""TextRuntime — LLM invocation via the Claude CLI (text-only mode).

Unlike ClaudeCodeRuntime, this runtime does NOT give the agent tool access.
It runs `claude` in print mode for pure text generation tasks (planning,
reviewing, summarizing, etc.).
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from typing import Any

from aqm.core.agent import AgentDefinition
from aqm.core.task import Task
from aqm.runtime.base import AbstractRuntime, OutputCallback

logger = logging.getLogger(__name__)


def _check_claude_cli_available() -> None:
    """Verify that the ``claude`` CLI binary is on PATH."""
    if shutil.which("claude") is None:
        raise FileNotFoundError(
            "The 'claude' CLI was not found on PATH. "
            "Please install Claude Code CLI first: "
            "https://docs.anthropic.com/en/docs/claude-code"
        )


class TextRuntime(AbstractRuntime):
    """Runs the Claude CLI in print mode for text-only generation.

    Uses `claude -p <prompt> --print` which outputs text to stdout
    without any tool use or file access.
    """

    def __init__(self, project_root=None) -> None:
        self._project_root = project_root

    @property
    def name(self) -> str:
        return "text"

    def run(
        self,
        prompt: str,
        agent: AgentDefinition,
        task: Task,
        on_output: OutputCallback = None,
    ) -> str:
        _check_claude_cli_available()

        cmd: list[str] = ["claude", "-p", prompt, "--print"]

        if agent.system_prompt:
            cmd.extend(["--system-prompt", agent.system_prompt])

        if agent.model:
            cmd.extend(["--model", agent.model])

        logger.info(
            "[TextRuntime] Running agent '%s' (model=%s)",
            agent.id,
            agent.model or "default",
        )

        if on_output:
            return self._run_streaming(cmd, agent, on_output)

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
        )

        if result.returncode != 0:
            error_msg = (
                result.stderr.strip() or f"Exit code: {result.returncode}"
            )
            logger.error(
                "[TextRuntime] Agent '%s' failed: %s", agent.id, error_msg
            )
            raise RuntimeError(
                f"Claude CLI execution failed (agent={agent.id}): {error_msg}"
            )

        output = result.stdout.strip()
        logger.info(
            "[TextRuntime] Agent '%s' completed (%d chars)",
            agent.id,
            len(output),
        )
        return output

    def _run_streaming(
        self,
        cmd: list[str],
        agent: AgentDefinition,
        on_output: OutputCallback,
    ) -> str:
        """Run with line-by-line streaming via Popen."""
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        lines: list[str] = []
        try:
            for line in proc.stdout:
                lines.append(line)
                try:
                    on_output(line.rstrip("\n"))
                except Exception:
                    pass  # Never let callback errors kill the pipeline

            proc.wait(timeout=300)
        except subprocess.TimeoutExpired:
            proc.kill()
            raise RuntimeError(
                f"Claude CLI timed out (agent={agent.id})"
            )

        if proc.returncode != 0:
            error_msg = (
                proc.stderr.read().strip() if proc.stderr
                else f"Exit code: {proc.returncode}"
            )
            logger.error(
                "[TextRuntime] Agent '%s' failed: %s", agent.id, error_msg
            )
            raise RuntimeError(
                f"Claude CLI execution failed (agent={agent.id}): {error_msg}"
            )

        output = "".join(lines).strip()
        logger.info(
            "[TextRuntime] Agent '%s' completed (%d chars)",
            agent.id,
            len(output),
        )
        return output
