"""Tests for runtime retry feature — RetryConfig, error classification, and pipeline retry loop."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch
import time

import pytest
import yaml

from aqm.core.agent import AgentDefinition, RetryConfig, load_agents
from aqm.core.task import StageRecord, Task, TaskStatus
from aqm.core.pipeline import Pipeline
from aqm.queue.file import FileQueue
from aqm.runtime.base import RuntimeExecutionError


# ═══════════════════════════════════════════════════════════════════════
# 1. RetryConfig MODEL
# ═══════════════════════════════════════════════════════════════════════


class TestRetryConfigModel:

    def test_defaults(self):
        rc = RetryConfig()
        assert rc.max_retries == 0
        assert rc.fallback_context_strategy is None
        assert rc.backoff == 0

    def test_custom_values(self):
        rc = RetryConfig(max_retries=3, fallback_context_strategy="last_only", backoff=5)
        assert rc.max_retries == 3
        assert rc.fallback_context_strategy == "last_only"
        assert rc.backoff == 5

    def test_all_valid_strategies(self):
        for strategy in ("none", "last_only", "own", "shared", "both"):
            rc = RetryConfig(fallback_context_strategy=strategy)
            assert rc.fallback_context_strategy == strategy

    def test_invalid_strategy_raises(self):
        with pytest.raises(Exception):
            RetryConfig(fallback_context_strategy="invalid_strategy")

    def test_none_fallback_strategy(self):
        rc = RetryConfig(fallback_context_strategy=None)
        assert rc.fallback_context_strategy is None

    def test_serialization_roundtrip(self):
        rc = RetryConfig(max_retries=2, fallback_context_strategy="own", backoff=10)
        data = rc.model_dump()
        restored = RetryConfig.model_validate(data)
        assert restored.max_retries == 2
        assert restored.fallback_context_strategy == "own"
        assert restored.backoff == 10


# ═══════════════════════════════════════════════════════════════════════
# 2. AgentDefinition WITH RETRY
# ═══════════════════════════════════════════════════════════════════════


class TestAgentDefinitionRetry:

    def test_agent_no_retry_by_default(self):
        agent = AgentDefinition(id="a", runtime="claude", system_prompt="{{ input }}")
        assert agent.retry is None

    def test_agent_with_retry(self):
        agent = AgentDefinition(
            id="a", runtime="claude", system_prompt="{{ input }}",
            retry={"max_retries": 2, "backoff": 3},
        )
        assert agent.retry is not None
        assert agent.retry.max_retries == 2
        assert agent.retry.backoff == 3

    def test_agent_retry_none_explicit(self):
        agent = AgentDefinition(
            id="a", runtime="claude", system_prompt="{{ input }}",
            retry=None,
        )
        assert agent.retry is None

    def test_load_agents_yaml_with_retry(self, tmp_project):
        yaml_content = {
            "agents": [
                {
                    "id": "worker",
                    "runtime": "claude",
                    "system_prompt": "Work: {{ input }}",
                    "retry": {
                        "max_retries": 2,
                        "fallback_context_strategy": "last_only",
                        "backoff": 5,
                    },
                },
            ]
        }
        yaml_path = tmp_project / ".aqm" / "agents.yaml"
        yaml_path.write_text(yaml.dump(yaml_content), encoding="utf-8")

        agents = load_agents(yaml_path)
        assert agents["worker"].retry is not None
        assert agents["worker"].retry.max_retries == 2
        assert agents["worker"].retry.fallback_context_strategy == "last_only"
        assert agents["worker"].retry.backoff == 5

    def test_load_agents_yaml_without_retry(self, tmp_project):
        yaml_content = {
            "agents": [
                {
                    "id": "worker",
                    "runtime": "claude",
                    "system_prompt": "Work: {{ input }}",
                },
            ]
        }
        yaml_path = tmp_project / ".aqm" / "agents.yaml"
        yaml_path.write_text(yaml.dump(yaml_content), encoding="utf-8")

        agents = load_agents(yaml_path)
        assert agents["worker"].retry is None


# ═══════════════════════════════════════════════════════════════════════
# 3. StageRecord RETRY FIELDS
# ═══════════════════════════════════════════════════════════════════════


class TestStageRecordRetry:

    def test_default_retry_fields(self):
        stage = StageRecord(stage_number=1, agent_id="a")
        assert stage.retry_count == 0
        assert stage.retry_reason is None

    def test_retry_fields_set(self):
        stage = StageRecord(
            stage_number=1, agent_id="a",
            retry_count=2, retry_reason="timeout error",
        )
        assert stage.retry_count == 2
        assert stage.retry_reason == "timeout error"

    def test_serialization_includes_retry(self):
        stage = StageRecord(
            stage_number=1, agent_id="a",
            retry_count=1, retry_reason="cli_missing",
        )
        data = stage.model_dump()
        assert data["retry_count"] == 1
        assert data["retry_reason"] == "cli_missing"

    def test_task_with_retry_stage_serializes(self):
        task = Task(description="test")
        stage = StageRecord(
            stage_number=1, agent_id="a",
            retry_count=3, retry_reason="context_overflow",
        )
        task.add_stage(stage)
        json_str = task.model_dump_json()
        restored = Task.model_validate_json(json_str)
        assert restored.stages[0].retry_count == 3
        assert restored.stages[0].retry_reason == "context_overflow"


# ═══════════════════════════════════════════════════════════════════════
# 4. RuntimeExecutionError
# ═══════════════════════════════════════════════════════════════════════


class TestRuntimeExecutionError:

    def test_default_category(self):
        err = RuntimeExecutionError("oops")
        assert err.error_category == "unknown"
        assert err.partial_output == ""

    def test_custom_category(self):
        err = RuntimeExecutionError("timed out", error_category="timeout")
        assert err.error_category == "timeout"

    def test_partial_output(self):
        err = RuntimeExecutionError("fail", partial_output="partial stuff")
        assert err.partial_output == "partial stuff"

    def test_all_categories(self):
        for cat in ("timeout", "cli_missing", "context_overflow", "unknown"):
            err = RuntimeExecutionError("test", error_category=cat)
            assert err.error_category == cat

    def test_str_message(self):
        err = RuntimeExecutionError("Something went wrong")
        assert str(err) == "Something went wrong"


# ═══════════════════════════════════════════════════════════════════════
# 5. _classify_error HELPERS
# ═══════════════════════════════════════════════════════════════════════


class TestClassifyError:

    def test_claude_classify_timeout(self):
        from aqm.runtime.claude import _classify_error
        assert _classify_error("request timed out", 1) == "timeout"
        assert _classify_error("TIMEOUT exceeded", 1) == "timeout"

    def test_claude_classify_context_overflow(self):
        from aqm.runtime.claude import _classify_error
        assert _classify_error("context window exceeded", 1) == "context_overflow"
        assert _classify_error("token limit reached", 1) == "context_overflow"
        assert _classify_error("prompt too long", 1) == "context_overflow"

    def test_claude_classify_cli_missing(self):
        from aqm.runtime.claude import _classify_error
        assert _classify_error("command not found", 127) == "cli_missing"
        assert _classify_error("no such file or directory", 1) == "cli_missing"
        assert _classify_error("", 127) == "cli_missing"

    def test_claude_classify_unknown(self):
        from aqm.runtime.claude import _classify_error
        assert _classify_error("some random error", 1) == "unknown"
        assert _classify_error("", 1) == "unknown"

    def test_gemini_classify(self):
        from aqm.runtime.gemini import _classify_error
        assert _classify_error("timed out", 1) == "timeout"
        assert _classify_error("context window", 1) == "context_overflow"
        assert _classify_error("not found", 127) == "cli_missing"
        assert _classify_error("other", 1) == "unknown"

    def test_codex_classify(self):
        from aqm.runtime.codex import _classify_error
        assert _classify_error("timeout error", 1) == "timeout"
        assert _classify_error("token limit", 1) == "context_overflow"
        assert _classify_error("", 127) == "cli_missing"
        assert _classify_error("blah", 1) == "unknown"


# ═══════════════════════════════════════════════════════════════════════
# 6. PIPELINE RETRY LOOP
# ═══════════════════════════════════════════════════════════════════════


class TestPipelineRetryLoop:

    def _make_pipeline(self, tmp_project, agent_dict):
        queue = FileQueue(tmp_project / ".aqm" / "file-queue")
        pipeline = Pipeline(agent_dict, queue, tmp_project)
        return pipeline, queue

    def test_no_retry_config_fails_immediately(self, tmp_project):
        """Agent without retry config should fail on first runtime error."""
        agents = {
            "worker": AgentDefinition(
                id="worker", runtime="claude",
                system_prompt="{{ input }}",
                # No retry config
            ),
        }
        pipeline, queue = self._make_pipeline(tmp_project, agents)
        mock_rt = MagicMock()
        mock_rt.name = "mock"
        mock_rt.run.side_effect = RuntimeExecutionError(
            "CLI crashed", error_category="unknown",
        )
        pipeline._runtimes["claude"] = mock_rt

        task = Task(description="no retry")
        queue.push(task, "worker")
        result = pipeline.run_task(task, "worker")

        assert result.status == TaskStatus.failed
        assert mock_rt.run.call_count == 1
        assert result.stages[0].retry_count == 0

    def test_retry_succeeds_on_second_attempt(self, tmp_project):
        """Agent with retry should succeed if second attempt works."""
        agents = {
            "worker": AgentDefinition(
                id="worker", runtime="claude",
                system_prompt="{{ input }}",
                retry={"max_retries": 2, "backoff": 0},
            ),
        }
        pipeline, queue = self._make_pipeline(tmp_project, agents)
        mock_rt = MagicMock()
        mock_rt.name = "mock"
        # Fail first, succeed second
        mock_rt.run.side_effect = [
            RuntimeExecutionError("timeout", error_category="timeout"),
            "success output",
        ]
        pipeline._runtimes["claude"] = mock_rt

        task = Task(description="retry success")
        queue.push(task, "worker")
        result = pipeline.run_task(task, "worker")

        assert result.status == TaskStatus.completed
        assert mock_rt.run.call_count == 2
        assert result.stages[0].retry_count == 1
        assert "timeout" in (result.stages[0].retry_reason or "")

    def test_retry_exhausted_fails(self, tmp_project):
        """All retry attempts exhausted should fail the task."""
        agents = {
            "worker": AgentDefinition(
                id="worker", runtime="claude",
                system_prompt="{{ input }}",
                retry={"max_retries": 2, "backoff": 0},
            ),
        }
        pipeline, queue = self._make_pipeline(tmp_project, agents)
        mock_rt = MagicMock()
        mock_rt.name = "mock"
        mock_rt.run.side_effect = RuntimeExecutionError(
            "persistent error", error_category="context_overflow",
        )
        pipeline._runtimes["claude"] = mock_rt

        task = Task(description="retry fail")
        queue.push(task, "worker")
        result = pipeline.run_task(task, "worker")

        assert result.status == TaskStatus.failed
        # 1 initial + 2 retries = 3 attempts
        assert mock_rt.run.call_count == 3
        assert result.stages[0].retry_count == 2
        assert "category=context_overflow" in result.stages[0].output_text

    def test_retry_with_partial_output(self, tmp_project):
        """RuntimeExecutionError with partial_output preserves it on final failure."""
        agents = {
            "worker": AgentDefinition(
                id="worker", runtime="claude",
                system_prompt="{{ input }}",
                retry={"max_retries": 1, "backoff": 0},
            ),
        }
        pipeline, queue = self._make_pipeline(tmp_project, agents)
        mock_rt = MagicMock()
        mock_rt.name = "mock"
        mock_rt.run.side_effect = RuntimeExecutionError(
            "timed out", partial_output="partial result here",
            error_category="timeout",
        )
        pipeline._runtimes["claude"] = mock_rt

        task = Task(description="partial out")
        queue.push(task, "worker")
        result = pipeline.run_task(task, "worker")

        assert result.status == TaskStatus.failed
        assert "PARTIAL OUTPUT" in result.stages[0].output_text
        assert "partial result here" in result.stages[0].output_text
        assert "category=timeout" in result.stages[0].output_text

    def test_retry_non_runtime_error_no_retry(self, tmp_project):
        """Non-RuntimeExecutionError should still be retried (general Exception)."""
        agents = {
            "worker": AgentDefinition(
                id="worker", runtime="claude",
                system_prompt="{{ input }}",
                retry={"max_retries": 1, "backoff": 0},
            ),
        }
        pipeline, queue = self._make_pipeline(tmp_project, agents)
        mock_rt = MagicMock()
        mock_rt.name = "mock"
        mock_rt.run.side_effect = RuntimeError("generic crash")
        pipeline._runtimes["claude"] = mock_rt

        task = Task(description="generic error")
        queue.push(task, "worker")
        result = pipeline.run_task(task, "worker")

        assert result.status == TaskStatus.failed
        # Generic exceptions are also retried (the loop catches Exception)
        assert mock_rt.run.call_count == 2

    def test_retry_records_reason_only_on_retry(self, tmp_project):
        """retry_reason should be None when task succeeds on first attempt."""
        agents = {
            "worker": AgentDefinition(
                id="worker", runtime="claude",
                system_prompt="{{ input }}",
                retry={"max_retries": 2, "backoff": 0},
            ),
        }
        pipeline, queue = self._make_pipeline(tmp_project, agents)
        mock_rt = MagicMock()
        mock_rt.name = "mock"
        mock_rt.run.return_value = "first try success"
        pipeline._runtimes["claude"] = mock_rt

        task = Task(description="first try")
        queue.push(task, "worker")
        result = pipeline.run_task(task, "worker")

        assert result.status == TaskStatus.completed
        assert result.stages[0].retry_count == 0
        assert result.stages[0].retry_reason is None

    def test_error_format_backward_compat(self, tmp_project):
        """Error output should contain 'ERROR:' for backward compatibility."""
        agents = {
            "worker": AgentDefinition(
                id="worker", runtime="claude",
                system_prompt="{{ input }}",
            ),
        }
        pipeline, queue = self._make_pipeline(tmp_project, agents)
        mock_rt = MagicMock()
        mock_rt.name = "mock"
        mock_rt.run.side_effect = RuntimeExecutionError(
            "something broke", error_category="unknown",
        )
        pipeline._runtimes["claude"] = mock_rt

        task = Task(description="error format")
        queue.push(task, "worker")
        result = pipeline.run_task(task, "worker")

        assert "ERROR:" in result.stages[0].output_text

    def test_retry_zero_max_retries_means_no_retry(self, tmp_project):
        """retry config with max_retries=0 should behave like no retry."""
        agents = {
            "worker": AgentDefinition(
                id="worker", runtime="claude",
                system_prompt="{{ input }}",
                retry={"max_retries": 0},
            ),
        }
        pipeline, queue = self._make_pipeline(tmp_project, agents)
        mock_rt = MagicMock()
        mock_rt.name = "mock"
        mock_rt.run.side_effect = RuntimeExecutionError("fail")
        pipeline._runtimes["claude"] = mock_rt

        task = Task(description="zero retry")
        queue.push(task, "worker")
        result = pipeline.run_task(task, "worker")

        assert result.status == TaskStatus.failed
        assert mock_rt.run.call_count == 1


# ═══════════════════════════════════════════════════════════════════════
# 7. SCHEMA VALIDATION
# ═══════════════════════════════════════════════════════════════════════


class TestSchemaRetryConfig:

    def test_schema_has_retry_config_definition(self):
        import json
        schema_path = Path(__file__).parent.parent / "schema" / "agents-schema.json"
        if not schema_path.exists():
            pytest.skip("schema not found")
        schema = json.loads(schema_path.read_text())
        assert "RetryConfig" in schema.get("definitions", {})

    def test_schema_agent_has_retry_property(self):
        import json
        schema_path = Path(__file__).parent.parent / "schema" / "agents-schema.json"
        if not schema_path.exists():
            pytest.skip("schema not found")
        schema = json.loads(schema_path.read_text())
        agent_def = schema["definitions"]["AgentDefinition"]
        assert "retry" in agent_def["properties"]

    def test_schema_validates_retry_config(self):
        try:
            import jsonschema
        except ImportError:
            pytest.skip("jsonschema not installed")

        import json
        schema_path = Path(__file__).parent.parent / "schema" / "agents-schema.json"
        if not schema_path.exists():
            pytest.skip("schema not found")

        schema = json.loads(schema_path.read_text())
        data = {
            "agents": [
                {
                    "id": "worker",
                    "runtime": "claude",
                    "system_prompt": "{{ input }}",
                    "retry": {
                        "max_retries": 2,
                        "fallback_context_strategy": "last_only",
                        "backoff": 5,
                    },
                },
            ]
        }
        jsonschema.validate(data, schema)

    def test_schema_rejects_invalid_retry_strategy(self):
        try:
            import jsonschema
        except ImportError:
            pytest.skip("jsonschema not installed")

        import json
        schema_path = Path(__file__).parent.parent / "schema" / "agents-schema.json"
        if not schema_path.exists():
            pytest.skip("schema not found")

        schema = json.loads(schema_path.read_text())
        data = {
            "agents": [
                {
                    "id": "worker",
                    "runtime": "claude",
                    "system_prompt": "{{ input }}",
                    "retry": {
                        "max_retries": 2,
                        "fallback_context_strategy": "INVALID",
                    },
                },
            ]
        }
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(data, schema)

    def test_both_schemas_in_sync(self):
        import json
        schema1 = Path(__file__).parent.parent / "schema" / "agents-schema.json"
        schema2 = Path(__file__).parent.parent / "aqm" / "schema" / "agents-schema.json"
        if not schema1.exists() or not schema2.exists():
            pytest.skip("schema files not found")
        s1 = json.loads(schema1.read_text())
        s2 = json.loads(schema2.read_text())
        # Both should have RetryConfig
        assert "RetryConfig" in s1.get("definitions", {})
        assert "RetryConfig" in s2.get("definitions", {})
        # Both should match
        assert s1["definitions"]["RetryConfig"] == s2["definitions"]["RetryConfig"]
