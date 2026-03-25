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
import json

from aqm.runtime.base import AbstractRuntime, OutputCallback, ThinkingCallback

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
        on_thinking: ThinkingCallback = None,
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
            return self._run_streaming(cmd, agent, on_output, on_thinking)

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
        on_thinking: ThinkingCallback = None,
    ) -> str:
        """Run with streaming via Popen.

        When *on_thinking* is provided, uses ``--output-format stream-json``
        to separate thinking blocks from assistant text.
        """
        if on_thinking:
            # --print + --output-format=stream-json requires --verbose
            stream_cmd = list(cmd)
            if "--verbose" not in stream_cmd:
                stream_cmd.append("--verbose")
            stream_cmd.extend(["--output-format", "stream-json"])
            return self._run_stream_json(stream_cmd, agent, on_output, on_thinking)

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        lines: list[str] = []
        try:
            while True:
                line = proc.stdout.readline()
                if not line:
                    break
                lines.append(line)
                try:
                    on_output(line.rstrip("\n"))
                except Exception:
                    pass

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

    def _run_stream_json(
        self,
        cmd: list[str],
        agent: AgentDefinition,
        on_output: OutputCallback,
        on_thinking: ThinkingCallback,
    ) -> str:
        """Run with ``--output-format stream-json`` to get thinking + text."""
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        output_parts: list[str] = []
        try:
            while True:
                line = proc.stdout.readline()
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    output_parts.append(line)
                    try:
                        on_output(line)
                    except Exception:
                        pass
                    continue

                etype = event.get("type", "")
                if etype == "assistant":
                    msg = event.get("message", {})
                    for block in msg.get("content", []):
                        btype = block.get("type", "")
                        if btype == "thinking":
                            thinking_text = block.get("thinking", "")
                            if thinking_text:
                                try:
                                    on_thinking(thinking_text)
                                except Exception:
                                    pass
                        elif btype == "text":
                            text = block.get("text", "")
                            if text:
                                output_parts.append(text)
                                try:
                                    on_output(text)
                                except Exception:
                                    pass
                elif etype == "result":
                    result_text = event.get("result", "")
                    if result_text and not output_parts:
                        output_parts.append(result_text)

            proc.wait(timeout=300)
        except subprocess.TimeoutExpired:
            proc.kill()
            raise RuntimeError(f"Claude CLI timed out (agent={agent.id})")

        if proc.returncode != 0:
            error_msg = (
                proc.stderr.read().strip() if proc.stderr
                else f"Exit code: {proc.returncode}"
            )
            logger.error("[TextRuntime] Agent '%s' failed: %s", agent.id, error_msg)
            raise RuntimeError(
                f"Claude CLI execution failed (agent={agent.id}): {error_msg}"
            )

        output = "".join(output_parts).strip()
        logger.info("[TextRuntime] Agent '%s' completed (%d chars)", agent.id, len(output))
        return output
