"""Pipeline — agent orchestration execution loop.

Tasks move between agents through queues,
pass through gates where approve/reject decisions are made,
and are forwarded to the next agent based on handoff conditions.
"""

from __future__ import annotations

import logging
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import copy

from aqm.core.agent import AgentDefinition, Handoff, load_agents
from aqm.core.context import build_payload, build_prompt
from aqm.core.context_file import ContextFile
from aqm.core.gate import (
    AbstractGate,
    GateResult,
    HumanGate,
    LLMGate,
)
from aqm.core.project import get_tasks_dir
from aqm.core.task import StageRecord, Task, TaskStatus
from aqm.queue.base import AbstractQueue
from aqm.runtime.base import AbstractRuntime
from aqm.runtime.claude import ClaudeCodeRuntime
from aqm.runtime.gemini import GeminiCLIRuntime
from aqm.runtime.codex import CodexCLIRuntime

from aqm.core.config import ProjectConfig

logger = logging.getLogger(__name__)

MAX_STAGES = 20  # Fallback; overridden by config.pipeline.max_stages

# Thread-safe set of task IDs that have been requested to cancel.
# Checked each iteration of the pipeline loop.
_cancelled_tasks: set[str] = set()
_cancel_lock = threading.Lock()


def cancel_task(task_id: str) -> None:
    """Request cancellation of a running task."""
    with _cancel_lock:
        _cancelled_tasks.add(task_id)


def is_cancelled(task_id: str) -> bool:
    """Check if a task has been requested to cancel."""
    with _cancel_lock:
        return task_id in _cancelled_tasks


class Pipeline:
    """Agent pipeline orchestrator."""

    def __init__(
        self,
        agents: dict[str, AgentDefinition],
        queue: AbstractQueue,
        project_root: Path,
        anthropic_client=None,
        config: ProjectConfig | None = None,
    ) -> None:
        self.agents = agents
        self.queue = queue
        self.project_root = project_root
        self._anthropic_client = anthropic_client
        self._runtimes: dict[str, AbstractRuntime] = {}
        self.config = config or ProjectConfig()

    @property
    def anthropic_client(self):
        """Lazy-initialize Anthropic client (used only for LLM gates)."""
        if self._anthropic_client is None:
            import anthropic

            self._anthropic_client = anthropic.Anthropic()
        return self._anthropic_client

    def _get_runtime(self, agent: AgentDefinition) -> AbstractRuntime:
        """Return a runtime instance matching the agent's runtime type.

        For ``claude`` runtime, auto-selects between text-only and Claude Code
        mode: if the agent has ``mcp`` servers or ``claude_code_flags``, it
        runs in full Claude Code mode (tool access); otherwise text-only.
        """
        rt = agent.runtime

        t = self.config.timeouts
        if rt == "claude":
            if "claude" not in self._runtimes:
                self._runtimes["claude"] = ClaudeCodeRuntime(self.project_root, timeout=t.claude)
            return self._runtimes["claude"]

        if rt not in self._runtimes:
            if rt == "gemini":
                self._runtimes[rt] = GeminiCLIRuntime(self.project_root, timeout=t.gemini)
            elif rt == "codex":
                self._runtimes[rt] = CodexCLIRuntime(self.project_root, timeout=t.codex)
            else:
                raise ValueError(f"Unknown runtime: {rt}")
        return self._runtimes[rt]

    def _get_gate(self, agent: AgentDefinition) -> Optional[AbstractGate]:
        """Return a gate instance matching the agent's gate configuration."""
        if not agent.gate:
            return None
        if agent.gate.type == "llm":
            return LLMGate(agent.gate, self.anthropic_client, gate_defaults=self.config.gate)
        elif agent.gate.type == "human":
            return HumanGate()
        else:
            raise ValueError(f"Unknown gate type: {agent.gate.type}")

    def _get_context_file(self, task: Task) -> ContextFile:
        """Return the context file manager for the task."""
        tasks_dir = get_tasks_dir(self.project_root)
        task_dir = tasks_dir / task.id
        if not task.context_dir:
            task.context_dir = str(task_dir)
        return ContextFile(task_dir)

    # ------------------------------------------------------------------
    # Handoff condition evaluation
    # ------------------------------------------------------------------

    def _evaluate_condition(
        self,
        condition: str,
        gate_result: Optional[GateResult],
        agent_output: str,
    ) -> bool:
        """Evaluate a handoff condition."""
        if condition == "always":
            return True
        if condition == "on_approve":
            return gate_result is not None and gate_result.decision == "approved"
        if condition == "on_reject":
            return gate_result is not None and gate_result.decision == "rejected"
        if condition == "on_pass":
            return gate_result is None or gate_result.decision == "approved"
        if condition == "auto":
            # Always "matches" — actual target is resolved from agent output.
            return True

        # Expression conditions (e.g., "severity == critical")
        try:
            eq_match = re.match(
                r"(\w+)\s*==\s*[\"']?(\w+)[\"']?", condition
            )
            if eq_match:
                key, value = eq_match.groups()
                return bool(re.search(
                    r'\b' + re.escape(value) + r'\b',
                    agent_output, re.IGNORECASE,
                ))

            in_match = re.match(
                r"(\w+)\s+in\s+\[([^\]]+)\]", condition
            )
            if in_match:
                key, values_str = in_match.groups()
                values = [
                    v.strip().strip("\"'")
                    for v in values_str.split(",")
                ]
                return any(
                    re.search(r'\b' + re.escape(v) + r'\b', agent_output, re.IGNORECASE)
                    for v in values
                )
        except Exception:
            logger.warning(f"Condition evaluation failed: {condition}")

        return False

    def _parse_auto_handoff_targets(self, agent_output: str) -> list[str]:
        """Extract agent IDs from ``HANDOFF: id1, id2`` directives in agent output.

        The agent can include one or more lines like::

            HANDOFF: developer
            HANDOFF: developer, qa

        Returns a deduplicated list of agent IDs in order of appearance.
        """
        targets: list[str] = []
        for m in re.finditer(r"HANDOFF:\s*(.+)", agent_output, re.IGNORECASE):
            for part in m.group(1).split(","):
                t = part.strip()
                if t and t not in targets:
                    targets.append(t)
        return targets

    def _resolve_handoffs(
        self,
        agent: AgentDefinition,
        gate_result: Optional[GateResult],
        agent_output: str,
        input_text: str,
    ) -> list[tuple[str, str]]:
        """Evaluate handoff conditions and return a list of (target_agent_id, payload).

        Supports:
        - **auto**: targets parsed from ``HANDOFF: <id>`` in agent output
        - **fan-out**: comma-separated ``to`` field (e.g. ``"qa, docs"``)
        - **multi-match**: all matching handoff rules contribute targets
        """
        results: list[tuple[str, str]] = []
        seen: set[str] = set()

        for handoff in agent.handoffs:
            if not self._evaluate_condition(
                handoff.condition, gate_result, agent_output
            ):
                continue

            payload = build_payload(
                handoff.payload,
                output=agent_output,
                input_text=input_text,
                reject_reason=(
                    gate_result.reason if gate_result else ""
                ),
                gate_result=(
                    gate_result.decision if gate_result else ""
                ),
            )

            if handoff.condition == "auto":
                # Agent decides: parse HANDOFF directives from output
                auto_targets = self._parse_auto_handoff_targets(agent_output)
                if not auto_targets:
                    logger.warning(
                        "[Pipeline] condition=auto but no HANDOFF directive "
                        "found in agent output; skipping handoff."
                    )
                    continue
                for target in auto_targets:
                    if target in self.agents and target not in seen:
                        results.append((target, payload))
                        seen.add(target)
                    elif target not in self.agents:
                        logger.warning(
                            f"[Pipeline] HANDOFF target '{target}' "
                            f"does not exist; skipping."
                        )
            else:
                # Static or expression condition — expand comma-separated targets
                targets = [t.strip() for t in handoff.to.split(",")]
                for target in targets:
                    if target not in seen:
                        results.append((target, payload))
                        seen.add(target)

        return results

    def run_task(
        self,
        task: Task,
        start_agent_id: str,
        input_text: str | None = None,
        on_stage_complete=None,
        on_stage_start=None,
        on_output=None,
        on_thinking=None,
        on_human_input_request=None,
        on_tool=None,
    ) -> Task:
        """Run a task through the pipeline.

        Args:
            task: The task to run
            start_agent_id: Starting agent ID
            input_text: Input to pass to the first agent (uses task.description if None)
            on_stage_complete: Stage completion callback (task, stage_record)
            on_stage_start: Stage start callback (task, agent_id, stage_number)
            on_output: Output line callback (line_text) for streaming
            on_thinking: Thinking line callback (line_text) for streaming thinking
            on_human_input_request: Callback (task, agent_id, questions) when human input needed
            on_tool: Tool use event callback (event_type, data_dict) for streaming tool use

        Returns:
            The Task in completed (or gate-awaiting) state
        """
        current_input = input_text or task.description
        current_agent_id = start_agent_id

        ctx_file = self._get_context_file(task)

        # Track per-agent gate rejection counts to prevent infinite reject loops.
        # Counts are reset when a different agent runs (so re-visiting an agent
        # after other work starts a fresh rejection budget).
        reject_counts: dict[str, int] = {}
        _prev_agent_id: str | None = None

        max_stages = self.config.pipeline.max_stages
        while task.next_stage_number <= max_stages:
            # Check for cancellation
            if is_cancelled(task.id):
                with _cancel_lock:
                    _cancelled_tasks.discard(task.id)
                task.status = TaskStatus.cancelled
                task.metadata["cancel_reason"] = "Cancelled by user"
                self.queue.update(task)
                logger.info(f"[Pipeline] {task.id} cancelled by user")
                return task

            if current_agent_id not in self.agents:
                raise ValueError(
                    f"Agent '{current_agent_id}' is not defined."
                )

            agent = self.agents[current_agent_id]
            # Reset reject counter when agent changes (fresh budget on re-visit)
            if current_agent_id != _prev_agent_id:
                reject_counts.pop(current_agent_id, None)
                _prev_agent_id = current_agent_id
            task.current_agent_id = current_agent_id
            task.status = TaskStatus.in_progress
            self.queue.update(task)

            # ── Session node: delegate to conversation loop ──
            if agent.type == "session":
                logger.info(
                    f"[Pipeline] {task.id} -> session '{agent.id}' "
                    f"(participants: {agent.participants})"
                )
                try:
                    output = self._run_session(
                        session=agent,
                        task=task,
                        input_text=current_input,
                        ctx_file=ctx_file,
                        on_turn_start=on_stage_start,
                        on_turn_complete=on_stage_complete,
                        on_output=on_output,
                        on_thinking=on_thinking,
                        on_tool=on_tool,
                    )
                    # Record the session as a single stage
                    stage = StageRecord(
                        stage_number=task.next_stage_number,
                        agent_id=agent.id,
                        task_name="session",
                        input_text=current_input,
                        output_text=output,
                        finished_at=datetime.now(timezone.utc),
                    )
                    task.add_stage(stage)
                    self.queue.update(task)
                    ctx_file.append_stage(
                        stage_number=stage.stage_number,
                        agent_id=agent.id,
                        task_name="session",
                        status="completed",
                        input_text=current_input,
                        output_text=output,
                    )
                    if on_stage_complete:
                        on_stage_complete(task, stage)
                except Exception as e:
                    stage = StageRecord(
                        stage_number=task.next_stage_number,
                        agent_id=agent.id,
                        task_name="session",
                        input_text=current_input,
                        output_text=f"ERROR: {e}",
                        finished_at=datetime.now(timezone.utc),
                    )
                    task.add_stage(stage)
                    task.status = TaskStatus.failed
                    self.queue.update(task)
                    logger.error(
                        f"[Pipeline] {task.id} session '{agent.id}' failed: {e}"
                    )
                    return task

                # Check if session was cancelled
                if is_cancelled(task.id):
                    with _cancel_lock:
                        _cancelled_tasks.discard(task.id)
                    task.status = TaskStatus.cancelled
                    task.metadata["cancel_reason"] = "Cancelled during session"
                    self.queue.update(task)
                    return task

                # Session nodes skip gate evaluation; proceed to handoffs
                gate_result: Optional[GateResult] = None
                # Jump to handoff resolution below
                # (output variable is set from session result)
                handoff_targets = self._resolve_handoffs(
                    agent, gate_result, output, current_input
                )

                if not handoff_targets:
                    task.status = TaskStatus.completed
                    self.queue.update(task)
                    logger.info(f"[Pipeline] {task.id} completed")
                    return task

                if len(handoff_targets) > 1:
                    for extra_agent_id, extra_payload in handoff_targets[1:]:
                        child = Task(
                            description=f"[fan-out from {task.id}] {task.description}",
                            metadata={"parent_task_id": task.id},
                        )
                        self.queue.push(child, extra_agent_id)
                        self.run_task(
                            child, extra_agent_id,
                            input_text=extra_payload,
                            on_stage_complete=on_stage_complete,
                            on_stage_start=on_stage_start,
                            on_output=on_output,
                            on_thinking=on_thinking,
                            on_tool=on_tool,
                        )

                next_agent_id, next_payload = handoff_targets[0]
                ctx_file.save_payload(next_payload)
                current_input = next_payload
                current_agent_id = next_agent_id
                continue

            # ── Regular agent execution ──
            logger.info(
                f"[Pipeline] {task.id} -> agent '{agent.id}' "
                f"(stage {task.next_stage_number})"
            )

            # ── Human input: "before" mode ──
            hi_cfg = agent.human_input
            if hi_cfg and hi_cfg.enabled and hi_cfg.mode in ("before", "both"):
                # Check if we already have a human response for this stage
                hi_key = f"_human_input_{agent.id}_{task.next_stage_number}"
                if hi_key not in task.metadata:
                    question = hi_cfg.prompt or (
                        f"Agent '{agent.name or agent.id}' needs your input before proceeding.\n\n"
                        f"Current task: {current_input[:500]}"
                    )
                    task.status = TaskStatus.awaiting_human_input
                    task.metadata["_human_input_pending"] = {
                        "agent_id": agent.id,
                        "stage_number": task.next_stage_number,
                        "questions": [question],
                        "mode": "before",
                    }
                    self.queue.update(task)

                    if on_human_input_request:
                        on_human_input_request(task, agent.id, [question])

                    logger.info(
                        f"[Pipeline] {task.id} awaiting human input "
                        f"(agent={agent.id}, mode=before)"
                    )
                    return task
                else:
                    # Human already responded — inject into input
                    human_response = task.metadata.pop(hi_key, "")
                    if human_response:
                        current_input = (
                            f"{current_input}\n\n"
                            f"--- User Input ---\n{human_response}"
                        )

            # Build prompt (respect agent's context_strategy + context_window)
            context_text = ctx_file.read_for_strategy(
                agent.id, agent.context_strategy, agent.context_window,
            )
            prompt = build_prompt(
                system_prompt_template=agent.system_prompt,
                input_text=current_input,
                context=context_text,
            )

            # Run agent
            stage = StageRecord(
                stage_number=task.next_stage_number,
                agent_id=agent.id,
                task_name=(
                    agent.handoffs[0].task if agent.handoffs else "execute"
                ),
                input_text=current_input,
            )

            if on_stage_start:
                on_stage_start(task, agent.id, task.next_stage_number)

            try:
                runtime = self._get_runtime(agent)
                output = runtime.run(prompt, agent, task, on_output=on_output, on_thinking=on_thinking, on_tool=on_tool)
                stage.output_text = output
                stage.finished_at = datetime.now(timezone.utc)
            except Exception as e:
                stage.output_text = f"ERROR: {e}"
                stage.finished_at = datetime.now(timezone.utc)
                task.add_stage(stage)
                task.status = TaskStatus.failed
                self.queue.update(task)

                ctx_file.append_stage(
                    stage_number=stage.stage_number,
                    agent_id=agent.id,
                    task_name=stage.task_name,
                    status="failed",
                    input_text=current_input,
                    output_text=stage.output_text,
                )
                logger.error(
                    f"[Pipeline] {task.id} agent '{agent.id}' execution failed: {e}"
                )
                return task

            # ── Human input: "on_demand" mode ──
            if hi_cfg and hi_cfg.enabled and hi_cfg.mode in ("on_demand", "both"):
                hi_questions = self._parse_human_input_requests(output)
                if hi_questions:
                    hi_key = f"_human_input_od_{agent.id}_{stage.stage_number}"
                    if hi_key not in task.metadata:
                        # Pause pipeline — record stage first
                        task.add_stage(stage)
                        self.queue.update(task)

                        task.status = TaskStatus.awaiting_human_input
                        task.metadata["_human_input_pending"] = {
                            "agent_id": agent.id,
                            "stage_number": stage.stage_number,
                            "questions": hi_questions,
                            "mode": "on_demand",
                        }
                        self.queue.update(task)

                        ctx_file.append_stage(
                            stage_number=stage.stage_number,
                            agent_id=agent.id,
                            task_name=stage.task_name,
                            status="awaiting_human_input",
                            input_text=current_input,
                            output_text=output,
                        )

                        if on_human_input_request:
                            on_human_input_request(task, agent.id, hi_questions)

                        logger.info(
                            f"[Pipeline] {task.id} awaiting human input "
                            f"(agent={agent.id}, mode=on_demand, "
                            f"questions={len(hi_questions)})"
                        )
                        return task

            # Gate evaluation
            gate_result: Optional[GateResult] = None
            gate = self._get_gate(agent)

            if gate is not None:
                gate_result = gate.evaluate(task, output)

                if gate_result is None:
                    # HumanGate — pause the pipeline
                    stage.gate_result = None
                    task.add_stage(stage)
                    task.status = TaskStatus.awaiting_gate
                    self.queue.update(task)

                    ctx_file.append_stage(
                        stage_number=stage.stage_number,
                        agent_id=agent.id,
                        task_name=stage.task_name,
                        status="awaiting_gate",
                        input_text=current_input,
                        output_text=output,
                    )

                    logger.info(
                        f"[Pipeline] {task.id} waiting for human gate "
                        f"(agent={agent.id})"
                    )
                    return task

                stage.gate_result = gate_result.decision  # type: ignore
                stage.reject_reason = gate_result.reason

                # Track reject counts per agent to prevent infinite loops
                if gate_result.decision == "rejected":
                    reject_counts[agent.id] = reject_counts.get(agent.id, 0) + 1
                    max_retries = agent.gate.max_retries if agent.gate else 3
                    if reject_counts[agent.id] > max_retries:
                        stage.output_text = output
                        stage.finished_at = datetime.now(timezone.utc)
                        task.add_stage(stage)
                        task.status = TaskStatus.failed
                        task.metadata["error"] = (
                            f"Agent '{agent.id}' exceeded max gate retries "
                            f"({max_retries}). Gate kept rejecting output."
                        )
                        self.queue.update(task)
                        logger.error(
                            f"[Pipeline] {task.id} agent '{agent.id}' exceeded "
                            f"max gate retries ({max_retries})"
                        )
                        return task

            # Record stage
            task.add_stage(stage)
            self.queue.update(task)

            ctx_file.append_stage(
                stage_number=stage.stage_number,
                agent_id=agent.id,
                task_name=stage.task_name,
                status=(
                    gate_result.decision if gate_result else "completed"
                ),
                input_text=current_input,
                output_text=output,
                reject_reason=(
                    gate_result.reason if gate_result else None
                ),
            )

            # Write to agent's private context file
            ctx_file.append_agent_context(
                agent_id=agent.id,
                stage_number=stage.stage_number,
                input_text=current_input,
                output_text=output,
            )

            if on_stage_complete:
                on_stage_complete(task, stage)

            # Handoff
            handoff_targets = self._resolve_handoffs(
                agent, gate_result, output, current_input
            )

            if not handoff_targets:
                task.status = TaskStatus.completed
                self.queue.update(task)
                logger.info(f"[Pipeline] {task.id} completed")
                return task

            # Fan-out: first target continues in this task; additional
            # targets spawn independent child tasks.
            if len(handoff_targets) > 1:
                for extra_agent_id, extra_payload in handoff_targets[1:]:
                    child = Task(
                        description=f"[fan-out from {task.id}] {task.description}",
                        metadata={"parent_task_id": task.id},
                    )
                    self.queue.push(child, extra_agent_id)
                    logger.info(
                        f"[Pipeline] {task.id} fan-out -> "
                        f"child {child.id} -> agent '{extra_agent_id}'"
                    )
                    # Run child task asynchronously (push to queue, run inline)
                    self.run_task(
                        child,
                        extra_agent_id,
                        input_text=extra_payload,
                        on_stage_complete=on_stage_complete,
                        on_stage_start=on_stage_start,
                        on_output=on_output,
                        on_thinking=on_thinking,
                        on_tool=on_tool,
                    )

            next_agent_id, next_payload = handoff_targets[0]

            ctx_file.save_payload(next_payload)

            current_input = next_payload
            current_agent_id = next_agent_id

        # Exceeded max_stages
        task.status = TaskStatus.failed
        task.metadata["error"] = (
            f"Exceeded maximum number of stages ({max_stages})."
        )
        self.queue.update(task)
        logger.error(f"[Pipeline] {task.id} exceeded maximum stages")
        return task

    @staticmethod
    def _parse_human_input_requests(agent_output: str) -> list[str]:
        """Extract ``HUMAN_INPUT: <question>`` directives from agent output."""
        questions: list[str] = []
        for m in re.finditer(r"HUMAN_INPUT:[ \t]*([^\n]+)", agent_output, re.IGNORECASE):
            q = m.group(1).strip()
            if q:
                questions.append(q)
        return questions

    def resume_human_input(
        self,
        task_id: str,
        response: str,
        on_stage_complete=None,
        on_stage_start=None,
        on_output=None,
        on_thinking=None,
        on_human_input_request=None,
        on_tool=None,
    ) -> Task:
        """Resume the pipeline after a human input response.

        Records the response in context files and resumes the pipeline
        from the agent that requested input.
        """
        task = self.queue.get(task_id)
        if task is None:
            raise ValueError(f"Task '{task_id}' not found.")
        if task.status != TaskStatus.awaiting_human_input:
            raise ValueError(
                f"Task '{task_id}' is not awaiting human input. "
                f"(current: {task.status.value})"
            )

        pending = task.metadata.pop("_human_input_pending", {})
        agent_id = pending.get("agent_id", task.current_agent_id)
        mode = pending.get("mode", "on_demand")
        questions = pending.get("questions", [])

        # Record in context files
        ctx_file = self._get_context_file(task)
        question_text = "\n".join(f"- {q}" for q in questions)
        ctx_file.append_human_input(
            agent_id=agent_id,
            question=question_text,
            response=response,
        )

        if mode == "before":
            # Store response and re-run the same agent
            stage_number = pending.get("stage_number", task.next_stage_number)
            hi_key = f"_human_input_{agent_id}_{stage_number}"
            task.metadata[hi_key] = response
            task.status = TaskStatus.in_progress
            self.queue.update(task)

            # Determine input — use the last payload or description
            payload_path = ctx_file.task_dir / "current_payload.md"
            if payload_path.exists():
                input_text = payload_path.read_text(encoding="utf-8")
            else:
                input_text = task.description

            return self.run_task(
                task,
                agent_id,
                input_text=input_text,
                on_stage_complete=on_stage_complete,
                on_stage_start=on_stage_start,
                on_output=on_output,
                on_thinking=on_thinking,
                on_human_input_request=on_human_input_request,
                on_tool=on_tool,
            )
        else:
            # on_demand: re-run the agent with human response appended
            hi_key = f"_human_input_od_{agent_id}_{pending.get('stage_number', 0)}"
            task.metadata[hi_key] = response
            task.status = TaskStatus.in_progress
            self.queue.update(task)

            # Build new input with human response
            latest = task.latest_stage
            agent_output = latest.output_text if latest else ""
            new_input = (
                f"{latest.input_text if latest else task.description}\n\n"
                f"--- Agent's Questions ---\n{question_text}\n\n"
                f"--- User Response ---\n{response}\n\n"
                f"--- Previous Agent Output ---\n{agent_output}\n\n"
                f"Continue based on the user's response."
            )

            # Find next agent in handoffs or re-run same agent
            agent = self.agents.get(agent_id)
            if agent and agent.handoffs:
                # Continue to handoff targets
                handoff_targets = self._resolve_handoffs(
                    agent, None, agent_output, latest.input_text if latest else "",
                )
                if handoff_targets:
                    next_agent_id, _ = handoff_targets[0]
                    ctx_file.save_payload(new_input)
                    return self.run_task(
                        task, next_agent_id,
                        input_text=new_input,
                        on_stage_complete=on_stage_complete,
                        on_stage_start=on_stage_start,
                        on_output=on_output,
                        on_thinking=on_thinking,
                        on_human_input_request=on_human_input_request,
                        on_tool=on_tool,
                    )

            # No handoffs — re-run same agent
            return self.run_task(
                task, agent_id,
                input_text=new_input,
                on_stage_complete=on_stage_complete,
                on_stage_start=on_stage_start,
                on_output=on_output,
                on_thinking=on_thinking,
                on_human_input_request=on_human_input_request,
                on_tool=on_tool,
            )

    def resume_task(
        self,
        task_id: str,
        decision: str,
        reason: str = "",
        on_stage_complete=None,
        on_stage_start=None,
        on_output=None,
        on_thinking=None,
        on_tool=None,
    ) -> Task:
        """Resume the pipeline after a human gate.

        Args:
            task_id: Task ID
            decision: "approved" or "rejected"
            reason: Reason for the decision

        Returns:
            The resumed Task
        """
        task = self.queue.get(task_id)
        if task is None:
            raise ValueError(f"Task '{task_id}' not found.")
        if task.status not in (TaskStatus.awaiting_gate, TaskStatus.approved, TaskStatus.rejected):
            raise ValueError(
                f"Task '{task_id}' is not in a resumable state. "
                f"(current: {task.status.value})"
            )

        latest = task.latest_stage
        if latest is None:
            raise ValueError("No stage records found.")

        # Record gate result
        latest.gate_result = decision  # type: ignore
        latest.reject_reason = reason
        latest.finished_at = datetime.now(timezone.utc)
        self.queue.update(task)

        # Update context file
        ctx_file = self._get_context_file(task)

        agent = self.agents[latest.agent_id]
        gate_result = GateResult(decision=decision, reason=reason)

        # Determine handoff
        handoff_targets = self._resolve_handoffs(
            agent,
            gate_result,
            latest.output_text,
            latest.input_text,
        )

        if not handoff_targets:
            task.status = TaskStatus.completed
            self.queue.update(task)
            return task

        next_agent_id, next_payload = handoff_targets[0]
        ctx_file.save_payload(next_payload)

        return self.run_task(
            task,
            next_agent_id,
            input_text=next_payload,
            on_stage_complete=on_stage_complete,
            on_stage_start=on_stage_start,
            on_output=on_output,
            on_thinking=on_thinking,
            on_tool=on_tool,
        )

    # ------------------------------------------------------------------
    # Conversational session execution
    # ------------------------------------------------------------------

    def _run_session(
        self,
        session: AgentDefinition,
        task: Task,
        input_text: str,
        ctx_file: ContextFile,
        on_turn_start=None,
        on_turn_complete=None,
        on_output=None,
        on_thinking=None,
        on_tool=None,
    ) -> str:
        """Run a conversational session among participant agents.

        Agents take turns in rounds.  Each agent sees the growing
        transcript via ``{{ transcript }}`` in its system prompt.
        The loop ends when consensus is detected or ``max_rounds``
        is reached.

        Returns the final session output (summary or last transcript).
        """
        from aqm.core.agent import ConsensusConfig
        from aqm.core.chunks import ChunkManager, parse_chunk_directives

        participants = [self.agents[pid] for pid in session.participants]
        consensus_cfg = session.consensus or ConsensusConfig()
        keyword = consensus_cfg.keyword.upper()
        require = consensus_cfg.require  # "all" | "majority"
        max_rounds = session.max_rounds

        # Track which agents have agreed
        agreements: dict[str, bool] = {a.id: False for a in participants}

        # Initialise transcript
        ctx_file.init_transcript(
            topic=input_text,
            participants=[a.id for a in participants],
        )

        # Initialise chunks (if configured)
        chunk_mgr = ChunkManager(ctx_file.task_dir)
        has_chunks = session.chunks is not None
        if has_chunks and session.chunks.initial:
            chunk_mgr.init_from_config(session.chunks.initial)

        logger.info(
            "[Pipeline] Session '%s' started (%d participants, max %d rounds)",
            session.id,
            len(participants),
            max_rounds,
        )

        final_output = ""

        for round_num in range(1, max_rounds + 1):
            if is_cancelled(task.id):
                break

            # Determine turn order for this round
            if session.turn_order == "moderator" and session.summary_agent:
                # Moderator goes first, then the rest
                order = []
                for a in participants:
                    if a.id == session.summary_agent:
                        order.insert(0, a)
                    else:
                        order.append(a)
            else:
                order = list(participants)

            for turn_idx, agent in enumerate(order):
                if is_cancelled(task.id):
                    break

                transcript = ctx_file.read_transcript()

                prompt = build_prompt(
                    system_prompt_template=agent.system_prompt,
                    input_text=input_text,
                    context=ctx_file.read_for_strategy(
                        agent.id, agent.context_strategy, agent.context_window,
                    ),
                    transcript=transcript,
                    chunks=chunk_mgr.summary() if has_chunks else "",
                )

                stage_num = task.next_stage_number

                if on_turn_start:
                    on_turn_start(task, agent.id, stage_num)

                try:
                    runtime = self._get_runtime(agent)
                    message = runtime.run(prompt, agent, task, on_output=on_output, on_thinking=on_thinking)
                except Exception as turn_err:
                    # Record failed turn but continue session
                    message = f"[ERROR: agent '{agent.id}' failed: {turn_err}]"
                    logger.error(
                        "[Pipeline] Session '%s' agent '%s' failed in round %d: %s",
                        session.id, agent.id, round_num, turn_err,
                    )

                # Record as a stage
                stage = StageRecord(
                    stage_number=stage_num,
                    agent_id=agent.id,
                    task_name=f"session:{session.id}:r{round_num}",
                    input_text=f"[round {round_num}]",
                    output_text=message,
                    finished_at=datetime.now(timezone.utc),
                )
                task.add_stage(stage)
                self.queue.update(task)

                # Write to agent's private context file
                ctx_file.append_agent_context(
                    agent_id=agent.id,
                    stage_number=stage_num,
                    input_text=f"[round {round_num}]",
                    output_text=message,
                )

                # Append to transcript
                ctx_file.append_turn(
                    round_number=round_num,
                    agent_id=agent.id,
                    message=message,
                    is_round_start=(turn_idx == 0),
                )

                # Parse chunk directives from agent output
                if has_chunks:
                    parse_chunk_directives(message, chunk_mgr, agent.id)

                if on_turn_complete:
                    on_turn_complete(task, stage)

                final_output = message

                # Check consensus
                if consensus_cfg.method == "vote":
                    if keyword in message.upper():
                        agreements[agent.id] = True
                elif consensus_cfg.method == "moderator_decides":
                    if (
                        agent.id == session.summary_agent
                        and keyword in message.upper()
                    ):
                        # Moderator calls consensus for everyone
                        for k in agreements:
                            agreements[k] = True

            # End of round — check if consensus reached
            agreed_count = sum(1 for v in agreements.values() if v)
            total = len(agreements)

            consensus_reached = False
            if require == "all":
                consensus_reached = agreed_count == total
            elif require == "majority":
                consensus_reached = agreed_count > total / 2

            logger.info(
                "[Pipeline] Session '%s' round %d: %d/%d agreed",
                session.id,
                round_num,
                agreed_count,
                total,
            )

            # Gate consensus on chunk completion
            if consensus_reached and has_chunks and consensus_cfg.require_chunks_done:
                if not chunk_mgr.all_done():
                    consensus_reached = False
                    logger.info(
                        "[Pipeline] Session '%s' consensus votes met but "
                        "chunks not all done — continuing",
                        session.id,
                    )

            if consensus_reached:
                logger.info(
                    "[Pipeline] Session '%s' consensus reached at round %d",
                    session.id,
                    round_num,
                )

                # Run summary agent if specified
                if session.summary_agent:
                    summary_agent = self.agents[session.summary_agent]
                    transcript = ctx_file.read_transcript()
                    summary_prompt = build_prompt(
                        system_prompt_template=summary_agent.system_prompt,
                        input_text=input_text,
                        context=ctx_file.read_for_strategy(
                            summary_agent.id, summary_agent.context_strategy,
                            summary_agent.context_window,
                        ),
                        transcript=transcript,
                        chunks=chunk_mgr.summary() if has_chunks else "",
                    )
                    runtime = self._get_runtime(summary_agent)
                    final_output = runtime.run(
                        summary_prompt, summary_agent, task,
                        on_output=on_output, on_thinking=on_thinking,
                        on_tool=on_tool,
                    )

                agreed_by = [k for k, v in agreements.items() if v]
                ctx_file.append_consensus(
                    round_number=round_num,
                    agreed_by=agreed_by,
                    summary=final_output,
                )

                task.metadata["session_consensus"] = True
                task.metadata["session_rounds"] = round_num
                if has_chunks:
                    total, done, _ = chunk_mgr.counts()
                    task.metadata["chunks_total"] = total
                    task.metadata["chunks_done"] = done
                return final_output

        # Max rounds exhausted
        logger.warning(
            "[Pipeline] Session '%s' max rounds (%d) reached without consensus",
            session.id,
            max_rounds,
        )
        task.metadata["session_consensus"] = False
        task.metadata["session_rounds"] = max_rounds
        if has_chunks:
            total, done, _ = chunk_mgr.counts()
            task.metadata["chunks_total"] = total
            task.metadata["chunks_done"] = done
        return final_output
