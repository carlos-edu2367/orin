from fastapi.testclient import TestClient

from agentos.api import ApiServices, AuthenticatedPrincipal, InMemorySecurityService, create_app


class Skills:
    def list(self, query):
        assert query["query"] == "debug"
        return {"items": [{"id": "systematic-debugging", "name": "Systematic Debugging", "description": "Debug failures.", "version": "1.0.0", "tags": ["debugging"], "source": "builtin", "available": True}], "next_cursor": None}

    def get(self, query):
        return {**self.list({"query": "debug"})["items"][0], "instructions": "# Workflow", "dependencies": ["testing"], "requires_tools": ["read_file"], "versions": ["1.0.0"]}

    def create(self, command):
        assert command["user_id"] == "user-1"
        return {"id": "custom-debug", "name": command["name"], "description": command["description"], "version": command["version"], "tags": command["tags"], "source": "custom", "available": True}

    def update(self, command):
        return self.get({"skill_id": command["skill_id"]})

    def agent_skills(self, query):
        return {"mode": "auto", "items": []}

    def set_agent_skills(self, command):
        assert command["mode"] == "pinned"
        assert command["skill_ids"] == ["systematic-debugging"]
        return {"mode": "pinned", "items": [self.list({"query": "debug"})["items"][0]]}

    def agents_for_skill(self, query):
        return {"items": [{"agent_id": "agent-1", "mode": "pinned"}]}


def _client() -> TestClient:
    security = InMemorySecurityService()
    security.add_pat("pat", AuthenticatedPrincipal("user-1", "credential-1", frozenset({"api"})))
    return TestClient(create_app(ApiServices(security=security, skills=Skills())))


def test_skills_api_lists_compact_metadata_and_creates_a_custom_skill() -> None:
    client = _client()
    headers = {"Authorization": "Bearer pat", "Idempotency-Key": "skill-1"}

    listed = client.get("/v1/skills?query=debug&limit=20", headers=headers)
    created = client.post("/v1/skills", headers=headers, json={"name": "Custom Debug", "description": "Debug internal failures.", "version": "1.0.0", "tags": ["debugging"], "instructions": "# Workflow"})

    assert listed.status_code == 200
    assert set(listed.json()["items"][0]) == {"id", "name", "description", "version", "tags", "source", "available"}
    assert created.status_code == 201
    assert created.json()["source"] == "custom"


def test_skills_api_manages_agent_auto_discovery_and_pinned_skills() -> None:
    client = _client()
    headers = {"Authorization": "Bearer pat", "Idempotency-Key": "skill-agent-1"}

    current = client.get("/v1/agents/agent-1/skills", headers=headers)
    pinned = client.put("/v1/agents/agent-1/skills", headers=headers, json={"mode": "pinned", "skill_ids": ["systematic-debugging"]})
    used_by = client.get("/v1/skills/systematic-debugging/agents", headers=headers)

    assert current.json() == {"mode": "auto", "items": []}
    assert pinned.json()["mode"] == "pinned"
    assert used_by.json()["items"] == [{"agent_id": "agent-1", "mode": "pinned"}]
