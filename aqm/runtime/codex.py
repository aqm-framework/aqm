"""CodexCLIRuntime — OpenAI Codex CLI integration.

Invokes ``codex exec`` in non-interactive (headless) mode.

Codex CLI reference:
  - ``codex exec "prompt"``: Non-interactive execution
  - ``-m`` / ``--model``: Model selection (default: gpt-5-codex)
  - ``--full-auto``: Workspace-write sandbox + on-request approvals
  - ``-o`` / ``--output-last-message``: Write final message to file
  - ``-C`` / ``--cd``: Set working directory

System prompts are passed by prepending to the prompt text, as the
Codex CLI does not have a dedicated system prompt flag.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

from aqm.core.agent import AgentDefinition
from aqm.core.task import Task
from aqm.runtime.base import AbstractRuntime, OutputCallback, ThinkingCallback, ToolCallback

logger = logging.getLogger(__name__)

_DEFAULT_CODEX_MODEL = "o4-mini"


def _check_codex_cli_available() -> None:
    """Verify that the ``codex`` CLI binary is on PATH."""
    if shutil.which("codex") is None:
        raise FileNotFoundError(
            "The 'codex' CLI was not found on PATH. "
            "Install it via: npm install -g @openai/codex "
            "or see https://github.com/openai/codex"
        )


class CodexCLIRuntime(AbstractRuntime):
    """Run OpenAI Codex CLI in non-interactive (exec) mode.

    Uses ``codex exec "prompt"`` which streams progress to stderr
    and prints the final agent message to stdout.
    """

    def __init__(self, project_root=None, timeout: int = 600) -> None:
        self._project_root = project_root or Path.cwd()
        self._timeout = timeout

    @property
    def name(self) -> str:
        return "codex_cli"

    def run(
        self,
        prompt: str,
        agent: AgentDefinition,
        task: Task,
        on_output: OutputCallback = None,
        on_thinking: ThinkingCallback = None,
        on_tool: ToolCallback = None,
    ) -> str:
        _check_codex_cli_available()

        # Codex CLI has no --system-prompt flag; prepend to user prompt.
        full_prompt = prompt
        if agent.system_prompt:
            full_prompt = f"[System Instructions]\n{agent.system_prompt}\n\n[Task]\n{prompt}"

        cmd: list[str] = [
            "codex", "exec",
            full_prompt,
            "--full-auto",
        ]

        if agent.model:
            cmd.extend(["-m", agent.model])

        # Set working directory
        cmd.extend(["-C", str(self._project_root)])

        logger.info(
            "[CodexCLIRuntime] Running agent '%s' (model=%s)",
            agent.id,
            agent.model or _DEFAULT_CODEX_MODEL,
        )

        if on_output:
            return self._run_streaming(cmd, agent, on_output, on_tool)

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=self._timeout,
        )

        if result.returncode != 0:
            error_msg = (
                result.stderr.strip() or f"Exit code: {result.returncode}"
            )
            logger.error(
                "[CodexCLIRuntime] Agent '%s' failed: %s", agent.id, error_msg
            )
            raise RuntimeError(
                f"Codex CLI execution failed (agent={agent.id}): {error_msg}"
            )

        output = result.stdout.strip()
        logger.info(
            "[CodexCLIRuntime] Agent '%s' completed (%d chars)",
            agent.id,
            len(output),
        )
        return output

    def _run_streaming(
        self,
        cmd: list[str],
        agent: AgentDefinition,
        on_output: OutputCallback,
        on_tool: ToolCallback = None,
    ) -> str:
        """Run with streaming via Popen.

        Codex CLI streams progress to stderr (tool actions, file reads/writes)
        and final output to stdout.  When ``on_tool`` is provided, stderr is
        also monitored via selectors for tool-related lines.
        """
        import json

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        output_parts: list[str] = []

        if not on_tool:
            # Simple stdout-only streaming (no stderr monitoring needed)
            try:
                while True:
                    line = proc.stdout.readline()  # type: ignore[union-attr]
                    if not line:
                        break
                    output_parts.append(line)
                    try:
                        on_output(line.rstrip("\n"))
                    except Exception:
                        pass
                proc.wait(timeout=self._timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                raise RuntimeError(f"Codex CLI timed out (agent={agent.id})")
        else:
            # Multiplex stdout + stderr for tool events
            import selectors
            sel = selectors.DefaultSelector()
            sel.register(proc.stdout, selectors.EVENT_READ, "stdout")  # type: ignore[union-attr]
            if proc.stderr:
                sel.register(proc.stderr, selectors.EVENT_READ, "stderr")

            open_streams = {"stdout", "stderr"} if proc.stderr else {"stdout"}

            try:
                while open_streams:
                    events = sel.select(timeout=30)
                    if not events:
                        if proc.poll() is not None:
                            break
                        continue

                    for key, _ in events:
                        stream_name = key.data
                        line = key.fileobj.readline()  # type: ignore[union-attr]
                        if not line:
                            open_streams.discard(stream_name)
                            continue

                        if stream_name == "stdout":
                            output_parts.append(line)
                            try:
                                on_output(line.rstrip("\n"))
                            except Exception:
                                pass
                        elif stream_name == "stderr":
                            stripped = line.strip()
                            if not stripped:
                                continue
                            try:
                                event = json.loads(stripped)
                                etype = event.get("type", "")
                                if etype == "function_call":
                                    try:
                                        on_tool("tool_start", {
                                            "tool": event.get("name", ""),
                                            "input": event.get("arguments", event.get("args", {})),
                                        })
                                    except Exception:
                                        pass
                                elif etype == "function_call_output":
                                    try:
                                        on_tool("tool_result", {
                                            "tool": event.get("name", ""),
                                            "content": event.get("output", ""),
                                        })
                                    except Exception:
                                        pass
                            except json.JSONDecodeError:
                                lower = stripped.lower()
                                if any(kw in lower for kw in ("reading ", "writing ", "executing ", "running ")):
                                    try:
                                        on_tool("tool_start", {
                                            "tool": "shell",
                                            "input": {"command": stripped},
                                        })
                                    except Exception:
                                        pass

                sel.close()
                proc.wait(timeout=self._timeout)
            except subprocess.TimeoutExpired:
                sel.close()
                proc.kill()
                raise RuntimeError(f"Codex CLI timed out (agent={agent.id})")

        if proc.returncode != 0:
            error_msg = (
                proc.stderr.read().strip() if proc.stderr  # type: ignore[union-attr]
                else f"Exit code: {proc.returncode}"
            )
            logger.error(
                "[CodexCLIRuntime] Agent '%s' failed: %s", agent.id, error_msg
            )
            raise RuntimeError(
                f"Codex CLI execution failed (agent={agent.id}): {error_msg}"
            )

        output = "".join(output_parts).strip()
        logger.info(
            "[CodexCLIRuntime] Agent '%s' completed (%d chars)",
            agent.id,
            len(output),
        )
        return output
