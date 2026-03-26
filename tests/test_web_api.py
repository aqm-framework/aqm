"""Web API tests — FastAPI TestClient for all endpoints.

Tests all REST API endpoints for tasks, pipelines, chunks, agents,
registry, validation, and SSE.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from aqm.core.agent import AgentDefinition
from aqm.core.project import init_project, save_pipeline, set_default_pipeline
from aqm.core.task import Task, TaskStatus
from aqm.queue.file import FileQueue


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def project(tmp_path):
    """Create a temporary aqm project with a default pipeline."""
    root = init_project(tmp_path)
    yaml_content = yaml.dump({
        "agents": [
            {
                "id": "writer",
                "name": "Writer",
                "runtime": "claude",
                "system_prompt": "Write: {{ input }}",
                "handoffs": [{"to": "reviewer"}],
            },
            {
                "id": "reviewer",
                "name": "Reviewer",
                "runtime": "claude",
                "system_prompt": "Review: {{ input }}",
            },
        ]
    })
    save_pipeline(root, "default", yaml_content)
    set_default_pipeline(root, "default")
    return root


@pytest.fixture
def client(project):
    """FastAPI TestClient for the aqm web app."""
    try:
        from fastapi.testclient import TestClient
    except ImportError:
        pytest.skip("fastapi not installed")
    from aqm.web.app import create_app
    app = create_app(project)
    return TestClient(app)


# ═══════════════════════════════════════════════════════════════════════
# HTML PAGES
# ═══════════════════════════════════════════════════════════════════════


class TestHTMLPages:

    def test_dashboard(self, client):
        r = client.get("/")
        assert r.status_code == 200
        assert "Dashboard" in r.text
        assert "sidebar" in r.text  # new sidebar layout

    def test_agents_page(self, client):
        r = client.get("/agents")
        assert r.status_code == 200
        assert "Agent Pipeline" in r.text

    def test_pipelines_page(self, client):
        r = client.get("/pipelines")
        assert r.status_code == 200
        assert "Pipelines" in r.text
        assert "default" in r.text

    def test_pipelines_edit(self, client):
        r = client.get("/pipelines?edit=default")
        assert r.status_code == 200
        assert "Editing: default" in r.text
        assert "yamlEditor" in r.text

    def test_task_detail(self, client):
        # Create a task first
        r = client.post("/api/tasks", json={"description": "Test task"})
        assert r.status_code == 200
        task_id = r.json()["id"]
        r = client.get(f"/tasks/{task_id}")
        assert r.status_code == 200
        assert task_id in r.text
        assert "breadcrumb" in r.text  # breadcrumbs

    def test_task_detail_not_found(self, client):
        r = client.get("/tasks/T-NONEXISTENT")
        assert r.status_code == 404

    def test_registry_page(self, client):
        r = client.get("/registry")
        assert r.status_code == 200
        assert "Registry" in r.text

    def test_validate_page(self, client):
        r = client.get("/validate")
        assert r.status_code == 200
        assert "Validate" in r.text

    def test_sidebar_navigation_links(self, client):
        r = client.get("/")
        assert 'href="/"' in r.text
        assert 'href="/agents"' in r.text
        assert 'href="/pipelines"' in r.text
        assert 'href="/registry"' in r.text
        assert 'href="/validate"' in r.text

    def test_theme_toggle_present(self, client):
        r = client.get("/")
        assert "toggleTheme" in r.text
        assert "data-theme" in r.text or "aqm-theme" in r.text

    def test_theme_toggle_button(self, client):
        r = client.get("/")
        assert "toggleTheme" in r.text


# ═══════════════════════════════════════════════════════════════════════
# TASK API
# ═══════════════════════════════════════════════════════════════════════


class TestTaskAPI:

    def test_create_task(self, client):
        r = client.post("/api/tasks", json={"description": "Test"})
        assert r.status_code == 200
        assert "id" in r.json()

    def test_list_tasks(self, client):
        client.post("/api/tasks", json={"description": "Task 1"})
        client.post("/api/tasks", json={"description": "Task 2"})
        r = client.get("/api/tasks")
        assert r.status_code == 200
        assert len(r.json()) >= 2

    def test_list_tasks_with_filter(self, client):
        r = client.get("/api/tasks?status=completed")
        assert r.status_code == 200

    def test_get_task(self, client):
        r = client.post("/api/tasks", json={"description": "Detail test"})
        task_id = r.json()["id"]
        r = client.get(f"/api/tasks/{task_id}")
        assert r.status_code == 200
        assert r.json()["id"] == task_id

    def test_get_task_not_found(self, client):
        r = client.get("/api/tasks/T-NONEXIST")
        assert r.status_code == 404

    def test_set_priority(self, client):
        r = client.post("/api/tasks", json={"description": "Prio test"})
        task_id = r.json()["id"]
        r = client.post(f"/api/tasks/{task_id}/priority", json={"priority": "high"})
        assert r.status_code == 200

    def test_cancel_task(self, client):
        r = client.post("/api/tasks", json={"description": "Cancel test"})
        task_id = r.json()["id"]
        r = client.post(f"/api/tasks/{task_id}/cancel", json={})
        assert r.status_code == 200
        assert r.json()["status"] == "cancelled"

    def test_cancel_with_reason(self, client):
        r = client.post("/api/tasks", json={"description": "Cancel reason"})
        task_id = r.json()["id"]
        r = client.post(f"/api/tasks/{task_id}/cancel", json={"reason": "No longer needed"})
        assert r.status_code == 200
        # Verify reason stored
        task = client.get(f"/api/tasks/{task_id}").json()
        assert task["metadata"].get("cancel_reason") == "No longer needed"

    def test_cancel_already_cancelled(self, client):
        r = client.post("/api/tasks", json={"description": "Double cancel"})
        task_id = r.json()["id"]
        client.post(f"/api/tasks/{task_id}/cancel", json={})
        r = client.post(f"/api/tasks/{task_id}/cancel", json={})
        assert r.status_code == 400

    def test_restart_task(self, client):
        r = client.post("/api/tasks", json={"description": "Restart test"})
        task_id = r.json()["id"]
        # Cancel first to make it restartable
        client.post(f"/api/tasks/{task_id}/cancel", json={})
        r = client.post(f"/api/tasks/{task_id}/restart", json={})
        assert r.status_code == 200
        assert r.json()["status"] == "restarting"

    def test_restart_with_from_stage(self, client):
        r = client.post("/api/tasks", json={"description": "Restart stage"})
        task_id = r.json()["id"]
        client.post(f"/api/tasks/{task_id}/cancel", json={})
        r = client.post(f"/api/tasks/{task_id}/restart", json={"from_stage": 1})
        assert r.status_code == 200


# ═══════════════════════════════════════════════════════════════════════
# CONTEXT API
# ═══════════════════════════════════════════════════════════════════════


class TestContextAPI:

    def test_get_context_not_found(self, client):
        r = client.get("/api/tasks/T-NONEXIST/context")
        assert r.status_code == 404

    def test_get_context(self, client, project):
        # Create a task and write context
        from aqm.core.project import get_tasks_dir
        from aqm.core.context_file import ContextFile
        r = client.post("/api/tasks", json={"description": "Context test"})
        task_id = r.json()["id"]
        task_dir = get_tasks_dir(project) / task_id
        task_dir.mkdir(parents=True, exist_ok=True)
        ctx = ContextFile(task_dir)
        ctx.append_stage(
            stage_number=1, agent_id="writer", task_name="write",
            status="completed", input_text="hello", output_text="world",
        )
        r = client.get(f"/api/tasks/{task_id}/context")
        assert r.status_code == 200
        assert "text/plain" in r.headers["content-type"]
        assert "stage 1" in r.text


# ═══════════════════════════════════════════════════════════════════════
# PIPELINE API
# ═══════════════════════════════════════════════════════════════════════


class TestPipelineAPI:

    def test_list_pipelines(self, client):
        r = client.get("/api/pipelines")
        assert r.status_code == 200
        data = r.json()
        assert "pipelines" in data
        assert "default" in data
        assert any(p["name"] == "default" for p in data["pipelines"])

    def test_get_pipeline(self, client):
        r = client.get("/api/pipelines/default")
        assert r.status_code == 200
        data = r.json()
        assert data["name"] == "default"
        assert "content" in data
        assert "agents" in data["content"]

    def test_get_pipeline_not_found(self, client):
        r = client.get("/api/pipelines/nonexistent")
        assert r.status_code == 404

    def test_create_pipeline(self, client):
        content = yaml.dump({"agents": [{"id": "a", "runtime": "claude", "system_prompt": "hi"}]})
        r = client.post("/api/pipelines", json={"name": "new-pipe", "content": content})
        assert r.status_code == 200
        assert r.json()["status"] == "created"
        # Verify it exists
        r = client.get("/api/pipelines/new-pipe")
        assert r.status_code == 200

    def test_create_duplicate_pipeline(self, client):
        r = client.post("/api/pipelines", json={"name": "default", "content": "agents: []"})
        assert r.status_code == 409

    def test_update_pipeline(self, client):
        new_content = yaml.dump({"agents": [{"id": "updated", "runtime": "claude", "system_prompt": "x"}]})
        r = client.put("/api/pipelines/default", json={"content": new_content})
        assert r.status_code == 200
        # Verify content updated
        r = client.get("/api/pipelines/default")
        assert "updated" in r.json()["content"]

    def test_update_nonexistent(self, client):
        r = client.put("/api/pipelines/nope", json={"content": "x"})
        assert r.status_code == 404

    def test_delete_pipeline(self, client):
        # Create a second pipeline first
        client.post("/api/pipelines", json={"name": "temp", "content": "agents: []"})
        r = client.delete("/api/pipelines/temp")
        assert r.status_code == 200

    def test_delete_last_pipeline_rejected(self, client):
        r = client.delete("/api/pipelines/default")
        assert r.status_code == 400
        assert "only pipeline" in r.json()["detail"].lower() or "Cannot delete" in r.json()["detail"]

    def test_duplicate_pipeline(self, client):
        r = client.post("/api/pipelines/default/duplicate", json={"new_name": "copy-of-default"})
        assert r.status_code == 200
        # Verify copy exists
        r = client.get("/api/pipelines/copy-of-default")
        assert r.status_code == 200

    def test_set_default_pipeline(self, client):
        client.post("/api/pipelines", json={"name": "alt", "content": "agents: []"})
        r = client.post("/api/pipelines/default", json={"name": "alt"})
        assert r.status_code == 200
        assert r.json()["default"] == "alt"

    def test_download_yaml(self, client):
        r = client.get("/api/pipelines/default/yaml")
        assert r.status_code == 200
        assert "text/yaml" in r.headers["content-type"]
        assert "attachment" in r.headers.get("content-disposition", "")


# ═══════════════════════════════════════════════════════════════════════
# AGENTS API
# ═══════════════════════════════════════════════════════════════════════


class TestAgentsAPI:

    def test_get_agents(self, client):
        r = client.get("/api/agents")
        assert r.status_code == 200
        agents = r.json()
        assert len(agents) == 2
        ids = [a["id"] for a in agents]
        assert "writer" in ids
        assert "reviewer" in ids

    def test_get_agents_with_pipeline(self, client):
        r = client.get("/api/agents?pipeline=default")
        assert r.status_code == 200
        assert len(r.json()) == 2

    def test_get_agents_fields(self, client):
        r = client.get("/api/agents")
        agent = r.json()[0]
        assert "id" in agent
        assert "name" in agent
        assert "runtime" in agent
        assert "handoffs" in agent
        assert "mcp" in agent


# ═══════════════════════════════════════════════════════════════════════
# CHUNKS API
# ═══════════════════════════════════════════════════════════════════════


class TestChunksAPI:

    def _create_task(self, client):
        r = client.post("/api/tasks", json={"description": "Chunk test"})
        return r.json()["id"]

    def test_list_chunks_empty(self, client):
        task_id = self._create_task(client)
        r = client.get(f"/api/tasks/{task_id}/chunks")
        assert r.status_code == 200
        assert r.json() == []

    def test_add_chunk(self, client):
        task_id = self._create_task(client)
        r = client.post(f"/api/tasks/{task_id}/chunks", json={"description": "Build login"})
        assert r.status_code == 200

    def test_add_and_list_chunks(self, client):
        task_id = self._create_task(client)
        client.post(f"/api/tasks/{task_id}/chunks", json={"description": "Chunk A"})
        client.post(f"/api/tasks/{task_id}/chunks", json={"description": "Chunk B"})
        r = client.get(f"/api/tasks/{task_id}/chunks")
        assert len(r.json()) == 2

    def test_update_chunk_status(self, client):
        task_id = self._create_task(client)
        client.post(f"/api/tasks/{task_id}/chunks", json={"description": "X"})
        chunks = client.get(f"/api/tasks/{task_id}/chunks").json()
        chunk_id = chunks[0]["id"]
        r = client.patch(f"/api/tasks/{task_id}/chunks/{chunk_id}", json={"status": "done"})
        assert r.status_code == 200

    def test_delete_chunk(self, client):
        task_id = self._create_task(client)
        client.post(f"/api/tasks/{task_id}/chunks", json={"description": "Del"})
        chunks = client.get(f"/api/tasks/{task_id}/chunks").json()
        chunk_id = chunks[0]["id"]
        r = client.delete(f"/api/tasks/{task_id}/chunks/{chunk_id}")
        assert r.status_code == 200
        # Verify deleted
        chunks = client.get(f"/api/tasks/{task_id}/chunks").json()
        assert len(chunks) == 0


# ═══════════════════════════════════════════════════════════════════════
# VALIDATE API
# ═══════════════════════════════════════════════════════════════════════


class TestValidateAPI:

    def test_validate_valid_yaml(self, client):
        r = client.post("/api/validate")
        assert r.status_code == 200
        # Default pipeline should be valid
        data = r.json()
        assert data.get("valid") is True or "agent_count" in data

    def test_validate_with_content(self, client):
        yaml_content = yaml.dump({
            "agents": [{"id": "a", "name": "A", "runtime": "claude", "system_prompt": "{{ input }}"}]
        })
        r = client.post("/api/validate", json={"yaml_content": yaml_content})
        assert r.status_code == 200
