"""GitHub-based pipeline registry.

Provides pull, search, and publish operations using a GitHub repository
as the central pipeline registry. Falls back to bundled examples when
GitHub is unavailable.

Default registry: aqm-framework/registry
Structure:
    pipelines/
        <pipeline-name>/
            agents.yaml
            meta.json
    index.json
"""

from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError

logger = logging.getLogger(__name__)

DEFAULT_REGISTRY_REPO = "aqm-framework/registry"
REGISTRY_BRANCH = "main"

# GitHub raw content base URL
_RAW_BASE = "https://raw.githubusercontent.com/{repo}/{branch}"


@dataclass
class PipelineMeta:
    """Pipeline metadata from registry."""

    name: str
    description: str = ""
    author: str = ""
    version: str = ""
    tags: list[str] = field(default_factory=list)
    agents_count: int = 0
    source: str = ""  # "github", "local", "bundled"


def _github_raw_url(repo: str, path: str, branch: str = REGISTRY_BRANCH) -> str:
    """Build a GitHub raw content URL."""
    base = _RAW_BASE.format(repo=repo, branch=branch)
    return f"{base}/{path}"


def _fetch_url(url: str, timeout: int = 15) -> str | None:
    """Fetch a URL and return its content, or None on failure."""
    try:
        req = Request(url, headers={"User-Agent": "aqm-cli"})
        with urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8")
    except (URLError, OSError, TimeoutError) as e:
        logger.debug("Failed to fetch %s: %s", url, e)
        return None


def _gh_cli_available() -> bool:
    """Check if the GitHub CLI (gh) is installed and authenticated."""
    try:
        result = subprocess.run(
            ["gh", "auth", "status"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


# ── Pull ─────────────────────────────────────────────────────────────────


def pull_from_github(
    pipeline_name: str,
    repo: str = DEFAULT_REGISTRY_REPO,
) -> tuple[str, PipelineMeta] | None:
    """Pull a pipeline YAML from the GitHub registry.

    Returns (yaml_content, meta) or None if not found.
    """
    # Try fetching agents.yaml
    yaml_url = _github_raw_url(repo, f"pipelines/{pipeline_name}/agents.yaml")
    content = _fetch_url(yaml_url)
    if content is None:
        return None

    # Try fetching meta.json (optional)
    meta_url = _github_raw_url(repo, f"pipelines/{pipeline_name}/meta.json")
    meta_content = _fetch_url(meta_url)

    meta = PipelineMeta(name=pipeline_name, source="github")
    if meta_content:
        try:
            data = json.loads(meta_content)
            meta = PipelineMeta(
                name=data.get("name", pipeline_name),
                description=data.get("description", ""),
                author=data.get("author", ""),
                version=data.get("version", ""),
                tags=data.get("tags", []),
                agents_count=data.get("agents_count", 0),
                source="github",
            )
        except json.JSONDecodeError:
            pass

    return content, meta


# ── Search ───────────────────────────────────────────────────────────────


def search_github(
    query: str | None = None,
    repo: str = DEFAULT_REGISTRY_REPO,
) -> list[PipelineMeta]:
    """Search the GitHub registry for pipelines.

    Fetches index.json from the registry and filters by query.
    """
    index_url = _github_raw_url(repo, "index.json")
    content = _fetch_url(index_url)
    if content is None:
        return []

    try:
        entries = json.loads(content)
    except json.JSONDecodeError:
        logger.debug("Failed to parse index.json")
        return []

    if not isinstance(entries, list):
        return []

    results: list[PipelineMeta] = []
    for entry in entries:
        meta = PipelineMeta(
            name=entry.get("name", ""),
            description=entry.get("description", ""),
            author=entry.get("author", ""),
            version=entry.get("version", ""),
            tags=entry.get("tags", []),
            agents_count=entry.get("agents_count", 0),
            source="github",
        )
        results.append(meta)

    if query:
        q = query.lower()
        results = [
            m
            for m in results
            if q in m.name.lower()
            or q in m.description.lower()
            or any(q in t.lower() for t in m.tags)
        ]

    return results


# ── Publish ──────────────────────────────────────────────────────────────


@dataclass
class PublishResult:
    """Result of a publish operation."""

    success: bool
    pr_url: str = ""
    error: str = ""


def publish_to_github(
    agents_yaml_path: Path,
    pipeline_name: str,
    description: str = "",
    repo: str = DEFAULT_REGISTRY_REPO,
) -> PublishResult:
    """Publish a pipeline to the GitHub registry via PR.

    Workflow:
        1. Fork the registry repo (if not already forked)
        2. Create a branch
        3. Add pipelines/<name>/agents.yaml and meta.json
        4. Create a PR

    Requires: gh CLI installed and authenticated.
    """
    if not _gh_cli_available():
        return PublishResult(
            success=False,
            error=(
                "GitHub CLI (gh) is required for publishing.\n"
                "  Install: https://cli.github.com\n"
                "  Then run: gh auth login"
            ),
        )

    import yaml as _yaml
    import tempfile
    import shutil

    # Read and validate the YAML
    try:
        content = agents_yaml_path.read_text(encoding="utf-8")
        data = _yaml.safe_load(content)
    except Exception as e:
        return PublishResult(success=False, error=f"Failed to read agents.yaml: {e}")

    if not isinstance(data, dict) or "agents" not in data:
        return PublishResult(
            success=False, error="Invalid agents.yaml: missing 'agents' key"
        )

    # Build meta.json
    agents_list = data.get("agents", [])
    meta = {
        "name": pipeline_name,
        "description": description,
        "version": "0.1.0",
        "tags": [],
        "agents_count": len(agents_list),
    }

    # Get current gh user for author field
    user_result = subprocess.run(
        ["gh", "api", "user", "--jq", ".login"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if user_result.returncode == 0:
        meta["author"] = user_result.stdout.strip()

    branch_name = f"publish/{pipeline_name}"

    # Clone the registry repo to a temp directory
    tmpdir = tempfile.mkdtemp(prefix="aqm-publish-")
    try:
        # Fork (idempotent) and clone
        fork_result = subprocess.run(
            ["gh", "repo", "fork", repo, "--clone=false"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        # fork_result may fail if already forked, that's OK
        logger.debug("Fork result: %s %s", fork_result.stdout, fork_result.stderr)

        # Clone the fork
        clone_result = subprocess.run(
            [
                "gh",
                "repo",
                "clone",
                repo,
                tmpdir,
                "--",
                "--depth=1",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if clone_result.returncode != 0:
            return PublishResult(
                success=False,
                error=f"Failed to clone registry: {clone_result.stderr.strip()}",
            )

        # Create branch
        subprocess.run(
            ["git", "checkout", "-b", branch_name],
            cwd=tmpdir,
            capture_output=True,
            text=True,
        )

        # Create pipeline directory
        pipeline_dir = Path(tmpdir) / "pipelines" / pipeline_name
        pipeline_dir.mkdir(parents=True, exist_ok=True)

        # Write agents.yaml
        (pipeline_dir / "agents.yaml").write_text(content, encoding="utf-8")

        # Write meta.json
        (pipeline_dir / "meta.json").write_text(
            json.dumps(meta, indent=2) + "\n", encoding="utf-8"
        )

        # Git add, commit, push
        subprocess.run(
            ["git", "add", "."],
            cwd=tmpdir,
            capture_output=True,
            text=True,
        )

        commit_msg = f"Add pipeline: {pipeline_name}"
        if description:
            commit_msg += f"\n\n{description}"

        subprocess.run(
            ["git", "commit", "-m", commit_msg],
            cwd=tmpdir,
            capture_output=True,
            text=True,
        )

        push_result = subprocess.run(
            ["git", "push", "-u", "origin", branch_name],
            cwd=tmpdir,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if push_result.returncode != 0:
            return PublishResult(
                success=False,
                error=f"Failed to push: {push_result.stderr.strip()}",
            )

        # Create PR
        pr_body = (
            f"## New Pipeline: {pipeline_name}\n\n"
            f"{description}\n\n"
            f"- Agents: {len(agents_list)}\n"
            f"- Params: {len(data.get('params', {}))}\n"
        )

        pr_result = subprocess.run(
            [
                "gh",
                "pr",
                "create",
                "--repo",
                repo,
                "--title",
                f"Add pipeline: {pipeline_name}",
                "--body",
                pr_body,
                "--head",
                branch_name,
            ],
            cwd=tmpdir,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if pr_result.returncode != 0:
            return PublishResult(
                success=False,
                error=f"Failed to create PR: {pr_result.stderr.strip()}",
            )

        pr_url = pr_result.stdout.strip()
        return PublishResult(success=True, pr_url=pr_url)

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
