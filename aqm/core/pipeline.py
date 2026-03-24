"""Pipeline — agent orchestration execution loop.

Tasks move between agents through queues,
pass through gates where approve/reject decisions are made,
and are forwarded to the next agent based on handoff conditions.
"""

from __future__ import annotations

import logging
import re
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
from aqm.runtime.text import TextRuntime
from aqm.runtime.base import AbstractRuntime
from aqm.runtime.claude_code import ClaudeCodeRuntime

logger = logging.getLogger(__name__)

MAX_STAGES = 20

# Thread-safe set of task IDs that have been requested to cancel.
# Checked each iteration of the pipeline loop.
_cancelled_tasks: set[str] = set()


def cancel_task(task_id: str) -> None:
    """Request cancellation of a running task."""
    _cancelled_tasks.add(task_id)


def is_cancelled(task_id: str) -> bool:
    """Check if a task has been requested to cancel."""
    return task_id in _cancelled_tasks


class Pipeline:
    """Agent pipeline orchestrator."""

    def __init__(
        self,
        agents: dict[str, AgentDefinition],
        queue: AbstractQueue,
        project_root: Path,
        anthropic_client=None,
    ) -> None:
        self.agents = agents
        self.queue = queue
        self.project_root = project_root
        self._anthropic_client = anthropic_client
        self._runtimes: dict[str, AbstractRuntime] = {}

    @property
    def anthropic_client(self):
        """Lazy-initialize Anthropic client (used only for LLM gates)."""
        if self._anthropic_client is None:
            import anthropic

            self._anthropic_client = anthropic.Anthropic()
        return self._anthropic_client

    def _get_runtime(self, agent: AgentDefinition) -> AbstractRuntime:
        """Return a runtime instance matching the agent's runtime type."""
        if agent.runtime not in self._runtimes:
            if agent.runtime == "text":
                self._runtimes["text"] = TextRuntime(self.project_root)
            elif agent.runtime == "claude_code":
                self._runtimes["claude_code"] = ClaudeCodeRuntime(
                    self.project_root
                )
            else:
                raise ValueError(f"Unknown runtime: {agent.runtime}")
        return self._runtimes[agent.runtime]

    def _get_gate(self, agent: AgentDefinition) -> Optional[AbstractGate]:
        """Return a gate instance matching the agent's gate configuration."""
        if not agent.gate:
            return None
        if agent.gate.type == "llm":
            return LLMGate(agent.gate, self.anthropic_client)
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
                return value.lower() in agent_output.lower()

            in_match = re.match(
                r"(\w+)\s+in\s+\[([^\]]+)\]", condition
            )
            if in_match:
                key, values_str = in_match.groups()
                values = [
                    v.strip().strip("\"'").lower()
                    for v in values_str.split(",")
                ]
                output_lower = agent_output.lower()
                return any(v in output_lower for v in values)
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
    ) -> Task:
        """Run a task through the pipeline.

        Args:
            task: The task to run
            start_agent_id: Starting agent ID
            input_text: Input to pass to the first agent (uses task.description if None)
            on_stage_complete: Stage completion callback (task, stage_record)

        Returns:
            The Task in completed (or gate-awaiting) state
        """
        current_input = input_text or task.description
        current_agent_id = start_agent_id

        ctx_file = self._get_context_file(task)

        while task.next_stage_number <= MAX_STAGES:
            # Check for cancellation
            if is_cancelled(task.id):
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
            task.current_agent_id = current_agent_id
            task.status = TaskStatus.in_progress
            self.queue.update(task)

            logger.info(
                f"[Pipeline] {task.id} -> agent '{agent.id}' "
                f"(stage {task.next_stage_number})"
            )

            # Build prompt
            context_text = ctx_file.read()
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

            try:
                runtime = self._get_runtime(agent)
                output = runtime.run(prompt, agent, task)
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
                    )

            next_agent_id, next_payload = handoff_targets[0]

            ctx_file.save_payload(next_payload)

            current_input = next_payload
            current_agent_id = next_agent_id

        # Exceeded MAX_STAGES
        task.status = TaskStatus.failed
        task.metadata["error"] = (
            f"Exceeded maximum number of stages ({MAX_STAGES})."
        )
        self.queue.update(task)
        logger.error(f"[Pipeline] {task.id} exceeded maximum stages")
        return task

    def resume_task(
        self,
        task_id: str,
        decision: str,
        reason: str = "",
        on_stage_complete=None,
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
        if task.status != TaskStatus.awaiting_gate:
            raise ValueError(
                f"Task '{task_id}' is not in gate-awaiting state. "
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
        )
