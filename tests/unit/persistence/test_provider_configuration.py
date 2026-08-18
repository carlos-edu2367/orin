import pytest
from datetime import UTC, datetime
from sqlalchemy import create_engine, select, update

from agentos.persistence.postgres.provider_configuration import PostgresProviderConfigurationAdapter
from agentos.persistence.postgres.schema import metadata, provider_api_keys, provider_configurations
from agentos.persistence.provider_secrets import ProviderSecretCipher


def test_omniroute_configuration_with_an_empty_key_creates_no_key_row() -> None:
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    cipher = ProviderSecretCipher("unit-test-provider-key")
    configuration = PostgresProviderConfigurationAdapter(engine, cipher=cipher)

    saved = configuration.configure({
        "provider": "omniroute",
        "user_id": "local-user",
        "enabled": True,
        "api_key": "",
        "base_url": "http://127.0.0.1:20128/v1",
    })

    with engine.connect() as connection:
        keys = connection.execute(
            select(provider_api_keys).where(
                provider_api_keys.c.user_id == "local-user",
                provider_api_keys.c.provider == "omniroute",
            )
        ).mappings().all()

    assert saved["enabled"] is True
    assert keys == []


def _adapter() -> PostgresProviderConfigurationAdapter:
    engine = create_engine("sqlite://")
    metadata.create_all(engine, tables=[provider_configurations, provider_api_keys])
    return PostgresProviderConfigurationAdapter(engine, cipher=ProviderSecretCipher(b"0" * 32))


def _command(**overrides: object) -> dict[str, object]:
    return {"provider": "ollama", "user_id": "user-1", "enabled": True, "api_key": "", "base_url": None, **overrides}


def test_a_local_ollama_is_configured_without_a_key() -> None:
    state = _adapter().configure(_command(base_url="http://localhost:11434"))

    assert state["provider"] == "ollama"
    assert state["enabled"] is True
    assert state["base_url"] == "http://localhost:11434"
    assert not any("api_key" in key for key in state)


def test_the_local_default_applies_when_no_url_is_given() -> None:
    assert _adapter().configure(_command())["base_url"] == "http://localhost:11434"


def test_ollama_cloud_refuses_to_be_configured_without_a_key() -> None:
    """The mode comes from the host, so the key rule has to read it too."""
    with pytest.raises(ValueError):
        _adapter().configure(_command(base_url="https://ollama.com"))


def test_ollama_cloud_is_configured_with_a_key() -> None:
    state = _adapter().configure(_command(base_url="https://ollama.com/v1", api_key="cloud-secret"))

    assert state["base_url"] == "https://ollama.com"


def test_ollama_cloud_key_is_trimmed_before_storage() -> None:
    adapter = _adapter()
    adapter.configure(_command(base_url="https://ollama.com", api_key="  cloud-secret\n"))

    with adapter._engine.connect() as connection:
        stored = connection.execute(
            select(provider_api_keys.c.api_key_ciphertext).where(
                provider_api_keys.c.user_id == "user-1",
                provider_api_keys.c.provider == "ollama",
                provider_api_keys.c.position == 0,
            )
        ).scalar_one()

    assert adapter._cipher.decrypt(stored) == "cloud-secret"


def test_reconfiguring_a_provider_invalidates_its_model_catalog() -> None:
    adapter = _adapter()
    adapter.configure(_command(base_url="http://localhost:11434"))
    refreshed_at = datetime(2026, 8, 12, tzinfo=UTC)
    with adapter._engine.begin() as connection:
        connection.execute(update(provider_configurations).values(catalog_refreshed_at=refreshed_at))

    state = adapter.configure(_command(base_url="https://ollama.com", api_key="cloud-secret"))

    assert state["catalog_refreshed_at"] is None

    with adapter._engine.connect() as connection:
        actual = connection.execute(
            select(provider_configurations.c.catalog_refreshed_at).where(
                provider_configurations.c.user_id == "user-1",
                provider_configurations.c.provider == "ollama",
            )
        ).scalar_one()
    assert actual is None


def test_ollama_cloud_connection_test_verifies_the_key_after_catalog_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeOllama:
        def __init__(self) -> None:
            self.verified: tuple[str, str, str] | None = None

        def fetch(self, api_key: str, *, base_url: str) -> list[dict[str, object]]:
            assert api_key == "cloud-secret"
            assert base_url == "https://ollama.com"
            return [{"id": "gemma4:31b"}]

        def verify_cloud_access(self, api_key: str, *, base_url: str, model: str) -> None:
            self.verified = (api_key, base_url, model)

    fake = FakeOllama()
    monkeypatch.setattr("agentos.persistence.postgres.provider_configuration.OllamaCatalogClient", lambda: fake)

    result = _adapter().test_connection({"provider": "ollama", "api_key": "cloud-secret", "base_url": "https://ollama.com"})

    assert result["connected"] is True
    assert fake.verified == ("cloud-secret", "https://ollama.com", "gemma4:31b")


def test_ollama_cloud_connection_test_skips_a_model_that_rejects_access(monkeypatch: pytest.MonkeyPatch) -> None:
    from agentos.provider_catalog.ollama import OllamaCloudAuthenticationError

    class FakeOllama:
        def __init__(self) -> None:
            self.verified: list[str] = []

        def fetch(self, api_key: str, *, base_url: str) -> list[dict[str, object]]:
            return [{"id": "restricted-model"}, {"id": "accessible-model"}]

        def verify_cloud_access(self, api_key: str, *, base_url: str, model: str) -> None:
            self.verified.append(model)
            if model == "restricted-model":
                raise OllamaCloudAuthenticationError

    fake = FakeOllama()
    monkeypatch.setattr("agentos.persistence.postgres.provider_configuration.OllamaCatalogClient", lambda: fake)

    result = _adapter().test_connection({"provider": "ollama", "api_key": "cloud-secret", "base_url": "https://ollama.com"})

    assert result["connected"] is True
    assert fake.verified == ["restricted-model", "accessible-model"]


def test_a_rejected_provider_still_cannot_be_connection_tested() -> None:
    with pytest.raises(ValueError):
        _adapter().test_connection({"provider": "openai", "api_key": "k", "base_url": None})


def test_ollama_cloud_connection_test_with_no_key_is_a_credential_error_not_an_internal_one(monkeypatch: pytest.MonkeyPatch) -> None:
    """Testing Cloud with a blank key is a rejected credential, not a 500.

    The gateway only maps ``ProviderCredentialRejectedError`` to its 422
    "credentials rejected" response; any other exception the adapter lets
    through (a plain ``RuntimeError``/``ValueError``) falls to the generic
    500 handler and reads to the user as "the provider is unavailable".
    """
    from agentos.api.contracts import ProviderCredentialRejectedError
    from agentos.provider_catalog.ollama import OllamaCloudAuthenticationError

    class FakeOllama:
        def fetch(self, api_key: str, *, base_url: str) -> list[dict[str, object]]:
            return [{"id": "gemma4:31b"}]

        def verify_cloud_access(self, api_key: str, *, base_url: str, model: str) -> None:
            if not api_key.strip():
                raise OllamaCloudAuthenticationError("Ollama Cloud API key is required")

    monkeypatch.setattr("agentos.persistence.postgres.provider_configuration.OllamaCatalogClient", FakeOllama)

    with pytest.raises(ProviderCredentialRejectedError):
        _adapter().test_connection({"provider": "ollama", "api_key": "", "base_url": "https://ollama.com"})
