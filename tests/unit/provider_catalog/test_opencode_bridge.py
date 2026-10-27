from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from agentos.agentic.provider_stream import (
    HTTPProviderStreamTransport,
    NormalizedStreamItem,
    ProviderKeyRejected,
    StreamKind,
)
from agentos.api.contracts import ProviderCredentialRejectedError
from agentos.persistence.postgres.provider_configuration import (
    PostgresProviderConfigurationAdapter,
)
from agentos.persistence.postgres.schema import metadata, provider_configurations
from agentos.persistence.provider_secrets import ProviderSecretCipher
from agentos.persistence.sqlite import create_local_engine
from agentos.provider_catalog.models import (
    PROVIDERS_WITH_BASE_URL,
    PROVIDERS_WITH_OPTIONAL_KEY,
    ProviderCatalogContext,
)
from agentos.provider_catalog.opencode import (
    DEFAULT_OPENCODE_LOCAL_BASE_URL,
    OPENCODE_CLOUD_BASE_URL,
    OpenCodeModelCatalogClient,
    is_opencode_cloud,
    normalize_opencode_base_url,
    stream_opencode_bridge,
)
from agentos.provider_catalog.service import ProviderModelCatalogService


_TEST_CIPHER_KEY = "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="


def test_opencode_registered_in_base_url_and_optional_key_sets() -> None:
    assert "opencode" in PROVIDERS_WITH_BASE_URL
    assert "opencode" in PROVIDERS_WITH_OPTIONAL_KEY


@pytest.mark.parametrize(
    ("raw", "expected", "cloud"),
    [
        ("http://127.0.0.1:18446", "http://127.0.0.1:18446", False),
        ("http://127.0.0.1:18446/", "http://127.0.0.1:18446", False),
        ("http://localhost:18446/v1", "http://localhost:18446", False),
        ("https://opencode.ai/zen/v1/", "https://opencode.ai/zen/v1", True),
        ("https://opencode.ai", "https://opencode.ai/zen/v1", True),
    ],
)
def test_normalize_opencode_base_url_accepts_loopback_and_cloud(
    raw: str, expected: str, cloud: bool
) -> None:
    normalized = normalize_opencode_base_url(raw)
    assert normalized == expected
    assert is_opencode_cloud(normalized) is cloud


@pytest.mark.parametrize(
    "malicious_url",
    [
        "",
        "   ",
        "ftp://127.0.0.1:18446",
        "http://user:pass@127.0.0.1:18446",
        "http://127.0.0.1:18446?token=secret",
        "http://127.0.0.1:18446#frag",
        "http://169.254.169.254/latest/meta-data",
        "http://192.168.1.50:18446",
        "http://10.0.0.1:18446",
        "https://evil-opencode.ai/zen/v1",
        "https://attacker.example.com",
    ],
)
def test_normalize_opencode_base_url_rejects_ssrf_and_invalid_urls(
    malicious_url: str,
) -> None:
    with pytest.raises(ValueError):
        normalize_opencode_base_url(malicious_url)


def test_catalog_client_fetches_local_bridge_config_providers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_urls: list[str] = []

    class _FakeBridgeManager:
        def ensure_running(self, base_url: str) -> None:
            captured_urls.append(f"ensure:{base_url}")

    class _Response:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict[str, object]:
            return {
                "providers": [
                    {
                        "id": "opencode",
                        "name": "OpenCode",
                        "models": {
                            "big-pickle": {
                                "id": "big-pickle",
                                "name": "Big Pickle",
                                "limit": {"context": 200000, "output": 128000},
                                "capabilities": {
                                    "reasoning": True,
                                    "toolcall": True,
                                    "input": {"text": True, "image": False},
                                    "output": {"text": True},
                                },
                                "cost": {"input": 0, "output": 0},
                            },
                            "mimo-v2.6-flash-free": {
                                "id": "mimo-v2.6-flash-free",
                                "name": "MiMo v2.6 Flash Free",
                                "limit": {"context": 128000, "output": 64000},
                                "capabilities": {
                                    "reasoning": True,
                                    "toolcall": True,
                                    "input": {"text": True, "image": True},
                                    "output": {"text": True},
                                },
                                "cost": {"input": 0, "output": 0},
                            },
                        },
                    }
                ]
            }

    def fake_get(url: str, **kwargs: Any) -> _Response:
        captured_urls.append(url)
        return _Response()

    monkeypatch.setattr(httpx, "get", fake_get)
    client = OpenCodeModelCatalogClient(bridge_manager=_FakeBridgeManager())
    raw_models = client.fetch("", base_url="http://127.0.0.1:18446")

    assert captured_urls == [
        "ensure:http://127.0.0.1:18446",
        "http://127.0.0.1:18446/config/providers",
    ]
    by_id = {str(m["id"]): m for m in raw_models}
    assert set(by_id) == {"big-pickle", "mimo-v2.6-flash-free"}
    assert by_id["big-pickle"]["name"] == "Big Pickle"
    assert by_id["big-pickle"]["context_length"] == 200000
    assert "tools" in by_id["big-pickle"]["supported_parameters"]  # type: ignore[operator]
    assert by_id["mimo-v2.6-flash-free"]["input_modalities"] == ["text", "image"]


def test_stream_opencode_bridge_enforces_read_tool_gate_and_parses_orin_tool_calls() -> None:
    requests_sent: list[tuple[str, str, dict[str, Any]]] = []

    class _FakeBridgeManager:
        def ensure_running(self, base_url: str) -> None:
            pass

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        body = json.loads(request.content.decode("utf-8")) if request.content else {}
        requests_sent.append((request.method, str(request.url), body))
        if request.url.path == "/session":
            return httpx.Response(200, json={"id": "ses_test_123"})
        if request.url.path == "/session/ses_test_123/message":
            return httpx.Response(
                200,
                json={
                    "info": {
                        "id": "msg_1",
                        "role": "assistant",
                        "tokens": {"input": 120, "output": 35, "reasoning": 10},
                        "error": None,
                    },
                    "parts": [
                        {"type": "reasoning", "text": "Checking README first."},
                        {
                            "type": "text",
                            "text": (
                                "Vou ler o arquivo.\n"
                                '<orin_tool_call>{"name": "read_file", "arguments": {"path": "README.md"}}</orin_tool_call>\n'
                                '<orin_tool_call>{"name": "unauthorized_shell", "arguments": {"cmd": "rm -rf /"}}</orin_tool_call>\n'
                                '<orin_tool_call>{malformed json</orin_tool_call>'
                            ),
                        },
                    ],
                },
            )
        return httpx.Response(404)

    mock_client = httpx.Client(transport=httpx.MockTransport(handler))
    items = list(
        stream_opencode_bridge(
            base_url="http://127.0.0.1:18446",
            model="big-pickle",
            messages=[
                {"role": "system", "content": "Você é o agente Orin."},
                {"role": "user", "content": "Leia o README.md"},
            ],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "read_file",
                        "description": "Read a file from the workspace",
                        "parameters": {
                            "type": "object",
                            "properties": {"path": {"type": "string"}},
                            "required": ["path"],
                        },
                    },
                }
            ],
            tool_choice="auto",
            client=mock_client,
            bridge_manager=_FakeBridgeManager(),
        )
    )

    assert len(requests_sent) == 2
    _, msg_url, msg_body = requests_sent[1]
    assert msg_url == "http://127.0.0.1:18446/session/ses_test_123/message"
    # Critical FreeTier gate check: "read" MUST be True and all other built-ins False
    assert msg_body["tools"]["read"] is True
    assert msg_body["tools"]["bash"] is False
    assert msg_body["tools"]["write"] is False
    assert msg_body["tools"]["edit"] is False
    assert msg_body["model"] == {"providerID": "opencode", "modelID": "big-pickle"}

    thinking_items = [i for i in items if i.kind is StreamKind.THINKING]
    text_items = [i for i in items if i.kind is StreamKind.TEXT]
    tool_items = [i for i in items if i.kind is StreamKind.TOOL_CALL]
    usage_items = [i for i in items if i.kind is StreamKind.USAGE]
    finish_items = [i for i in items if i.kind is StreamKind.FINISH]

    assert len(thinking_items) == 1
    assert thinking_items[0].thinking == "Checking README first."

    # Only visible text outside valid <orin_tool_call> blocks should be emitted as TEXT
    assert any("Vou ler o arquivo." in (i.text or "") for i in text_items)
    assert not any("<orin_tool_call>" in (i.text or "") for i in text_items)

    # Only the whitelisted tool (`read_file`) is emitted; `unauthorized_shell` and malformed JSON are rejected
    assert len(tool_items) == 1
    assert tool_items[0].tool_name == "read_file"
    assert tool_items[0].arguments_delta == '{"path": "README.md"}'

    assert len(usage_items) == 1
    assert usage_items[0].usage is not None
    assert usage_items[0].usage.input_tokens == 120
    assert usage_items[0].usage.output_tokens == 35

    assert len(finish_items) == 1
    assert str(finish_items[0].finish_reason).lower() == "tool_calls"


def test_http_provider_stream_transport_delegates_local_opencode_to_bridge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/session":
            return httpx.Response(200, json={"id": "ses_local_1"})
        if request.url.path == "/session/ses_local_1/message":
            return httpx.Response(
                200,
                json={
                    "info": {"id": "m1", "role": "assistant", "tokens": {"input": 10, "output": 5}},
                    "parts": [{"type": "text", "text": "pong"}],
                },
            )
        return httpx.Response(404)

    monkeypatch.setattr(
        "agentos.provider_catalog.opencode._DEFAULT_BRIDGE_MANAGER.ensure_running",
        lambda base_url: None,
    )
    transport = HTTPProviderStreamTransport(
        provider="opencode",
        base_url="http://127.0.0.1:18446",
        api_key="",
        model="mimo-v2.6-flash-free",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    items = list(transport.stream({"messages": [{"role": "user", "content": "ping"}]}))
    texts = [i.text for i in items if i.kind is StreamKind.TEXT]
    assert texts == ["pong"]


def test_provider_configuration_allows_keyless_local_opencode_and_requires_key_for_cloud(
    tmp_path: Any,
) -> None:
    engine = create_local_engine(f"sqlite:///{tmp_path / 'opencode_cfg.db'}")
    metadata.create_all(engine)
    cipher = ProviderSecretCipher(_TEST_CIPHER_KEY)
    adapter = PostgresProviderConfigurationAdapter(engine, cipher=cipher)

    # Local Bridge mode: no API key required
    local_cfg = adapter.configure(
        {
            "provider": "opencode",
            "user_id": "user-1",
            "enabled": True,
            "api_key": "",
            "base_url": DEFAULT_OPENCODE_LOCAL_BASE_URL,
        }
    )
    assert local_cfg["enabled"] is True
    assert local_cfg["base_url"] == DEFAULT_OPENCODE_LOCAL_BASE_URL

    # Cloud Zen mode: API key IS required
    with pytest.raises(ValueError, match="provider API key is required"):
        adapter.configure(
            {
                "provider": "opencode",
                "user_id": "user-1",
                "enabled": True,
                "api_key": "",
                "base_url": OPENCODE_CLOUD_BASE_URL,
            }
        )


def test_chat_worker_opencode_transport_without_keys_defaults_to_local_bridge(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sqlalchemy import insert
    from agentos.workers.chat import ChatWorker

    monkeypatch.setenv("AGENTOS_PROVIDER_ENCRYPTION_KEY", _TEST_CIPHER_KEY)
    engine = create_local_engine(f"sqlite:///{tmp_path / 'worker_opencode.db'}")
    metadata.create_all(engine)
    now = datetime.now(UTC)
    with engine.begin() as conn:
        conn.execute(
            insert(provider_configurations).values(
                user_id="user-1",
                provider="opencode",
                enabled=True,
                base_url=None,
                secret_ref="ref-1",
                key_cooldown_seconds=60,
                catalog_refreshed_at=now,
                created_at=now,
                updated_at=now,
            )
        )

    worker = ChatWorker.__new__(ChatWorker)
    worker.store = type("S", (), {"_engine": engine})()

    transport = worker._provider_transport(
        {"provider": "opencode", "user_id": "user-1", "model_id": "deepseek-v4.1-flash-free"}
    )
    assert isinstance(transport, HTTPProviderStreamTransport)
    assert transport.base_url == DEFAULT_OPENCODE_LOCAL_BASE_URL

    direct = worker._transport_for("user-1", "opencode", "deepseek-v4.1-flash-free")
    assert isinstance(direct, HTTPProviderStreamTransport)
    assert direct.base_url == DEFAULT_OPENCODE_LOCAL_BASE_URL


def test_catalog_service_list_auto_refreshes_local_opencode_after_save_or_legacy_null_url(
    tmp_path: Any,
) -> None:
    from sqlalchemy import insert, select
    from agentos.persistence.postgres.provider_models import PostgresProviderCatalogRepository

    engine = create_local_engine(f"sqlite:///{tmp_path / 'catalog_auto.db'}")
    metadata.create_all(engine)
    cipher = ProviderSecretCipher(_TEST_CIPHER_KEY)
    cfg = PostgresProviderConfigurationAdapter(engine, cipher=cipher)
    repo = PostgresProviderCatalogRepository(engine, cipher=cipher)

    class _FakeUpstream:
        def fetch(self, api_key: str, *, base_url: str = "") -> list[dict[str, object]]:
            return [
                {
                    "id": "deepseek-v4.1-flash-free",
                    "name": "DeepSeek V4.1 Flash Free",
                    "context_length": 128000,
                    "supported_parameters": ["tools"],
                    "input_modalities": ["text"],
                    "output_modalities": ["text"],
                }
            ]

    service = ProviderModelCatalogService(repo, {"opencode": _FakeUpstream()})
    ctx = ProviderCatalogContext("user-1", "conversation.send")

    # Case 1: User just clicked "Salvar" on Local Bridge (`catalog_refreshed_at=None`)
    cfg.configure(
        {
            "provider": "opencode",
            "user_id": "user-1",
            "enabled": True,
            "api_key": "",
            "base_url": DEFAULT_OPENCODE_LOCAL_BASE_URL,
        }
    )
    models = service.list(ctx, "opencode")
    assert [m.model_id for m in models] == ["deepseek-v4.1-flash-free"]
    assert models[0].display_name == "DeepSeek V4.1 Flash Free"

    # Case 2: Legacy 0.9.4 row with `base_url=None` and no API key
    now = datetime.now(UTC)
    with engine.begin() as conn:
        conn.execute(provider_configurations.delete())
        conn.execute(
            insert(provider_configurations).values(
                user_id="user-1",
                provider="opencode",
                enabled=True,
                base_url=None,
                secret_ref="legacy-ref",
                key_cooldown_seconds=60,
                catalog_refreshed_at=now,
                created_at=now,
                updated_at=now,
            )
        )
    models_legacy = service.list(ctx, "opencode")
    assert [m.model_id for m in models_legacy] == ["deepseek-v4.1-flash-free"]
    with engine.connect() as conn:
        saved_base_url = conn.execute(
            select(provider_configurations.c.base_url).where(
                provider_configurations.c.user_id == "user-1",
                provider_configurations.c.provider == "opencode",
            )
        ).scalar_one()
    assert saved_base_url == DEFAULT_OPENCODE_LOCAL_BASE_URL


def test_stream_opencode_bridge_parses_fenced_json_tool_calls_and_preserves_late_system_notes() -> None:
    captured_payloads: list[dict[str, Any]] = []

    class _FakeBridgeManager:
        def ensure_running(self, base_url: str) -> None:
            pass

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        body = json.loads(request.content.decode("utf-8")) if request.content else {}
        if request.url.path == "/session":
            return httpx.Response(200, json={"id": "ses_fenced"})
        if request.url.path == "/session/ses_fenced/message":
            captured_payloads.append(body)
            return httpx.Response(
                200,
                json={
                    "info": {"id": "m1", "role": "assistant", "tokens": {"input": 50, "output": 20}},
                    "parts": [
                        {
                            "type": "text",
                            "text": (
                                "Executando busca:\n"
                                "<orin_tool_call>\n"
                                "```json\n"
                                '{"name": "web_search", "arguments": {"query": "digital products 2026"}}\n'
                                "```\n"
                                "</orin_tool_call>"
                            ),
                        }
                    ],
                },
            )
        return httpx.Response(404)

    items = list(
        stream_opencode_bridge(
            base_url="http://127.0.0.1:18446",
            model="deepseek-v4.1-flash-free",
            messages=[
                {"role": "system", "content": "Stable system prompt."},
                {"role": "user", "content": "Crie uma landing page."},
                {"role": "system", "content": "Fase atual: PLAN. Ferramentas disponíveis: web_search."},
            ],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "web_search",
                        "description": "Search the web",
                        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
                    },
                }
            ],
            client=httpx.Client(transport=httpx.MockTransport(handler)),
            bridge_manager=_FakeBridgeManager(),
        )
    )

    tool_items = [i for i in items if i.kind is StreamKind.TOOL_CALL]
    text_items = [i for i in items if i.kind is StreamKind.TEXT]
    assert len(tool_items) == 1
    assert tool_items[0].tool_name == "web_search"
    assert tool_items[0].arguments_delta == '{"query": "digital products 2026"}'
    assert not any("<orin_tool_call>" in (i.text or "") for i in text_items)
    assert "Fase atual: PLAN" in captured_payloads[0]["parts"][0]["text"]

