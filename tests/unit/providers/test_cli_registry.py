"""Unit tests for the subscription CLI provider registry and configuration gates."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

import pytest
from sqlalchemy import create_engine, insert

from agentos.agentic.provider_stream import HTTPProviderStreamTransport, NormalizedStreamItem, StreamKind
from agentos.persistence.postgres.migrate import upgrade
from agentos.persistence.postgres.provider_configuration import PostgresProviderConfigurationAdapter
from agentos.persistence.postgres.provider_models import PostgresProviderCatalogRepository
from agentos.persistence.postgres.schema import metadata, provider_configurations
from agentos.persistence.provider_secrets import ProviderSecretCipher
from agentos.provider_catalog.claude_code import DEFAULT_CLAUDE_CLI_BRIDGE_URL
from agentos.provider_catalog.models import ProviderCatalogContext
from agentos.provider_catalog.service import ProviderCatalogUnavailable, ProviderModelCatalogService
import agentos.providers.cli_registry as cli_registry_mod
from agentos.providers.cli_adapter import (
    CliCapabilities,
    CliModelInfo,
    CliTurnRequest,
    ToolServerWiring,
)
from agentos.providers.cli_registry import (
    CLI_PROVIDERS,
    cli_adapter_for,
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


def test_cli_registry_has_no_global_antigravity_opt_in_cache() -> None:
    """Global opt-in state is forbidden because it leaks across users in server mode."""
    assert not hasattr(cli_registry_mod, "_ANTIGRAVITY_RISK_ACKNOWLEDGED_AT")
    assert not hasattr(cli_registry_mod, "set_antigravity_risk_acknowledged")
    assert not hasattr(cli_registry_mod, "antigravity_risk_acknowledged_at")
    assert not hasattr(cli_registry_mod, "is_antigravity_risk_acknowledged")


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

    # 5. Calling cli_adapter_for without risk_acknowledged_at STILL returns None (no global cache),
    # while passing the user's DB risk_acknowledged_at returns the adapter and models appear in the selector
    assert cli_adapter_for("google-antigravity", None) is None
    assert (
        cli_adapter_for(
            "google-antigravity",
            None,
            risk_acknowledged_at=inspected.get("risk_acknowledged_at"),
        )
        is stub
    )
    receipt = service.refresh(ctx, "google-antigravity")
    assert receipt.count == 1
    models = service.list(ctx, "google-antigravity")
    assert [m.model_id for m in models] == ["gemini-2.5-pro"]


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


def test_antigravity_opt_in_bypass_vectors_and_chat_worker_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agentos.providers.claude_cli.provider import ClaudeCliProvider
    from agentos.workers.chat import ChatWorker

    monkeypatch.setenv("AGENTOS_PROVIDER_ENCRYPTION_KEY", "0" * 32)
    codex_stub = _StubSubscriptionAdapter("codex", "gpt-5.4")
    agy_stub = _StubSubscriptionAdapter("antigravity", "gemini-2.5-pro")
    monkeypatch.setitem(CLI_PROVIDERS, "openai-codex", lambda: codex_stub)
    monkeypatch.setitem(CLI_PROVIDERS, "google-antigravity", lambda: agy_stub)

    # 1. Whitespace or "false"/"0" strings must NOT satisfy risk_acknowledged_at
    assert cli_adapter_for("google-antigravity", None, risk_acknowledged_at=None) is None
    assert cli_adapter_for("google-antigravity", None, risk_acknowledged_at="   ") is None
    assert cli_adapter_for("google-antigravity", None, risk_acknowledged_at="false") is None
    assert cli_adapter_for("google-antigravity", None, risk_acknowledged_at="0") is None

    # 2. Explicit risk_acknowledged=False must override a stale risk_acknowledged_at timestamp
    engine = _sqlite_engine()
    cipher = _cipher()
    config_adapter = PostgresProviderConfigurationAdapter(engine, cipher=cipher)
    with pytest.raises(ValueError, match=r"(?i)(aceit|risco|termos|risk|acknowledge)"):
        config_adapter.configure(
            {
                "provider": "google-antigravity",
                "user_id": "user_1",
                "enabled": True,
                "api_key": "",
                "base_url": None,
                "risk_acknowledged": False,
                "risk_acknowledged_at": "2026-11-06T12:00:00Z",
            }
        )

    # 3. ChatWorker._provider_transport and _transport_for route openai-codex directly to ClaudeCliProvider
    config_adapter.configure(
        {
            "provider": "openai-codex",
            "user_id": "user_1",
            "enabled": True,
            "api_key": "",
            "base_url": None,
        }
    )
    worker = ChatWorker.__new__(ChatWorker)
    worker.store = type("S", (), {"_engine": engine})()

    codex_transport = worker._provider_transport(
        {"provider": "openai-codex", "user_id": "user_1", "model_id": "gpt-5.4"}
    )
    assert isinstance(codex_transport, ClaudeCliProvider)
    assert codex_transport.adapter is codex_stub
    codex_transport.close()

    codex_sub = worker._transport_for("user_1", "openai-codex", "gpt-5.4")
    assert isinstance(codex_sub, ClaudeCliProvider)
    assert codex_sub.adapter is codex_stub
    codex_sub.close()


def test_antigravity_multi_user_isolation_across_all_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """User A acknowledges risk in DB and gets access across all paths; User B in the same DB/process remains blocked."""
    from agentos.providers.claude_cli.provider import ClaudeCliProvider
    from agentos.workers.chat import ChatWorker
    from tests.integration.server_world import build_server_world

    agy_stub = _StubSubscriptionAdapter("antigravity", "gemini-2.5-pro")
    monkeypatch.setitem(CLI_PROVIDERS, "google-antigravity", lambda: agy_stub)

    world = build_server_world(tmp_path, monkeypatch)
    user_a = world.admin("user_a")
    user_b = world.member("user_b", admin=user_a)

    # --- Path 1: /v1/providers/* HTTP API ---
    # User A acknowledges risk and enables google-antigravity
    res_a_put = user_a.api.put(
        "/v1/providers/google-antigravity",
        json={"enabled": True, "risk_acknowledged": True},
        headers=user_a.headers(),
    )
    assert res_a_put.status_code == 200, res_a_put.text
    body_a = res_a_put.json()
    assert body_a["enabled"] is True
    assert body_a.get("risk_acknowledged_at") is not None

    # User B tries to enable google-antigravity without risk_acknowledged -> rejected
    res_b_put = user_b.api.put(
        "/v1/providers/google-antigravity",
        json={"enabled": True},
        headers=user_b.headers(),
    )
    assert res_b_put.status_code >= 400, res_b_put.text

    # User A refreshes and lists models via API -> succeeds
    res_a_refresh = user_a.api.post(
        "/v1/providers/google-antigravity/models:refresh",
        headers=user_a.headers(),
    )
    assert res_a_refresh.status_code == 200, res_a_refresh.text
    res_a_models = user_a.api.get("/v1/providers/google-antigravity/models")
    assert res_a_models.status_code == 200, res_a_models.text
    assert [m["model_id"] for m in res_a_models.json()["items"]] == ["gemini-2.5-pro"]

    # User B refreshes and lists models via API -> blocked / empty
    res_b_refresh = user_b.api.post(
        "/v1/providers/google-antigravity/models:refresh",
        headers=user_b.headers(),
    )
    assert res_b_refresh.status_code >= 400, res_b_refresh.text
    res_b_models = user_b.api.get("/v1/providers/google-antigravity/models")
    assert res_b_models.status_code == 200, res_b_models.text
    assert res_b_models.json()["items"] == []

    # Even if a DB row with enabled=True and risk_acknowledged_at=None is inserted directly for User B:
    now = datetime.now(UTC)
    with world.engine.begin() as conn:
        conn.execute(
            insert(provider_configurations).values(
                user_id=user_b.user_id,
                provider="google-antigravity",
                enabled=True,
                base_url=None,
                secret_ref="ref-user-b",
                key_cooldown_seconds=60,
                catalog_refreshed_at=now,
                risk_acknowledged_at=None,
                created_at=now,
                updated_at=now,
            )
        )

    # --- Path 2: cli_adapter_for ---
    assert cli_adapter_for("google-antigravity", None) is None
    assert cli_adapter_for("google-antigravity", None, risk_acknowledged_at=None) is None
    assert (
        cli_adapter_for(
            "google-antigravity",
            None,
            risk_acknowledged_at=body_a["risk_acknowledged_at"],
        )
        is agy_stub
    )

    # --- Path 3: ProviderModelCatalogService ---
    catalog_service: ProviderModelCatalogService = world.services.provider_catalog
    ctx_a = ProviderCatalogContext(user_id=user_a.user_id, purpose="provider.catalog")
    ctx_b = ProviderCatalogContext(user_id=user_b.user_id, purpose="provider.catalog")

    assert [m.model_id for m in catalog_service.list(ctx_a, "google-antigravity")] == ["gemini-2.5-pro"]
    assert catalog_service.list(ctx_b, "google-antigravity") == []
    with pytest.raises(ProviderCatalogUnavailable):
        catalog_service.refresh(ctx_b, "google-antigravity")

    # --- Path 4: ChatWorker ---
    worker = ChatWorker.__new__(ChatWorker)
    worker.store = type("S", (), {"_engine": world.engine})()

    transport_a = worker._provider_transport(
        {"provider": "google-antigravity", "user_id": user_a.user_id, "model_id": "gemini-2.5-pro"}
    )
    assert isinstance(transport_a, ClaudeCliProvider)
    assert transport_a.adapter is agy_stub
    transport_a.close()

    sub_a = worker._transport_for(user_a.user_id, "google-antigravity", "gemini-2.5-pro")
    assert isinstance(sub_a, ClaudeCliProvider)
    assert sub_a.adapter is agy_stub
    sub_a.close()

    with pytest.raises(ValueError, match="provider unavailable"):
        worker._provider_transport(
            {"provider": "google-antigravity", "user_id": user_b.user_id, "model_id": "gemini-2.5-pro"}
        )
    with pytest.raises(ValueError, match="provider unavailable"):
        worker._transport_for(user_b.user_id, "google-antigravity", "gemini-2.5-pro")

    # --- Path 5: HTTPProviderStreamTransport (provider_stream) ---
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

    # User B (no risk_acknowledged_at) -> rejected before any CLI or HTTP invocation
    stream_b = HTTPProviderStreamTransport(
        provider="google-antigravity",
        model="gemini-2.5-pro",
        api_key="",
        base_url="",
    )
    with pytest.raises(ValueError, match=r"(?i)(aceit|risco|termos|risk|acknowledge|unavailable)"):
        list(
            stream_b.stream(
                {
                    "execution_id": "exec_b",
                    "user_id": user_b.user_id,
                    "model": "gemini-2.5-pro",
                    "messages": [{"role": "user", "content": "oi"}],
                    "tools": [],
                }
            )
        )
    assert captured_adapters == []

    # User A (explicit risk_acknowledged_at from DB) -> succeeds
    stream_a = HTTPProviderStreamTransport(
        provider="google-antigravity",
        model="gemini-2.5-pro",
        api_key="",
        base_url="",
        risk_acknowledged_at=body_a["risk_acknowledged_at"],
    )
    events_a = list(
        stream_a.stream(
            {
                "execution_id": "exec_a",
                "user_id": user_a.user_id,
                "model": "gemini-2.5-pro",
                "messages": [{"role": "user", "content": "oi"}],
                "tools": [],
            }
        )
    )
    assert any(ev.kind is StreamKind.TEXT and ev.text == "from-antigravity:gemini-2.5-pro" for ev in events_a)
    assert captured_adapters == [agy_stub]
    world.engine.dispose()



def test_redteam_bogus_risk_acknowledged_at_strings_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Red Team C4: Non-ISO strings or integers in risk_acknowledged_at must be rejected by
    cli_adapter_for and by PUT /v1/providers/google-antigravity (even with in-memory FakeProviderConfiguration).
    """
    from fastapi.testclient import TestClient
    from agentos.api import ApiServices, AuthenticatedPrincipal, InMemorySecurityService, create_app

    agy_stub = _StubSubscriptionAdapter("antigravity", "gemini-2.5-pro")
    monkeypatch.setitem(CLI_PROVIDERS, "google-antigravity", lambda: agy_stub)

    for bad_ack in (
        None,
        False,
        0,
        1,
        "   ",
        "false",
        "0",
        "no",
        "none",
        "null",
        "true",
        "yes",
        "1",
        "not-a-timestamp",
    ):
        assert (
            cli_adapter_for("google-antigravity", None, risk_acknowledged_at=bad_ack)
            is None
        )

    class _RecordingConfigPort:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        def configure(self, command: dict[str, Any]) -> dict[str, Any]:
            self.calls.append(command)
            return {
                "provider": command["provider"],
                "enabled": command["enabled"],
                "has_secret": False,
                "key_cooldown_seconds": 60,
                "catalog_refreshed_at": None,
                "risk_acknowledged_at": command.get("risk_acknowledged_at"),
            }

    security = InMemorySecurityService()
    security.add_pat(
        "pat-ok",
        AuthenticatedPrincipal("user-1", "cred-1", frozenset({"api"})),
    )
    cfg_port = _RecordingConfigPort()
    app = create_app(ApiServices(security=security, provider_configuration=cfg_port))  # type: ignore[arg-type]
    client = TestClient(app, raise_server_exceptions=False)
    for bogus_str in ("true", "yes", "1", "not-a-timestamp", "0", "false"):
        resp = client.put(
            "/v1/providers/google-antigravity",
            json={"enabled": True, "risk_acknowledged_at": bogus_str},
            headers={"Authorization": "Bearer pat-ok", "Idempotency-Key": f"idem-{bogus_str}"},
        )
        assert resp.status_code >= 400, f"Expected failure for bogus risk_acknowledged_at={bogus_str!r}, got {resp.status_code}: {resp.text}"
    assert cfg_port.calls == []


def test_passo1_final_review_fixes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify all Passo 1 final review fixes:
    1. _resolve_native_cwd POSIX UID suffix & Antigravity .git stability across Turn 1 -> Turn 2
    2. ensure_isolated_cwd purges .agents, .codex, AGENTS.md, AGENTS.override.md, GEMINI.md
    3. Catalog input_modalities == ('text',) for openai-codex and google-antigravity + encode_codex_user_turn rejects image blocks
    4. PostgresProviderModelCatalog, KNOWN_PROVIDERS, and reasoning_supported include openai-codex and google-antigravity
    5. ClaudeCliProvider.stream() with AntigravityCliAdapter applies wire_no_tools (--agent orin --settings) and cleans up
    """
    import os
    from agentos.agentic.effort import reasoning_supported
    from agentos.agentic.native_turn import _resolve_native_cwd
    from agentos.cli_code.selection import KNOWN_PROVIDERS
    from agentos.provider_catalog.resolver_catalog import (
        AuthorizedModelListQuery,
        PostgresProviderModelCatalog,
    )
    from agentos.providers.antigravity_cli.adapter import AntigravityCliAdapter
    from agentos.providers.claude_cli.command import (
        ensure_isolated_cwd,
    )
    from agentos.providers.codex_cli.adapter import CodexCliAdapter
    from agentos.providers.codex_cli.command import encode_codex_user_turn

    # 1a. Antigravity workdir with .git created on Turn 1 does not flip to /tmp on Turn 2
    fake_orin_home = tmp_path / "orin_home_outside_git"
    monkeypatch.setattr(
        "agentos.agentic.native_turn.native_workdir",
        lambda slug="claude": fake_orin_home / "data" / f"{slug}-cli" / "workdir",
    )
    turn1_cwd = _resolve_native_cwd("antigravity")
    assert turn1_cwd == fake_orin_home / "data" / "antigravity-cli" / "workdir"
    turn1_cwd.mkdir(parents=True, exist_ok=True)
    (turn1_cwd / ".git").mkdir(exist_ok=True)
    turn2_cwd = _resolve_native_cwd("antigravity")
    assert turn2_cwd == turn1_cwd

    # 1b. When inside a git repo on POSIX, non-claude adapters preserve UID in isolated cwd name
    git_orin_home = tmp_path / "git_repo" / "orin_home"
    (tmp_path / "git_repo" / ".git").mkdir(parents=True)
    monkeypatch.setattr(
        "agentos.agentic.native_turn.native_workdir",
        lambda slug="claude": git_orin_home / "data" / f"{slug}-cli" / "workdir",
    )
    monkeypatch.setattr(
        "agentos.agentic.native_turn.default_isolated_cwd",
        lambda: tmp_path / "orin-claude-cli-cwd-4242",
    )
    assert _resolve_native_cwd("codex").name == "orin-codex-cli-cwd-4242-native"
    assert _resolve_native_cwd("antigravity").name == "orin-antigravity-cli-cwd-4242-native"

    monkeypatch.setenv("AGENTOS_PROVIDER_ENCRYPTION_KEY", "0" * 32)

    # 2. ensure_isolated_cwd purges .agents, .codex, AGENTS.md, AGENTS.override.md, GEMINI.md
    iso_dir = tmp_path / "iso_cwd"
    iso_dir.mkdir(parents=True)
    (iso_dir / ".agents" / "agents").mkdir(parents=True)
    (iso_dir / ".agents" / "agents" / "orin.md").write_text("stale", encoding="utf-8")
    (iso_dir / ".codex").mkdir(parents=True)
    (iso_dir / "AGENTS.md").write_text("injected", encoding="utf-8")
    (iso_dir / "AGENTS.override.md").write_text("injected", encoding="utf-8")
    (iso_dir / "GEMINI.md").write_text("injected", encoding="utf-8")
    ensure_isolated_cwd(iso_dir)
    for entry in (".agents", ".codex", "AGENTS.md", "AGENTS.override.md", "GEMINI.md"):
        assert not (iso_dir / entry).exists(), f"{entry} was not purged by ensure_isolated_cwd"

    # 3. encode_codex_user_turn rejects image blocks & catalog stores input_modalities==("text",)
    media_note = encode_codex_user_turn([{"type": "image", "source": {"type": "base64", "data": "AAAA"}}]).decode()
    assert "AAAA" not in media_note and "omitida" in media_note

    engine = _sqlite_engine()
    cipher = _cipher()
    config_adapter = PostgresProviderConfigurationAdapter(engine, cipher=cipher)
    repo = PostgresProviderCatalogRepository(engine, cipher=cipher)
    service = ProviderModelCatalogService(repo)
    ctx = ProviderCatalogContext(user_id="user_1", purpose="provider.catalog")

    monkeypatch.setitem(CLI_PROVIDERS, "openai-codex", CodexCliAdapter)
    monkeypatch.setitem(CLI_PROVIDERS, "google-antigravity", AntigravityCliAdapter)

    config_adapter.configure(
        {
            "provider": "openai-codex",
            "user_id": "user_1",
            "enabled": True,
            "api_key": "",
            "base_url": None,
        }
    )
    config_adapter.configure(
        {
            "provider": "google-antigravity",
            "user_id": "user_1",
            "enabled": True,
            "api_key": "",
            "base_url": None,
            "risk_acknowledged": True,
        }
    )
    service.refresh(ctx, "openai-codex")
    service.refresh(ctx, "google-antigravity")

    codex_models = service.list(ctx, "openai-codex")
    agy_models = service.list(ctx, "google-antigravity")
    assert codex_models and all(m.input_modalities == ("text",) for m in codex_models)
    # agy's stream-json input is text-only, but an image in an MCP tool result
    # reaches the model, so Gemini and Claude are catalogued as seeing images.
    agy_by_id = {m.model_id: m.input_modalities for m in agy_models}
    assert agy_by_id["gemini-4-argon-high"] == (
        "text",
        "image",
        "audio",
        "video",
    )
    assert agy_by_id["claude-sonnet-5-5-high"] == ("text", "image")
    assert agy_by_id["gpt-oss-120b-medium"] == ("text",)

    # 4. PostgresProviderModelCatalog, KNOWN_PROVIDERS, and reasoning_supported
    assert "openai-codex" in KNOWN_PROVIDERS
    assert "google-antigravity" in KNOWN_PROVIDERS
    assert reasoning_supported("openai-codex", "gpt-5.4", ("tools", "reasoning")) is True
    assert reasoning_supported("google-antigravity", "gemini-2.5-pro", ("tools", "reasoning")) is True

    resolver_cat = PostgresProviderModelCatalog(engine)
    page = resolver_cat.list_models(
        AuthorizedModelListQuery(context=ctx)
    )
    providers_in_page = {str(item.provider_ref).rsplit(":", 1)[-1] for item in page.items}
    assert "openai-codex" in providers_in_page
    assert "google-antigravity" in providers_in_page

    # 5. ClaudeCliProvider.stream() with AntigravityCliAdapter applies wire_no_tools (--agent orin --settings) and cleans up
    from agentos.providers.claude_cli.provider import ClaudeCliProvider

    captured_cmd: list[str] = []
    captured_settings_exists_during_run: list[bool] = []
    captured_settings_Paths: list[Path] = []

    class _FakePopen:
        def __init__(self, cmd: list[str], **kwargs: Any) -> None:
            import io
            captured_cmd.extend(cmd)
            if "--settings" in cmd:
                sp = Path(cmd[cmd.index("--settings") + 1])
                captured_settings_Paths.append(sp)
                captured_settings_exists_during_run.append(sp.is_file())
            self.stdin = io.StringIO()
            self.stdout = io.StringIO(
                '{"event":"init","conversation_id":"s-1","init":{"model":"gemini-3.1-pro-high","tools":[]}}\n'
                '{"event":"result","conversation_id":"s-1","result":{"status":"SUCCESS","finished_reason":"","response":"OK","usage":{"input_tokens":5,"output_tokens":2}}}\n'
            )
            self.stderr = io.StringIO("")
            self.returncode = 0

        def poll(self) -> int:
            return 0

        def wait(self, timeout: float | None = None) -> int:
            return 0

    agy_adapter = AntigravityCliAdapter()
    monkeypatch.setattr(agy_adapter, "resolve_binary", lambda env=None: ("fake-agy", (1, 5, 2)))
    prov = ClaudeCliProvider(
        model="gemini-3.1-pro-high",
        adapter=agy_adapter,
        popen_factory=_FakePopen,
    )
    items = list(
        prov.stream(
            {
                "model": "gemini-3.1-pro-high",
                "system_prompt": "Custom fallback system prompt",
                "messages": [{"role": "user", "content": "olá"}],
            }
        )
    )
    assert any(i.kind is StreamKind.TEXT and i.text == "OK" for i in items)
    assert "--agent" in captured_cmd and "orin" in captured_cmd
    assert "--settings" in captured_cmd
    assert captured_settings_exists_during_run == [True]
    assert all(not p.exists() for p in captured_settings_Paths)
    prov.close()
