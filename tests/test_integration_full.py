"""Full integration tests — validates ALL documented features end-to-end.

Each test creates a temporary project with agents.yaml, runs the pipeline
with mock runtimes, and verifies that data flows correctly between agents.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from aqm.core.agent import AgentDefinition, ConsensusConfig, load_agents
from aqm.core.context import build_prompt
from aqm.core.context_file import ContextFile
from aqm.core.gate import GateResult
from aqm.core.pipeline import Pipeline
from aqm.core.project import get_tasks_dir, init_project
from aqm.core.task import Task, TaskStatus
from aqm.queue.file import FileQueue


# ── Helpers ───────────────────────────────────────────────────────────


def _make_pipeline(agents, tmp_project, config=None):
    """Create a Pipeline with mock runtime."""
    queue = FileQueue(tmp_project / ".aqm" / "queue")
    pipeline = Pipeline(agents, queue, tmp_project, config=config)
    return pipeline, queue


def _mock_runtime(responses):
    """Create a mock runtime that returns responses in order."""
    mock_rt = MagicMock()
    mock_rt.name = "mock"
    if callable(responses):
        mock_rt.run.side_effect = responses
    else:
        mock_rt.run.side_effect = list(responses)
    return mock_rt


# ═══════════════════════════════════════════════════════════════════════
# 1. BASIC PIPELINE — data passes between agents
# ═══════════════════════════════════════════════════════════════════════


class TestBasicPipeline:
    """Agent A → Agent B → Agent C, verifying data flows correctly."""

    def test_linear_pipeline_data_flow(self, tmp_project):
        agents = {
            "planner": AgentDefinition(
                id="planner", runtime="claude",
                system_prompt="Plan: {{ input }}",
                handoffs=[{"to": "developer"}],
            ),
            "developer": AgentDefinition(
                id="developer", runtime="claude",
                system_prompt="Build: {{ input }}",
                handoffs=[{"to": "qa"}],
            ),
            "qa": AgentDefinition(
                id="qa", runtime="claude",
                system_prompt="Test: {{ input }}",
            ),
        }
        pipeline, queue = _make_pipeline(agents, tmp_project)

        prompts = []

        def capture(prompt, agent, task, **kw):
            prompts.append((agent.id, prompt))
            return f"Output from {agent.id}"

        pipeline._runtimes["claude"] = _mock_runtime(capture)

        task = Task(description="Add login feature")
        queue.push(task, "planner")
        result = pipeline.run_task(task, "planner")

        assert result.status == TaskStatus.completed
        assert len(result.stages) == 3

        # Verify each agent was called
        agent_ids = [p[0] for p in prompts]
        assert agent_ids == ["planner", "developer", "qa"]

        # Verify handoff payload: developer gets planner's output
        assert "Output from planner" in prompts[1][1] or "Add login feature" in prompts[1][1]

        # Verify context.md was written
        ctx_file = ContextFile(get_tasks_dir(tmp_project) / task.id)
        content = ctx_file.read()
        assert "Output from planner" in content
        assert "Output from developer" in content
        assert "Output from qa" in content

    def test_pipeline_records_stages(self, tmp_project):
        agents = {
            "a": AgentDefinition(
                id="a", runtime="claude",
                system_prompt="{{ input }}",
                handoffs=[{"to": "b"}],
            ),
            "b": AgentDefinition(
                id="b", runtime="claude",
                system_prompt="{{ input }}",
            ),
        }
        pipeline, queue = _make_pipeline(agents, tmp_project)
        pipeline._runtimes["claude"] = _mock_runtime(["out-a", "out-b"])

        task = Task(description="test")
        queue.push(task, "a")
        result = pipeline.run_task(task, "a")

        assert result.stages[0].agent_id == "a"
        assert result.stages[0].output_text == "out-a"
        assert result.stages[1].agent_id == "b"
        assert result.stages[1].output_text == "out-b"


# ═══════════════════════════════════════════════════════════════════════
# 2. ALL CONTEXT STRATEGIES
# ═══════════════════════════════════════════════════════════════════════


class TestContextStrategies:
    """Verify all 5 context strategies inject the correct data."""

    def _run_two_agent_pipeline(self, tmp_project, strategy):
        agents = {
            "first": AgentDefinition(
                id="first", runtime="claude",
                system_prompt="{{ input }}",
                handoffs=[{"to": "second"}],
            ),
            "second": AgentDefinition(
                id="second", runtime="claude",
                context_strategy=strategy,
                context_window=1,
                system_prompt="Context: {{ context }}\nInput: {{ input }}",
            ),
        }
        pipeline, queue = _make_pipeline(agents, tmp_project)

        prompts = []

        def capture(prompt, agent, task, **kw):
            prompts.append((agent.id, prompt))
            return f"Output from {agent.id}: detailed analysis here"

        pipeline._runtimes["claude"] = _mock_runtime(capture)
        task = Task(description="test context")
        queue.push(task, "first")
        pipeline.run_task(task, "first")
        return prompts

    def test_strategy_both(self, tmp_project):
        prompts = self._run_two_agent_pipeline(tmp_project, "both")
        second_prompt = prompts[1][1]
        assert "Output from first" in second_prompt

    def test_strategy_shared(self, tmp_project):
        prompts = self._run_two_agent_pipeline(tmp_project, "shared")
        second_prompt = prompts[1][1]
        assert "Output from first" in second_prompt

    def test_strategy_own(self, tmp_project):
        prompts = self._run_two_agent_pipeline(tmp_project, "own")
        second_prompt = prompts[1][1]
        # 'own' strategy: context is empty (no private notes), but input still has handoff payload
        assert "Context: \n" in second_prompt or "Context: \nInput:" in second_prompt

    def test_strategy_last_only(self, tmp_project):
        prompts = self._run_two_agent_pipeline(tmp_project, "last_only")
        second_prompt = prompts[1][1]
        assert "Output from first" in second_prompt

    def test_strategy_none(self, tmp_project):
        prompts = self._run_two_agent_pipeline(tmp_project, "none")
        second_prompt = prompts[1][1]
        # 'none' strategy: context is empty, but input still has handoff payload
        assert "Context: \n" in second_prompt or "Context: \nInput:" in second_prompt


# ═══════════════════════════════════════════════════════════════════════
# 3. CONVERSATIONAL SESSION + CONSENSUS VOTING
# ═══════════════════════════════════════════════════════════════════════


class TestSessionConsensus:
    """Session with round-robin discussion and vote-based consensus."""

    def test_vote_all_consensus(self, tmp_project):
        agents = {
            "arch": AgentDefinition(
                id="arch", runtime="claude",
                system_prompt="Architect: {{ input }} {{ transcript }}",
            ),
            "sec": AgentDefinition(
                id="sec", runtime="claude",
                system_prompt="Security: {{ input }} {{ transcript }}",
            ),
            "session": AgentDefinition(
                id="session", type="session",
                participants=["arch", "sec"],
                max_rounds=3,
                consensus=ConsensusConfig(method="vote", require="all"),
            ),
        }
        pipeline, queue = _make_pipeline(agents, tmp_project)

        call_count = [0]

        def respond(prompt, agent, task, **kw):
            call_count[0] += 1
            if call_count[0] <= 2:
                # Round 1: both agree
                return f"[{agent.id}] Analysis done. VOTE: AGREE"
            return f"[{agent.id}] extra round"

        pipeline._runtimes["claude"] = _mock_runtime(respond)

        task = Task(description="Choose auth method")
        queue.push(task, "session")
        result = pipeline.run_task(task, "session")

        assert result.status == TaskStatus.completed
        assert result.metadata.get("session_consensus") is True
        assert result.metadata.get("session_rounds") == 1

        # Verify transcript was created
        ctx_file = ContextFile(get_tasks_dir(tmp_project) / task.id)
        transcript = ctx_file.read_transcript()
        assert "arch" in transcript
        assert "sec" in transcript
        assert "VOTE: AGREE" in transcript

    def test_vote_majority_consensus(self, tmp_project):
        agents = {
            "a": AgentDefinition(id="a", runtime="claude", system_prompt="{{ input }} {{ transcript }}"),
            "b": AgentDefinition(id="b", runtime="claude", system_prompt="{{ input }} {{ transcript }}"),
            "c": AgentDefinition(id="c", runtime="claude", system_prompt="{{ input }} {{ transcript }}"),
            "session": AgentDefinition(
                id="session", type="session",
                participants=["a", "b", "c"],
                max_rounds=2,
                consensus=ConsensusConfig(method="vote", require="majority"),
            ),
        }
        pipeline, queue = _make_pipeline(agents, tmp_project)

        def respond(prompt, agent, task, **kw):
            if agent.id in ("a", "b"):
                return f"[{agent.id}] I agree. VOTE: AGREE"
            return f"[{agent.id}] I disagree."  # c never agrees

        pipeline._runtimes["claude"] = _mock_runtime(respond)

        task = Task(description="Vote test")
        queue.push(task, "session")
        result = pipeline.run_task(task, "session")

        # 2 out of 3 = majority
        assert result.metadata.get("session_consensus") is True

    def test_no_consensus_max_rounds(self, tmp_project):
        agents = {
            "a": AgentDefinition(id="a", runtime="claude", system_prompt="{{ input }}"),
            "b": AgentDefinition(id="b", runtime="claude", system_prompt="{{ input }}"),
            "session": AgentDefinition(
                id="session", type="session",
                participants=["a", "b"],
                max_rounds=2,
                consensus=ConsensusConfig(method="vote", require="all"),
            ),
        }
        pipeline, queue = _make_pipeline(agents, tmp_project)

        def respond(prompt, agent, task, **kw):
            return f"[{agent.id}] No agreement"  # Never votes

        pipeline._runtimes["claude"] = _mock_runtime(respond)

        task = Task(description="Deadlock test")
        queue.push(task, "session")
        result = pipeline.run_task(task, "session")

        assert result.metadata.get("session_consensus") is False
        assert result.metadata.get("session_rounds") == 2

    def test_session_then_handoff(self, tmp_project):
        """Session completes, then hands off to next agent."""
        agents = {
            "a": AgentDefinition(id="a", runtime="claude", system_prompt="{{ input }} {{ transcript }}"),
            "b": AgentDefinition(id="b", runtime="claude", system_prompt="{{ input }} {{ transcript }}"),
            "session": AgentDefinition(
                id="session", type="session",
                participants=["a", "b"],
                max_rounds=2,
                consensus=ConsensusConfig(method="vote", require="all"),
                handoffs=[{"to": "implementer"}],
            ),
            "implementer": AgentDefinition(
                id="implementer", runtime="claude",
                system_prompt="Implement: {{ input }}",
            ),
        }
        pipeline, queue = _make_pipeline(agents, tmp_project)

        call_order = []

        def respond(prompt, agent, task, **kw):
            call_order.append(agent.id)
            if agent.id in ("a", "b"):
                return f"[{agent.id}] VOTE: AGREE"
            return f"Implemented based on session"

        pipeline._runtimes["claude"] = _mock_runtime(respond)

        task = Task(description="Session then implement")
        queue.push(task, "session")
        result = pipeline.run_task(task, "session")

        assert result.status == TaskStatus.completed
        assert "implementer" in call_order


# ═══════════════════════════════════════════════════════════════════════
# 4. QUALITY GATES — LLM approve/reject + retry
# ═══════════════════════════════════════════════════════════════════════


class TestQualityGates:
    """LLM gate auto-evaluation with reject→retry flow."""

    def test_gate_approve_flows_forward(self, tmp_project):
        agents = {
            "dev": AgentDefinition(
                id="dev", runtime="claude",
                system_prompt="{{ input }}",
                gate={"type": "llm", "prompt": "Is it good?"},
                handoffs=[{"to": "deploy", "condition": "on_approve"}],
            ),
            "deploy": AgentDefinition(
                id="deploy", runtime="claude",
                system_prompt="{{ input }}",
            ),
        }
        pipeline, queue = _make_pipeline(agents, tmp_project)
        pipeline._runtimes["claude"] = _mock_runtime(["code output", "deployed"])

        mock_gate = MagicMock()
        mock_gate.evaluate.return_value = GateResult(decision="approved", reason="looks good")
        pipeline._get_gate = lambda agent: mock_gate if agent.gate else None

        task = Task(description="Gate approve test")
        queue.push(task, "dev")
        result = pipeline.run_task(task, "dev")

        assert result.status == TaskStatus.completed
        assert len(result.stages) == 2
        assert result.stages[0].gate_result == "approved"

    def test_gate_reject_retries(self, tmp_project):
        agents = {
            "dev": AgentDefinition(
                id="dev", runtime="claude",
                system_prompt="{{ input }}",
                gate={"type": "llm", "prompt": "Good?", "max_retries": 2},
                handoffs=[
                    {"to": "deploy", "condition": "on_approve"},
                    {"to": "dev", "condition": "on_reject", "payload": "Fix: {{ reject_reason }}\n{{ output }}"},
                ],
            ),
            "deploy": AgentDefinition(
                id="deploy", runtime="claude",
                system_prompt="{{ input }}",
            ),
        }
        pipeline, queue = _make_pipeline(agents, tmp_project)

        call_count = [0]

        def respond(prompt, agent, task, **kw):
            call_count[0] += 1
            if agent.id == "deploy":
                return "deployed"
            return f"code v{call_count[0]}"

        pipeline._runtimes["claude"] = _mock_runtime(respond)

        gate_call = [0]

        def gate_eval(task, output):
            gate_call[0] += 1
            if gate_call[0] < 3:
                return GateResult(decision="rejected", reason="needs improvement")
            return GateResult(decision="approved", reason="good now")

        mock_gate = MagicMock()
        mock_gate.evaluate.side_effect = gate_eval
        pipeline._get_gate = lambda agent: mock_gate if agent.gate else None

        task = Task(description="Retry test")
        queue.push(task, "dev")
        result = pipeline.run_task(task, "dev")

        assert result.status == TaskStatus.completed
        # dev called 3 times (2 rejects + 1 approve), then deploy
        assert call_count[0] == 4

    def test_gate_exceed_max_retries_fails(self, tmp_project):
        agents = {
            "dev": AgentDefinition(
                id="dev", runtime="claude",
                system_prompt="{{ input }}",
                gate={"type": "llm", "prompt": "Good?", "max_retries": 1},
                handoffs=[{"to": "dev", "condition": "on_reject"}],
            ),
        }
        pipeline, queue = _make_pipeline(agents, tmp_project)
        pipeline._runtimes["claude"] = _mock_runtime(lambda p, a, t, **kw: "bad code")

        mock_gate = MagicMock()
        mock_gate.evaluate.return_value = GateResult(decision="rejected", reason="always bad")
        pipeline._get_gate = lambda agent: mock_gate if agent.gate else None

        task = Task(description="Max retry test")
        queue.push(task, "dev")
        result = pipeline.run_task(task, "dev")

        assert result.status == TaskStatus.failed
        assert "exceeded max gate retries" in result.metadata.get("error", "")

    def test_human_gate_pauses_pipeline(self, tmp_project):
        agents = {
            "dev": AgentDefinition(
                id="dev", runtime="claude",
                system_prompt="{{ input }}",
                gate={"type": "human"},
            ),
        }
        pipeline, queue = _make_pipeline(agents, tmp_project)
        pipeline._runtimes["claude"] = _mock_runtime(["code output"])

        task = Task(description="Human gate test")
        queue.push(task, "dev")
        result = pipeline.run_task(task, "dev")

        assert result.status == TaskStatus.awaiting_gate


# ═══════════════════════════════════════════════════════════════════════
# 5. HANDOFF STRATEGIES — static, fan-out, auto, conditional
# ═══════════════════════════════════════════════════════════════════════


class TestHandoffStrategies:

    def test_conditional_on_approve_on_reject(self, tmp_project):
        agents = {
            "worker": AgentDefinition(
                id="worker", runtime="claude",
                system_prompt="{{ input }}",
                gate={"type": "llm", "prompt": "ok?"},
                handoffs=[
                    {"to": "success", "condition": "on_approve"},
                    {"to": "failure", "condition": "on_reject"},
                ],
            ),
            "success": AgentDefinition(id="success", runtime="claude", system_prompt="{{ input }}"),
            "failure": AgentDefinition(id="failure", runtime="claude", system_prompt="{{ input }}"),
        }
        pipeline, queue = _make_pipeline(agents, tmp_project)
        pipeline._runtimes["claude"] = _mock_runtime(["work done", "success path"])

        mock_gate = MagicMock()
        mock_gate.evaluate.return_value = GateResult(decision="approved", reason="ok")
        pipeline._get_gate = lambda a: mock_gate if a.gate else None

        task = Task(description="Conditional test")
        queue.push(task, "worker")
        result = pipeline.run_task(task, "worker")

        assert result.status == TaskStatus.completed
        assert result.stages[-1].agent_id == "success"

    def test_auto_handoff_from_agent_output(self, tmp_project):
        agents = {
            "router": AgentDefinition(
                id="router", runtime="claude",
                system_prompt="{{ input }}",
                handoffs=[{"to": "*", "condition": "auto"}],
            ),
            "backend": AgentDefinition(id="backend", runtime="claude", system_prompt="{{ input }}"),
            "frontend": AgentDefinition(id="frontend", runtime="claude", system_prompt="{{ input }}"),
        }
        pipeline, queue = _make_pipeline(agents, tmp_project)

        def respond(prompt, agent, task, **kw):
            if agent.id == "router":
                return "This is a backend task.\nHANDOFF: backend"
            return f"Done by {agent.id}"

        pipeline._runtimes["claude"] = _mock_runtime(respond)

        task = Task(description="Auto handoff")
        queue.push(task, "router")
        result = pipeline.run_task(task, "router")

        assert result.status == TaskStatus.completed
        assert result.stages[-1].agent_id == "backend"

    def test_fan_out_parallel(self, tmp_project):
        agents = {
            "router": AgentDefinition(
                id="router", runtime="claude",
                system_prompt="{{ input }}",
                handoffs=[{"to": "qa, docs", "condition": "always"}],
            ),
            "qa": AgentDefinition(id="qa", runtime="claude", system_prompt="{{ input }}"),
            "docs": AgentDefinition(id="docs", runtime="claude", system_prompt="{{ input }}"),
        }
        pipeline, queue = _make_pipeline(agents, tmp_project)

        called_agents = []

        def respond(prompt, agent, task, **kw):
            called_agents.append(agent.id)
            return f"Done by {agent.id}"

        pipeline._runtimes["claude"] = _mock_runtime(respond)

        task = Task(description="Fan-out test")
        queue.push(task, "router")
        result = pipeline.run_task(task, "router")

        # router + qa (primary) + docs (child task)
        assert "router" in called_agents
        assert "qa" in called_agents
        assert "docs" in called_agents

    def test_expression_condition(self, tmp_project):
        agents = {
            "analyzer": AgentDefinition(
                id="analyzer", runtime="claude",
                system_prompt="{{ input }}",
                handoffs=[
                    {"to": "critical_handler", "condition": "severity == critical"},
                    {"to": "normal_handler", "condition": "severity == low"},
                ],
            ),
            "critical_handler": AgentDefinition(id="critical_handler", runtime="claude", system_prompt="{{ input }}"),
            "normal_handler": AgentDefinition(id="normal_handler", runtime="claude", system_prompt="{{ input }}"),
        }
        pipeline, queue = _make_pipeline(agents, tmp_project)
        pipeline._runtimes["claude"] = _mock_runtime([
            "Assessment: severity is critical, needs immediate fix",
            "Handled critical issue",
        ])

        task = Task(description="Expression condition")
        queue.push(task, "analyzer")
        result = pipeline.run_task(task, "analyzer")

        assert result.status == TaskStatus.completed
        assert result.stages[-1].agent_id == "critical_handler"


# ═══════════════════════════════════════════════════════════════════════
# 6. CHUNK DECOMPOSITION
# ═══════════════════════════════════════════════════════════════════════


class TestChunkDecomposition:

    def test_chunks_initial_and_directives(self, tmp_project):
        """Session with initial chunks, agents complete them via directives."""
        agents = {
            "pm": AgentDefinition(id="pm", runtime="claude", system_prompt="{{ input }} {{ chunks }}"),
            "dev": AgentDefinition(id="dev", runtime="claude", system_prompt="{{ input }} {{ chunks }}"),
            "session": AgentDefinition(
                id="session", type="session",
                participants=["pm", "dev"],
                max_rounds=2,
                consensus=ConsensusConfig(method="vote", require="all", require_chunks_done=True),
                chunks={"enabled": True, "initial": ["Setup DB", "Build API", "Write tests"]},
            ),
        }
        pipeline, queue = _make_pipeline(agents, tmp_project)

        round_num = [0]

        def respond(prompt, agent, task, **kw):
            round_num[0] += 1
            if round_num[0] <= 2:
                # Round 1: complete chunks
                if agent.id == "pm":
                    return "CHUNK_DONE: C-001\nCHUNK_DONE: C-002\nLet's proceed. VOTE: AGREE"
                return "CHUNK_DONE: C-003\nAll done. VOTE: AGREE"
            return f"[{agent.id}] extra"

        pipeline._runtimes["claude"] = _mock_runtime(respond)

        task = Task(description="Chunk test")
        queue.push(task, "session")
        result = pipeline.run_task(task, "session")

        assert result.metadata.get("session_consensus") is True
        assert result.metadata.get("chunks_total") == 3
        assert result.metadata.get("chunks_done") == 3

    def test_chunks_block_consensus(self, tmp_project):
        """require_chunks_done=True blocks consensus when chunks are incomplete."""
        agents = {
            "a": AgentDefinition(id="a", runtime="claude", system_prompt="{{ input }}"),
            "b": AgentDefinition(id="b", runtime="claude", system_prompt="{{ input }}"),
            "session": AgentDefinition(
                id="session", type="session",
                participants=["a", "b"],
                max_rounds=2,
                consensus=ConsensusConfig(method="vote", require="all", require_chunks_done=True),
                chunks={"enabled": True, "initial": ["Task 1", "Task 2"]},
            ),
        }
        pipeline, queue = _make_pipeline(agents, tmp_project)

        def respond(prompt, agent, task, **kw):
            # Everyone votes agree but chunks are NOT completed
            return f"[{agent.id}] VOTE: AGREE"

        pipeline._runtimes["claude"] = _mock_runtime(respond)

        task = Task(description="Blocked chunks")
        queue.push(task, "session")
        result = pipeline.run_task(task, "session")

        # Consensus should NOT be reached because chunks not done
        assert result.metadata.get("session_consensus") is False

    def test_chunk_add_directive(self, tmp_project):
        """Agents can add new chunks via CHUNK_ADD directive."""
        agents = {
            "a": AgentDefinition(id="a", runtime="claude", system_prompt="{{ input }}"),
            "b": AgentDefinition(id="b", runtime="claude", system_prompt="{{ input }}"),
            "session": AgentDefinition(
                id="session", type="session",
                participants=["a", "b"],
                max_rounds=1,
                consensus=ConsensusConfig(method="vote", require="all"),
                chunks={"enabled": True, "initial": []},
            ),
        }
        pipeline, queue = _make_pipeline(agents, tmp_project)

        def respond(prompt, agent, task, **kw):
            if agent.id == "a":
                return "CHUNK_ADD: New work item\nVOTE: AGREE"
            return "VOTE: AGREE"

        pipeline._runtimes["claude"] = _mock_runtime(respond)

        task = Task(description="Chunk add")
        queue.push(task, "session")
        result = pipeline.run_task(task, "session")

        assert result.metadata.get("chunks_total") == 1


# ═══════════════════════════════════════════════════════════════════════
# 7. HUMAN INPUT — before and on_demand modes
# ═══════════════════════════════════════════════════════════════════════


class TestHumanInput:

    def test_before_mode_pauses(self, tmp_project):
        agents = {
            "planner": AgentDefinition(
                id="planner", runtime="claude",
                system_prompt="{{ input }}",
                human_input={"enabled": True, "mode": "before", "prompt": "What features?"},
            ),
        }
        pipeline, queue = _make_pipeline(agents, tmp_project)
        pipeline._runtimes["claude"] = _mock_runtime(["planned output"])

        task = Task(description="Human before test")
        queue.push(task, "planner")
        result = pipeline.run_task(task, "planner")

        assert result.status == TaskStatus.awaiting_human_input
        pending = result.metadata.get("_human_input_pending", {})
        assert pending.get("mode") == "before"
        assert pending.get("agent_id") == "planner"

    def test_before_mode_resume(self, tmp_project):
        agents = {
            "planner": AgentDefinition(
                id="planner", runtime="claude",
                system_prompt="{{ input }}",
                human_input={"enabled": True, "mode": "before", "prompt": "What features?"},
            ),
        }
        pipeline, queue = _make_pipeline(agents, tmp_project)

        prompts = []

        def capture(prompt, agent, task, **kw):
            prompts.append(prompt)
            return "planned with user input"

        pipeline._runtimes["claude"] = _mock_runtime(capture)

        task = Task(description="Resume test")
        queue.push(task, "planner")

        # First run pauses
        result = pipeline.run_task(task, "planner")
        assert result.status == TaskStatus.awaiting_human_input

        # Resume with human response
        result = pipeline.resume_human_input(
            task.id, "I want dark mode and JWT auth"
        )

        assert result.status == TaskStatus.completed
        assert "dark mode" in prompts[0] or "JWT auth" in prompts[0]

    def test_on_demand_mode(self, tmp_project):
        agents = {
            "dev": AgentDefinition(
                id="dev", runtime="claude",
                system_prompt="{{ input }}",
                human_input={"enabled": True, "mode": "on_demand"},
            ),
        }
        pipeline, queue = _make_pipeline(agents, tmp_project)
        pipeline._runtimes["claude"] = _mock_runtime([
            "Working on it. HUMAN_INPUT: Which database to use?",
        ])

        task = Task(description="On demand test")
        queue.push(task, "dev")
        result = pipeline.run_task(task, "dev")

        assert result.status == TaskStatus.awaiting_human_input
        pending = result.metadata.get("_human_input_pending", {})
        assert pending.get("mode") == "on_demand"
        assert "Which database" in pending.get("questions", [""])[0]


# ═══════════════════════════════════════════════════════════════════════
# 8. PAYLOAD TEMPLATES — data transformation between agents
# ═══════════════════════════════════════════════════════════════════════


class TestPayloadTemplates:

    def test_custom_payload_passes_data(self, tmp_project):
        agents = {
            "analyzer": AgentDefinition(
                id="analyzer", runtime="claude",
                system_prompt="{{ input }}",
                handoffs=[{
                    "to": "handler",
                    "payload": "ANALYSIS: {{ output }}\nORIGINAL: {{ input }}",
                }],
            ),
            "handler": AgentDefinition(
                id="handler", runtime="claude",
                system_prompt="{{ input }}",
            ),
        }
        pipeline, queue = _make_pipeline(agents, tmp_project)

        prompts = []

        def capture(prompt, agent, task, **kw):
            prompts.append((agent.id, prompt))
            return "analysis result"

        pipeline._runtimes["claude"] = _mock_runtime(capture)

        task = Task(description="Custom payload test")
        queue.push(task, "analyzer")
        pipeline.run_task(task, "analyzer")

        handler_input = prompts[1][1]
        assert "ANALYSIS: analysis result" in handler_input
        assert "ORIGINAL: Custom payload test" in handler_input

    def test_reject_reason_in_payload(self, tmp_project):
        agents = {
            "dev": AgentDefinition(
                id="dev", runtime="claude",
                system_prompt="{{ input }}",
                gate={"type": "llm", "prompt": "ok?"},
                handoffs=[{
                    "to": "dev",
                    "condition": "on_reject",
                    "payload": "FIX: {{ reject_reason }}\nCODE: {{ output }}",
                }],
            ),
        }
        pipeline, queue = _make_pipeline(agents, tmp_project)

        prompts = []
        call = [0]

        def capture(prompt, agent, task, **kw):
            call[0] += 1
            prompts.append(prompt)
            return f"code v{call[0]}"

        pipeline._runtimes["claude"] = _mock_runtime(capture)

        gate_call = [0]
        mock_gate = MagicMock()

        def gate_eval(task, output):
            gate_call[0] += 1
            if gate_call[0] == 1:
                return GateResult(decision="rejected", reason="missing error handling")
            return GateResult(decision="approved", reason="ok")

        mock_gate.evaluate.side_effect = gate_eval
        pipeline._get_gate = lambda a: mock_gate if a.gate else None

        task = Task(description="Reject payload test")
        queue.push(task, "dev")
        result = pipeline.run_task(task, "dev")

        # Second call should receive reject reason
        assert "FIX: missing error handling" in prompts[1]
        assert "CODE: code v1" in prompts[1]


# ═══════════════════════════════════════════════════════════════════════
# 9. YAML LOADING — full agents.yaml with all features
# ═══════════════════════════════════════════════════════════════════════


class TestYAMLLoading:
    """Load a complete agents.yaml and verify all features parse correctly."""

    def test_full_yaml_pipeline(self, tmp_project):
        yaml_content = {
            "entry_point": "first",
            "agents": [
                {
                    "id": "planner",
                    "runtime": "claude",
                    "context_strategy": "none",
                    "system_prompt": "Plan: {{ input }}",
                    "human_input": {"enabled": True, "mode": "before", "prompt": "Requirements?"},
                    "handoffs": [{"to": "developer"}],
                },
                {
                    "id": "developer",
                    "runtime": "gemini",
                    "context_strategy": "last_only",
                    "context_window": 1,
                    "system_prompt": "Build: {{ input }}",
                    "mcp": [{"server": "github"}],
                    "handoffs": [{"to": "reviewer"}],
                },
                {
                    "id": "reviewer",
                    "runtime": "claude",
                    "context_strategy": "shared",
                    "context_window": 2,
                    "system_prompt": "Review: {{ input }}",
                    "gate": {"type": "llm", "prompt": "Production ready?", "max_retries": 3},
                    "handoffs": [
                        {"to": "deployer", "condition": "on_approve"},
                        {"to": "developer", "condition": "on_reject", "payload": "Fix: {{ reject_reason }}"},
                    ],
                },
                {
                    "id": "deployer",
                    "runtime": "codex",
                    "context_strategy": "none",
                    "system_prompt": "Deploy: {{ input }}",
                },
            ],
        }
        yaml_path = tmp_project / ".aqm" / "agents.yaml"
        yaml_path.write_text(yaml.dump(yaml_content), encoding="utf-8")

        agents = load_agents(yaml_path)

        assert len(agents) == 4
        assert agents["planner"].context_strategy == "none"
        assert agents["planner"].human_input.mode == "before"
        assert agents["developer"].runtime == "gemini"
        assert agents["developer"].context_strategy == "last_only"
        assert agents["developer"].mcp[0].server == "github"
        assert agents["reviewer"].gate.max_retries == 3
        assert agents["reviewer"].context_strategy == "shared"
        assert agents["deployer"].context_strategy == "none"

    def test_session_yaml_loading(self, tmp_project):
        yaml_content = {
            "agents": [
                {"id": "arch", "runtime": "claude", "system_prompt": "{{ input }} {{ transcript }}"},
                {"id": "sec", "runtime": "gemini", "system_prompt": "{{ input }} {{ transcript }}"},
                {
                    "id": "review_session",
                    "type": "session",
                    "participants": ["arch", "sec"],
                    "turn_order": "round_robin",
                    "max_rounds": 5,
                    "consensus": {
                        "method": "vote",
                        "keyword": "VOTE: AGREE",
                        "require": "majority",
                        "require_chunks_done": True,
                    },
                    "summary_agent": "arch",
                    "chunks": {
                        "enabled": True,
                        "initial": ["Review architecture", "Check security"],
                    },
                    "handoffs": [{"to": "arch"}],
                },
            ],
        }
        yaml_path = tmp_project / ".aqm" / "agents.yaml"
        yaml_path.write_text(yaml.dump(yaml_content), encoding="utf-8")

        agents = load_agents(yaml_path)
        session = agents["review_session"]

        assert session.type == "session"
        assert session.participants == ["arch", "sec"]
        assert session.consensus.method == "vote"
        assert session.consensus.require == "majority"
        assert session.consensus.require_chunks_done is True
        assert session.summary_agent == "arch"
        assert session.chunks.initial == ["Review architecture", "Check security"]


# ═══════════════════════════════════════════════════════════════════════
# 10. CONTEXT FILE — per-agent files + smart windowing
# ═══════════════════════════════════════════════════════════════════════


class TestContextFileIntegration:

    def test_per_agent_files_created(self, tmp_project):
        """Pipeline creates per-agent context files."""
        agents = {
            "a": AgentDefinition(
                id="a", runtime="claude",
                system_prompt="{{ input }}",
                handoffs=[{"to": "b"}],
            ),
            "b": AgentDefinition(
                id="b", runtime="claude",
                system_prompt="{{ input }}",
            ),
        }
        pipeline, queue = _make_pipeline(agents, tmp_project)
        pipeline._runtimes["claude"] = _mock_runtime(["output-a", "output-b"])

        task = Task(description="File test")
        queue.push(task, "a")
        pipeline.run_task(task, "a")

        ctx_file = ContextFile(get_tasks_dir(tmp_project) / task.id)

        # Per-agent files exist
        assert ctx_file.read_agent_context("a") != ""
        assert ctx_file.read_agent_context("b") != ""
        assert "output-a" in ctx_file.read_agent_context("a")
        assert "output-b" in ctx_file.read_agent_context("b")

        # Shared context has both
        shared = ctx_file.read()
        assert "output-a" in shared
        assert "output-b" in shared

    def test_smart_windowing_compresses_old_stages(self, tmp_project):
        """Long pipeline compresses old context."""
        agents = {}
        for i in range(8):
            agent_id = f"agent_{i}"
            next_id = f"agent_{i+1}" if i < 7 else None
            handoffs = [{"to": next_id}] if next_id else []
            agents[agent_id] = AgentDefinition(
                id=agent_id, runtime="claude",
                context_strategy="shared",
                context_window=2,
                system_prompt="{{ context }}",
                handoffs=handoffs,
            )
        pipeline, queue = _make_pipeline(agents, tmp_project)

        prompts = []

        def capture(prompt, agent, task, **kw):
            prompts.append((agent.id, len(prompt)))
            return f"Output from {agent.id} with detailed analysis " * 10

        pipeline._runtimes["claude"] = _mock_runtime(capture)

        task = Task(description="Windowing test")
        queue.push(task, "agent_0")
        result = pipeline.run_task(task, "agent_0")

        assert result.status == TaskStatus.completed

        # Last agent's prompt should be much smaller than if full context were used
        ctx_file = ContextFile(get_tasks_dir(tmp_project) / task.id)
        full = ctx_file.read()
        smart = ctx_file.read_smart(context_window=2)
        assert len(smart) < len(full) * 0.6
