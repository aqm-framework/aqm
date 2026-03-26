"""Token efficiency tests — measures before/after token savings.

Simulates pipelines with varying stage counts and compares token usage
across different context strategies and the optimized summarization.
No external dependencies — uses simple whitespace tokenization as proxy.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from aqm.core.agent import AgentDefinition
from aqm.core.context import build_prompt
from aqm.core.context_file import ContextFile
from aqm.core.pipeline import Pipeline
from aqm.core.task import Task, TaskStatus
from aqm.queue.file import FileQueue


def _token_count(text: str) -> int:
    """Approximate token count (whitespace split — roughly 1.3x real tokens)."""
    return len(text.split())


def _char_count(text: str) -> int:
    return len(text)


def _build_stages(cf: ContextFile, n: int, output_size: int = 300) -> None:
    """Append n realistic stages to context.md."""
    for i in range(1, n + 1):
        cf.append_stage(
            stage_number=i,
            agent_id=f"agent_{i}",
            task_name=f"task_{i}",
            status="completed",
            input_text=f"Process step {i}: analyze the data and produce output for downstream agents. "
                       f"Consider context from previous stages. Input payload chunk {i}.",
            output_text=(
                f"Stage {i} output: After careful analysis, here are the findings. "
                + "The data shows significant patterns in user behavior. " * (output_size // 50)
                + f"Conclusion for stage {i}: proceed to next step."
            ),
        )


# ── Strategy comparison ──────────────────────────────────────────────


class TestStrategyTokenComparison:
    """Compare token usage across all context strategies."""

    @pytest.mark.parametrize("n_stages", [5, 10, 20])
    def test_strategy_comparison(self, tmp_path, n_stages):
        """For a given stage count, measure tokens for each strategy."""
        cf = ContextFile(tmp_path / "task")
        _build_stages(cf, n_stages)

        # Also create per-agent context for 'own' and 'both'
        for i in range(1, n_stages + 1):
            cf.append_agent_context(
                agent_id=f"agent_{i}",
                stage_number=i,
                input_text=f"input {i}",
                output_text=f"Stage {i} private notes. Short summary.",
            )

        strategies = ["none", "last_only", "own", "shared", "both"]
        results: dict[str, int] = {}

        for strategy in strategies:
            text = cf.read_for_strategy(f"agent_{n_stages}", strategy, context_window=3)
            results[strategy] = _token_count(text)

        full_tokens = _token_count(cf.read())

        # Assertions: ordering from most to least efficient
        assert results["none"] == 0, "none strategy should produce zero tokens"
        assert results["last_only"] < full_tokens * 0.5, "last_only should be under 50% of full"
        assert results["own"] < full_tokens * 0.3, "own should be under 30% of full"
        assert results["shared"] < full_tokens, "shared (smart) should be less than full"

        if n_stages > 5:
            # For larger pipelines, shared should be significantly smaller
            assert results["shared"] < full_tokens * 0.7, (
                f"shared should be under 70% of full for {n_stages} stages"
            )


class TestSmartContextEfficiency:
    """Test that read_smart compression ratio improves with more stages."""

    def test_compression_ratio_scales(self, tmp_path):
        """More stages → better compression ratio."""
        ratios = {}
        for n in [5, 10, 20]:
            cf = ContextFile(tmp_path / f"task-{n}")
            _build_stages(cf, n)
            full = cf.read()
            smart = cf.read_smart(context_window=3)
            ratios[n] = len(smart) / len(full)

        # Compression should improve as stages increase
        assert ratios[10] < ratios[5], "10 stages should compress better than 5"
        assert ratios[20] < ratios[10], "20 stages should compress better than 10"
        # 20 stages with window=3 should compress to under 35%
        assert ratios[20] < 0.35, f"20-stage ratio {ratios[20]:.2%} should be under 35%"

    def test_summary_line_is_compact(self, tmp_path):
        """Each summarized stage should be a single short line."""
        cf = ContextFile(tmp_path / "task")
        _build_stages(cf, 10)
        smart = cf.read_smart(context_window=2)

        # Extract summary lines (between [history] and [recent])
        history_section = smart.split("[recent]")[0]
        summary_lines = [
            line for line in history_section.split("\n")
            if line.startswith("- ")
        ]

        assert len(summary_lines) == 8, "should have 8 summarized stages (10 - window of 2)"
        for line in summary_lines:
            # Each summary line should be reasonably short
            assert len(line) < 200, f"Summary line too long ({len(line)} chars): {line[:100]}..."


# ── Pipeline integration with token tracking ──────────────────────────


class TestPipelineTokenTracking:
    """Run a mock pipeline and measure total prompt tokens per strategy."""

    def _run_pipeline_and_measure(self, tmp_project, strategy, n_agents=6):
        """Run a pipeline with n agents and return total prompt tokens."""
        agents = {}
        for i in range(n_agents):
            agent_id = f"agent_{i}"
            handoffs = []
            if i < n_agents - 1:
                handoffs = [{"to": f"agent_{i + 1}"}]
            agents[agent_id] = AgentDefinition(
                id=agent_id,
                runtime="claude",
                context_strategy=strategy,
                context_window=2,
                system_prompt="You are agent {{ input }}. Context:\n{{ context }}",
                handoffs=handoffs,
            )

        queue = FileQueue(tmp_project / ".aqm" / f"queue-{strategy}")
        pipeline = Pipeline(agents, queue, tmp_project)

        # Track all prompts sent to runtime
        prompts_sent: list[str] = []

        mock_rt = MagicMock()
        mock_rt.name = "mock"

        def capture_prompt(prompt, agent, task, on_output=None, on_thinking=None, on_tool=None):
            prompts_sent.append(prompt)
            return f"Output from {agent.id}: analysis complete. Result data here. " * 5
        mock_rt.run.side_effect = capture_prompt
        pipeline._runtimes["claude"] = mock_rt

        task = Task(description="End-to-end pipeline test")
        queue.push(task, "agent_0")
        result = pipeline.run_task(task, "agent_0")

        assert result.status == TaskStatus.completed
        assert len(prompts_sent) == n_agents

        total_tokens = sum(_token_count(p) for p in prompts_sent)
        total_chars = sum(_char_count(p) for p in prompts_sent)
        return total_tokens, total_chars, prompts_sent

    def test_none_strategy_minimal_tokens(self, tmp_project):
        """none strategy: total tokens much less than 'both' strategy."""
        tokens_none, _, _ = self._run_pipeline_and_measure(tmp_project, "none", 6)

        import tempfile
        from aqm.core.project import init_project
        with tempfile.TemporaryDirectory() as td:
            proj = init_project(Path(td))
            tokens_both, _, _ = self._run_pipeline_and_measure(proj, "both", 6)

        assert tokens_none < tokens_both, (
            f"none ({tokens_none}) should use fewer tokens than both ({tokens_both})"
        )

    def test_last_only_bounded_growth(self, tmp_project):
        """last_only should produce less tokens than 'both' strategy."""
        tokens_last, _, _ = self._run_pipeline_and_measure(tmp_project, "last_only", 6)

        import tempfile
        from aqm.core.project import init_project
        with tempfile.TemporaryDirectory() as td:
            proj = init_project(Path(td))
            tokens_both, _, _ = self._run_pipeline_and_measure(proj, "both", 6)

        assert tokens_last < tokens_both, (
            f"last_only ({tokens_last}) should use fewer tokens than both ({tokens_both})"
        )

    def test_strategies_ordered_by_efficiency(self, tmp_project):
        """none < last_only < own < shared ≤ both in total tokens."""
        results = {}
        for strategy in ["none", "last_only", "shared", "both"]:
            # Use separate tmp projects to avoid context file interference
            import tempfile
            from aqm.core.project import init_project
            with tempfile.TemporaryDirectory() as td:
                proj = init_project(Path(td))
                tokens, chars, _ = self._run_pipeline_and_measure(proj, strategy, 8)
                results[strategy] = tokens

        assert results["none"] < results["last_only"], (
            f"none ({results['none']}) should < last_only ({results['last_only']})"
        )
        assert results["last_only"] < results["shared"], (
            f"last_only ({results['last_only']}) should < shared ({results['shared']})"
        )
        assert results["shared"] <= results["both"] * 1.1, (
            f"shared ({results['shared']}) should ≤ both ({results['both']})"
        )

    def test_token_savings_report(self, tmp_project):
        """Generate a human-readable report comparing all strategies."""
        import tempfile
        from aqm.core.project import init_project

        n_agents = 10
        results = {}
        for strategy in ["none", "last_only", "own", "shared", "both"]:
            with tempfile.TemporaryDirectory() as td:
                proj = init_project(Path(td))
                tokens, chars, prompts = self._run_pipeline_and_measure(
                    proj, strategy, n_agents
                )
                results[strategy] = {
                    "total_tokens": tokens,
                    "total_chars": chars,
                    "avg_prompt_tokens": tokens // n_agents,
                    "last_prompt_tokens": _token_count(prompts[-1]),
                }

        baseline = results["both"]["total_tokens"]

        print("\n" + "=" * 70)
        print(f"TOKEN EFFICIENCY REPORT ({n_agents}-agent pipeline)")
        print("=" * 70)
        print(f"{'Strategy':<12} {'Total Tok':>10} {'Avg/Agent':>10} {'Last Prompt':>12} {'Savings':>10}")
        print("-" * 70)
        for strategy in ["both", "shared", "own", "last_only", "none"]:
            r = results[strategy]
            savings = (1 - r["total_tokens"] / baseline) * 100 if baseline else 0
            print(
                f"{strategy:<12} {r['total_tokens']:>10,} {r['avg_prompt_tokens']:>10,} "
                f"{r['last_prompt_tokens']:>12,} {savings:>9.1f}%"
            )
        print("=" * 70)

        # Verify real savings exist
        assert results["none"]["total_tokens"] < baseline * 0.5
        assert results["last_only"]["total_tokens"] < baseline * 0.8


# ── Edge cases ────────────────────────────────────────────────────────


class TestTokenEdgeCases:
    def test_empty_context(self, tmp_path):
        cf = ContextFile(tmp_path / "task")
        for strategy in ["none", "last_only", "own", "shared", "both"]:
            result = cf.read_for_strategy("agent", strategy)
            assert result == ""

    def test_single_stage_all_strategies_same(self, tmp_path):
        cf = ContextFile(tmp_path / "task")
        _build_stages(cf, 1)
        shared = cf.read_for_strategy("agent_1", "shared")
        both = cf.read_for_strategy("agent_1", "both")
        # With 1 stage and no agent context, shared and both should be same
        assert shared == both

    def test_none_strategy_in_agent_definition(self):
        a = AgentDefinition(id="test", runtime="claude", context_strategy="none")
        assert a.context_strategy == "none"

    def test_last_only_strategy_in_agent_definition(self):
        a = AgentDefinition(id="test", runtime="claude", context_strategy="last_only")
        assert a.context_strategy == "last_only"

    def test_context_window_1(self, tmp_path):
        """Window of 1 with many stages should produce maximum compression."""
        cf = ContextFile(tmp_path / "task")
        _build_stages(cf, 15)
        smart = cf.read_smart(context_window=1)
        full = cf.read()
        ratio = len(smart) / len(full)
        assert ratio < 0.30, f"Window=1 should compress to <30%, got {ratio:.1%}"
