import pytest
from sqlalchemy import create_engine, select

from agentos.persistence.postgres.provider_configuration import PostgresProviderConfigurationAdapter
from agentos.persistence.postgres.schema import metadata, provider_configurations
from agentos.persistence.provider_secrets import ProviderSecretCipher


def test_omniroute_configuration_persists_an_empty_gateway_key_encrypted() -> None:
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
        stored = connection.execute(
            select(provider_configurations.c.api_key_ciphertext).where(
                provider_configurations.c.user_id == "local-user",
                provider_configurations.c.provider == "omniroute",
            )
        ).scalar_one()

    assert saved["enabled"] is True
    assert stored.startswith("enc:v1:")
    assert cipher.decrypt(stored) == ""


def _adapter() -> PostgresProviderConfigurationAdapter:
    engine = create_engine("sqlite://")
    metadata.create_all(engine, tables=[provider_configurations])
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


def test_ollama_cloud_connection_test_verifies_the_key_after_catalog_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeOllama:
        def __init__(self) -> None:
            self.verified: tuple[str, str, str] | None = None

        def fetch(self, api_key: str, *, base_url: str) -> list[dict[str, object]]:
            assert api_key == "cloud-secret"
            assert base_url == "https://ollama.com"
            return [{"id": "deepseek-v4-flash:cloud"}]

        def verify_cloud_access(self, api_key: str, *, base_url: str, model: str) -> None:
            self.verified = (api_key, base_url, model)

    fake = FakeOllama()
    monkeypatch.setattr("agentos.persistence.postgres.provider_configuration.OllamaCatalogClient", lambda: fake)

    result = _adapter().test_connection({"provider": "ollama", "api_key": "cloud-secret", "base_url": "https://ollama.com"})

    assert result["connected"] is True
    assert fake.verified == ("cloud-secret", "https://ollama.com", "deepseek-v4-flash:cloud")


def test_a_rejected_provider_still_cannot_be_connection_tested() -> None:
    with pytest.raises(ValueError):
        _adapter().test_connection({"provider": "openai", "api_key": "k", "base_url": None})
