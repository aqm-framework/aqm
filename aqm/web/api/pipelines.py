"""Pipeline CRUD API router."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)


class CreatePipelineRequest(BaseModel):
    name: str
    content: str


class UpdatePipelineRequest(BaseModel):
    content: str


class DuplicateRequest(BaseModel):
    new_name: str


class SetDefaultRequest(BaseModel):
    name: str


class AgentRequest(BaseModel):
    id: str
    name: str = ""
    runtime: str = "claude"
    system_prompt: str = ""
    handoffs: list[dict] = []
    gate: dict | None = None
    mcp: list[dict] = []
    context_strategy: str = "both"
    human_input: dict | None = None


def create_pipelines_router(project_root: Path) -> APIRouter:
    from aqm.core.project import (
        delete_pipeline,
        get_default_pipeline,
        get_pipeline_path,
        list_pipelines,
        save_pipeline,
        set_default_pipeline,
    )

    router = APIRouter()

    @router.get("/api/pipelines")
    async def api_list_pipelines():
        pipelines = list_pipelines(project_root)
        default = get_default_pipeline(project_root) or "default"
        result = []
        for name in pipelines:
            try:
                path = get_pipeline_path(project_root, name)
                agent_count = 0
                content = path.read_text(encoding="utf-8")
                agent_count = content.count("- id:")
                result.append({
                    "name": name,
                    "agent_count": agent_count,
                    "is_default": name == default,
                })
            except FileNotFoundError:
                result.append({"name": name, "agent_count": 0, "is_default": name == default})
        return {"pipelines": result, "default": default}

    @router.get("/api/pipelines/{name}")
    async def api_get_pipeline(name: str):
        try:
            path = get_pipeline_path(project_root, name)
        except FileNotFoundError:
            raise HTTPException(404, f"Pipeline '{name}' not found")
        content = path.read_text(encoding="utf-8")
        default = get_default_pipeline(project_root) or "default"
        return {
            "name": name,
            "content": content,
            "is_default": name == default,
        }

    @router.post("/api/pipelines")
    async def api_create_pipeline(req: CreatePipelineRequest):
        pipelines = list_pipelines(project_root)
        if req.name in pipelines:
            raise HTTPException(409, f"Pipeline '{req.name}' already exists")
        if not req.name.strip():
            raise HTTPException(400, "Pipeline name cannot be empty")
        save_pipeline(project_root, req.name, req.content)
        return {"name": req.name, "status": "created"}

    @router.put("/api/pipelines/{name}")
    async def api_update_pipeline(name: str, req: UpdatePipelineRequest):
        try:
            get_pipeline_path(project_root, name)
        except FileNotFoundError:
            raise HTTPException(404, f"Pipeline '{name}' not found")
        save_pipeline(project_root, name, req.content)
        return {"name": name, "status": "updated"}

    @router.delete("/api/pipelines/{name}")
    async def api_delete_pipeline(name: str):
        try:
            delete_pipeline(project_root, name)
        except FileNotFoundError:
            raise HTTPException(404, f"Pipeline '{name}' not found")
        except ValueError as e:
            raise HTTPException(400, str(e))
        return {"name": name, "status": "deleted"}

    @router.post("/api/pipelines/{name}/duplicate")
    async def api_duplicate_pipeline(name: str, req: DuplicateRequest):
        try:
            path = get_pipeline_path(project_root, name)
        except FileNotFoundError:
            raise HTTPException(404, f"Pipeline '{name}' not found")
        pipelines = list_pipelines(project_root)
        if req.new_name in pipelines:
            raise HTTPException(409, f"Pipeline '{req.new_name}' already exists")
        content = path.read_text(encoding="utf-8")
        save_pipeline(project_root, req.new_name, content)
        return {"name": req.new_name, "status": "created"}

    @router.get("/api/pipelines/{name}/yaml")
    async def api_download_yaml(name: str):
        try:
            path = get_pipeline_path(project_root, name)
        except FileNotFoundError:
            raise HTTPException(404, f"Pipeline '{name}' not found")
        content = path.read_text(encoding="utf-8")
        return PlainTextResponse(
            content,
            media_type="text/yaml",
            headers={"Content-Disposition": f"attachment; filename={name}.yaml"},
        )

    @router.post("/api/pipelines/default")
    async def api_set_default(req: SetDefaultRequest):
        pipelines = list_pipelines(project_root)
        if req.name not in pipelines:
            raise HTTPException(404, f"Pipeline '{req.name}' not found")
        set_default_pipeline(project_root, req.name)
        return {"default": req.name}

    # ── Agent CRUD within a pipeline ─────────────────────────────────

    def _load_pipeline_yaml(name: str) -> tuple[dict, str]:
        """Load and parse pipeline YAML. Returns (data, raw_content)."""
        import yaml as _yaml
        try:
            path = get_pipeline_path(project_root, name)
        except FileNotFoundError:
            raise HTTPException(404, f"Pipeline '{name}' not found")
        content = path.read_text(encoding="utf-8")
        data = _yaml.safe_load(content) or {}
        return data, content

    def _save_pipeline_yaml(name: str, data: dict) -> None:
        """Serialize data back to YAML and save."""
        import yaml as _yaml
        content = _yaml.dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False)
        save_pipeline(project_root, name, content)

    @router.get("/api/pipelines/{name}/agents")
    async def api_list_agents(name: str):
        data, _ = _load_pipeline_yaml(name)
        agents = data.get("agents", [])
        return [
            {
                "id": a.get("id", ""),
                "name": a.get("name", ""),
                "runtime": a.get("runtime", ""),
                "system_prompt": a.get("system_prompt", ""),
                "handoffs": a.get("handoffs", []),
                "gate": a.get("gate"),
                "mcp": a.get("mcp", []),
                "context_strategy": a.get("context_strategy", "both"),
                "human_input": a.get("human_input"),
            }
            for a in agents
        ]

    @router.post("/api/pipelines/{name}/agents")
    async def api_add_agent(name: str, req: AgentRequest):
        data, _ = _load_pipeline_yaml(name)
        agents = data.get("agents", [])
        # Check for duplicate ID
        if any(a.get("id") == req.id for a in agents):
            raise HTTPException(409, f"Agent '{req.id}' already exists")
        agent_dict = {"id": req.id, "name": req.name, "runtime": req.runtime, "system_prompt": req.system_prompt}
        if req.handoffs:
            agent_dict["handoffs"] = req.handoffs
        if req.gate:
            agent_dict["gate"] = req.gate
        if req.mcp:
            agent_dict["mcp"] = req.mcp
        if req.context_strategy != "both":
            agent_dict["context_strategy"] = req.context_strategy
        if req.human_input:
            agent_dict["human_input"] = req.human_input
        agents.append(agent_dict)
        data["agents"] = agents
        _save_pipeline_yaml(name, data)
        return {"status": "added", "agent_id": req.id}

    @router.put("/api/pipelines/{name}/agents/{agent_id}")
    async def api_update_agent(name: str, agent_id: str, req: AgentRequest):
        data, _ = _load_pipeline_yaml(name)
        agents = data.get("agents", [])
        idx = next((i for i, a in enumerate(agents) if a.get("id") == agent_id), None)
        if idx is None:
            raise HTTPException(404, f"Agent '{agent_id}' not found")
        agent_dict = {"id": req.id, "name": req.name, "runtime": req.runtime, "system_prompt": req.system_prompt}
        if req.handoffs:
            agent_dict["handoffs"] = req.handoffs
        if req.gate:
            agent_dict["gate"] = req.gate
        if req.mcp:
            agent_dict["mcp"] = req.mcp
        if req.context_strategy != "both":
            agent_dict["context_strategy"] = req.context_strategy
        if req.human_input:
            agent_dict["human_input"] = req.human_input
        agents[idx] = agent_dict
        data["agents"] = agents
        _save_pipeline_yaml(name, data)
        return {"status": "updated", "agent_id": req.id}

    @router.delete("/api/pipelines/{name}/agents/{agent_id}")
    async def api_delete_agent(name: str, agent_id: str):
        data, _ = _load_pipeline_yaml(name)
        agents = data.get("agents", [])
        new_agents = [a for a in agents if a.get("id") != agent_id]
        if len(new_agents) == len(agents):
            raise HTTPException(404, f"Agent '{agent_id}' not found")
        data["agents"] = new_agents
        _save_pipeline_yaml(name, data)
        return {"status": "deleted", "agent_id": agent_id}

    return router
