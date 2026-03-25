"""Tests for conversational session nodes (type: session)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from aqm.core.agent import (
    AgentDefinition,
    ConsensusConfig,
    load_agents,
)
from aqm.core.context_file import ContextFile
from aqm.core.pipeline import Pipeline
from aqm.core.task import Task, TaskStatus
from aqm.queue.file import FileQueue


# ── ConsensusConfig model ─────────────────────────────────────────────


class TestConsensusConfig:
    def test_defaults(self):
        cfg = ConsensusConfig()
        assert cfg.method == "vote"
        assert cfg.keyword == "VOTE: AGREE"
        assert cfg.require == "all"

    def test_custom(self):
        cfg = ConsensusConfig(
            method="moderator_decides",
            keyword="CONSENSUS_REACHED",
            require="majority",
        )
        assert cfg.method == "moderator_decides"
        assert cfg.keyword == "CONSENSUS_REACHED"
        assert cfg.require == "majority"


# ── AgentDefinition session type ──────────────────────────────────────


class TestSessionAgentDefinition:
    def test_default_type_is_agent(self):
        a = AgentDefinition(id="test", runtime="claude")
        assert a.type == "agent"
        assert a.participants == []

    def test_session_type(self):
        a = AgentDefinition(
            id="review_session",
            type="session",
            participants=["a", "b"],
            max_rounds=5,
            consensus=ConsensusConfig(keyword="I AGREE"),
        )
        assert a.type == "session"
        assert a.runtime is None
        assert a.participants == ["a", "b"]
        assert a.max_rounds == 5
        assert a.consensus.keyword == "I AGREE"

    def test_session_default_max_rounds(self):
        a = AgentDefinition(id="s", type="session", participants=["x"])
        assert a.max_rounds == 10

    def test_session_turn_order_default(self):
        a = AgentDefinition(id="s", type="session", participants=["x"])
        assert a.turn_order == "round_robin"


# ── YAML loading with sessions ────────────────────────────────────────


class TestSessionYAMLLoading:
    def test_load_session_node(self, tmp_project):
        yaml_content = {
            "agents": [
                {
                    "id": "architect",
                    "runtime": "claude",
                    "system_prompt": "Arch: {{ input }} {{ transcript }}",
                },
                {
                    "id": "reviewer",
                    "runtime": "gemini",
                    "system_prompt": "Review: {{ input }} {{ transcript }}",
                },
                {
                    "id": "design_review",
                    "type": "session",
                    "participants": ["architect", "reviewer"],
                    "max_rounds": 5,
                    "consensus": {
                        "method": "vote",
                        "keyword": "VOTE: AGREE",
                        "require": "all",
                    },
                    "summary_agent": "architect",
                },
            ]
        }
        yaml_path = tmp_project / ".aqm" / "agents.yaml"
        yaml_path.write_text(yaml.dump(yaml_content), encoding="utf-8")

        agents = load_agents(yaml_path)
        assert "design_review" in agents
        session = agents["design_review"]
        assert session.type == "session"
        assert session.participants == ["architect", "reviewer"]
        assert session.consensus.method == "vote"
        assert session.summary_agent == "architect"

    def test_session_missing_participants_raises(self, tmp_project):
        yaml_content = {
            "agents": [
                {
                    "id": "empty_session",
                    "type": "session",
                },
            ]
        }
        yaml_path = tmp_project / ".aqm" / "agents.yaml"
        yaml_path.write_text(yaml.dump(yaml_content), encoding="utf-8")

        with pytest.raises(ValueError, match="at least one participant"):
            load_agents(yaml_path)

    def test_session_invalid_participant_raises(self, tmp_project):
        yaml_content = {
            "agents": [
                {
                    "id": "bad_session",
                    "type": "session",
                    "participants": ["nonexistent"],
                },
            ]
        }
        yaml_path = tmp_project / ".aqm" / "agents.yaml"
        yaml_path.write_text(yaml.dump(yaml_content), encoding="utf-8")

        with pytest.raises(ValueError, match="does not exist"):
            load_agents(yaml_path)

    def test_session_participant_cannot_be_session(self, tmp_project):
        yaml_content = {
            "agents": [
                {"id": "a", "runtime": "claude", "system_prompt": "{{ input }}"},
                {
                    "id": "inner",
                    "type": "session",
                    "participants": ["a"],
                },
                {
                    "id": "outer",
                    "type": "session",
                    "participants": ["inner"],
                },
            ]
        }
        yaml_path = tmp_project / ".aqm" / "agents.yaml"
        yaml_path.write_text(yaml.dump(yaml_content), encoding="utf-8")

        with pytest.raises(ValueError, match="cannot be another session"):
            load_agents(yaml_path)

    def test_agent_without_runtime_raises(self, tmp_project):
        yaml_content = {
            "agents": [
                {"id": "bad", "system_prompt": "{{ input }}"},
            ]
        }
        yaml_path = tmp_project / ".aqm" / "agents.yaml"
        yaml_path.write_text(yaml.dump(yaml_content), encoding="utf-8")

        with pytest.raises(ValueError, match="requires a 'runtime'"):
            load_agents(yaml_path)

    def test_mixed_pipeline_with_session(self, tmp_project):
        """Batch agent -> session -> batch agent."""
        yaml_content = {
            "agents": [
                {
                    "id": "planner",
                    "runtime": "claude",
                    "system_prompt": "Plan: {{ input }}",
                    "handoffs": [{"to": "review_session"}],
                },
                {
                    "id": "arch",
                    "runtime": "claude",
                    "system_prompt": "{{ input }} {{ transcript }}",
                },
                {
                    "id": "sec",
                    "runtime": "claude",
                    "system_prompt": "{{ input }} {{ transcript }}",
                },
                {
                    "id": "review_session",
                    "type": "session",
                    "participants": ["arch", "sec"],
                    "consensus": {"method": "vote", "require": "all"},
                    "handoffs": [{"to": "implementer"}],
                },
                {
                    "id": "implementer",
                    "runtime": "claude",
                    "system_prompt": "Implement: {{ input }}",
                },
            ]
        }
        yaml_path = tmp_project / ".aqm" / "agents.yaml"
        yaml_path.write_text(yaml.dump(yaml_content), encoding="utf-8")

        agents = load_agents(yaml_path)
        assert agents["planner"].handoffs[0].to == "review_session"
        assert agents["review_session"].type == "session"
        assert agents["review_session"].handoffs[0].to == "implementer"


# ── Transcript helpers ────────────────────────────────────────────────


class TestTranscriptHelpers:
    def test_init_and_read_transcript(self, tmp_path):
        cf = ContextFile(tmp_path / "task-1")
        cf.init_transcript(topic="Design login", participants=["a", "b"])

        transcript = cf.read_transcript()
        assert "Design login" in transcript
        assert "a, b" in transcript

    def test_append_turn(self, tmp_path):
        cf = ContextFile(tmp_path / "task-1")
        cf.init_transcript(topic="Test", participants=["x"])
        cf.append_turn(
            round_number=1, agent_id="x", message="Hello!", is_round_start=True,
        )

        transcript = cf.read_transcript()
        assert "## Round 1" in transcript
        assert "[x]" in transcript
        assert "Hello!" in transcript

    def test_append_multiple_turns(self, tmp_path):
        cf = ContextFile(tmp_path / "task-1")
        cf.init_transcript(topic="Test", participants=["a", "b"])
        cf.append_turn(round_number=1, agent_id="a", message="Hi", is_round_start=True)
        cf.append_turn(round_number=1, agent_id="b", message="Hey")
        cf.append_turn(round_number=2, agent_id="a", message="Round 2", is_round_start=True)

        transcript = cf.read_transcript()
        assert "## Round 1" in transcript
        assert "## Round 2" in transcript
        assert "[a]" in transcript
        assert "[b]" in transcript

    def test_append_consensus(self, tmp_path):
        cf = ContextFile(tmp_path / "task-1")
        cf.init_transcript(topic="Test", participants=["a", "b"])
        cf.append_consensus(
            round_number=3,
            agreed_by=["a", "b"],
            summary="We all agree on X.",
        )

        transcript = cf.read_transcript()
        assert "Consensus Reached (Round 3)" in transcript
        assert "a, b" in transcript
        assert "We all agree on X." in transcript

    def test_read_empty_transcript(self, tmp_path):
        cf = ContextFile(tmp_path / "task-empty")
        assert cf.read_transcript() == ""


# ── Pipeline session execution ────────────────────────────────────────


class TestPipelineSessionExecution:
    def _make_agents(self):
        return {
            "arch": AgentDefinition(
                id="arch", runtime="claude",
                system_prompt="{{ input }} {{ transcript }}",
            ),
            "sec": AgentDefinition(
                id="sec", runtime="claude",
                system_prompt="{{ input }} {{ transcript }}",
            ),
            "review": AgentDefinition(
                id="review",
                type="session",
                participants=["arch", "sec"],
                max_rounds=5,
                consensus=ConsensusConfig(
                    method="vote",
                    keyword="VOTE: AGREE",
                    require="all",
                ),
            ),
        }

    def test_session_consensus_reached(self, tmp_project):
        """Both agents agree in round 1 → consensus reached."""
        agents = self._make_agents()
        queue = FileQueue(tmp_project / ".aqm" / "file-queue")
        pipeline = Pipeline(agents, queue, tmp_project)

        # Mock runtimes: both agents vote agree
        mock_rt = MagicMock()
        mock_rt.name = "mock"
        mock_rt.run.side_effect = [
            "I think this is good. VOTE: AGREE",  # arch round 1
            "Looks secure. VOTE: AGREE",           # sec round 1
        ]
        pipeline._runtimes["claude"] = mock_rt

        task = Task(description="Design login")
        queue.push(task, "review")

        result = pipeline.run_task(task, "review")
        assert result.status == TaskStatus.completed
        assert result.metadata.get("session_consensus") is True
        assert result.metadata.get("session_rounds") == 1

    def test_session_multi_round_consensus(self, tmp_project):
        """Agents disagree in round 1, agree in round 2."""
        agents = self._make_agents()
        queue = FileQueue(tmp_project / ".aqm" / "file-queue")
        pipeline = Pipeline(agents, queue, tmp_project)

        mock_rt = MagicMock()
        mock_rt.name = "mock"
        mock_rt.run.side_effect = [
            "Looks fine to me. VOTE: AGREE",       # arch round 1
            "I have concerns about token storage.", # sec round 1 (no vote)
            "Addressed the concern. VOTE: AGREE",   # arch round 2
            "OK, resolved. VOTE: AGREE",            # sec round 2
        ]
        pipeline._runtimes["claude"] = mock_rt

        task = Task(description="Design auth")
        queue.push(task, "review")

        result = pipeline.run_task(task, "review")
        assert result.metadata.get("session_consensus") is True
        assert result.metadata.get("session_rounds") == 2

    def test_session_max_rounds_exhausted(self, tmp_project):
        """No consensus after max_rounds → completed with consensus=False."""
        agents = self._make_agents()
        # Override max_rounds to 2
        agents["review"] = AgentDefinition(
            id="review",
            type="session",
            participants=["arch", "sec"],
            max_rounds=2,
            consensus=ConsensusConfig(method="vote", require="all"),
        )
        queue = FileQueue(tmp_project / ".aqm" / "file-queue")
        pipeline = Pipeline(agents, queue, tmp_project)

        mock_rt = MagicMock()
        mock_rt.name = "mock"
        # Nobody ever votes
        mock_rt.run.return_value = "I disagree with the approach."
        pipeline._runtimes["claude"] = mock_rt

        task = Task(description="Deadlocked topic")
        queue.push(task, "review")

        result = pipeline.run_task(task, "review")
        assert result.metadata.get("session_consensus") is False
        assert result.metadata.get("session_rounds") == 2

    def test_session_majority_consensus(self, tmp_project):
        """Majority consensus: 2 out of 3 agents agree."""
        agents = {
            "a": AgentDefinition(
                id="a", runtime="claude", system_prompt="{{ input }} {{ transcript }}",
            ),
            "b": AgentDefinition(
                id="b", runtime="claude", system_prompt="{{ input }} {{ transcript }}",
            ),
            "c": AgentDefinition(
                id="c", runtime="claude", system_prompt="{{ input }} {{ transcript }}",
            ),
            "session": AgentDefinition(
                id="session",
                type="session",
                participants=["a", "b", "c"],
                max_rounds=3,
                consensus=ConsensusConfig(method="vote", require="majority"),
            ),
        }
        queue = FileQueue(tmp_project / ".aqm" / "file-queue")
        pipeline = Pipeline(agents, queue, tmp_project)

        mock_rt = MagicMock()
        mock_rt.name = "mock"
        mock_rt.run.side_effect = [
            "VOTE: AGREE",        # a agrees
            "VOTE: AGREE",        # b agrees
            "I still disagree.",   # c does not agree
        ]
        pipeline._runtimes["claude"] = mock_rt

        task = Task(description="Test majority")
        queue.push(task, "session")

        result = pipeline.run_task(task, "session")
        assert result.metadata.get("session_consensus") is True
        assert result.metadata.get("session_rounds") == 1

    def test_session_with_summary_agent(self, tmp_project):
        """Summary agent runs after consensus to produce final output."""
        agents = {
            "a": AgentDefinition(
                id="a", runtime="claude", system_prompt="{{ input }} {{ transcript }}",
            ),
            "b": AgentDefinition(
                id="b", runtime="claude", system_prompt="{{ input }} {{ transcript }}",
            ),
            "session": AgentDefinition(
                id="session",
                type="session",
                participants=["a", "b"],
                max_rounds=5,
                consensus=ConsensusConfig(method="vote", require="all"),
                summary_agent="a",
            ),
        }
        queue = FileQueue(tmp_project / ".aqm" / "file-queue")
        pipeline = Pipeline(agents, queue, tmp_project)

        mock_rt = MagicMock()
        mock_rt.name = "mock"
        mock_rt.run.side_effect = [
            "VOTE: AGREE",                          # a round 1
            "VOTE: AGREE",                          # b round 1
            "FINAL SUMMARY: we agreed on design X", # summary agent
        ]
        pipeline._runtimes["claude"] = mock_rt

        task = Task(description="Summarize test")
        queue.push(task, "session")

        result = pipeline.run_task(task, "session")
        assert result.metadata.get("session_consensus") is True
        # The session stage output should be the summary
        session_stage = [s for s in result.stages if s.task_name == "session"]
        assert len(session_stage) == 1
        assert "FINAL SUMMARY" in session_stage[0].output_text

    def test_session_in_pipeline_chain(self, tmp_project):
        """Batch agent → session → batch agent chain."""
        agents = {
            "planner": AgentDefinition(
                id="planner", runtime="claude",
                system_prompt="Plan: {{ input }}",
                handoffs=[{"to": "review"}],
            ),
            "arch": AgentDefinition(
                id="arch", runtime="claude",
                system_prompt="{{ input }} {{ transcript }}",
            ),
            "sec": AgentDefinition(
                id="sec", runtime="claude",
                system_prompt="{{ input }} {{ transcript }}",
            ),
            "review": AgentDefinition(
                id="review",
                type="session",
                participants=["arch", "sec"],
                max_rounds=5,
                consensus=ConsensusConfig(method="vote", require="all"),
                handoffs=[{"to": "implementer"}],
            ),
            "implementer": AgentDefinition(
                id="implementer", runtime="claude",
                system_prompt="Implement: {{ input }}",
            ),
        }
        queue = FileQueue(tmp_project / ".aqm" / "file-queue")
        pipeline = Pipeline(agents, queue, tmp_project)

        mock_rt = MagicMock()
        mock_rt.name = "mock"
        mock_rt.run.side_effect = [
            "Here is the plan.",        # planner
            "Good plan. VOTE: AGREE",   # arch in session
            "Secure enough. VOTE: AGREE", # sec in session
            "Implementation done.",      # implementer
        ]
        pipeline._runtimes["claude"] = mock_rt

        task = Task(description="Build feature")
        queue.push(task, "planner")

        result = pipeline.run_task(task, "planner")
        assert result.status == TaskStatus.completed
        # Should have: planner stage + session turns (2) + session summary stage + implementer stage
        # Actually: planner(1) + arch turn(2) + sec turn(3) + session-stage(4) + implementer(5)
        assert len(result.stages) >= 4
        agent_ids = [s.agent_id for s in result.stages]
        assert "planner" in agent_ids
        assert "review" in agent_ids  # session summary stage
        assert "implementer" in agent_ids

    def test_session_moderator_decides(self, tmp_project):
        """moderator_decides: only summary_agent can trigger consensus."""
        agents = {
            "mod": AgentDefinition(
                id="mod", runtime="claude",
                system_prompt="{{ input }} {{ transcript }}",
            ),
            "dev": AgentDefinition(
                id="dev", runtime="claude",
                system_prompt="{{ input }} {{ transcript }}",
            ),
            "session": AgentDefinition(
                id="session",
                type="session",
                participants=["mod", "dev"],
                max_rounds=5,
                turn_order="moderator",
                consensus=ConsensusConfig(
                    method="moderator_decides",
                    keyword="CONSENSUS_REACHED",
                ),
                summary_agent="mod",
            ),
        }
        queue = FileQueue(tmp_project / ".aqm" / "file-queue")
        pipeline = Pipeline(agents, queue, tmp_project)

        mock_rt = MagicMock()
        mock_rt.name = "mock"
        mock_rt.run.side_effect = [
            "Let's discuss. Mod speaking.",        # mod round 1
            "CONSENSUS_REACHED by dev.",           # dev round 1 (ignored - not moderator)
            "I declare CONSENSUS_REACHED.",         # mod round 2 (this counts!)
            "Agreed.",                              # dev round 2
            "Final summary from moderator.",        # summary agent call after consensus
        ]
        pipeline._runtimes["claude"] = mock_rt

        task = Task(description="Mod test")
        queue.push(task, "session")

        result = pipeline.run_task(task, "session")
        # Consensus should be reached in round 2 (when moderator says it)
        assert result.metadata.get("session_consensus") is True
        assert result.metadata.get("session_rounds") == 2

    def test_session_records_turns_as_stages(self, tmp_project):
        """Each turn in the session is recorded as a StageRecord."""
        agents = self._make_agents()
        queue = FileQueue(tmp_project / ".aqm" / "file-queue")
        pipeline = Pipeline(agents, queue, tmp_project)

        mock_rt = MagicMock()
        mock_rt.name = "mock"
        mock_rt.run.side_effect = [
            "VOTE: AGREE",
            "VOTE: AGREE",
        ]
        pipeline._runtimes["claude"] = mock_rt

        task = Task(description="Test stages")
        queue.push(task, "review")

        result = pipeline.run_task(task, "review")

        # 2 turn stages + 1 session summary stage = 3 total
        turn_stages = [s for s in result.stages if s.task_name.startswith("session:")]
        assert len(turn_stages) == 2
        assert turn_stages[0].agent_id == "arch"
        assert turn_stages[1].agent_id == "sec"

    def test_session_callbacks_called(self, tmp_project):
        """on_turn_start and on_turn_complete callbacks are invoked."""
        agents = self._make_agents()
        queue = FileQueue(tmp_project / ".aqm" / "file-queue")
        pipeline = Pipeline(agents, queue, tmp_project)

        mock_rt = MagicMock()
        mock_rt.name = "mock"
        mock_rt.run.side_effect = ["VOTE: AGREE", "VOTE: AGREE"]
        pipeline._runtimes["claude"] = mock_rt

        starts = []
        completes = []

        task = Task(description="Callback test")
        queue.push(task, "review")

        result = pipeline.run_task(
            task, "review",
            on_stage_start=lambda t, aid, sn: starts.append((aid, sn)),
            on_stage_complete=lambda t, s: completes.append(s.agent_id),
        )

        # Turns + session summary stage
        assert len(starts) >= 2
        assert "arch" in [s[0] for s in starts]
        assert "sec" in [s[0] for s in starts]
        assert "review" in completes  # session stage complete callback
