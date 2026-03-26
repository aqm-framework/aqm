"""Registry API endpoints — search, pull, publish with version support."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from aqm.core.project import get_agents_yaml_path, save_pipeline

logger = logging.getLogger(__name__)


class PullRequest(BaseModel):
    pipeline_name: str
    version: Optional[str] = None
    repo: Optional[str] = None
    offline: bool = False


class PublishRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    version: Optional[str] = None
    local_only: bool = False


def create_registry_router(project_root: Path) -> APIRouter:
    router = APIRouter()
    agents_yaml_path = get_agents_yaml_path(project_root)

    @router.get("/api/registry/search")
    async def api_search(
        query: Optional[str] = Query(None),
        offline: bool = Query(False),
    ):
        from aqm.registry import search_github, DEFAULT_REGISTRY_REPO, list_local_versions

        results = []

        # GitHub search
        if not offline:
            github_results = search_github(query=query, repo=DEFAULT_REGISTRY_REPO)
            for m in github_results:
                results.append({
                    "name": m.name,
                    "description": m.description,
                    "author": m.author,
                    "version": m.version,
                    "versions": m.versions,
                    "latest": m.latest,
                    "tags": m.tags,
                    "agents_count": m.agents_count,
                    "source": "github",
                })

        # Local registry (versioned)
        seen = {r["name"] for r in results}
        local_dir = Path.home() / ".aqm" / "registry"
        if local_dir.is_dir():
            for d in sorted(local_dir.iterdir()):
                if not d.is_dir():
                    continue
                local_versions = list_local_versions(d.name)
                # Check if there's any version or legacy agents.yaml
                has_content = local_versions or (d / "agents.yaml").exists()
                if has_content and d.name not in seen:
                    desc = ""
                    # Try to read meta from latest version
                    if local_versions:
                        latest_v = local_versions[-1]
                        meta_path = d / latest_v / "meta.json"
                        if meta_path.exists():
                            try:
                                meta = json.loads(meta_path.read_text("utf-8"))
                                desc = meta.get("description", "")
                            except Exception:
                                pass
                    if not query or query.lower() in d.name.lower() or query.lower() in desc.lower():
                        results.append({
                            "name": d.name,
                            "description": desc,
                            "author": "",
                            "version": local_versions[-1] if local_versions else "",
                            "versions": local_versions,
                            "latest": local_versions[-1] if local_versions else "",
                            "tags": [],
                            "agents_count": 0,
                            "source": "local",
                        })

        return results

    @router.post("/api/registry/pull")
    async def api_pull(req: PullRequest):
        import yaml as _yaml
        from aqm.registry import (
            DEFAULT_REGISTRY_REPO,
            parse_name_version,
            pull_from_github,
            pull_from_local,
            save_to_local_registry,
        )

        name, inline_version = parse_name_version(req.pipeline_name)
        version = req.version or inline_version

        content = None
        source = ""
        pulled_version = ""

        # GitHub
        if not req.offline:
            result = pull_from_github(name, version=version, repo=req.repo or DEFAULT_REGISTRY_REPO)
            if result:
                content, meta = result
                pulled_version = meta.version or version or ""
                source = "github"

        # Local registry
        if content is None:
            result = pull_from_local(name, version=version)
            if result:
                content, meta = result
                pulled_version = meta.version or version or ""
                source = "local"

        if content is None:
            raise HTTPException(404, f"Pipeline '{name}' not found")

        # Save to project pipelines
        save_pipeline(project_root, name, content)

        # Cache in local registry
        if pulled_version:
            save_to_local_registry(name, pulled_version, content)

        data = _yaml.safe_load(content)
        agents_count = len(data.get("agents", []))

        return {
            "success": True,
            "pipeline_name": name,
            "version": pulled_version,
            "source": source,
            "agents_count": agents_count,
        }

    @router.post("/api/registry/publish")
    async def api_publish(req: PublishRequest):
        import yaml as _yaml
        from aqm.registry import (
            DEFAULT_REGISTRY_REPO,
            increment_version,
            list_versions,
            publish_to_github,
            save_to_local_registry,
        )

        if not agents_yaml_path.exists():
            raise HTTPException(400, "No agents.yaml found")

        content = agents_yaml_path.read_text(encoding="utf-8")
        data = _yaml.safe_load(content)

        if not isinstance(data, dict) or "agents" not in data:
            raise HTTPException(400, "Invalid agents.yaml")

        pipeline_name = req.name or project_root.name
        agents_count = len(data.get("agents", []))

        # Determine version
        version = req.version
        if not version:
            existing = list_versions(pipeline_name, repo=DEFAULT_REGISTRY_REPO)
            all_v = sorted(set(existing.get("github", []) + existing.get("local", [])))
            version = increment_version(all_v[-1]) if all_v else "1.0.0"

        # Save to local registry (versioned)
        meta_dict = {
            "name": pipeline_name,
            "description": req.description or "",
            "version": version,
            "agents_count": agents_count,
        }
        ver_dir = save_to_local_registry(pipeline_name, version, content, meta_dict)

        result = {
            "success": True,
            "name": pipeline_name,
            "version": version,
            "agents_count": agents_count,
            "location": str(ver_dir),
        }

        # GitHub publish
        if not req.local_only:
            pub_result = publish_to_github(
                agents_yaml_path=agents_yaml_path,
                pipeline_name=pipeline_name,
                description=req.description or "",
                version=version,
                repo=DEFAULT_REGISTRY_REPO,
            )
            if pub_result.success:
                result["pr_url"] = pub_result.pr_url
            else:
                result["github_error"] = pub_result.error

        return result

    @router.get("/api/registry/{name}/versions")
    async def api_list_versions(
        name: str,
        offline: bool = Query(False),
    ):
        from aqm.registry import DEFAULT_REGISTRY_REPO, list_versions as _list_versions

        if offline:
            from aqm.registry import list_local_versions
            return {"name": name, "versions": list_local_versions(name), "github": [], "local": list_local_versions(name)}

        result = _list_versions(name, repo=DEFAULT_REGISTRY_REPO)
        return {
            "name": name,
            "github": result.get("github", []),
            "local": result.get("local", []),
            "versions": sorted(set(result.get("github", []) + result.get("local", []))),
        }

    return router
