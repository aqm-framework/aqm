#!/usr/bin/env python3
"""Token efficiency benchmark — real pipeline simulation.

Creates temporary project folders with agents.yaml pipelines,
runs them with a mock runtime, and compares token usage across
all context strategies. Outputs a detailed report.

Usage:
    python -m tests.bench_token_efficiency
"""
from __future__ import annotations

import sys
import tempfile
import textwrap
import time
from pathlib import Path

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from unittest.mock import MagicMock

import yaml

from aqm.core.agent import AgentDefinition, load_agents
from aqm.core.context import build_prompt
from aqm.core.context_file import ContextFile
from aqm.core.pipeline import Pipeline
from aqm.core.project import init_project
from aqm.core.task import Task, TaskStatus
from aqm.queue.file import FileQueue


# ── Token counting ────────────────────────────────────────────────────

def token_count(text: str) -> int:
    """Approximate token count. Whitespace-split is ~1.3x real BPE tokens,
    so we use a slightly adjusted formula for closer approximation."""
    if not text:
        return 0
    # Average English: ~4 chars per token for GPT/Claude BPE
    return max(1, len(text) // 4)


# ── Realistic agent outputs ──────────────────────────────────────────

REALISTIC_OUTPUTS = {
    "planner": textwrap.dedent("""\
        ## Implementation Plan

        After analyzing the requirements, here is the structured plan:

        1. **Database Schema Changes**
           - Add `user_preferences` table with columns: id, user_id, theme, language, notifications
           - Add foreign key constraint to users table
           - Create migration script

        2. **API Endpoints**
           - GET /api/v1/preferences/:userId — fetch preferences
           - PUT /api/v1/preferences/:userId — update preferences
           - Validation middleware for preference values

        3. **Frontend Components**
           - PreferencesPage component with form
           - ThemeSelector dropdown
           - NotificationToggle switch
           - Integration with existing settings layout

        4. **Testing**
           - Unit tests for preference model
           - Integration tests for API endpoints
           - E2E tests for preference flow

        Estimated complexity: Medium. No breaking changes to existing APIs.
    """),
    "architect": textwrap.dedent("""\
        ## Architecture Review

        The proposed plan is sound. Key considerations:

        **Database**: Using a separate `user_preferences` table rather than adding
        columns to the users table is the right call — it follows the Single
        Responsibility Principle and allows us to add preference categories without
        schema changes.

        **API Design**: RESTful endpoints look correct. I recommend adding:
        - Rate limiting on PUT endpoint (max 10 updates/minute)
        - ETag support for optimistic concurrency
        - Default preferences factory for new users

        **Caching Strategy**: Preferences are read-heavy. Implement:
        - Redis cache with 5-minute TTL
        - Cache invalidation on PUT
        - Fallback to DB on cache miss

        **Security**: Ensure userId in path matches authenticated user's token.
        Add input sanitization for theme/language values (whitelist approach).

        APPROVED with the caching and security additions noted above.
    """),
    "developer": textwrap.dedent("""\
        ## Implementation Complete

        Changes made across 8 files:

        ```python
        # models/preferences.py
        class UserPreference(Base):
            __tablename__ = 'user_preferences'
            id = Column(Integer, primary_key=True)
            user_id = Column(Integer, ForeignKey('users.id'), unique=True)
            theme = Column(String(20), default='light')
            language = Column(String(10), default='en')
            notifications = Column(Boolean, default=True)
            updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
        ```

        ```python
        # api/preferences.py
        @router.get("/preferences/{user_id}")
        async def get_preferences(user_id: int, current_user: User = Depends(get_current_user)):
            if current_user.id != user_id:
                raise HTTPException(403)
            prefs = await PreferenceService.get_or_create(user_id)
            return PreferenceResponse.from_orm(prefs)
        ```

        - Added Redis caching layer as recommended by architect
        - Rate limiting configured at 10 req/min on PUT
        - Migration script: `alembic/versions/003_add_preferences.py`
        - Input validation uses whitelist for theme: [light, dark, system]
        - Language validation against ISO 639-1 codes

        All linting passes. Ready for QA.
    """),
    "qa": textwrap.dedent("""\
        ## QA Report

        ### Test Results: 24/24 PASSING

        **Unit Tests** (12/12):
        - UserPreference model creation ✓
        - Default values applied correctly ✓
        - Theme validation rejects invalid values ✓
        - Language validation rejects invalid codes ✓
        - Preference serialization/deserialization ✓
        - Cache key generation ✓
        - Cache invalidation on update ✓
        - Rate limiter blocks excessive requests ✓
        - Authorization check (own user only) ✓
        - ETag generation and comparison ✓
        - Migration up/down ✓
        - Default factory for new users ✓

        **Integration Tests** (8/8):
        - GET returns defaults for new user ✓
        - PUT updates and returns new values ✓
        - PUT invalidates cache ✓
        - GET serves from cache on second call ✓
        - 403 on accessing other user's preferences ✓
        - 429 on rate limit exceeded ✓
        - Concurrent updates handled via ETag ✓
        - Preference persists across sessions ✓

        **E2E Tests** (4/4):
        - Full preference update flow ✓
        - Theme change reflects in UI ✓
        - Language change updates translations ✓
        - Notification toggle sends test notification ✓

        **Coverage**: 94.2% on new code.
        **Performance**: GET p99 = 12ms (cached), PUT p99 = 45ms.

        APPROVED — all tests passing, coverage above threshold.
    """),
    "reviewer": textwrap.dedent("""\
        ## Code Review

        Reviewed all 8 changed files. Overall quality: Good.

        **Approved with minor suggestions:**

        1. `models/preferences.py:15` — Consider adding `__repr__` for debugging
        2. `api/preferences.py:23` — The 403 error could include a more descriptive message
        3. `services/cache.py:45` — TTL constant should be configurable via environment variable
        4. `tests/test_preferences.py:89` — Add edge case test for empty string theme

        None of these are blocking. The implementation follows the architecture
        review recommendations correctly. Security considerations are properly
        addressed. Redis caching implementation is clean.

        APPROVED
    """),
    "deployer": textwrap.dedent("""\
        ## Deployment Summary

        Successfully deployed to staging environment.

        **Steps executed:**
        1. Database migration applied (003_add_preferences) — 0.3s
        2. Redis cache warmed for existing users — 12s
        3. API servers rolling restart (0 downtime) — 45s
        4. Health checks passing on all 3 instances
        5. Smoke tests executed — all green

        **Rollback plan**: `alembic downgrade -1` + restart API servers.
        Migration is backward-compatible (new table only, no column changes).

        Staging URL: https://staging.example.com/api/v1/preferences
        Ready for production deployment pending approval.
    """),
}


def _get_realistic_output(agent_id: str, stage: int) -> str:
    """Return a realistic output for the given agent."""
    # Map agent IDs to output templates
    for key, output in REALISTIC_OUTPUTS.items():
        if key in agent_id:
            return output
    # Generic fallback
    return (
        f"Stage {stage} completed by {agent_id}. "
        f"Analysis shows positive results across all metrics. "
        f"The implementation follows best practices and passes validation. "
        f"Recommending to proceed to the next stage. " * 3
        + f"\nFinal assessment: APPROVED for stage {stage}."
    )


# ── Pipeline builder ──────────────────────────────────────────────────

def create_pipeline_yaml(n_agents: int, strategy: str, context_window: int = 3) -> dict:
    """Generate agents.yaml config for a linear pipeline."""
    agent_roles = ["planner", "architect", "developer", "qa", "reviewer", "deployer",
                   "monitor", "optimizer", "documenter", "finalizer"]
    agents = []
    for i in range(n_agents):
        role = agent_roles[i % len(agent_roles)]
        agent_id = f"{role}_{i}" if i >= len(agent_roles) else role
        agent = {
            "id": agent_id,
            "runtime": "claude",
            "context_strategy": strategy,
            "context_window": context_window,
            "system_prompt": (
                f"You are {role}. Task: {{{{ input }}}}\n"
                f"Context:\n{{{{ context }}}}"
            ),
        }
        if i < n_agents - 1:
            next_role = agent_roles[(i + 1) % len(agent_roles)]
            next_id = f"{next_role}_{i+1}" if (i + 1) >= len(agent_roles) else next_role
            agent["handoffs"] = [{"to": next_id}]
        agents.append(agent)
    return {"agents": agents}


def run_benchmark(n_agents: int, strategy: str, context_window: int = 3) -> dict:
    """Run a full pipeline benchmark and return metrics."""
    with tempfile.TemporaryDirectory() as td:
        root = init_project(Path(td))
        yaml_config = create_pipeline_yaml(n_agents, strategy, context_window)
        yaml_path = root / ".aqm" / "agents.yaml"
        yaml_path.write_text(yaml.dump(yaml_config), encoding="utf-8")

        agents = load_agents(yaml_path)
        queue = FileQueue(root / ".aqm" / "queue")
        pipeline = Pipeline(agents, queue, root)

        # Capture every prompt
        prompts_sent: list[str] = []
        agent_ids_order: list[str] = []
        call_count = [0]

        mock_rt = MagicMock()
        mock_rt.name = "mock"

        def capture_and_respond(prompt, agent, task, on_output=None, on_thinking=None, on_tool=None):
            prompts_sent.append(prompt)
            agent_ids_order.append(agent.id)
            call_count[0] += 1
            return _get_realistic_output(agent.id, call_count[0])

        mock_rt.run.side_effect = capture_and_respond
        pipeline._runtimes["claude"] = mock_rt

        task = Task(description="Implement user preferences feature with database, API, frontend, and full testing.")
        first_agent = list(agents.keys())[0]
        queue.push(task, first_agent)

        start = time.perf_counter()
        result = pipeline.run_task(task, first_agent)
        elapsed = time.perf_counter() - start

        # Compute metrics
        prompt_tokens = [token_count(p) for p in prompts_sent]
        prompt_chars = [len(p) for p in prompts_sent]

        # Read final context.md size
        from aqm.core.project import get_tasks_dir
        tasks_dir = get_tasks_dir(root)
        ctx_path = tasks_dir / task.id / "context.md"
        context_size = ctx_path.stat().st_size if ctx_path.exists() else 0

        return {
            "strategy": strategy,
            "context_window": context_window,
            "n_agents": n_agents,
            "status": result.status.value,
            "stages": len(result.stages),
            "elapsed_s": elapsed,
            "total_prompt_tokens": sum(prompt_tokens),
            "total_prompt_chars": sum(prompt_chars),
            "per_agent_tokens": prompt_tokens,
            "per_agent_chars": prompt_chars,
            "agent_ids": agent_ids_order,
            "avg_prompt_tokens": sum(prompt_tokens) // len(prompt_tokens),
            "max_prompt_tokens": max(prompt_tokens),
            "min_prompt_tokens": min(prompt_tokens),
            "last_prompt_tokens": prompt_tokens[-1],
            "context_md_bytes": context_size,
        }


# ── Main benchmark ────────────────────────────────────────────────────

def print_report(results: list[dict], title: str):
    """Print a formatted comparison table."""
    print(f"\n{'=' * 85}")
    print(f"  {title}")
    print(f"{'=' * 85}")

    baseline = results[0]["total_prompt_tokens"]

    print(f"\n{'Strategy':<12} {'Window':>6} {'Total Tok':>10} {'Avg/Agent':>10} "
          f"{'Max Prompt':>10} {'Last':>8} {'Savings':>8} {'Status':>10}")
    print("-" * 85)

    for r in results:
        savings = (1 - r["total_prompt_tokens"] / baseline) * 100 if baseline else 0
        print(
            f"{r['strategy']:<12} {r['context_window']:>6} "
            f"{r['total_prompt_tokens']:>10,} {r['avg_prompt_tokens']:>10,} "
            f"{r['max_prompt_tokens']:>10,} {r['last_prompt_tokens']:>8,} "
            f"{savings:>7.1f}% {r['status']:>10}"
        )

    print()

    # Per-agent breakdown for baseline vs best
    print("  Per-Agent Token Breakdown (baseline 'both' vs most efficient 'none'):")
    print(f"  {'Agent':<15} {'both':>10} {'none':>10} {'Delta':>10} {'Reduction':>10}")
    print(f"  {'-' * 55}")
    both_r = next(r for r in results if r["strategy"] == "both")
    none_r = next(r for r in results if r["strategy"] == "none")
    for i, agent_id in enumerate(both_r["agent_ids"]):
        bt = both_r["per_agent_tokens"][i]
        nt = none_r["per_agent_tokens"][i]
        delta = bt - nt
        pct = (delta / bt * 100) if bt else 0
        print(f"  {agent_id:<15} {bt:>10,} {nt:>10,} {delta:>+10,} {pct:>9.1f}%")

    print()


def print_growth_analysis(results_by_size: dict[int, list[dict]]):
    """Show how token usage scales with pipeline depth."""
    print(f"\n{'=' * 85}")
    print("  TOKEN GROWTH vs PIPELINE DEPTH")
    print(f"{'=' * 85}")
    print(f"\n{'Agents':>8} {'both':>10} {'shared':>10} {'last_only':>10} {'none':>10} "
          f"{'both→none':>10}")
    print("-" * 65)

    for n_agents, results in sorted(results_by_size.items()):
        row = {r["strategy"]: r["total_prompt_tokens"] for r in results}
        savings = (1 - row.get("none", 0) / row.get("both", 1)) * 100
        print(
            f"{n_agents:>8} {row.get('both', 0):>10,} {row.get('shared', 0):>10,} "
            f"{row.get('last_only', 0):>10,} {row.get('none', 0):>10,} "
            f"{savings:>9.1f}%"
        )

    print()


def main():
    print("\n" + "#" * 85)
    print("#  AQM Token Efficiency Benchmark")
    print("#  Running real pipeline simulations with mock runtimes")
    print("#" * 85)

    strategies = ["both", "shared", "last_only", "own", "none"]
    results_by_size: dict[int, list[dict]] = {}

    # ── Benchmark 1: 6-agent pipeline ──
    print("\n>>> Running 6-agent pipeline benchmarks...")
    results_6 = []
    for strategy in strategies:
        r = run_benchmark(6, strategy, context_window=3)
        results_6.append(r)
    results_by_size[6] = results_6
    print_report(results_6, "6-AGENT PIPELINE (context_window=3)")

    # ── Benchmark 2: 10-agent pipeline ──
    print(">>> Running 10-agent pipeline benchmarks...")
    results_10 = []
    for strategy in strategies:
        r = run_benchmark(10, strategy, context_window=3)
        results_10.append(r)
    results_by_size[10] = results_10
    print_report(results_10, "10-AGENT PIPELINE (context_window=3)")

    # ── Benchmark 3: 6-agent with different windows ──
    print(">>> Running context_window comparison (6 agents, strategy=shared)...")
    print(f"\n{'=' * 60}")
    print("  CONTEXT WINDOW SIZE EFFECT (6 agents, shared strategy)")
    print(f"{'=' * 60}")
    print(f"\n{'Window':>8} {'Total Tok':>10} {'Last Prompt':>12} {'ctx.md KB':>10}")
    print("-" * 45)
    for window in [0, 1, 2, 3, 5]:
        r = run_benchmark(6, "shared", context_window=window)
        ctx_kb = r["context_md_bytes"] / 1024
        print(f"{window:>8} {r['total_prompt_tokens']:>10,} "
              f"{r['last_prompt_tokens']:>12,} {ctx_kb:>9.1f}")
    print()

    # ── Growth analysis ──
    print(">>> Running pipeline depth scaling...")
    for n in [3, 15]:
        results_n = []
        for strategy in ["both", "shared", "last_only", "none"]:
            r = run_benchmark(n, strategy, context_window=3)
            results_n.append(r)
        results_by_size[n] = results_n

    print_growth_analysis(results_by_size)

    # ── Final summary ──
    print("=" * 85)
    print("  SUMMARY")
    print("=" * 85)

    r10_both = next(r for r in results_10 if r["strategy"] == "both")
    r10_last = next(r for r in results_10 if r["strategy"] == "last_only")
    r10_none = next(r for r in results_10 if r["strategy"] == "none")

    print(f"""
  10-agent pipeline baseline (both):     {r10_both['total_prompt_tokens']:>8,} tokens
  With last_only strategy:               {r10_last['total_prompt_tokens']:>8,} tokens  ({(1-r10_last['total_prompt_tokens']/r10_both['total_prompt_tokens'])*100:.0f}% savings)
  With none strategy:                    {r10_none['total_prompt_tokens']:>8,} tokens  ({(1-r10_none['total_prompt_tokens']/r10_both['total_prompt_tokens'])*100:.0f}% savings)

  Recommendation:
  - Use 'last_only' for agents that only need the previous stage's output
  - Use 'none' for agents with self-contained prompts (no context needed)
  - Use 'shared' with context_window=2-3 for agents needing broader history
  - Keep 'both' only for agents that genuinely need full pipeline history
""")
    print("=" * 85)
    print("  Benchmark complete. All pipelines ran to completion.\n")


if __name__ == "__main__":
    main()
