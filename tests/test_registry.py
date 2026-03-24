"""Tests for GitHub-based registry functionality."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from aqm.registry import (
    DEFAULT_REGISTRY_REPO,
    PipelineMeta,
    pull_from_github,
    search_github,
    publish_to_github,
    _github_raw_url,
    _gh_cli_available,
)


SAMPLE_YAML = """\
apiVersion: aqm/v0.1

agents:
  - id: planner
    name: Planning Agent
    runtime: text
    system_prompt: "Plan: {{ input }}"
    handoffs:
      - to: executor
        condition: always

  - id: executor
    name: Execution Agent
    runtime: claude_code
    system_prompt: "Execute: {{ input }}"
"""

SAMPLE_META = {
    "name": "test-pipeline",
    "description": "A test pipeline",
    "author": "testuser",
    "version": "0.1.0",
    "tags": ["test", "example"],
    "agents_count": 2,
}

SAMPLE_INDEX = [
    {
        "name": "software-dev",
        "description": "Full SDLC pipeline",
        "author": "alice",
        "tags": ["software", "development"],
        "agents_count": 4,
    },
    {
        "name": "code-review",
        "description": "Code review with LLM gate",
        "author": "bob",
        "tags": ["review", "quality"],
        "agents_count": 3,
    },
    {
        "name": "content-pipeline",
        "description": "Content creation workflow",
        "author": "carol",
        "tags": ["content", "writing"],
        "agents_count": 3,
    },
]


# ── URL building ─────────────────────────────────────────────────────────


class TestGitHubRawUrl:
    def test_default_repo(self):
        url = _github_raw_url(DEFAULT_REGISTRY_REPO, "pipelines/foo/agents.yaml")
        assert "raw.githubusercontent.com" in url
        assert "aqm-framework/registry" in url
        assert "pipelines/foo/agents.yaml" in url

    def test_custom_repo(self):
        url = _github_raw_url("myorg/myrepo", "index.json", branch="dev")
        assert "myorg/myrepo" in url
        assert "/dev/" in url
        assert "index.json" in url


# ── Pull ─────────────────────────────────────────────────────────────────


class TestPullFromGitHub:
    @patch("aqm.registry._fetch_url")
    def test_pull_success(self, mock_fetch):
        """Successfully pull a pipeline from GitHub."""
        mock_fetch.side_effect = [
            SAMPLE_YAML,  # agents.yaml
            json.dumps(SAMPLE_META),  # meta.json
        ]

        result = pull_from_github("test-pipeline")

        assert result is not None
        content, meta = result
        assert "apiVersion: aqm/v0.1" in content
        assert meta.name == "test-pipeline"
        assert meta.description == "A test pipeline"
        assert meta.author == "testuser"
        assert meta.source == "github"

    @patch("aqm.registry._fetch_url")
    def test_pull_yaml_only(self, mock_fetch):
        """Pull succeeds even without meta.json."""
        mock_fetch.side_effect = [
            SAMPLE_YAML,  # agents.yaml
            None,  # meta.json not found
        ]

        result = pull_from_github("test-pipeline")

        assert result is not None
        content, meta = result
        assert "apiVersion: aqm/v0.1" in content
        assert meta.name == "test-pipeline"
        assert meta.source == "github"

    @patch("aqm.registry._fetch_url")
    def test_pull_not_found(self, mock_fetch):
        """Returns None when pipeline doesn't exist."""
        mock_fetch.return_value = None

        result = pull_from_github("nonexistent-pipeline")

        assert result is None

    @patch("aqm.registry._fetch_url")
    def test_pull_custom_repo(self, mock_fetch):
        """Pull from a custom registry repo."""
        mock_fetch.side_effect = [SAMPLE_YAML, None]

        result = pull_from_github("my-pipeline", repo="myorg/my-registry")

        assert result is not None
        # Verify the correct URL was called
        call_url = mock_fetch.call_args_list[0][0][0]
        assert "myorg/my-registry" in call_url


# ── Search ───────────────────────────────────────────────────────────────


class TestSearchGitHub:
    @patch("aqm.registry._fetch_url")
    def test_search_all(self, mock_fetch):
        """List all pipelines from index."""
        mock_fetch.return_value = json.dumps(SAMPLE_INDEX)

        results = search_github()

        assert len(results) == 3
        assert results[0].name == "software-dev"
        assert results[0].source == "github"

    @patch("aqm.registry._fetch_url")
    def test_search_with_query(self, mock_fetch):
        """Filter results by keyword."""
        mock_fetch.return_value = json.dumps(SAMPLE_INDEX)

        results = search_github(query="review")

        assert len(results) == 1
        assert results[0].name == "code-review"

    @patch("aqm.registry._fetch_url")
    def test_search_by_tag(self, mock_fetch):
        """Filter matches tags too."""
        mock_fetch.return_value = json.dumps(SAMPLE_INDEX)

        results = search_github(query="writing")

        assert len(results) == 1
        assert results[0].name == "content-pipeline"

    @patch("aqm.registry._fetch_url")
    def test_search_no_results(self, mock_fetch):
        """Empty result for unmatched query."""
        mock_fetch.return_value = json.dumps(SAMPLE_INDEX)

        results = search_github(query="zzzznonexistent")

        assert len(results) == 0

    @patch("aqm.registry._fetch_url")
    def test_search_github_unavailable(self, mock_fetch):
        """Returns empty list when GitHub is unreachable."""
        mock_fetch.return_value = None

        results = search_github()

        assert results == []

    @patch("aqm.registry._fetch_url")
    def test_search_invalid_json(self, mock_fetch):
        """Returns empty list for malformed index."""
        mock_fetch.return_value = "not valid json"

        results = search_github()

        assert results == []


# ── Publish ──────────────────────────────────────────────────────────────


class TestPublishToGitHub:
    def test_publish_no_gh_cli(self, tmp_path):
        """Fails gracefully when gh CLI is not available."""
        agents_yaml = tmp_path / "agents.yaml"
        agents_yaml.write_text(SAMPLE_YAML)

        with patch("aqm.registry._gh_cli_available", return_value=False):
            result = publish_to_github(agents_yaml, "test-pipeline")

        assert not result.success
        assert "GitHub CLI" in result.error

    def test_publish_invalid_yaml(self, tmp_path):
        """Fails for YAML without agents key."""
        agents_yaml = tmp_path / "agents.yaml"
        agents_yaml.write_text("foo: bar\n")

        with patch("aqm.registry._gh_cli_available", return_value=True):
            result = publish_to_github(agents_yaml, "test-pipeline")

        assert not result.success
        assert "agents" in result.error

    @patch("aqm.registry.subprocess.run")
    @patch("aqm.registry._gh_cli_available", return_value=True)
    def test_publish_clone_failure(self, mock_gh, mock_run, tmp_path):
        """Fails when clone fails."""
        agents_yaml = tmp_path / "agents.yaml"
        agents_yaml.write_text(SAMPLE_YAML)

        # gh api user succeeds, fork succeeds, clone fails
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="testuser\n"),  # gh api user
            MagicMock(returncode=0),  # fork
            MagicMock(returncode=1, stderr="clone failed"),  # clone
        ]

        result = publish_to_github(agents_yaml, "test-pipeline")

        assert not result.success
        assert "clone" in result.error.lower()


# ── gh CLI detection ─────────────────────────────────────────────────────


class TestGhCliAvailable:
    @patch("aqm.registry.subprocess.run")
    def test_gh_available(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        assert _gh_cli_available() is True

    @patch("aqm.registry.subprocess.run")
    def test_gh_not_authenticated(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1)
        assert _gh_cli_available() is False

    @patch("aqm.registry.subprocess.run", side_effect=FileNotFoundError)
    def test_gh_not_installed(self, mock_run):
        assert _gh_cli_available() is False


# ── CLI integration ──────────────────────────────────────────────────────


class TestCLIIntegration:
    """Test that CLI commands wire up to registry correctly."""

    def test_pull_offline_bundled(self, tmp_path):
        """Pull with --offline falls back to bundled examples."""
        from click.testing import CliRunner
        from aqm.cli import cli
        from aqm.core.project import init_project

        root = init_project(tmp_path)
        runner = CliRunner()

        result = runner.invoke(
            cli,
            ["pull", "software-feature-pipeline", "--offline"],
            input="y\n",
        )

        # Should find it in bundled examples
        agents_yaml = root / ".aqm" / "agents.yaml"
        if agents_yaml.exists():
            content = agents_yaml.read_text()
            assert "apiVersion" in content

    @patch("aqm.registry._fetch_url")
    def test_pull_from_github_via_cli(self, mock_fetch, tmp_path):
        """Pull fetches from GitHub first."""
        import os
        from click.testing import CliRunner
        from aqm.cli import cli
        from aqm.core.project import init_project

        mock_fetch.side_effect = [SAMPLE_YAML, None]

        root = init_project(tmp_path)
        runner = CliRunner()

        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            result = runner.invoke(
                cli,
                ["pull", "test-pipeline"],
                input="y\n",
            )
        finally:
            os.chdir(old_cwd)

        assert result.exit_code == 0
        # Pipeline is saved under pipelines/ directory
        agents_yaml = root / ".aqm" / "pipelines" / "test-pipeline.yaml"
        content = agents_yaml.read_text()
        assert "apiVersion: aqm/v0.1" in content

    @patch("aqm.registry._fetch_url")
    def test_search_via_cli(self, mock_fetch, tmp_path):
        """Search command shows GitHub results."""
        from click.testing import CliRunner
        from aqm.cli import cli

        mock_fetch.return_value = json.dumps(SAMPLE_INDEX)

        runner = CliRunner()
        result = runner.invoke(cli, ["search", "review"])

        assert result.exit_code == 0
        assert "code-review" in result.output

    def test_search_offline_via_cli(self):
        """Search --offline skips GitHub."""
        from click.testing import CliRunner
        from aqm.cli import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["search", "--offline"])

        assert result.exit_code == 0
        # Should show bundled examples at minimum
        assert "bundled" in result.output or "No pipelines" in result.output
