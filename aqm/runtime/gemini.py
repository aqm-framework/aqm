"""GeminiCLIRuntime — Google Gemini CLI integration.

Invokes the ``gemini`` CLI as a subprocess in headless (non-interactive) mode.

Gemini CLI reference:
  - ``-p`` / ``--prompt``: Non-interactive prompt
  - ``-m`` / ``--model``: Model selection (default: auto)
  - System prompt: via ``GEMINI_SYSTEM_MD`` env var pointing to a .md file
  - ``-o`` / ``--output-format``: text | json | stream-json
"""

from __future__ import annotations

import atexit
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from aqm.core.agent import AgentDefinition
from aqm.core.task import Task
from aqm.runtime.base import AbstractRuntime, OutputCallback, RuntimeExecutionError, ThinkingCallback, ToolCallback

logger = logging.getLogger(__name__)


def _classify_error(stderr: str, returncode: int) -> str:
    """Classify a CLI error into a coarse category."""
    lower = stderr.lower() if stderr else ""
    if "context window" in lower or "token limit" in lower or "too long" in lower:
        return "context_overflow"
    if returncode == 127 or "not found" in lower or "no such file" in lower:
        return "cli_missing"
    if "timed out" in lower or "timeout" in lower:
        return "timeout"
    return "unknown"


# Default model when agent.model is not specified
_DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"

# Safety-net cleanup for temp files
_TEMP_FILES_TO_CLEANUP: list[Path] = []


def _cleanup_temp_files() -> None:
    for p in _TEMP_FILES_TO_CLEANUP:
        try:
            p.unlink(missing_ok=True)
        except OSError:
            pass


atexit.register(_cleanup_temp_files)


def _write_temp_file(content: str, *, prefix: str, suffix: str) -> Path:
    """Write content to a temp file and register for cleanup."""
    fd, path_str = tempfile.mkstemp(prefix=prefix, suffix=suffix)
    path = Path(path_str)
    _TEMP_FILES_TO_CLEANUP.append(path)
    try:
        os.write(fd, content.encode("utf-8"))
    finally:
        os.close(fd)
    return path


def _check_gemini_cli_available() -> None:
    """Verify that the ``gemini`` CLI binary is on PATH."""
    if shutil.which("gemini") is None:
        raise FileNotFoundError(
            "The 'gemini' CLI was not found on PATH. "
            "Install it via: npm install -g @google/gemini-cli "
            "or see https://github.com/google-gemini/gemini-cli"
        )


class GeminiCLIRuntime(AbstractRuntime):
    """Run Gemini via the ``gemini`` CLI in non-interactive (headless) mode.

    Uses ``gemini -p <prompt>`` which outputs text to stdout.
    System prompts are passed via a temporary .md file set through
    the ``GEMINI_SYSTEM_MD`` environment variable.
    """

    def __init__(self, project_root=None, timeout: int = 300) -> None:
        self._project_root = project_root
        self._timeout = timeout

    @property
    def name(self) -> str:
        return "gemini_cli"

    def run(
        self,
        prompt: str,
        agent: AgentDefinition,
        task: Task,
        on_output: OutputCallback = None,
        on_thinking: ThinkingCallback = None,
        on_tool: ToolCallback = None,
    ) -> str:
        _check_gemini_cli_available()

        cmd: list[str] = ["gemini", "-p", prompt]

        if agent.model:
            cmd.extend(["-m", agent.model])

        if agent.cli_flags:
            cmd.extend(agent.cli_flags)

        # Gemini CLI uses GEMINI_SYSTEM_MD env var for system prompts,
        # not a --system-prompt flag. Write to a temp .md file.
        system_prompt_path: Path | None = None
        env = os.environ.copy()
        if agent.system_prompt:
            system_prompt_path = _write_temp_file(
                agent.system_prompt,
                prefix="aqm_gemini_sys_",
                suffix=".md",
            )
            env["GEMINI_SYSTEM_MD"] = str(system_prompt_path)

        logger.info(
            "[GeminiCLIRuntime] Running agent '%s' (model=%s)",
            agent.id,
            agent.model or _DEFAULT_GEMINI_MODEL,
        )

        try:
            if on_output:
                return self._run_streaming(cmd, env, agent, on_output, on_tool)

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self._timeout,
                env=env,
            )

            if result.returncode != 0:
                stderr = result.stderr.strip()
                error_category = _classify_error(stderr, result.returncode)
                error_msg = (
                    stderr or f"process exited with code {result.returncode}"
                )
                logger.error(
                    "[GeminiCLIRuntime] Agent '%s' failed (%s): %s",
                    agent.id,
                    error_category,
                    error_msg,
                )
                raise RuntimeExecutionError(
                    f"Gemini CLI execution failed (agent={agent.id}): {error_msg}",
                    partial_output=result.stdout.strip(),
                    error_category=error_category,
                )

            output = result.stdout.strip()
            logger.info(
                "[GeminiCLIRuntime] Agent '%s' completed (%d chars)",
                agent.id,
                len(output),
            )
            return output

        finally:
            if system_prompt_path is not None:
                system_prompt_path.unlink(missing_ok=True)
                if system_prompt_path in _TEMP_FILES_TO_CLEANUP:
                    _TEMP_FILES_TO_CLEANUP.remove(system_prompt_path)

    def _run_streaming(
        self,
        cmd: list[str],
        env: dict[str, str],
        agent: AgentDefinition,
        on_output: OutputCallback,
        on_tool: ToolCallback = None,
    ) -> str:
        """Run with line-by-line streaming via Popen.

        Gemini CLI with ``-o stream-json`` outputs JSON events including
        tool use (functionCall/functionResponse).  Falls back to plain
        text streaming when JSON parsing fails.
        """
        import json

        # Use stream-json for structured output when tool callback present
        stream_cmd = list(cmd)
        if on_tool:
            stream_cmd.extend(["-o", "stream-json"])

        proc = subprocess.Popen(
            stream_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )

        output_parts: list[str] = []
        try:
            while True:
                line = proc.stdout.readline()  # type: ignore[union-attr]
                if not line:
                    break

                stripped = line.strip()
                if not stripped:
                    continue

                # Try to parse as JSON for structured events
                if on_tool:
                    try:
                        event = json.loads(stripped)
                        etype = event.get("type", "")

                        # Gemini stream-json: functionCall events
                        if etype == "functionCall" or "functionCall" in event:
                            fc = event.get("functionCall", event)
                            try:
                                on_tool("tool_start", {
                                    "tool": fc.get("name", ""),
                                    "input": fc.get("args", {}),
                                })
                            except Exception:
                                pass
                            continue

                        # Gemini stream-json: functionResponse events
                        if etype == "functionResponse" or "functionResponse" in event:
                            fr = event.get("functionResponse", event)
                            try:
                                on_tool("tool_result", {
                                    "tool": fr.get("name", ""),
                                    "content": fr.get("response", ""),
                                })
                            except Exception:
                                pass
                            continue

                        # Text content
                        text = event.get("text", "")
                        if text:
                            output_parts.append(text)
                            try:
                                on_output(text)
                            except Exception:
                                pass
                            continue
                    except json.JSONDecodeError:
                        pass  # Fall through to plain text handling

                # Plain text line
                output_parts.append(line)
                try:
                    on_output(line.rstrip("\n"))
                except Exception:
                    pass

        except subprocess.TimeoutExpired:
            raise RuntimeExecutionError(
                f"Gemini CLI timed out (agent={agent.id})",
                partial_output="".join(output_parts).strip(),
                error_category="timeout",
            )
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.wait()

        if proc.returncode != 0:
            try:
                stderr = (
                    proc.stderr.read().strip() if proc.stderr  # type: ignore[union-attr]
                    else ""
                )
            except (ValueError, OSError):
                stderr = ""
            error_category = _classify_error(stderr, proc.returncode)
            error_msg = stderr or f"process exited with code {proc.returncode}"
            logger.error(
                "[GeminiCLIRuntime] Agent '%s' failed (%s): %s",
                agent.id,
                error_category,
                error_msg,
            )
            raise RuntimeExecutionError(
                f"Gemini CLI execution failed (agent={agent.id}): {error_msg}",
                partial_output="".join(output_parts).strip(),
                error_category=error_category,
            )

        output = "".join(output_parts).strip()
        logger.info(
            "[GeminiCLIRuntime] Agent '%s' completed (%d chars)",
            agent.id,
            len(output),
        )
        return output
