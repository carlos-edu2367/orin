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
