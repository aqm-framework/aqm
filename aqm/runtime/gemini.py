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
from aqm.runtime.base import AbstractRuntime, OutputCallback

logger = logging.getLogger(__name__)

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

    def __init__(self, project_root=None) -> None:
        self._project_root = project_root

    @property
    def name(self) -> str:
        return "gemini_cli"

    def run(
        self,
        prompt: str,
        agent: AgentDefinition,
        task: Task,
        on_output: OutputCallback = None,
    ) -> str:
        _check_gemini_cli_available()

        cmd: list[str] = ["gemini", "-p", prompt]

        if agent.model:
            cmd.extend(["-m", agent.model])

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
                return self._run_streaming(cmd, env, agent, on_output)

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300,
                env=env,
            )

            if result.returncode != 0:
                error_msg = (
                    result.stderr.strip() or f"Exit code: {result.returncode}"
                )
                logger.error(
                    "[GeminiCLIRuntime] Agent '%s' failed: %s", agent.id, error_msg
                )
                raise RuntimeError(
                    f"Gemini CLI execution failed (agent={agent.id}): {error_msg}"
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
    ) -> str:
        """Run with line-by-line streaming via Popen."""
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )

        lines: list[str] = []
        try:
            while True:
                line = proc.stdout.readline()  # type: ignore[union-attr]
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
                f"Gemini CLI timed out (agent={agent.id})"
            )

        if proc.returncode != 0:
            error_msg = (
                proc.stderr.read().strip() if proc.stderr  # type: ignore[union-attr]
                else f"Exit code: {proc.returncode}"
            )
            logger.error(
                "[GeminiCLIRuntime] Agent '%s' failed: %s", agent.id, error_msg
            )
            raise RuntimeError(
                f"Gemini CLI execution failed (agent={agent.id}): {error_msg}"
            )

        output = "".join(lines).strip()
        logger.info(
            "[GeminiCLIRuntime] Agent '%s' completed (%d chars)",
            agent.id,
            len(output),
        )
        return output
