from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from agentos.agentic.settings import AgentRuntimeSettingsStore
from agentos.api.gateway import ApiServices, create_app
from agentos.api.security import AuthenticatedPrincipal, InMemorySecurityService


def _client_with_conversation(tmp_path: Path) -> tuple[TestClient, str, dict[str, str]]:
    """Same wiring as tests/unit/test_orin_code_phase_b.py (gateway permission endpoints)."""

    class StubConversationApp:
        def get(self, conversation_id: str, user_id: str) -> dict[str, object]:
            return {
                "conversation_id": conversation_id,
                "title": "Test",
                "state": "running",
                "provider": "openai",
                "model_id": "gpt-4o",
                "messages": [],
                "turns": [],
                "activities": [],
                "activity_cursor": "0",
            }

    security = InMemorySecurityService()
    security.add_pat(
        "test-pat",
        AuthenticatedPrincipal(user_id="local-user", credential_ref="pat-1", scopes=frozenset({"api"})),
    )
    services = ApiServices(
        security=security,
        conversation_application=StubConversationApp(),  # type: ignore[arg-type]
        agentic_runtime=AgentRuntimeSettingsStore(tmp_path / "runtime.json"),
        workspace_root=tmp_path / "workspaces",
    )
    return TestClient(create_app(services)), "conv-trust", {"Authorization": "Bearer test-pat"}


def test_trust_endpoints_round_trip(tmp_path: Path) -> None:
    client, conversation_id, auth_headers = _client_with_conversation(tmp_path)
    initial = client.get(f"/v1/conversations/{conversation_id}/workspace-trust", headers=auth_headers)
    assert initial.status_code == 200
    assert initial.json()["trusted"] is False
    path = initial.json()["path"]
    assert isinstance(path, str) and path

    response = client.post(
        f"/v1/conversations/{conversation_id}/workspace-trust",
        headers={**auth_headers, "Idempotency-Key": "trust-1"},
    )
    assert response.status_code == 200 and response.json()["trusted"] is True
    assert response.json()["path"] == path
    after = client.get(f"/v1/conversations/{conversation_id}/workspace-trust", headers=auth_headers)
    assert after.json()["trusted"] is True
