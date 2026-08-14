from fastapi.testclient import TestClient

from agentos.api import ApiServices, AuthenticatedPrincipal, InMemorySecurityService, create_app
from agentos.skills.service import SkillLibraryService


class Skills:
    def list(self, query):
        assert query["query"] == "debug"
        return {"items": [{"id": "systematic-debugging", "name": "Systematic Debugging", "description": "Debug failures.", "version": "1.0.0", "tags": ["debugging"], "source": "builtin", "available": True}], "next_cursor": None}

    def get(self, query):
        return {**self.list({"query": "debug"})["items"][0], "instructions": "# Workflow", "dependencies": ["testing"], "requires_tools": ["read_file"], "versions": ["1.0.0"]}

    def create(self, command):
        assert command["user_id"] == "user-1"
        assert command["when_to_use"] == ["a regression occurs"]
        assert command["dependencies"]["tools"] == ["read_file"]
        return {"id": "custom-debug", "name": command["name"], "description": command["description"], "version": command["version"], "tags": command["tags"], "source": "custom", "available": True}

    def update(self, command):
        return self.get({"skill_id": command["skill_id"]})

    def remove_version(self, command):
        assert command["skill_id"] == "systematic-debugging"
        assert command["version"] == "0.9.0"
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
    created = client.post("/v1/skills", headers=headers, json={"name": "Custom Debug", "description": "Debug internal failures.", "version": "1.0.0", "tags": ["debugging"], "when_to_use": ["a regression occurs"], "when_not_to_use": ["for style review"], "dependencies": {"tools": ["read_file"]}, "instructions": "# Workflow"})

    assert listed.status_code == 200
    assert set(listed.json()["items"][0]) == {"id", "name", "description", "version", "tags", "source", "available"}
    assert created.status_code == 201
    assert created.json()["source"] == "custom"


def test_skills_api_rejects_a_custom_skill_with_unavailable_requirements() -> None:
    security = InMemorySecurityService()
    security.add_pat("pat", AuthenticatedPrincipal("user-1", "credential-1", frozenset({"api"})))
    client = TestClient(create_app(ApiServices(security=security, skills=SkillLibraryService(builtins=()))))
    headers = {"Authorization": "Bearer pat", "Idempotency-Key": "skill-unavailable-1"}

    created = client.post(
        "/v1/skills", headers=headers,
        json={"name": "PDF Extractor", "description": "Extract PDF data.", "requires_tools": ["extract_pdf_text"], "instructions": "# Workflow"},
    )
    assert created.status_code == 422


def test_skills_api_manages_agent_auto_discovery_and_pinned_skills() -> None:
    client = _client()
    headers = {"Authorization": "Bearer pat", "Idempotency-Key": "skill-agent-1"}

    current = client.get("/v1/agents/agent-1/skills", headers=headers)
    pinned = client.put("/v1/agents/agent-1/skills", headers=headers, json={"mode": "pinned", "skill_ids": ["systematic-debugging"]})
    used_by = client.get("/v1/skills/systematic-debugging/agents", headers=headers)

    assert current.json() == {"mode": "auto", "items": []}
    assert pinned.json()["mode"] == "pinned"
    assert used_by.json()["items"] == [{"agent_id": "agent-1", "mode": "pinned"}]


def test_skills_api_removes_a_requested_old_version() -> None:
    client = _client()
    response = client.delete(
        "/v1/skills/systematic-debugging/versions/0.9.0",
        headers={"Authorization": "Bearer pat", "Idempotency-Key": "skill-version-remove-1"},
    )

    assert response.status_code == 200
    assert response.json()["id"] == "systematic-debugging"
