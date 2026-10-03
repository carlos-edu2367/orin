"""Profile B must never read, change or observe profile A's data.

Every /v1 route must be classified below. A new route without a class fails
``test_every_route_is_classified`` until someone decides how it is isolated.
"""
import json
from datetime import UTC, datetime

import pytest
from fastapi.routing import APIRoute
from sqlalchemy import insert

from agentos.conversations.chat import PostgresChatStore
from agentos.installation import reset_cached_paths
from agentos.persistence.postgres.agentic_activity import PostgresAgenticActivityStore
from agentos.persistence.postgres.schema import agent_memories, provider_configurations, provider_model_catalog
from agentos.projects.store import PostgresProjectStore
from agentos.scheduler.scheduled_chats import ScheduledChatInput, ScheduledChatService

from tests.integration.server_world import build_server_world

SECRET = "SEGREDO-A"

# A route is OWNED when its path names a resource that belongs to someone;
# B must get 404 (reads) or 404/422 (writes with a placeholder body) and A's
# data must be intact afterwards. LISTING routes must not mention A's data.
# The other classes hold no other profile's data by construction.
OWNED = {
    ("DELETE", "/v1/uploads/{upload_id}"),
    ("GET", "/v1/projects/{project_id}"), ("PATCH", "/v1/projects/{project_id}"),
    ("POST", "/v1/projects/{project_id}/archive"), ("POST", "/v1/projects/{project_id}/conversations"),
    ("GET", "/v1/projects/{project_id}/memories"), ("DELETE", "/v1/projects/{project_id}/memories/{memory_id}"),
    ("DELETE", "/v1/schedules/{schedule_id}"),
    ("DELETE", "/v1/memories/{memory_id}"), ("PATCH", "/v1/memories/{memory_id}"),
    ("GET", "/v1/conversations/{conversation_id}"),
    ("POST", "/v1/conversations/{conversation_id}/workspace/inspect"),
    ("PUT", "/v1/conversations/{conversation_id}/workspace"),
    ("DELETE", "/v1/conversations/{conversation_id}/workspace"),
    ("GET", "/v1/conversations/{conversation_id}/files/{path:path}"),
    ("POST", "/v1/conversations/{conversation_id}/files/{path:path}/open"),
    ("POST", "/v1/conversations/{conversation_id}/messages"),
    ("POST", "/v1/conversations/{conversation_id}/cancel"),
    ("GET", "/v1/conversations/{conversation_id}/overview"),
    ("GET", "/v1/conversations/{conversation_id}/events"),
    ("GET", "/v1/skills/{skill_id}"), ("PUT", "/v1/skills/{skill_id}"),
    ("DELETE", "/v1/skills/{skill_id}/versions/{version}"), ("GET", "/v1/skills/{skill_id}/agents"),
    ("GET", "/v1/mcp/servers/{server_id}"), ("POST", "/v1/mcp/servers/{server_id}/approve"),
    ("POST", "/v1/mcp/servers/{server_id}/test"), ("POST", "/v1/mcp/servers/{server_id}/oauth/start"),
    ("POST", "/v1/mcp/servers/{server_id}/oauth/cancel"), ("PUT", "/v1/mcp/servers/{server_id}/enabled"),
    ("PUT", "/v1/mcp/servers/{server_id}/tools/{tool_name}/enabled"), ("DELETE", "/v1/mcp/servers/{server_id}"),
    ("POST", "/v1/executions/{execution_id}/control"), ("POST", "/v1/executions/{execution_id}/input"),
    ("GET", "/v1/executions/{execution_id}"),
    ("POST", "/v1/plugins/{plugin_id}/approve"), ("PUT", "/v1/plugins/{plugin_id}/enabled"),
    ("PUT", "/v1/plugins/{plugin_id}/hooks-enabled"), ("DELETE", "/v1/plugins/{plugin_id}"),
    ("PATCH", "/v1/providers/{provider}/keys/{key_id}"), ("DELETE", "/v1/providers/{provider}/keys/{key_id}"),
    ("POST", "/v1/events/streams/{stream_id}/read"), ("GET", "/v1/events/streams/{stream_id}"),
    *{("GET", f"/v1/{resource}/{{resource_id}}") for resource in ("agents", "capabilities", "tools", "workspaces", "artifacts")},
}
LISTING = {
    ("GET", "/v1/conversations"), ("GET", "/v1/schedules"), ("GET", "/v1/projects"), ("GET", "/v1/projects/sidebar"),
    ("GET", "/v1/memories"), ("GET", "/v1/skills"), ("GET", "/v1/plugins"), ("GET", "/v1/plugins/commands"),
    ("GET", "/v1/plugins/marketplaces"), ("GET", "/v1/mcp/servers"), ("GET", "/v1/executions"),
    ("GET", "/v1/runtime/quality"), ("GET", "/v1/files"),
    *{("GET", f"/v1/{resource}") for resource in ("agents", "capabilities", "tools", "workspaces", "artifacts")},
}
SCOPED = {
    ("GET", "/v1/providers/{provider}"), ("PUT", "/v1/providers/{provider}"), ("DELETE", "/v1/providers/{provider}"),
    ("GET", "/v1/providers/{provider}/keys"), ("POST", "/v1/providers/{provider}/keys"),
    ("PUT", "/v1/providers/{provider}/keys:reorder"), ("PUT", "/v1/providers/{provider}/keys:cooldown"),
    ("POST", "/v1/providers/{provider}/models:refresh"), ("GET", "/v1/providers/{provider}/models"),
    ("POST", "/v1/providers/{provider}/models"), ("DELETE", "/v1/providers/{provider}/custom-models/{model_id:path}"),
    ("PUT", "/v1/providers/{provider}/favorites/{model_id:path}"), ("DELETE", "/v1/providers/{provider}/favorites/{model_id:path}"),
    ("POST", "/v1/providers/ollama/test"),
    ("GET", "/v1/runtime/settings"), ("PUT", "/v1/runtime/settings"),
    ("GET", "/v1/code-mode/settings"), ("PUT", "/v1/code-mode/settings"),
    ("GET", "/v1/settings/vision-model"), ("PUT", "/v1/settings/vision-model"),
    ("GET", "/v1/files/download"),
    # Skill preferences keyed by (caller, agent_id): an agent id names a role,
    # not a record someone owns, so every profile reads and writes its own row.
    ("GET", "/v1/agents/{agent_id}/skills"), ("PUT", "/v1/agents/{agent_id}/skills"),
}
CREATE = {
    ("POST", "/v1/executions"), ("POST", "/v1/uploads"), ("POST", "/v1/workspaces/inspect"), ("POST", "/v1/conversations"),
    ("POST", "/v1/schedules"), ("POST", "/v1/projects"), ("POST", "/v1/skills"), ("POST", "/v1/plugins/inspect"),
    ("POST", "/v1/plugins/library/infer-mcp"), ("POST", "/v1/plugins/marketplaces"), ("POST", "/v1/mcp/servers"),
    ("POST", "/v1/events/streams"), ("POST", "/v1/files/folders"), ("POST", "/v1/files/import"),
}
INSTANCE = {
    ("GET", "/v1/plugins/library"), ("GET", "/v1/mcp/catalog"), ("GET", "/v1/installation/status"),
    ("DELETE", "/v1/installation/versions/{version}"), ("POST", "/v1/installation/update"),
    ("POST", "/v1/providers/omniroute/test"), ("POST", "/v1/providers/omniroute/install"), ("GET", "/v1/providers/omniroute/install"),
    ("GET", "/v1/providers/omniroute/runtime"), ("PUT", "/v1/providers/omniroute/runtime"), ("POST", "/v1/providers/omniroute/runtime/actions"),
    # Bound to a single-use state value created by the profile that started the sign-in.
    ("GET", "/v1/mcp/oauth/callback"),
}
AUTH = {("GET", "/v1/auth/me"), ("POST", "/v1/auth/setup"), ("POST", "/v1/auth/login"), ("POST", "/v1/auth/logout"), ("POST", "/v1/auth/password")}
ADMIN = {("GET", "/v1/admin/users"), ("POST", "/v1/admin/users"), ("PATCH", "/v1/admin/users/{user_id}"), ("POST", "/v1/admin/users/{user_id}/reset-password")}
CLASSIFIED = OWNED | LISTING | SCOPED | CREATE | INSTANCE | AUTH | ADMIN


@pytest.fixture()
def world(tmp_path, monkeypatch):
    yield build_server_world(tmp_path, monkeypatch)
    reset_cached_paths()


def _routes(app):
    for route in app.routes:
        if isinstance(route, APIRoute) and route.path.startswith("/v1"):
            for method in route.methods - {"HEAD", "OPTIONS"}:
                yield method, route.path


def test_every_route_is_classified(world):
    unclassified = sorted(set(_routes(world.app)) - CLASSIFIED)
    assert not unclassified, f"classify these routes in test_profile_isolation.py: {unclassified}"
    stale = sorted(CLASSIFIED - set(_routes(world.app)))
    assert not stale, f"these classified routes no longer exist: {stale}"


def _seed(world, a):
    now = datetime.now(UTC)
    engine = world.engine
    project = PostgresProjectStore(engine).create(user_id=a.user_id, name=f"{SECRET} projeto", description=None)
    chat = PostgresChatStore(engine, PostgresAgenticActivityStore(engine, "cursor-secret"))
    conversation = chat.create(user_id=a.user_id, message=f"{SECRET} conversa", provider="openrouter", model_id="model-1", idempotency_key="seed-a")
    with engine.begin() as connection:
        connection.execute(insert(agent_memories).values(memory_id="mem_a", user_id=a.user_id, fact=f"{SECRET} memória", tags=[], created_at=now, updated_at=now))
        connection.execute(insert(provider_model_catalog).values(
            user_id=a.user_id, provider="openrouter", model_id="model-1", display_name="Model",
            capabilities=[], input_modalities=[], output_modalities=[], refreshed_at=now, created_at=now, updated_at=now,
        ))
        connection.execute(insert(provider_configurations).values(
            user_id=a.user_id, provider="openrouter", enabled=True, model=None, base_url=None, secret_ref="test",
            key_cooldown_seconds=60, catalog_refreshed_at=now, created_at=now, updated_at=now,
        ))
    schedule = ScheduledChatService(engine).create(a.user_id, ScheduledChatInput(f"{SECRET} agenda", "openrouter", "model-1", "UTC", "hourly"), idempotency_key="seed-a")
    skill = a.api.post("/v1/skills", json={"name": f"{SECRET} skill", "description": "d", "instructions": "faça"}, headers=a.headers())
    assert skill.status_code == 201, skill.text
    server = a.api.post("/v1/mcp/servers", json={"display_name": f"{SECRET} mcp", "transport": "http", "url": "https://mcp.example.com/mcp"}, headers=a.headers())
    assert server.status_code == 201, server.text
    upload = a.api.post("/v1/uploads", files={"file": ("a.txt", f"{SECRET} upload".encode(), "text/plain")}, headers=a.headers())
    assert upload.status_code == 201, upload.text
    a.api.post("/v1/files/folders", json={"parent": "", "name": "segredo-a"}, headers=a.headers())
    return {
        "project_id": project.project_id,
        "conversation_id": conversation.conversation_id,
        "memory_id": "mem_a",
        "schedule_id": schedule["schedule_id"],
        "skill_id": skill.json()["id"],
        "version": "1.0.0",
        "server_id": server.json()["server_id"],
        "tool_name": "anything",
        "upload_id": upload.json()["upload_id"],
        "path": "notes.md",
    }


SEEDED_PARAMS = {"project_id", "conversation_id", "memory_id", "schedule_id", "skill_id", "version", "server_id", "tool_name", "upload_id", "path"}


def _fill(path: str, seeds: dict[str, str]) -> tuple[str, bool]:
    seeded = True
    for segment in path.split("/"):
        if segment.startswith("{") and segment.endswith("}"):
            name = segment[1:-1].split(":")[0]
            value = seeds.get(name)
            if value is None:
                value, seeded = f"missing-{name}", False
            path = path.replace(segment, value)
    return path, seeded


def _snapshot(a, seeds):
    paths = [f"/v1/projects/{seeds['project_id']}", f"/v1/conversations/{seeds['conversation_id']}",
             f"/v1/skills/{seeds['skill_id']}", f"/v1/mcp/servers/{seeds['server_id']}", "/v1/memories", "/v1/schedules"]
    snapshot = {}
    for path in paths:
        response = a.api.get(path)
        assert response.status_code == 200, (path, response.text)
        snapshot[path] = response.json()
    return snapshot


def _without_volatile(value):
    if isinstance(value, dict):
        return {key: _without_volatile(item) for key, item in value.items() if key not in {"updated_at", "last_seen_at", "next_fire_at", "checked_at"}}
    if isinstance(value, list):
        return [_without_volatile(item) for item in value]
    return value


def test_profile_b_cannot_reach_profile_a(world):
    a = world.admin()
    b = world.member("bruno", admin=a)
    seeds = _seed(world, a)
    before = _without_volatile(_snapshot(a, seeds))
    leaks: list[str] = []

    for method, template in sorted(OWNED):
        path, seeded = _fill(template, seeds)
        headers = {**b.headers(), "Content-Type": "application/json"}
        if template.endswith("/events") or template == "/v1/events/streams/{stream_id}":
            with b.api.stream(method, path, headers=headers) as response:
                status, text = response.status_code, ""
        else:
            response = b.api.request(method, path, headers=headers, content=json.dumps({}) if method != "GET" else None)
            status, text = response.status_code, response.text
        if SECRET in text:
            leaks.append(f"{method} {template} leaked A's data")
        if seeded:
            allowed = {404} if method in {"GET", "DELETE"} else {404, 422}
        else:
            allowed = set(range(400, 600))
        if status not in allowed:
            leaks.append(f"{method} {template} answered {status} to profile B")

    for method, template in sorted(LISTING):
        response = b.api.request(method, template, params={"path": ""} if template == "/v1/files" else None)
        if SECRET in response.text or any(str(value) in response.text for key, value in seeds.items() if key in {"project_id", "conversation_id", "skill_id", "server_id", "schedule_id"}):
            leaks.append(f"{method} {template} lists A's data")
        if template == "/v1/files" and "segredo-a" in response.text:
            leaks.append("B sees A's file area")

    assert not leaks, "\n".join(leaks)
    assert _without_volatile(_snapshot(a, seeds)) == before


def test_admin_routes_refuse_a_member(world):
    a = world.admin()
    b = world.member("bruno", admin=a)
    for method, template in sorted(ADMIN):
        path, _ = _fill(template, {"user_id": a.user_id})
        response = b.api.request(method, path, headers={**b.headers(), "Content-Type": "application/json"}, content=json.dumps({}) if method != "GET" else None)
        assert response.status_code in {403, 422}, (method, template, response.status_code)
        if response.status_code == 403:
            assert response.json()["error"]["code"] == "admin_required"


def test_per_profile_settings_do_not_cross(world):
    a = world.admin()
    b = world.member("bruno", admin=a)
    assert a.api.put("/v1/runtime/settings", json={"max_iterations": 7}, headers=a.headers()).status_code == 200
    assert b.api.get("/v1/runtime/settings").json().get("max_iterations") != 7
