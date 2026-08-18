from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, insert

from agentos.agentic.provider_key_fallback import MultiKeyProviderStreamTransport
from agentos.persistence.postgres.schema import metadata, provider_api_keys, provider_configurations
from agentos.persistence.provider_secrets import ProviderSecretCipher

_TEST_ENCRYPTION_KEY = "0" * 32


@pytest.fixture(autouse=True)
def _encryption_key(monkeypatch: pytest.MonkeyPatch) -> None:
    # _provider_transport builds its own PostgresProviderApiKeyAdapter without
    # an injected cipher, so it decrypts via ProviderSecretCipher.from_environment()
    # exactly as production does -- this fixture is what makes that resolve to
    # the same key the test data below is encrypted with.
    monkeypatch.setenv("AGENTOS_PROVIDER_ENCRYPTION_KEY", _TEST_ENCRYPTION_KEY)


def _engine_with_provider(provider: str, *, base_url: str | None, with_key: bool):
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    now = datetime.now(UTC)
    with engine.begin() as connection:
        connection.execute(insert(provider_configurations).values(
            user_id="user-1", provider=provider, enabled=True, base_url=base_url,
            secret_ref="ref", key_cooldown_seconds=60, catalog_refreshed_at=None,
            created_at=now, updated_at=now,
        ))
        if with_key:
            cipher = ProviderSecretCipher(_TEST_ENCRYPTION_KEY)
            connection.execute(insert(provider_api_keys).values(
                user_id="user-1", provider=provider, label=None,
                api_key_ciphertext=cipher.encrypt("sk-configured"), secret_ref="key-ref",
                position=0, status="active", cooldown_until=None, created_at=now, updated_at=now,
            ))
    return engine


def test_a_provider_with_at_least_one_key_gets_the_fallback_wrapper() -> None:
    from agentos.workers.chat import ChatWorker

    worker = ChatWorker.__new__(ChatWorker)
    worker.store = type("S", (), {"_engine": _engine_with_provider("openai", base_url=None, with_key=True)})()

    transport = worker._provider_transport({"provider": "openai", "user_id": "user-1", "model_id": "gpt-test"})

    assert isinstance(transport, MultiKeyProviderStreamTransport)


def test_an_optional_key_provider_with_no_keys_gets_a_plain_transport() -> None:
    # OmniRoute (not Ollama) on purpose: an Ollama turn also calls
    # self._num_ctx_for(turn), which reaches into catalog/context-window
    # lookups this bare, hand-built ChatWorker instance does not set up.
    # OmniRoute exercises the same "optional key, zero keys configured"
    # branch in _provider_transport without that extra dependency.
    from agentos.agentic.provider_stream import HTTPProviderStreamTransport
    from agentos.workers.chat import ChatWorker

    worker = ChatWorker.__new__(ChatWorker)
    worker.store = type("S", (), {"_engine": _engine_with_provider("omniroute", base_url="http://127.0.0.1:20128/v1", with_key=False)})()

    transport = worker._provider_transport({"provider": "omniroute", "user_id": "user-1", "model_id": "auto"})

    assert isinstance(transport, HTTPProviderStreamTransport)
