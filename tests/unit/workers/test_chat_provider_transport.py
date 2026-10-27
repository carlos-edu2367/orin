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


def test_opencode_provider_transport_uses_zen_base_url_and_rotates_keys_on_rejection(monkeypatch: pytest.MonkeyPatch) -> None:
    from agentos.agentic.provider_stream import HTTPProviderStreamTransport, NormalizedStreamItem, ProviderKeyRejected, StreamKind
    from agentos.workers.chat import ChatWorker

    engine = _engine_with_provider("opencode", base_url=None, with_key=True)
    cipher = ProviderSecretCipher(_TEST_ENCRYPTION_KEY)
    now = datetime.now(UTC)
    with engine.begin() as connection:
        connection.execute(insert(provider_api_keys).values(
            user_id="user-1", provider="opencode", label="backup",
            api_key_ciphertext=cipher.encrypt("oc-second-key"), secret_ref="key-ref-2",
            position=1, status="active", cooldown_until=None, created_at=now, updated_at=now,
        ))

    seen: list[tuple[str, str]] = []

    def fake_stream(self, request):
        seen.append((self.base_url, self._api_key))
        if self._api_key == "sk-configured":
            raise ProviderKeyRejected("rate limited")
        yield NormalizedStreamItem(StreamKind.TEXT, 1, text="ok from opencode")

    monkeypatch.setattr(HTTPProviderStreamTransport, "stream", fake_stream)
    worker = ChatWorker.__new__(ChatWorker)
    worker.store = type("S", (), {"_engine": engine})()

    transport = worker._provider_transport({"provider": "opencode", "user_id": "user-1", "model_id": "big-pickle"})
    items = list(transport.stream({}))

    assert isinstance(transport, MultiKeyProviderStreamTransport)
    assert seen == [
        ("https://opencode.ai/zen/v1", "sk-configured"),
        ("https://opencode.ai/zen/v1", "oc-second-key"),
    ]
    assert [item.text for item in items] == ["ok from opencode"]


def test_nvidia_provider_transport_uses_integrate_base_url_and_wraps_in_multi_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from agentos.agentic.provider_stream import HTTPProviderStreamTransport, NormalizedStreamItem, StreamKind
    from agentos.workers.chat import ChatWorker

    engine = _engine_with_provider("nvidia", base_url=None, with_key=True)
    seen: list[tuple[str, str, str]] = []

    def fake_stream(self, request):
        seen.append((self.base_url, self._api_key, self.model))
        yield NormalizedStreamItem(StreamKind.TEXT, 1, text="ok from nvidia")

    monkeypatch.setattr(HTTPProviderStreamTransport, "stream", fake_stream)
    worker = ChatWorker.__new__(ChatWorker)
    worker.store = type("S", (), {"_engine": engine})()

    transport = worker._provider_transport(
        {"provider": "nvidia", "user_id": "user-1", "model_id": "meta/llama-3.1-70b-instruct"}
    )
    items = list(transport.stream({}))

    assert isinstance(transport, MultiKeyProviderStreamTransport)
    assert seen == [("https://integrate.api.nvidia.com/v1", "sk-configured", "meta/llama-3.1-70b-instruct")]
    assert [item.text for item in items] == ["ok from nvidia"]


def test_transport_for_subagents_and_vision_returns_multi_key_and_rotates_on_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agentos.agentic.provider_stream import HTTPProviderStreamTransport, NormalizedStreamItem, ProviderKeyRejected, StreamKind
    from agentos.workers.chat import ChatWorker

    engine = _engine_with_provider("nvidia", base_url=None, with_key=True)
    cipher = ProviderSecretCipher(_TEST_ENCRYPTION_KEY)
    now = datetime.now(UTC)
    with engine.begin() as connection:
        connection.execute(
            insert(provider_api_keys).values(
                user_id="user-1",
                provider="nvidia",
                label="paid-backup",
                api_key_ciphertext=cipher.encrypt("nvapi-second-key"),
                secret_ref="key-ref-2",
                position=1,
                status="active",
                cooldown_until=None,
                created_at=now,
                updated_at=now,
            )
        )

    seen: list[tuple[str, str]] = []

    def fake_stream(self, request):
        seen.append((self.base_url, self._api_key))
        if self._api_key == "sk-configured":
            raise ProviderKeyRejected("out of credits", status_code=402, exhausted=True)
        yield NormalizedStreamItem(StreamKind.TEXT, 1, text="ok from subagent")

    monkeypatch.setattr(HTTPProviderStreamTransport, "stream", fake_stream)
    worker = ChatWorker.__new__(ChatWorker)
    worker.store = type("S", (), {"_engine": engine})()

    transport = worker._transport_for({"user_id": "user-1"}, "nvidia")
    items = list(transport.stream({}))

    assert isinstance(transport, MultiKeyProviderStreamTransport)
    assert seen == [
        ("https://integrate.api.nvidia.com/v1", "sk-configured"),
        ("https://integrate.api.nvidia.com/v1", "nvapi-second-key"),
    ]
    assert [item.text for item in items] == ["ok from subagent"]


def test_opencode_provider_transport_without_keys_defaults_to_local_bridge_url() -> None:
    from agentos.agentic.provider_stream import HTTPProviderStreamTransport
    from agentos.workers.chat import ChatWorker

    engine = _engine_with_provider("opencode", base_url=None, with_key=False)
    worker = ChatWorker.__new__(ChatWorker)
    worker.store = type("S", (), {"_engine": engine})()

    transport = worker._provider_transport({"provider": "opencode", "user_id": "user-1", "model_id": "deepseek-v4.1-flash-free"})

    assert isinstance(transport, HTTPProviderStreamTransport)
    assert transport.base_url == "http://127.0.0.1:18446"


def test_worker_media_policy_and_client_support_cross_provider_defaults_and_key_rotation() -> None:
    import base64
    import httpx
    from agentos.persistence.postgres.schema import (
        media_model_selections,
        provider_api_keys,
        provider_configurations,
    )
    from agentos.workers.chat import ChatWorker

    engine = _engine_with_provider("nvidia", base_url=None, with_key=True)
    cipher = ProviderSecretCipher(_TEST_ENCRYPTION_KEY)
    now = datetime.now(UTC)
    with engine.begin() as conn:
        conn.execute(
            insert(provider_configurations).values(
                user_id="user-1",
                provider="openrouter",
                secret_ref="ref-or-1",
                base_url=None,
                enabled=True,
                key_cooldown_seconds=60,
                catalog_refreshed_at=None,
                created_at=now,
                updated_at=now,
            )
        )
        conn.execute(
            insert(provider_api_keys).values(
                [
                    {
                        "user_id": "user-1",
                        "provider": "openrouter",
                        "label": "or-key-1",
                        "api_key_ciphertext": cipher.encrypt("sk-or-exhausted"),
                        "secret_ref": "ref-or-1",
                        "position": 0,
                        "status": "active",
                        "cooldown_until": None,
                        "created_at": now,
                        "updated_at": now,
                    },
                    {
                        "user_id": "user-1",
                        "provider": "openrouter",
                        "label": "or-key-2",
                        "api_key_ciphertext": cipher.encrypt("sk-or-valid"),
                        "secret_ref": "ref-or-2",
                        "position": 1,
                        "status": "active",
                        "cooldown_until": None,
                        "created_at": now,
                        "updated_at": now,
                    },
                ]
            )
        )
        conn.execute(
            insert(media_model_selections).values(
                user_id="user-1",
                modality="image",
                provider="openrouter",
                model_id="qwen/qwen-image-2.1-pro",
                updated_at=now,
            )
        )

    worker = ChatWorker.__new__(ChatWorker)
    worker.store = type("S", (), {"_engine": engine})()
    turn = {"user_id": "user-1", "provider": "nvidia", "model_id": "meta/llama-3.1-70b-instruct"}

    policy = worker._media_policy_for_turn(turn)
    target = policy.resolve_target("image")
    assert target.provider == "openrouter"
    assert target.model_id == "qwen/qwen-image-2.1-pro"

    auth_headers: list[str] = []
    png_b64 = base64.b64encode(b"\x89PNG\r\n\x1a\nfake").decode("ascii")

    def mock_or_handler(request: httpx.Request) -> httpx.Response:
        auth = request.headers.get("Authorization", "")
        auth_headers.append(auth)
        if "sk-or-exhausted" in auth:
            return httpx.Response(402, json={"error": {"message": "Insufficient credits"}})
        return httpx.Response(200, json={"data": [{"b64_json": png_b64, "media_type": "image/png"}]})

    build_client = worker._media_client_for_turn(turn)
    media_client = build_client("openrouter", transport=httpx.MockTransport(mock_or_handler))
    assert media_client is not None
    data, ext = media_client.generate_image(model="qwen/qwen-image-2.1-pro", prompt="A dragon")
    assert ext == "png"
    assert data == b"\x89PNG\r\n\x1a\nfake"
    assert auth_headers == ["Bearer sk-or-exhausted", "Bearer sk-or-valid"]

