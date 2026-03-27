"""ClaudeCodeRuntime -- Run Claude Code CLI as a subprocess."""

from __future__ import annotations

import atexit
import json
import logging
import os
import selectors
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from aqm.core.agent import AgentDefinition, MCPServerConfig
from aqm.core.task import Task
from aqm.runtime.base import AbstractRuntime, OutputCallback, RuntimeExecutionError, ThinkingCallback, ToolCallback

logger = logging.getLogger(__name__)

# Registry of temp files to clean up on interpreter exit (safety net).
_TEMP_FILES_TO_CLEANUP: list[Path] = []


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


def _cleanup_temp_files() -> None:
    """Remove any leftover temp files at interpreter shutdown."""
    for p in _TEMP_FILES_TO_CLEANUP:
        try:
            p.unlink(missing_ok=True)
        except OSError:
            pass


atexit.register(_cleanup_temp_files)


def _build_mcp_config(servers: list[MCPServerConfig]) -> dict:
    """Convert a list of MCP server configs to the Claude Code --mcp-config JSON format.

    The expected format for Claude Code CLI is::

        {
          "mcpServers": {
            "server-name": {
              "command": "npx",
              "args": ["-y", "@modelcontextprotocol/server-xxx"],
              "env": {}
            }
          }
        }
    """
    config: dict = {"mcpServers": {}}
    for server in servers:
        entry: dict = {}
        if server.command:
            entry["command"] = server.command
            entry["args"] = server.args
        else:
            # Default to npx-based invocation when only the server name is provided.
            entry["command"] = "npx"
            entry["args"] = [
                "-y",
                f"@modelcontextprotocol/server-{server.server}",
            ] + server.args
        # Always include the "env" key -- Claude Code CLI expects it to be present.
        entry["env"] = server.env if server.env else {}
        config["mcpServers"][server.server] = entry
    return config


def _write_temp_file(content: str, *, prefix: str, suffix: str) -> Path:
    """Write *content* to a named temp file and register it for crash-safe cleanup.

    The file is closed before returning so that subprocesses can read it on
    all platforms (Windows locks open files).  The file is also registered in
    ``_TEMP_FILES_TO_CLEANUP`` so that an ``atexit`` handler will remove it
    even if the caller forgets or the process crashes before ``finally``.
    """
    fd, path_str = tempfile.mkstemp(prefix=prefix, suffix=suffix)
    path = Path(path_str)
    _TEMP_FILES_TO_CLEANUP.append(path)
    try:
        os.write(fd, content.encode("utf-8"))
    finally:
        os.close(fd)
    return path


def _redact_command(cmd: list[str]) -> list[str]:
    """Return a copy of *cmd* with sensitive flag values replaced by ``<REDACTED>``.

    Redacts the value following ``--system-prompt`` since it may contain
    proprietary instructions.  Also redacts very long ``-p`` prompt values
    to keep log lines manageable.
    """
    redacted: list[str] = []
    sensitive_flags = {"--system-prompt"}
    skip_next = False
    for i, token in enumerate(cmd):
        if skip_next:
            redacted.append("<REDACTED>")
            skip_next = False
            continue
        if token in sensitive_flags:
            redacted.append(token)
            skip_next = True
            continue
        redacted.append(token)
    return redacted


def _check_claude_cli_available() -> None:
    """Verify that the ``claude`` CLI binary is on PATH.

    Raises :class:`FileNotFoundError` with a helpful message when it is not.
    """
    if shutil.which("claude") is None:
        raise FileNotFoundError(
            "The 'claude' CLI was not found on PATH. "
            "Please install Claude Code CLI first: "
            "https://docs.anthropic.com/en/docs/claude-code"
        )


class ClaudeCodeRuntime(AbstractRuntime):
    """Execute a task by invoking the Claude Code CLI as a subprocess."""

    def __init__(self, project_root: Path | None = None, timeout: int = 600) -> None:
        self.project_root = project_root or Path.cwd()
        self._timeout = timeout

    @property
    def name(self) -> str:
        return "claude_code"

    def run(
        self,
        prompt: str,
        agent: AgentDefinition,
        task: Task,
        on_output: OutputCallback = None,
        on_thinking: ThinkingCallback = None,
        on_tool: ToolCallback = None,
    ) -> str:
        # --- Pre-flight check ---------------------------------------------------
        _check_claude_cli_available()

        # Build the command.  We use ``subprocess.run`` with a *list* (no
        # shell=True) so that quotes, newlines, and other special characters in
        # the prompt / system-prompt are passed verbatim to the CLI without any
        # shell interpretation.
        cmd: list[str] = ["claude", "--print", "-p", prompt]

        # --- System prompt -------------------------------------------------------
        if agent.system_prompt:
            cmd.extend(["--system-prompt", agent.system_prompt])

        if agent.model:
            cmd.extend(["--model", agent.model])

        if agent.cli_flags:
            cmd.extend(agent.cli_flags)

        # --- Non-interactive permission handling ----------------------------------
        # In --print mode, stdin is not available so Claude cannot prompt for
        # tool permissions.  Auto-add --dangerously-skip-permissions to prevent
        # the process from hanging indefinitely.
        if "--print" in cmd and "--dangerously-skip-permissions" not in cmd:
            cmd.append("--dangerously-skip-permissions")
            logger.warning(
                "[ClaudeCodeRuntime] Auto-adding --dangerously-skip-permissions "
                "for non-interactive --print mode (agent=%s)",
                agent.id,
            )

        # --- MCP server config ---------------------------------------------------
        mcp_config_path: Path | None = None
        if agent.mcp:
            mcp_config = _build_mcp_config(agent.mcp)
            mcp_config_path = _write_temp_file(
                json.dumps(mcp_config, indent=2),
                prefix="aq_mcp_",
                suffix=".json",
            )
            cmd.extend(["--mcp-config", str(mcp_config_path)])

        # --- Logging (redacted) --------------------------------------------------
        logger.info(
            "[ClaudeCodeRuntime] Running agent '%s' | command: %s",
            agent.id,
            " ".join(_redact_command(cmd)),
        )

        try:
            if on_output:
                output = self._run_streaming(cmd, agent, on_output, on_thinking, on_tool)
            else:
                try:
                    result = subprocess.run(
                        cmd,
                        capture_output=True,
                        text=True,
                        cwd=str(self.project_root),
                        timeout=self._timeout,
                    )
                except FileNotFoundError:
                    raise RuntimeExecutionError(
                        f"Claude Code CLI not found (agent={agent.id}): "
                        "ensure 'claude' is installed and on PATH",
                        error_category="cli_missing",
                    )
                except subprocess.TimeoutExpired:
                    raise RuntimeExecutionError(
                        f"Claude Code timed out after {self._timeout}s (agent={agent.id})",
                        error_category="timeout",
                    )

                if result.returncode != 0:
                    stderr = result.stderr.strip()
                    error_category = _classify_error(stderr, result.returncode)
                    error_msg = (
                        stderr or f"process exited with code {result.returncode}"
                    )
                    logger.error(
                        "[ClaudeCodeRuntime] Agent '%s' failed (%s): %s",
                        agent.id,
                        error_category,
                        error_msg,
                    )
                    raise RuntimeExecutionError(
                        f"Claude Code execution failed (agent={agent.id}): {error_msg}",
                        partial_output=result.stdout.strip(),
                        error_category=error_category,
                    )

                output = result.stdout.strip()

            logger.info(
                "[ClaudeCodeRuntime] Agent '%s' completed (%d chars)",
                agent.id,
                len(output),
            )
            return output

        finally:
            # Clean up temp files even if the process crashes or times out.
            if mcp_config_path is not None:
                mcp_config_path.unlink(missing_ok=True)
                if mcp_config_path in _TEMP_FILES_TO_CLEANUP:
                    _TEMP_FILES_TO_CLEANUP.remove(mcp_config_path)

    def _run_streaming(
        self,
        cmd: list[str],
        agent: AgentDefinition,
        on_output: OutputCallback,
        on_thinking: ThinkingCallback = None,
        on_tool: ToolCallback = None,
    ) -> str:
        """Run with true token-level streaming via ``--include-partial-messages``.

        Always uses ``--output-format stream-json --include-partial-messages``
        for real-time token streaming.  Thinking blocks are forwarded to
        *on_thinking* when provided.
        """
        stream_cmd = list(cmd)
        if "--verbose" not in stream_cmd:
            stream_cmd.append("--verbose")
        stream_cmd.extend([
            "--output-format", "stream-json",
            "--include-partial-messages",
        ])
        return self._run_stream_json(stream_cmd, agent, on_output, on_thinking, on_tool)

    def _run_stream_json(
        self,
        cmd: list[str],
        agent: AgentDefinition,
        on_output: OutputCallback,
        on_thinking: ThinkingCallback = None,
        on_tool: ToolCallback = None,
    ) -> str:
        """Run with ``--output-format stream-json`` for real-time streaming.

        Handles two event formats:
        - **stream_event** (with ``--include-partial-messages``):
          Token-level deltas via ``content_block_delta``.
        - **assistant** (without partial messages):
          Full message with complete content blocks.
        """
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=str(self.project_root),
        )

        output_parts: list[str] = []
        got_stream_events = False  # Track if we received deltas
        callback_errors = 0
        _MAX_CALLBACK_ERRORS = 10
        # Use selectors for non-blocking reads with heartbeat support.
        # This prevents the CLI from appearing hung during long operations.
        HEARTBEAT_INTERVAL = 30  # seconds
        sel = selectors.DefaultSelector()
        sel.register(proc.stdout, selectors.EVENT_READ)
        try:
            while True:
                events = sel.select(timeout=HEARTBEAT_INTERVAL)
                if not events:
                    # No output for HEARTBEAT_INTERVAL — send keepalive
                    if on_output:
                        try:
                            on_output("")
                        except Exception:
                            pass
                    # Check if process has exited
                    if proc.poll() is not None:
                        break
                    continue
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
                    except Exception as cb_err:
                        callback_errors += 1
                        if callback_errors <= _MAX_CALLBACK_ERRORS:
                            logger.warning(
                                '[ClaudeCodeRuntime] callback error (agent=%s): %s',
                                agent.id, cb_err,
                            )
                    continue

                etype = event.get("type", "")

                # Token-level streaming via --include-partial-messages
                if etype == "stream_event":
                    got_stream_events = True
                    inner = event.get("event", {})
                    inner_type = inner.get("type", "")

                    if inner_type == "content_block_start":
                        cb = inner.get("content_block", {})
                        if cb.get("type") == "tool_use" and on_tool:
                            try:
                                on_tool("tool_start", {
                                    "tool_use_id": cb.get("id", ""),
                                    "tool": cb.get("name", ""),
                                })
                            except Exception:
                                pass

                    elif inner_type == "content_block_delta":
                        delta = inner.get("delta", {})
                        delta_type = delta.get("type", "")
                        if delta_type == "thinking_delta":
                            thinking_text = delta.get("thinking", "")
                            if thinking_text and on_thinking:
                                try:
                                    on_thinking(thinking_text)
                                except Exception as cb_err:
                                    callback_errors += 1
                                    if callback_errors <= _MAX_CALLBACK_ERRORS:
                                        logger.warning(
                                            '[ClaudeCodeRuntime] callback error (agent=%s): %s',
                                            agent.id, cb_err,
                                        )
                                    elif callback_errors == _MAX_CALLBACK_ERRORS + 1:
                                        logger.error(
                                            '[ClaudeCodeRuntime] Too many callback errors (agent=%s), suppressing',
                                            agent.id,
                                        )
                        elif delta_type == "text_delta":
                            text = delta.get("text", "")
                            if text:
                                output_parts.append(text)
                                try:
                                    on_output(text)
                                except Exception as cb_err:
                                    callback_errors += 1
                                    if callback_errors <= _MAX_CALLBACK_ERRORS:
                                        logger.warning(
                                            '[ClaudeCodeRuntime] callback error (agent=%s): %s',
                                            agent.id, cb_err,
                                        )
                                    elif callback_errors == _MAX_CALLBACK_ERRORS + 1:
                                        logger.error(
                                            '[ClaudeCodeRuntime] Too many callback errors (agent=%s), suppressing',
                                            agent.id,
                                        )
                        elif delta_type == "input_json_delta" and on_tool:
                            partial = delta.get("partial_json", "")
                            if partial:
                                try:
                                    on_tool("tool_input", {"partial_json": partial})
                                except Exception:
                                    pass

                # Full message fallback — only used when NO stream_events
                # were received (i.e. --include-partial-messages not active)
                elif etype == "assistant" and not got_stream_events:
                    msg = event.get("message", {})
                    for block in msg.get("content", []):
                        btype = block.get("type", "")
                        if btype == "thinking":
                            thinking_text = block.get("thinking", "")
                            if thinking_text and on_thinking:
                                try:
                                    on_thinking(thinking_text)
                                except Exception as cb_err:
                                    callback_errors += 1
                                    if callback_errors <= _MAX_CALLBACK_ERRORS:
                                        logger.warning(
                                            '[ClaudeCodeRuntime] callback error (agent=%s): %s',
                                            agent.id, cb_err,
                                        )
                                    elif callback_errors == _MAX_CALLBACK_ERRORS + 1:
                                        logger.error(
                                            '[ClaudeCodeRuntime] Too many callback errors (agent=%s), suppressing',
                                            agent.id,
                                        )
                        elif btype == "tool_use" and on_tool:
                            try:
                                on_tool("tool_start", {
                                    "tool_use_id": block.get("id", ""),
                                    "tool": block.get("name", ""),
                                    "input": block.get("input", {}),
                                })
                            except Exception:
                                pass
                        elif btype == "text":
                            text = block.get("text", "")
                            if text:
                                output_parts.append(text)
                                try:
                                    on_output(text)
                                except Exception as cb_err:
                                    callback_errors += 1
                                    if callback_errors <= _MAX_CALLBACK_ERRORS:
                                        logger.warning(
                                            '[ClaudeCodeRuntime] callback error (agent=%s): %s',
                                            agent.id, cb_err,
                                        )
                                    elif callback_errors == _MAX_CALLBACK_ERRORS + 1:
                                        logger.error(
                                            '[ClaudeCodeRuntime] Too many callback errors (agent=%s), suppressing',
                                            agent.id,
                                        )

                elif etype == "tool_result" and on_tool:
                    try:
                        on_tool("tool_result", {
                            "tool_use_id": event.get("tool_use_id", ""),
                            "content": event.get("content", ""),
                        })
                    except Exception:
                        pass

                elif etype == "result":
                    result_text = event.get("result", "")
                    if result_text and not output_parts:
                        output_parts.append(result_text)

        except subprocess.TimeoutExpired:
            raise RuntimeExecutionError(
                f"Claude Code timed out after {self._timeout}s (agent={agent.id})",
                partial_output="".join(output_parts).strip(),
                error_category="timeout",
            )
        finally:
            sel.close()
            if proc.poll() is None:
                proc.kill()
            proc.wait()

        if proc.returncode != 0:
            try:
                stderr = (
                    proc.stderr.read().strip() if proc.stderr
                    else ""
                )
            except (ValueError, OSError):
                stderr = ""
            error_category = _classify_error(stderr, proc.returncode)
            error_msg = stderr or f"process exited with code {proc.returncode}"
            logger.error(
                "[ClaudeCodeRuntime] Agent '%s' failed (%s): %s",
                agent.id,
                error_category,
                error_msg,
            )
            raise RuntimeExecutionError(
                f"Claude Code execution failed (agent={agent.id}): {error_msg}",
                partial_output="".join(output_parts).strip(),
                error_category=error_category,
            )

        return "".join(output_parts).strip()
