from __future__ import annotations

from fastapi.testclient import TestClient

from agentos.api.events import InMemoryClientEventStream
from agentos.api.gateway import ApiServices, create_app
from agentos.api.security import AuthenticatedPrincipal, InMemorySecurityService
from agentos.providers.http import OpenRouterHTTPAdapter, ProviderHTTPSettings
from agentos.providers.models import AuthorizedProviderInvocationQuery, GenerationSucceeded, ProviderRef
from tests.fixtures.agentic.provider_server import DeterministicProviderServer, make_provider_request


def _client(*, maximum_requests: int = 120) -> tuple[TestClient, InMemorySecurityService, str, InMemoryClientEventStream]:
    security = InMemorySecurityService(maximum_requests=maximum_requests)
    token = "pat-agentic-boundary"
    security.add_pat(token, AuthenticatedPrincipal("user:agentic", "credential:agentic", frozenset({"api"})))
    events = InMemoryClientEventStream()
    app = create_app(ApiServices(security=security, events=events))
    return TestClient(app), security, token, events


def test_rate_limit_returns_retryable_public_envelope_without_credential() -> None:
    client, _, token, _ = _client(maximum_requests=1)
    headers = {"Authorization": f"Bearer {token}"}

    first = client.post("/v1/events/streams", headers=headers, json={"execution_ids": ["execution:agentic"]})
    second = client.post("/v1/events/streams", headers=headers, json={"execution_ids": ["execution:agentic"]})

    assert first.status_code == 201
    assert second.status_code == 429
    assert second.json()["error"]["retryable"] is True
    assert token not in second.text


def test_cursor_is_monotonic_tamper_evident_and_owned() -> None:
    client, security, owner_token, events_service = _client()
    headers = {"Authorization": f"Bearer {owner_token}"}
    opened = client.post("/v1/events/streams", headers=headers, json={"execution_ids": ["execution:agentic"]})
    assert opened.status_code == 201

    events_service.append("execution:agentic", "AgenticFixture", {"content": "safe"})
    stream_id, cursor = opened.json()["stream_id"], opened.json()["cursor"]
    read = client.post(f"/v1/events/streams/{stream_id}/read", headers=headers, json={"cursor": cursor})
    assert read.status_code == 200
    next_cursor = read.json()["cursor"]
    again = client.post(f"/v1/events/streams/{stream_id}/read", headers=headers, json={"cursor": next_cursor})
    assert again.status_code == 200
    assert again.json()["events"] == []

    tampered = client.post(f"/v1/events/streams/{stream_id}/read", headers=headers, json={"cursor": next_cursor[:-1] + ("0" if next_cursor[-1] != "0" else "1")})
    assert tampered.status_code == 409
    assert tampered.json()["error"]["code"] == "cursor_invalid"
    assert "signature" not in tampered.text

    stranger_token = "pat-agentic-stranger"
    security.add_pat(stranger_token, AuthenticatedPrincipal("user:stranger", "credential:stranger", frozenset({"api"})))
    stranger = client.post(f"/v1/events/streams/{stream_id}/read", headers={"Authorization": f"Bearer {stranger_token}"}, json={"cursor": next_cursor})
    assert stranger.status_code == 403


def test_provider_secret_stays_at_http_edge_and_public_state_is_redacted() -> None:
    secret = "sk-agentic-redaction-secret"
    with DeterministicProviderServer(secret=secret) as provider:
        adapter = OpenRouterHTTPAdapter(ProviderHTTPSettings(ProviderRef("provider:deterministic"), provider.base_url, secret, "deterministic-model"))
        request = make_provider_request(invocation_id="invocation:redaction")
        try:
            outcome = adapter.generate(request)
            snapshot = adapter.inspect(AuthorizedProviderInvocationQuery(request.context, request.invocation_id))
            public_state = repr(outcome) + repr(snapshot) + repr(adapter)
        finally:
            adapter.close()

    assert isinstance(outcome, GenerationSucceeded)
    assert provider.requests[0]["headers"]["authorization"] == f"Bearer {secret}"
    assert secret not in public_state
