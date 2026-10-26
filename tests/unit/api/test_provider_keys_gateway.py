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

    def mark_exhausted(self, key_id: int) -> None:
        row = self.rows.get(int(key_id))
        if row is None:
            raise ApplicationNotFoundError(str(key_id))
        row["status"] = "exhausted"
        row["cooldown_until"] = None

    def activate_key(self, command: dict[str, object]) -> dict[str, object]:
        row = self.rows.get(int(command["key_id"]))
        if row is None:
            raise ApplicationNotFoundError(str(command["key_id"]))
        row["status"] = "active"
        row["cooldown_until"] = None
        return row


class _FakeProviderConfiguration:
    def __init__(self, response: dict[str, object] | None = None) -> None:
        self.calls: list[dict[str, object]] = []
        self._response = response or {
            "reachable": True,
            "model_count": 3,
            "authenticated": True,
            "api_key": "must-not-leak",
        }

    def test_connection(self, command: dict[str, object]) -> dict[str, object]:
        self.calls.append(command)
        return dict(self._response)


AUTH = {"Authorization": "Bearer pat-test"}


def _client(fake: _FakeProviderApiKeys, provider_configuration: _FakeProviderConfiguration | None = None) -> TestClient:
    security = InMemorySecurityService()
    security.add_pat("pat-test", AuthenticatedPrincipal("user-1", "credential-1", frozenset({"api"})))
    app = create_app(
        ApiServices(
            security=security,
            provider_api_keys=fake,
            provider_configuration=provider_configuration,
        )
    )
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


def test_opencode_supports_multiple_keys_and_reordering() -> None:
    client = _client(_FakeProviderApiKeys())

    k1 = client.post(
        "/v1/providers/opencode/keys", json={"api_key": "oc-key-alpha", "label": "opencode-1"},
        headers={**AUTH, "Idempotency-Key": "oc-add-1"},
    )
    k2 = client.post(
        "/v1/providers/opencode/keys", json={"api_key": "oc-key-beta", "label": "opencode-2"},
        headers={**AUTH, "Idempotency-Key": "oc-add-2"},
    )
    assert k1.status_code == 201 and k2.status_code == 201
    id1, id2 = k1.json()["id"], k2.json()["id"]

    reordered = client.put(
        "/v1/providers/opencode/keys:reorder", json={"ordered_ids": [id2, id1]},
        headers={**AUTH, "Idempotency-Key": "oc-reorder-1"},
    )
    assert reordered.status_code == 200
    assert [item["label"] for item in reordered.json()] == ["opencode-2", "opencode-1"]
    assert "oc-key-alpha" not in reordered.text and "oc-key-beta" not in reordered.text


def test_activating_an_exhausted_key_resets_its_status_to_active() -> None:
    fake = _FakeProviderApiKeys()
    client = _client(fake)
    added = client.post(
        "/v1/providers/nvidia/keys",
        json={"api_key": "nvapi-free-tier-1", "label": "NVIDIA Free 1"},
        headers={**AUTH, "Idempotency-Key": "nv-add-1"},
    )
    assert added.status_code == 201
    key_id = added.json()["id"]
    fake.mark_exhausted(key_id)
    assert client.get("/v1/providers/nvidia/keys", headers=AUTH).json()[0]["status"] == "exhausted"

    activated = client.post(
        f"/v1/providers/nvidia/keys/{key_id}:activate",
        headers={**AUTH, "Idempotency-Key": "nv-activate-1"},
    )

    assert activated.status_code == 200
    body = activated.json()
    assert body["id"] == key_id
    assert body["status"] == "active"
    assert body["cooldown_until"] is None
    assert "nvapi-free-tier-1" not in activated.text


def test_activating_a_missing_key_is_a_404() -> None:
    client = _client(_FakeProviderApiKeys())

    response = client.post(
        "/v1/providers/nvidia/keys/999:activate",
        headers={**AUTH, "Idempotency-Key": "nv-activate-missing"},
    )

    assert response.status_code == 404


def test_activating_key_rejects_unsupported_provider_and_invalid_key_id() -> None:
    import pytest

    client = _client(_FakeProviderApiKeys())

    with pytest.raises(ValueError):
        client.post(
            "/v1/providers/not-a-provider/keys/1:activate",
            headers={**AUTH, "Idempotency-Key": "nv-activate-bad-provider"},
        )

    bad_id = client.post(
        "/v1/providers/nvidia/keys/not-an-int:activate",
        headers={**AUTH, "Idempotency-Key": "nv-activate-bad-id"},
    )
    assert bad_id.status_code == 422


def test_testing_nvidia_connection_routes_through_gateway() -> None:
    config = _FakeProviderConfiguration()
    client = _client(_FakeProviderApiKeys(), provider_configuration=config)

    response = client.post(
        "/v1/providers/nvidia/test",
        json={"api_key": "nvapi-12345678", "base_url": "https://integrate.api.nvidia.com/v1"},
        headers={**AUTH, "Idempotency-Key": "nv-test-1"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["reachable"] is True
    assert body["model_count"] == 3
    assert body["authenticated"] is True
    assert "nvapi-12345678" not in response.text
    assert "must-not-leak" not in response.text
    assert len(config.calls) == 1
    assert config.calls[0]["provider"] == "nvidia"
    assert config.calls[0]["user_id"] == "user-1"
    assert config.calls[0]["api_key"] == "nvapi-12345678"
    assert config.calls[0]["base_url"] == "https://integrate.api.nvidia.com/v1"


def test_testing_nvidia_connection_supports_keyless_self_hosted_and_unauthenticated_response() -> None:
    config = _FakeProviderConfiguration(
        response={"reachable": True, "model_count": 0, "authenticated": False, "secret_token": "never-expose"}
    )
    client = _client(_FakeProviderApiKeys(), provider_configuration=config)

    unauth = client.post("/v1/providers/nvidia/test", json={"base_url": "http://localhost:8000/v1"})
    assert unauth.status_code == 401

    response = client.post(
        "/v1/providers/nvidia/test",
        json={"base_url": "http://localhost:8000/v1"},
        headers={**AUTH, "Idempotency-Key": "nv-test-local"},
    )
    assert response.status_code == 200
    assert response.json() == {"reachable": True, "model_count": 0, "authenticated": False}
    assert "never-expose" not in response.text
    assert config.calls[0]["api_key"] == ""
    assert config.calls[0]["base_url"] == "http://localhost:8000/v1"
