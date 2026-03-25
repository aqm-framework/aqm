"""GeminiRuntime — Google Gemini LLM integration (CLI and API modes).

Supports two modes selectable via agents.yaml:
  - ``gemini_cli``: Invokes the ``gemini`` CLI as a subprocess
  - ``gemini_api``: Uses the ``google-genai`` SDK directly

CLI mode mirrors the existing Claude CLI pattern (subprocess, no API key needed
if the CLI is already authenticated).  API mode is faster and supports streaming
natively but requires a ``GEMINI_API_KEY`` environment variable.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from typing import Any

from aqm.core.agent import AgentDefinition
from aqm.core.task import Task
from aqm.runtime.base import AbstractRuntime, OutputCallback

logger = logging.getLogger(__name__)

# Default model when agent.model is not specified
_DEFAULT_GEMINI_MODEL = "gemini-2.0-flash"


def _check_gemini_cli_available() -> None:
    """Verify that the ``gemini`` CLI binary is on PATH."""
    if shutil.which("gemini") is None:
        raise FileNotFoundError(
            "The 'gemini' CLI was not found on PATH. "
            "Please install it first: npm install -g @anthropic-ai/gemini-cli "
            "or see https://github.com/google-gemini/gemini-cli"
        )


class GeminiCLIRuntime(AbstractRuntime):
    """Run Gemini via the ``gemini`` CLI in non-interactive mode.

    Uses ``gemini -p <prompt>`` which outputs text to stdout.
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
            cmd.extend(["--model", agent.model])

        if agent.system_prompt:
            cmd.extend(["--system-prompt", agent.system_prompt])

        logger.info(
            "[GeminiCLIRuntime] Running agent '%s' (model=%s)",
            agent.id,
            agent.model or _DEFAULT_GEMINI_MODEL,
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


class GeminiAPIRuntime(AbstractRuntime):
    """Run Gemini via the ``google-genai`` SDK (API mode).

    Requires ``GEMINI_API_KEY`` environment variable.
    Install the SDK: ``pip install aqm[gemini]``
    """

    def __init__(self, project_root=None) -> None:
        self._project_root = project_root
        self._client: Any = None

    @property
    def name(self) -> str:
        return "gemini_api"

    def _get_client(self) -> Any:
        """Lazy-initialize the Gemini client."""
        if self._client is None:
            api_key = os.environ.get("GEMINI_API_KEY")
            if not api_key:
                raise ValueError(
                    "GEMINI_API_KEY environment variable is required for "
                    "gemini_api runtime. Set it via: "
                    "export GEMINI_API_KEY=your-key-here"
                )
            try:
                from google import genai
            except ImportError:
                raise ImportError(
                    "The 'google-genai' package is required for gemini_api "
                    "runtime. Install it via: pip install aqm[gemini]"
                )
            self._client = genai.Client(api_key=api_key)
        return self._client

    def run(
        self,
        prompt: str,
        agent: AgentDefinition,
        task: Task,
        on_output: OutputCallback = None,
    ) -> str:
        client = self._get_client()
        model = agent.model or _DEFAULT_GEMINI_MODEL

        logger.info(
            "[GeminiAPIRuntime] Running agent '%s' (model=%s)",
            agent.id,
            model,
        )

        # Build contents with system instruction if provided
        config: dict[str, Any] = {}
        if agent.system_prompt:
            from google.genai import types
            config["config"] = types.GenerateContentConfig(
                system_instruction=agent.system_prompt,
            )

        if on_output:
            return self._run_streaming(client, model, prompt, agent, config, on_output)

        response = client.models.generate_content(
            model=model,
            contents=prompt,
            **config,
        )

        output = response.text.strip() if response.text else ""
        logger.info(
            "[GeminiAPIRuntime] Agent '%s' completed (%d chars)",
            agent.id,
            len(output),
        )
        return output

    def _run_streaming(
        self,
        client: Any,
        model: str,
        prompt: str,
        agent: AgentDefinition,
        config: dict[str, Any],
        on_output: OutputCallback,
    ) -> str:
        """Run with streaming via the SDK."""
        chunks: list[str] = []

        response = client.models.generate_content_stream(
            model=model,
            contents=prompt,
            **config,
        )

        for chunk in response:
            text = chunk.text or ""
            if text:
                chunks.append(text)
                try:
                    for line in text.splitlines():
                        on_output(line)
                except Exception:
                    pass

        output = "".join(chunks).strip()
        logger.info(
            "[GeminiAPIRuntime] Agent '%s' completed (%d chars)",
            agent.id,
            len(output),
        )
        return output
