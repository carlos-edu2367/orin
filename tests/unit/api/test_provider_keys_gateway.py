from agentos.api import ApiServices, AuthenticatedPrincipal, InMemorySecurityService, create_app
from agentos.api.contracts import ApplicationNotFoundError
from fastapi.testclient import TestClient


class _FakeProviderApiKeys:
    def __init__(self) -> None:
        self.rows: dict[int, dict[str, object]] = {}
        self._next_id = 1

    def list_keys(self, query: dict[str, object]) -> list[dict[str, object]]:
        return sorted(self.rows.values(), key=lambda row: row["position"])

    def add_key(self, command: dict[str, object]) -> dict[str, object]:
        row = {"id": self._next_id, "label": command.get("label"), "position": len(self.rows), "status": "active", "cooldown_until": None}
        self.rows[self._next_id] = row
        self._next_id += 1
        return row

    def rename_key(self, command: dict[str, object]) -> dict[str, object]:
        row = self.rows.get(int(command["key_id"]))
        if row is None:
            raise ApplicationNotFoundError(str(command["key_id"]))
        row["label"] = command.get("label")
        return row

    def remove_key(self, command: dict[str, object]) -> None:
        if int(command["key_id"]) not in self.rows:
            raise ApplicationNotFoundError(str(command["key_id"]))
        del self.rows[int(command["key_id"])]

    def reorder_keys(self, command: dict[str, object]) -> list[dict[str, object]]:
        ordered_ids = [int(item) for item in command["ordered_ids"]]
        for position, key_id in enumerate(ordered_ids):
            self.rows[key_id]["position"] = position
        return self.list_keys(command)


AUTH = {"Authorization": "Bearer pat-test"}


def _client(fake: _FakeProviderApiKeys) -> TestClient:
    security = InMemorySecurityService()
    security.add_pat("pat-test", AuthenticatedPrincipal("user-1", "credential-1", frozenset({"api"})))
    app = create_app(ApiServices(security=security, provider_api_keys=fake))
    return TestClient(app)


def test_listing_keys_for_a_provider_with_none_returns_an_empty_array() -> None:
    client = _client(_FakeProviderApiKeys())

    response = client.get("/v1/providers/openai/keys", headers=AUTH)

    assert response.status_code == 200
    assert response.json() == []


def test_adding_a_key_returns_it_without_the_plaintext() -> None:
    client = _client(_FakeProviderApiKeys())

    response = client.post(
        "/v1/providers/openai/keys", json={"api_key": "sk-abcdefgh", "label": "conta free 1"},
        headers={**AUTH, "Idempotency-Key": "add-1"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["label"] == "conta free 1"
    assert "api_key" not in response.text and "sk-abcdefgh" not in response.text


def test_renaming_a_missing_key_is_a_404() -> None:
    client = _client(_FakeProviderApiKeys())

    response = client.patch(
        "/v1/providers/openai/keys/999", json={"label": "x"},
        headers={**AUTH, "Idempotency-Key": "rename-1"},
    )

    assert response.status_code == 404


def test_removing_a_key_is_a_204() -> None:
    fake = _FakeProviderApiKeys()
    client = _client(fake)
    added = client.post(
        "/v1/providers/openai/keys", json={"api_key": "sk-abcdefgh"},
        headers={**AUTH, "Idempotency-Key": "add-1"},
    ).json()

    response = client.delete(f"/v1/providers/openai/keys/{added['id']}", headers={**AUTH, "Idempotency-Key": "remove-1"})

    assert response.status_code == 204
    assert client.get("/v1/providers/openai/keys", headers=AUTH).json() == []


def test_reordering_rejects_an_unsupported_provider_name() -> None:
    import pytest

    client = _client(_FakeProviderApiKeys())

    with pytest.raises(ValueError):
        client.put(
            "/v1/providers/not-a-provider/keys:reorder", json={"ordered_ids": [1]},
            headers={**AUTH, "Idempotency-Key": "reorder-1"},
        )


def test_a_request_without_a_bearer_token_is_rejected() -> None:
    client = _client(_FakeProviderApiKeys())

    response = client.get("/v1/providers/openai/keys")

    assert response.status_code == 401
