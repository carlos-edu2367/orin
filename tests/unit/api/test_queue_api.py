from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from agentos.agentic.settings import AgentRuntimeSettingsStore
from agentos.api.gateway import ApiServices, create_app
from agentos.api.security import AuthenticatedPrincipal, InMemorySecurityService


class StubApp:
    def __init__(self) -> None:
        self.queue: list[dict] = []

    def get(self, conversation_id, user_id):
        return {"conversation_id": conversation_id, "title": "T", "state": "running", "provider": "openai", "model_id": "m",
                "messages": [], "turns": [], "activities": [], "activity_cursor": "0"}

    def enqueue(self, user_id, conversation_id, message, idempotency_key):
        item = {"queued": True, "queue_id": f"q{len(self.queue) + 1}", "state": "pending", "content": message}
        self.queue.append(item)
        return item

    def queued_inputs(self, conversation_id, user_id):
        return [item for item in self.queue if item["state"] == "pending"]

    def cancel_queued_input(self, conversation_id, user_id, queue_id):
        for item in self.queue:
            if item["queue_id"] == queue_id and item["state"] == "pending":
                item["state"] = "cancelled"
                return True
        return False


def _client(tmp_path: Path) -> tuple[TestClient, dict[str, str]]:
    security = InMemorySecurityService()
    security.add_pat("pat", AuthenticatedPrincipal(user_id="local-user", credential_ref="pat-1", scopes=frozenset({"api"})))
    services = ApiServices(
        security=security, conversation_application=StubApp(),  # type: ignore[arg-type]
        agentic_runtime=AgentRuntimeSettingsStore(tmp_path / "runtime.json"), workspace_root=tmp_path / "workspaces",
    )
    return TestClient(create_app(services)), {"Authorization": "Bearer pat"}


def test_queue_round_trip(tmp_path: Path) -> None:
    client, headers = _client(tmp_path)
    created = client.post("/v1/conversations/conv-1/queue", json={"message": "also do X"}, headers={**headers, "Idempotency-Key": "q-1"})
    assert created.status_code == 201 and created.json()["queued"] is True
    listed = client.get("/v1/conversations/conv-1/queue", headers=headers)
    assert listed.status_code == 200 and [item["content"] for item in listed.json()["items"]] == ["also do X"]
    queue_id = created.json()["queue_id"]
    assert client.delete(f"/v1/conversations/conv-1/queue/{queue_id}", headers=headers).status_code == 200
    assert client.delete(f"/v1/conversations/conv-1/queue/{queue_id}", headers=headers).status_code == 404


def test_queue_requires_a_message_and_an_idempotency_key(tmp_path: Path) -> None:
    client, headers = _client(tmp_path)
    assert client.post("/v1/conversations/conv-1/queue", json={"message": ""}, headers={**headers, "Idempotency-Key": "q-1"}).status_code in {400, 422}
    assert client.post("/v1/conversations/conv-1/queue", json={"message": "x"}, headers=headers).status_code == 403
