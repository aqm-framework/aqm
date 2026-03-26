"""GitHub-based pipeline registry with semantic versioning.

Provides pull, search, publish, and version management using a GitHub
repository as the central pipeline registry. Falls back to local cache
when GitHub is unavailable.

Default registry: aqm-framework/registry
Structure (versioned):
    pipelines/
        <pipeline-name>/
            versions.json       # {"versions":["1.0.0","1.1.0"],"latest":"1.1.0"}
            v1.0.0/
                agents.yaml
                meta.json
            v1.1.0/
                agents.yaml
                meta.json
            agents.yaml         # legacy unversioned (backward compat)
    index.json
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

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
    versions: list[str] = field(default_factory=list)
    latest: str = ""
    tags: list[str] = field(default_factory=list)
    agents_count: int = 0
    source: str = ""  # "github", "local", "bundled"


# ── Version Utilities ─────────────────────────────────────────────────


def parse_name_version(spec: str) -> tuple[str, str | None]:
    """Parse ``name@1.0.0`` into ``('name', '1.0.0')`` or ``('name', None)``."""
    if "@" in spec:
        name, version = spec.rsplit("@", 1)
        return name.strip(), version.strip() or None
    return spec.strip(), None


def increment_version(version: str) -> str:
    """Increment the patch component: ``'1.2.3'`` → ``'1.2.4'``.

    Returns ``'1.0.0'`` for empty or invalid input.
    """
    if not version:
        return "1.0.0"
    parts = version.split(".")
    try:
        parts = [int(p) for p in parts]
    except ValueError:
        return "1.0.0"
    while len(parts) < 3:
        parts.append(0)
    parts[2] += 1
    return ".".join(str(p) for p in parts[:3])


# ── URL helpers ───────────────────────────────────────────────────────


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


# ── Local Registry ────────────────────────────────────────────────────


def _local_registry_dir() -> Path:
    """Return the local registry base directory (~/.aqm/registry/)."""
    d = Path.home() / ".aqm" / "registry"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _migrate_legacy_local(name: str) -> None:
    """Migrate old flat ``{name}/agents.yaml`` to versioned ``{name}/0.1.0/``."""
    base = _local_registry_dir() / name
    legacy_yaml = base / "agents.yaml"
    if legacy_yaml.exists() and not (base / "versions.json").exists():
        ver_dir = base / "0.1.0"
        ver_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(legacy_yaml), str(ver_dir / "agents.yaml"))
        legacy_meta = base / "meta.json"
        if legacy_meta.exists():
            shutil.move(str(legacy_meta), str(ver_dir / "meta.json"))
        # Create versions.json
        (base / "versions.json").write_text(
            json.dumps({"versions": ["0.1.0"], "latest": "0.1.0"}, indent=2),
            encoding="utf-8",
        )
        logger.info("Migrated local registry '%s' to versioned format", name)


def save_to_local_registry(
    name: str,
    version: str,
    content: str,
    meta: dict | None = None,
) -> Path:
    """Save a pipeline to the versioned local registry."""
    base = _local_registry_dir() / name
    ver_dir = base / version
    ver_dir.mkdir(parents=True, exist_ok=True)

    (ver_dir / "agents.yaml").write_text(content, encoding="utf-8")
    if meta:
        (ver_dir / "meta.json").write_text(
            json.dumps(meta, indent=2) + "\n", encoding="utf-8"
        )

    # Update versions.json
    versions_path = base / "versions.json"
    if versions_path.exists():
        try:
            vdata = json.loads(versions_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            vdata = {"versions": [], "latest": ""}
    else:
        vdata = {"versions": [], "latest": ""}

    if version not in vdata["versions"]:
        vdata["versions"].append(version)
        vdata["versions"].sort(key=lambda v: [int(x) for x in v.split(".")] if all(p.isdigit() for p in v.split(".")) else [0])
    vdata["latest"] = version

    versions_path.write_text(json.dumps(vdata, indent=2), encoding="utf-8")
    return ver_dir


def list_local_versions(name: str) -> list[str]:
    """List locally cached versions of a pipeline."""
    base = _local_registry_dir() / name
    _migrate_legacy_local(name)
    versions_path = base / "versions.json"
    if not versions_path.exists():
        return []
    try:
        vdata = json.loads(versions_path.read_text(encoding="utf-8"))
        return vdata.get("versions", [])
    except json.JSONDecodeError:
        return []


def pull_from_local(
    name: str,
    version: str | None = None,
) -> tuple[str, PipelineMeta] | None:
    """Pull a pipeline from the local registry, optionally a specific version."""
    base = _local_registry_dir() / name
    _migrate_legacy_local(name)

    if version is None:
        versions_path = base / "versions.json"
        if versions_path.exists():
            try:
                vdata = json.loads(versions_path.read_text(encoding="utf-8"))
                version = vdata.get("latest", "")
            except json.JSONDecodeError:
                pass
        if not version:
            # Fallback: find any version directory
            for d in sorted(base.iterdir()) if base.exists() else []:
                if d.is_dir() and (d / "agents.yaml").exists():
                    version = d.name
                    break

    if not version:
        return None

    ver_dir = base / version
    yaml_path = ver_dir / "agents.yaml"
    if not yaml_path.exists():
        return None

    content = yaml_path.read_text(encoding="utf-8")
    meta = PipelineMeta(name=name, version=version, source="local")

    meta_path = ver_dir / "meta.json"
    if meta_path.exists():
        try:
            data = json.loads(meta_path.read_text(encoding="utf-8"))
            meta = PipelineMeta(
                name=data.get("name", name),
                description=data.get("description", ""),
                author=data.get("author", ""),
                version=data.get("version", version),
                tags=data.get("tags", []),
                agents_count=data.get("agents_count", 0),
                source="local",
            )
        except json.JSONDecodeError:
            pass

    # Populate versions
    meta.versions = list_local_versions(name)
    meta.latest = version

    return content, meta


# ── Pull (GitHub) ─────────────────────────────────────────────────────


def pull_from_github(
    pipeline_name: str,
    version: str | None = None,
    repo: str = DEFAULT_REGISTRY_REPO,
) -> tuple[str, PipelineMeta] | None:
    """Pull a pipeline YAML from the GitHub registry.

    Args:
        pipeline_name: Pipeline name.
        version: Specific version to pull. None = latest.
        repo: GitHub repo (owner/name).

    Returns:
        (yaml_content, meta) or None if not found.
    """
    resolved_version = version

    # If no version specified, try to discover latest from versions.json
    if resolved_version is None:
        versions_url = _github_raw_url(
            repo, f"pipelines/{pipeline_name}/versions.json"
        )
        versions_content = _fetch_url(versions_url)
        if versions_content:
            try:
                vdata = json.loads(versions_content)
                resolved_version = vdata.get("latest", "")
                if not resolved_version and vdata.get("versions"):
                    resolved_version = vdata["versions"][-1]
            except json.JSONDecodeError:
                pass

    # Try versioned path first
    if resolved_version:
        yaml_url = _github_raw_url(
            repo,
            f"pipelines/{pipeline_name}/v{resolved_version}/agents.yaml",
        )
        content = _fetch_url(yaml_url)
        if content:
            meta = _fetch_meta(
                repo, pipeline_name, resolved_version, versioned=True
            )
            return content, meta

    # Fallback: unversioned legacy path
    yaml_url = _github_raw_url(
        repo, f"pipelines/{pipeline_name}/agents.yaml"
    )
    content = _fetch_url(yaml_url)
    if content is None:
        return None

    meta = _fetch_meta(repo, pipeline_name, "", versioned=False)
    return content, meta


def _fetch_meta(
    repo: str,
    pipeline_name: str,
    version: str,
    versioned: bool,
) -> PipelineMeta:
    """Fetch meta.json from the registry."""
    if versioned and version:
        meta_url = _github_raw_url(
            repo,
            f"pipelines/{pipeline_name}/v{version}/meta.json",
        )
    else:
        meta_url = _github_raw_url(
            repo, f"pipelines/{pipeline_name}/meta.json"
        )

    meta_content = _fetch_url(meta_url)
    meta = PipelineMeta(name=pipeline_name, version=version, source="github")

    if meta_content:
        try:
            data = json.loads(meta_content)
            meta = PipelineMeta(
                name=data.get("name", pipeline_name),
                description=data.get("description", ""),
                author=data.get("author", ""),
                version=data.get("version", version),
                versions=data.get("versions", []),
                latest=data.get("latest", version),
                tags=data.get("tags", []),
                agents_count=data.get("agents_count", 0),
                source="github",
            )
        except json.JSONDecodeError:
            pass

    return meta


# ── List Versions ─────────────────────────────────────────────────────


def list_versions(
    pipeline_name: str,
    repo: str = DEFAULT_REGISTRY_REPO,
    include_local: bool = True,
) -> dict[str, list[str]]:
    """List all available versions of a pipeline.

    Returns:
        ``{"github": ["1.0.0", "1.1.0"], "local": ["1.0.0"]}``
    """
    result: dict[str, list[str]] = {"github": [], "local": []}

    # GitHub versions
    versions_url = _github_raw_url(
        repo, f"pipelines/{pipeline_name}/versions.json"
    )
    versions_content = _fetch_url(versions_url)
    if versions_content:
        try:
            vdata = json.loads(versions_content)
            result["github"] = vdata.get("versions", [])
        except json.JSONDecodeError:
            pass

    # Local versions
    if include_local:
        result["local"] = list_local_versions(pipeline_name)

    return result


# ── Search ────────────────────────────────────────────────────────────


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
            versions=entry.get("versions", []),
            latest=entry.get("latest", entry.get("version", "")),
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


# ── Publish ───────────────────────────────────────────────────────────


@dataclass
class PublishResult:
    """Result of a publish operation."""

    success: bool
    pr_url: str = ""
    error: str = ""
    version: str = ""


def publish_to_github(
    agents_yaml_path: Path,
    pipeline_name: str,
    description: str = "",
    version: str | None = None,
    repo: str = DEFAULT_REGISTRY_REPO,
) -> PublishResult:
    """Publish a pipeline to the GitHub registry via PR.

    Args:
        agents_yaml_path: Path to the agents.yaml file.
        pipeline_name: Pipeline name.
        description: Pipeline description.
        version: Semantic version. None = auto-increment from latest.
        repo: Target GitHub registry repo.

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

    import tempfile

    import yaml as _yaml

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

    # Determine version
    if version is None:
        existing = list_versions(pipeline_name, repo)
        all_versions = sorted(
            set(existing.get("github", []) + existing.get("local", []))
        )
        if all_versions:
            version = increment_version(all_versions[-1])
        else:
            version = "1.0.0"

    # Build meta.json
    agents_list = data.get("agents", [])
    meta = {
        "name": pipeline_name,
        "description": description,
        "version": version,
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

    branch_name = f"publish/{pipeline_name}-v{version}"

    # Clone the registry repo to a temp directory
    tmpdir = tempfile.mkdtemp(prefix="aqm-publish-")
    try:
        # Fork (idempotent) and clone
        subprocess.run(
            ["gh", "repo", "fork", repo, "--clone=false"],
            capture_output=True,
            text=True,
            timeout=30,
        )

        clone_result = subprocess.run(
            ["gh", "repo", "clone", repo, tmpdir, "--", "--depth=1"],
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

        # Create versioned pipeline directory
        pipeline_dir = Path(tmpdir) / "pipelines" / pipeline_name
        ver_dir = pipeline_dir / f"v{version}"
        ver_dir.mkdir(parents=True, exist_ok=True)

        # Write agents.yaml and meta.json to versioned dir
        (ver_dir / "agents.yaml").write_text(content, encoding="utf-8")
        (ver_dir / "meta.json").write_text(
            json.dumps(meta, indent=2) + "\n", encoding="utf-8"
        )

        # Update versions.json
        versions_path = pipeline_dir / "versions.json"
        if versions_path.exists():
            try:
                vdata = json.loads(versions_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                vdata = {"versions": [], "latest": ""}
        else:
            vdata = {"versions": [], "latest": ""}

        if version not in vdata["versions"]:
            vdata["versions"].append(version)
        vdata["latest"] = version
        versions_path.write_text(
            json.dumps(vdata, indent=2) + "\n", encoding="utf-8"
        )

        # Git add, commit, push
        subprocess.run(
            ["git", "add", "."], cwd=tmpdir, capture_output=True, text=True
        )

        commit_msg = f"Add pipeline: {pipeline_name} v{version}"
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
            f"## Pipeline: {pipeline_name} v{version}\n\n"
            f"{description}\n\n"
            f"- Agents: {len(agents_list)}\n"
            f"- Version: {version}\n"
        )

        pr_result = subprocess.run(
            [
                "gh", "pr", "create",
                "--repo", repo,
                "--title", f"Add pipeline: {pipeline_name} v{version}",
                "--body", pr_body,
                "--head", branch_name,
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
        return PublishResult(success=True, pr_url=pr_url, version=version)

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
