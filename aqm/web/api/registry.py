"""Registry API endpoints — search, pull, publish."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from aqm.core.project import get_agents_yaml_path

logger = logging.getLogger(__name__)


class PullRequest(BaseModel):
    pipeline_name: str
    repo: Optional[str] = None
    offline: bool = False


class PublishRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    local_only: bool = False


def create_registry_router(project_root: Path) -> APIRouter:
    router = APIRouter()
    agents_yaml_path = get_agents_yaml_path(project_root)

    @router.get("/api/registry/search")
    async def api_search(
        query: Optional[str] = Query(None),
        offline: bool = Query(False),
    ):
        from aqm.registry import search_github, DEFAULT_REGISTRY_REPO

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
                    "tags": m.tags,
                    "agents_count": m.agents_count,
                    "source": "github",
                })

        # Local registry
        seen = {r["name"] for r in results}
        local_dir = Path.home() / ".aqm" / "registry"
        if local_dir.is_dir():
            for d in sorted(local_dir.iterdir()):
                if d.is_dir() and (d / "agents.yaml").exists():
                    if d.name not in seen:
                        desc = ""
                        meta_path = d / "meta.json"
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
                                "version": "",
                                "tags": [],
                                "agents_count": 0,
                                "source": "local",
                            })

        return results

    @router.post("/api/registry/pull")
    async def api_pull(req: PullRequest):
        import yaml as _yaml
        from aqm.registry import pull_from_github, DEFAULT_REGISTRY_REPO

        content = None
        source = ""

        # GitHub
        if not req.offline:
            result = pull_from_github(req.pipeline_name, repo=req.repo or DEFAULT_REGISTRY_REPO)
            if result:
                content, meta = result
                source = "github"

        # Local registry
        if content is None:
            local_path = Path.home() / ".aqm" / "registry" / req.pipeline_name / "agents.yaml"
            if local_path.exists():
                content = local_path.read_text(encoding="utf-8")
                source = "local"

        if content is None:
            raise HTTPException(404, f"Pipeline '{req.pipeline_name}' not found")

        # Write to project
        agents_yaml_path.write_text(content, encoding="utf-8")

        # Count agents
        data = _yaml.safe_load(content)
        agents_count = len(data.get("agents", []))

        return {
            "success": True,
            "pipeline_name": req.pipeline_name,
            "source": source,
            "agents_count": agents_count,
        }

    @router.post("/api/registry/publish")
    async def api_publish(req: PublishRequest):
        import yaml as _yaml

        if not agents_yaml_path.exists():
            raise HTTPException(400, "No agents.yaml found")

        content = agents_yaml_path.read_text(encoding="utf-8")
        data = _yaml.safe_load(content)

        if not isinstance(data, dict) or "agents" not in data:
            raise HTTPException(400, "Invalid agents.yaml")

        pipeline_name = req.name or project_root.name
        agents_count = len(data.get("agents", []))

        # Save to local registry
        local_dir = Path.home() / ".aqm" / "registry" / pipeline_name
        local_dir.mkdir(parents=True, exist_ok=True)
        (local_dir / "agents.yaml").write_text(content, encoding="utf-8")

        meta = {
            "name": pipeline_name,
            "description": req.description or "",
            "agents_count": agents_count,
        }
        (local_dir / "meta.json").write_text(
            json.dumps(meta, indent=2) + "\n", encoding="utf-8"
        )

        result = {
            "success": True,
            "name": pipeline_name,
            "agents_count": agents_count,
            "location": str(local_dir),
        }

        # GitHub publish
        if not req.local_only:
            from aqm.registry import publish_to_github, DEFAULT_REGISTRY_REPO
            pub_result = publish_to_github(
                agents_yaml_path=agents_yaml_path,
                pipeline_name=pipeline_name,
                description=req.description or "",
                repo=DEFAULT_REGISTRY_REPO,
            )
            if pub_result.success:
                result["pr_url"] = pub_result.pr_url
            else:
                result["github_error"] = pub_result.error

        return result

    return router
