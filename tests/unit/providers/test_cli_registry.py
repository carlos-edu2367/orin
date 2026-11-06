"""Unit tests for the subscription CLI provider registry and configuration gates."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

import pytest
from sqlalchemy import create_engine

from agentos.agentic.provider_stream import HTTPProviderStreamTransport, NormalizedStreamItem, StreamKind
from agentos.persistence.postgres.provider_configuration import PostgresProviderConfigurationAdapter
from agentos.persistence.postgres.provider_models import PostgresProviderCatalogRepository
from agentos.persistence.postgres.schema import metadata
from agentos.persistence.provider_secrets import ProviderSecretCipher
from agentos.provider_catalog.claude_code import DEFAULT_CLAUDE_CLI_BRIDGE_URL
from agentos.provider_catalog.models import ProviderCatalogContext
from agentos.provider_catalog.service import ProviderCatalogUnavailable, ProviderModelCatalogService
from agentos.providers.cli_adapter import (
    CliCapabilities,
    CliModelInfo,
    CliTurnRequest,
    ToolServerWiring,
)
from agentos.providers.cli_registry import (
    CLI_PROVIDERS,
    cli_adapter_for,
    set_antigravity_risk_acknowledged,
)
from agentos.providers.models import FinishReason


class _StubSubscriptionAdapter:
    def __init__(self, name: str, model_id: str) -> None:
        self.name = name
        self._model_id = model_id
        self.capabilities = CliCapabilities(
            native_tool_loop=True,
            tool_list_changed=False,
            images_in_tool_results=False,
            persistent_stdin=False,
            reports_tool_inventory=False,
        )

    def models(self) -> tuple[CliModelInfo, ...]:
        return (
            CliModelInfo(
                id=self._model_id,
                display_name=f"Stub {self._model_id}",
                context_window=200_000,
                supported_efforts=("low", "medium", "high"),
            ),
        )

    def build_command(self, request: CliTurnRequest) -> list[str]:
        return [self.name, "exec", "--model", str(request.model or "")]

    def encode_user_turn(self, content: list[dict[str, Any]]) -> bytes:
        return b"hello\n"

    def wire_tool_server(self, handle: Any, directory: Path) -> ToolServerWiring:
        return ToolServerWiring(argv=(), env={}, files=())


def _sqlite_engine():
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    metadata.create_all(engine)
    return engine


def _cipher() -> ProviderSecretCipher:
    return ProviderSecretCipher("test-secret-key-for-cli-registry-32b")


def test_cli_adapter_for_anthropic_only_when_bridge_url_is_used() -> None:
    bridge_adapter = cli_adapter_for("anthropic", DEFAULT_CLAUDE_CLI_BRIDGE_URL)
    assert bridge_adapter is not None
    assert bridge_adapter.name == "claude"

    assert cli_adapter_for("anthropic", "https://api.anthropic.com") is None
    assert cli_adapter_for("anthropic", None) is None
    assert cli_adapter_for("openai", None) is None


def test_cli_adapter_for_openai_codex_and_provider_stream_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = _StubSubscriptionAdapter("codex", "gpt-5.4")
    monkeypatch.setitem(CLI_PROVIDERS, "openai-codex", lambda: stub)

    resolved = cli_adapter_for("openai-codex", None)
    assert resolved is stub

    captured_adapters: list[Any] = []

    class _FakeClaudeCliProvider:
        def __init__(self, *, base_url: str, model: str, adapter: Any = None, **kwargs: Any) -> None:
            self.base_url = base_url
            self.model = model
            self.adapter = adapter
            captured_adapters.append(adapter)

        def stream(self, request: Any, *, cancel_check: Any = None) -> Iterator[NormalizedStreamItem]:
            yield NormalizedStreamItem(kind=StreamKind.TEXT, text=f"from-{self.adapter.name}:{self.model}")
            yield NormalizedStreamItem(kind=StreamKind.FINISH, finish_reason=FinishReason.STOP)

    monkeypatch.setattr("agentos.providers.claude_cli.provider.ClaudeCliProvider", _FakeClaudeCliProvider)

    provider = HTTPProviderStreamTransport(
        provider="openai-codex",
        model="gpt-5.4",
        api_key="",
        base_url="",
    )
    req = {
        "execution_id": "exec_1",
        "user_id": "user_1",
        "model": "gpt-5.4",
        "messages": [{"role": "user", "content": "oi"}],
        "tools": [],
    }
    events = list(provider.stream(req))
    assert any(ev.kind is StreamKind.TEXT and ev.text == "from-codex:gpt-5.4" for ev in events)
    assert captured_adapters == [stub]


def test_configure_openai_codex_without_api_key_or_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = _StubSubscriptionAdapter("codex", "gpt-5.4")
    monkeypatch.setitem(CLI_PROVIDERS, "openai-codex", lambda: stub)

    engine = _sqlite_engine()
    cipher = _cipher()
    config_adapter = PostgresProviderConfigurationAdapter(engine, cipher=cipher)

    result = config_adapter.configure(
        {
            "provider": "openai-codex",
            "user_id": "user_1",
            "enabled": True,
            "api_key": "",
            "base_url": None,
        }
    )
    assert result["provider"] == "openai-codex"
    assert result["enabled"] is True
    assert result["has_secret"] is False

    repo = PostgresProviderCatalogRepository(engine, cipher=cipher)
    service = ProviderModelCatalogService(repo)
    ctx = ProviderCatalogContext(user_id="user_1", purpose="provider.catalog.refresh")
    receipt = service.refresh(ctx, "openai-codex")
    assert receipt.count == 1
    models = service.list(ctx, "openai-codex")
    assert [m.model_id for m in models] == ["gpt-5.4"]


def test_antigravity_disabled_until_risk_acknowledged(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = _StubSubscriptionAdapter("antigravity", "gemini-2.5-pro")
    monkeypatch.setitem(CLI_PROVIDERS, "google-antigravity", lambda: stub)
    set_antigravity_risk_acknowledged(None)

    # 1. Without acceptance, cli_adapter_for("google-antigravity", None) returns None
    assert cli_adapter_for("google-antigravity", None) is None

    engine = _sqlite_engine()
    cipher = _cipher()
    config_adapter = PostgresProviderConfigurationAdapter(engine, cipher=cipher)
    repo = PostgresProviderCatalogRepository(engine, cipher=cipher)
    service = ProviderModelCatalogService(repo)
    ctx = ProviderCatalogContext(user_id="user_1", purpose="provider.catalog")

    # 2. Saving configuration without risk acknowledgement is rejected with a message asking for acceptance
    with pytest.raises(ValueError, match=r"(?i)(aceit|risco|termos|risk|acknowledge)"):
        config_adapter.configure(
            {
                "provider": "google-antigravity",
                "user_id": "user_1",
                "enabled": True,
                "api_key": "",
                "base_url": None,
            }
        )

    # 3. Models do not appear in the selector before acceptance
    assert service.list(ctx, "google-antigravity") == []
    with pytest.raises(ProviderCatalogUnavailable):
        service.refresh(ctx, "google-antigravity")

    # 4. Once risk acknowledgement is provided, configuration saves risk_acknowledged_at in the provider config
    saved = config_adapter.configure(
        {
            "provider": "google-antigravity",
            "user_id": "user_1",
            "enabled": True,
            "api_key": "",
            "base_url": None,
            "risk_acknowledged": True,
        }
    )
    assert saved["provider"] == "google-antigravity"
    assert saved["enabled"] is True
    assert isinstance(saved.get("risk_acknowledged_at"), datetime)

    inspected = config_adapter.inspect({"provider": "google-antigravity", "user_id": "user_1"})
    assert isinstance(inspected.get("risk_acknowledged_at"), datetime)

    # 5. After acceptance, cli_adapter_for returns the adapter and models appear in the selector
    assert cli_adapter_for("google-antigravity", None) is stub
    receipt = service.refresh(ctx, "google-antigravity")
    assert receipt.count == 1
    models = service.list(ctx, "google-antigravity")
    assert [m.model_id for m in models] == ["gemini-2.5-pro"]

    # Clean up module state
    set_antigravity_risk_acknowledged(None)


def test_gateway_provider_name_and_setup_request_for_cli_providers() -> None:
    from agentos.api.gateway import ProviderSetupRequest, _provider_name, _provider_public

    assert _provider_name("openai-codex") == "openai-codex"
    assert _provider_name("google-antigravity") == "google-antigravity"

    codex_req = ProviderSetupRequest(enabled=True)
    assert codex_req.api_key is None
    assert codex_req.base_url is None
    assert codex_req.risk_acknowledged is None

    now = datetime.now(UTC)
    agy_req = ProviderSetupRequest(enabled=True, risk_acknowledged=True, risk_acknowledged_at=now)
    assert agy_req.risk_acknowledged is True
    assert agy_req.risk_acknowledged_at == now

    public = _provider_public(
        {
            "provider": "google-antigravity",
            "enabled": True,
            "secret_ref": "provider-secret:abc",
            "has_secret": False,
            "key_cooldown_seconds": 60,
            "catalog_refreshed_at": None,
            "risk_acknowledged_at": now,
        }
    )
    assert public["provider"] == "google-antigravity"
    assert public["risk_acknowledged_at"] == now.isoformat()
    assert "secret_ref" not in public

