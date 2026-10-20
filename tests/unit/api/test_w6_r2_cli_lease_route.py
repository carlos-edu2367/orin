"""W6-R2: `orin code` renews its turn's lease; only its token may, and only for its own turn."""
from __future__ import annotations

from fastapi.testclient import TestClient

from agentos.agentic import cli_lease
from agentos.api.gateway import ApiServices, ApplicationNotFoundError, create_app
from agentos.api.security import LoopbackSecurityService


class _Conversations:
    def get(self, conversation_id, user_id):
        if conversation_id != "chat_1":
            raise ApplicationNotFoundError(conversation_id)
        return {"conversation_id": "chat_1", "project_id": None, "turns": [{"turn_id": "turn_1", "state": "running"}]}


def _client() -> TestClient:
    app = create_app(ApiServices(security=LoopbackSecurityService(api_token=lambda: "tok"), conversation_application=_Conversations()))
    return TestClient(app, client=("127.0.0.1", 40000))


def test_the_lease_route_needs_the_cli_token() -> None:
    response = _client().post("/v1/conversations/chat_1/turns/turn_1/cli-lease", headers={"Idempotency-Key": "h1"})
    assert response.status_code == 403


def test_the_cli_token_renews_its_own_turns_lease() -> None:
    cli_lease.drop("turn_1")
    response = _client().post("/v1/conversations/chat_1/turns/turn_1/cli-lease",
                              headers={"Authorization": "Bearer tok", "Idempotency-Key": "h2"})
    assert response.status_code == 204
    assert cli_lease.expired("turn_1") is False


def test_a_turn_of_another_conversation_or_an_unknown_turn_is_not_found() -> None:
    headers = {"Authorization": "Bearer tok", "Idempotency-Key": "h3"}
    assert _client().post("/v1/conversations/chat_9/turns/turn_1/cli-lease", headers=headers).status_code == 404
    assert _client().post("/v1/conversations/chat_1/turns/turn_9/cli-lease", headers=headers).status_code == 404
