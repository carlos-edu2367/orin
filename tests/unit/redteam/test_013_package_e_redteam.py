"""Red Team regression suite for Package E (findings #1-#8, 2026-11-21)."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, select

from agentos.agentic.context_overflow import (
    is_synthetic_overflow_text,
    strip_synthetic_error_from_parts,
)
from agentos.agentic.native_turn import _RunnerSink
from agentos.agentic.runtime import AgenticLimits, AgenticTurnRuntime
from agentos.agentic.session import TurnSession
from agentos.agentic.settings import AgentRuntimeSettingsStore
from agentos.api import (
    ApiServices,
    AuthenticatedPrincipal,
    InMemoryClientEventStream,
    InMemorySecurityService,
    create_app,
)
from agentos.api.settings_routes import AgentFile
from agentos.configuration.capabilities import InstanceCapabilities
from agentos.configuration.mode import RuntimeMode
from agentos.conversations.chat import PostgresChatStore
from agentos.permissions.trust import WorkspaceTrustStore
from agentos.persistence.postgres.agentic_activity import PostgresAgenticActivityStore
from agentos.persistence.postgres.migrate import upgrade
from agentos.persistence.postgres.quota import QuotaSnapshotStore
from agentos.persistence.postgres.schema import conversation_messages, conversation_turns
from agentos.providers.antigravity_cli.errors import classify_antigravity_error
from agentos.providers.claude_cli.effective_window import (
    _lookup_override,
    effective_window_for,
)
from agentos.providers.claude_cli.errors import (
    ProviderTransportError,
    classify_result_error_info,
    error_info_to_transport_error,
)
from agentos.providers.cli_adapter import CliResult, CliUsage, RateLimitState
from agentos.providers.codex_cli.errors import classify_codex_error
from agentos.quota.claude_account import ClaudeAccountState
from agentos.quota.models import QuotaSnapshot
from agentos.quota.service import QuotaService
from agentos.settings.loader import load_effective_settings
from agentos.settings.paths import orin_managed_dir, orin_user_dir
from agentos.settings.registry import resolve, resolve_window_overrides_split
from agentos.workers.chat import ChatWorker

AUTH = {"Authorization": "Bearer test-pat"}


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


# ---------------------------------------------------------------------------
# Achado #1 [CRITICAL]: context.windowOverrides inflation & alias defeat
# ---------------------------------------------------------------------------


def test_redteam_01_untrusted_project_window_override_cannot_inflate_window(
    tmp_path: Path,
) -> None:
    project = tmp_path / "untrusted-repo"
    _write_json(
        project / ".orin" / "settings.json",
        {"context": {"windowOverrides": {"anthropic/claude-fable-5-2": 1_000_000}}},
    )
    settings = load_effective_settings(project, trusted=False)
    op_ov, proj_ov, managed_ov = resolve_window_overrides_split(settings)
    assert op_ov == {}
    assert managed_ov == {}
    assert proj_ov == {"anthropic/claude-fable-5-2": 1_000_000}

    account_no_credits = ClaudeAccountState(
        extra_usage_enabled=False,
        overage_models=frozenset({"Fable 5.2"}),
        usage_cache={},
        fetched_at_ms=None,
    )
    ew = effective_window_for(
        "anthropic",
        "claude-fable-5-2",
        window_overrides=op_ov,
        project_window_overrides=proj_ov,
        managed_window_overrides=managed_ov,
        account_state=account_no_credits,
        cli_catalog_window=1_000_000,
    )
    assert ew.tokens == 200_000
    assert ew.source == "account_credits"

    # A project override MAY still lower the effective window below base (200k -> 120k)
    ew_lowered = effective_window_for(
        "anthropic",
        "claude-fable-5-2",
        window_overrides={},
        project_window_overrides={"anthropic/claude-fable-5-2": 120_000},
        managed_window_overrides={},
        account_state=account_no_credits,
        cli_catalog_window=1_000_000,
    )
    assert ew_lowered.tokens == 120_000


def test_redteam_01_project_alias_cannot_defeat_user_override_and_lookup_uses_min(
    tmp_path: Path,
) -> None:
    # Direct _lookup_override must take min across all matching alias keys
    assert (
        _lookup_override(
            "anthropic",
            "claude-fable-5-2",
            {"claude-fable-5-2": 150_000, "anthropic/claude-fable-5-2": 1_000_000},
        )
        == 150_000
    )

    project = tmp_path / "repo"
    _write_json(
        orin_user_dir() / "settings.json",
        {"context": {"windowOverrides": {"claude-fable-5-2": 150_000}}},
    )
    _write_json(
        project / ".orin" / "settings.json",
        {"context": {"windowOverrides": {"anthropic/claude-fable-5-2": 1_000_000}}},
    )
    settings = load_effective_settings(project, trusted=False)
    op_ov, proj_ov, managed_ov = resolve_window_overrides_split(settings)

    ew = effective_window_for(
        "anthropic",
        "claude-fable-5-2",
        window_overrides=op_ov,
        project_window_overrides=proj_ov,
        managed_window_overrides=managed_ov,
    )
    assert ew.tokens == 150_000
    assert ew.source == "override"

    # Project alias with a LOWER value DOES restrict user override downward
    ew_restricted = effective_window_for(
        "anthropic",
        "claude-fable-5-2",
        window_overrides={"claude-fable-5-2": 300_000},
        project_window_overrides={"anthropic/claude-fable-5-2": 120_000},
        managed_window_overrides={},
    )
    assert ew_restricted.tokens == 120_000

    # Managed override ALWAYS wins over both user and project aliases
    ew_managed = effective_window_for(
        "anthropic",
        "claude-fable-5-2",
        window_overrides={"claude-fable-5-2": 100_000},
        project_window_overrides={"claude-fable-5-2": 90_000},
        managed_window_overrides={"anthropic/claude-fable-5-2": 400_000},
    )
    assert ew_managed.tokens == 400_000


# ---------------------------------------------------------------------------
# Achado #2 [HIGH]: SSE quota.updated delivery, shared QuotaService & resets_at
# ---------------------------------------------------------------------------


def test_redteam_02_quota_sse_delivers_without_15s_sleep_and_worker_notifies_stream(
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{(tmp_path / 'quota_sse.db').as_posix()}")
    upgrade(engine, "head")
    try:
        events = InMemoryClientEventStream()
        store = QuotaSnapshotStore(engine)
        api_quota_service = QuotaService(store)

        security = InMemorySecurityService()
        security.add_pat(
            "test-pat",
            AuthenticatedPrincipal("user-1", "cred-1", frozenset({"api"})),
        )
        app = create_app(
            ApiServices(
                security=security,
                events=events,
                quota_service=api_quota_service,
            )
        )

        # Simulate a ChatWorker constructed independently over the same DB engine
        chat_store = PostgresChatStore(engine, PostgresAgenticActivityStore(engine, "secret"))
        worker = ChatWorker(chat_store)
        kwargs = worker._quota_session_kwargs(engine, {"user_id": "user-1"})
        quota_sink = kwargs["quota_sink"]

        async def _stream_once() -> tuple[str, float]:
            received_chunks: list[str] = []
            disconnected = asyncio.Event()
            t0 = time.monotonic()

            async def _receive():
                await disconnected.wait()
                return {"type": "http.disconnect"}

            async def _send(message):
                if message["type"] == "http.response.body":
                    chunk = message.get("body", b"").decode("utf-8", errors="replace")
                    if chunk:
                        received_chunks.append(chunk)
                        if "quota.updated" in chunk:
                            disconnected.set()

            scope = {
                "type": "http",
                "asgi": {"version": "3.0"},
                "http_version": "1.1",
                "method": "GET",
                "scheme": "http",
                "path": "/v1/quota/stream",
                "raw_path": b"/v1/quota/stream",
                "query_string": b"",
                "headers": [(b"authorization", b"Bearer test-pat")],
                "client": ("127.0.0.1", 12345),
                "server": ("test", 80),
            }

            # Emit snapshot after 0.7s (past the first 0.5s queue poll slice where old code slept 15s)
            def _emit_after_delay() -> None:
                time.sleep(0.7)
                quota_sink(
                    "anthropic-cli",
                    RateLimitState.from_info(
                        {
                            "status": "allowed",
                            "unifiedWindows": {
                                "five_hour": {"utilization": 0.55, "resetsAt": 1793206800}
                            },
                        }
                    ),
                )

            thread = threading.Thread(target=_emit_after_delay, daemon=True)
            thread.start()
            try:
                await app(scope, _receive, _send)
                return "".join(received_chunks), time.monotonic() - t0
            finally:
                thread.join(timeout=2.0)

        body, elapsed = asyncio.run(asyncio.wait_for(_stream_once(), timeout=5.0))
        assert "event: quota.updated" in body
        assert "anthropic-cli" in body
        assert elapsed < 3.0, f"SSE delivery stalled ({elapsed:.2f}s); likely sleeping 15s"
    finally:
        engine.dispose()


def test_redteam_02_resets_at_preserved_on_transport_error_and_classic_runtime_sink() -> None:
    reset_ts = 1800000000.0
    rl = RateLimitState(status="rejected", resets_at=reset_ts)

    def _err_result(text: str) -> CliResult:
        return CliResult(
            subtype="error",
            is_error=True,
            result_text=text,
            stop_reason="error",
            num_turns=1,
            duration_ms=10,
            session_id="sess-1",
            api_error_status=429,
            usage=CliUsage(),
        )

    claude_info = classify_result_error_info(
        _err_result("rate limit"),
        rl,
        now=1799999000.0,
    )
    claude_exc = error_info_to_transport_error(claude_info)
    assert isinstance(claude_exc, ProviderTransportError)
    assert claude_exc.resets_at == reset_ts

    codex_info = classify_codex_error(
        _err_result("429 too many requests"),
        rl,
        now=1799999000.0,
    )
    assert codex_info.exception is not None
    assert codex_info.exception.resets_at == reset_ts

    agy_info = classify_antigravity_error(
        _err_result("429 RESOURCE_EXHAUSTED"),
        rl,
        now=1799999000.0,
    )
    assert agy_info.exception is not None
    assert agy_info.exception.resets_at == reset_ts

    # Classic AgenticTurnRuntime loop must call quota_error_sink with resets_at on 429
    captured: list[dict] = []

    class _RateLimitedProvider:
        def stream(self, request):
            raise ProviderTransportError(
                "429 rate limited",
                status_code=429,
                retry_after_seconds=60.0,
                resets_at=reset_ts,
            )

    class _MinimalStore:
        def load(self, turn_id):
            return {
                "turn_id": turn_id,
                "conversation_id": "conv-1",
                "user_id": "u1",
                "provider": "anthropic-cli",
                "model_id": "claude-fable-5-2",
                "user_message": "hi",
                "assistant_message_id": "msg-1",
            }

        def history_for_turn(self, turn):
            return [{"role": "user", "content": "hi"}]

        def delta(self, turn, text):
            pass

        def finish(self, turn, *, failed=False, code=None):
            pass

        def lifecycle(self, turn, state, **payload):
            pass

        def record(self, *args, **kwargs):
            pass

    class _EmptyToolset:
        def schemas(self):
            return []

    rt = AgenticTurnRuntime(
        store=_MinimalStore(),  # type: ignore[arg-type]
        provider=_RateLimitedProvider(),  # type: ignore[arg-type]
        toolset=_EmptyToolset(),  # type: ignore[arg-type]
        limits=AgenticLimits(
            deadline=timedelta(seconds=5),
            max_iterations=1,
            max_actions=1,
            max_provider_retries=0,
        ),
        quota_error_sink=lambda prov, **kw: captured.append({"provider": prov, **kw}),
    )
    result = rt.run("turn-429")
    assert result.state == "failed"
    assert len(captured) == 1
    assert captured[0]["provider"] == "anthropic-cli"
    assert captured[0]["resets_at"] == reset_ts


# ---------------------------------------------------------------------------
# Achado #3 [HIGH]: quota fallback applied BEFORE effective_window / limits
# ---------------------------------------------------------------------------


def test_redteam_03_turn_session_fallback_clears_stale_cli_model_arg() -> None:
    session = TurnSession.__new__(TurnSession)
    session.turn = {
        "user_id": "u1",
        "provider": "anthropic-cli",
        "model_id": "claude-fable-5-2",
    }
    session._cli_model_arg = "claude-fable-5-2[1m]"
    session._window_source = "credits_1m"
    session._window_detail = "1 M, créditos de uso ativos (1M)"
    session._window_rule_index = 3
    session._learned_window_tokens = None
    session.effective_settings = None
    recorded = []
    session._record = lambda *args, **kwargs: recorded.append(args)

    class _FakeGate:
        def select_fallback(self, user_id, provider, model_id, **kwargs):
            emit = kwargs.get("emit_event")
            if callable(emit):
                emit(
                    "quota.fallback_applied",
                    {
                        "from_provider": provider,
                        "from_model": model_id,
                        "to_provider": "openai-codex",
                        "to_model": "gpt-6-luna",
                    },
                )
            return SimpleNamespace(
                from_provider=provider,
                from_model=model_id,
                to_provider="openai-codex",
                to_model="gpt-6-luna",
            )

    session.quota_gate = _FakeGate()
    decision = session.apply_quota_fallback_before_turn()
    assert decision is not None
    assert session.turn["provider"] == "openai-codex"
    assert session.turn["model_id"] == "gpt-6-luna"
    assert session._cli_model_arg is None


def test_redteam_03_chat_worker_applies_fallback_before_window_and_updates_db(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_engine(f"sqlite:///{(tmp_path / 'fallback_worker.db').as_posix()}")
    upgrade(engine, "head")
    try:
        # Configure quota fallback in user settings
        _write_json(
            orin_user_dir() / "settings.json",
            {
                "claudeCli": {"longContextWithCredits": True},
                "quota": {
                    "policy": {"enabled": True},
                    "fallback": {
                        "enabled": True,
                        "chain": ["openai-codex/gpt-6-luna"],
                    },
                },
            },
        )
        # Mark anthropic-cli as exhausted in quota store
        qstore = QuotaSnapshotStore(engine)
        now = datetime.now(UTC)
        qstore.add(
            "u1",
            QuotaSnapshot(
                provider="anthropic-cli",
                window_kind="five_hour",
                used_percent=100.0,
                status="rejected",
                resets_at=now + timedelta(hours=2),
                source="rate_limit_event",
                observed_at=now,
            ),
        )

        chat_store = PostgresChatStore(engine, PostgresAgenticActivityStore(engine, "secret"))
        created = chat_store.create(
            user_id="u1",
            message="hello",
            provider="anthropic",
            model_id="claude-fable-5-2",
            idempotency_key="idem-fallback-1",
        )
        turn = chat_store.claim(created.turn_id)
        assert turn is not None

        worker = ChatWorker(chat_store)
        worker._claude_account_reader = SimpleNamespace(
            read=lambda: ClaudeAccountState(
                extra_usage_enabled=True,
                overage_models=frozenset({"Fable 5.2"}),
                usage_cache={},
                fetched_at_ms=None,
            )
        )
        monkeypatch.setattr(
            worker,
            "_catalog_row_for",
            lambda t: (
                {"base_url": "claude-cli://local", "context_window": 1_000_000}
                if t.get("provider") in {"anthropic", "anthropic-cli"}
                else {"context_window": 272_000}
            ),
        )
        monkeypatch.setattr(worker, "_provider_transport", lambda t: SimpleNamespace(turn_provider=t["provider"], turn_model=t["model_id"]))

        runtime = worker._runtime_for(dict(turn))
        # Fallback MUST have been applied before computing effective_window / cli_model_arg!
        assert runtime.cli_model_arg is None
        assert runtime.limits.context_window_tokens == 272_000

        with engine.connect() as conn:
            row = conn.execute(
                select(conversation_turns.c.provider, conversation_turns.c.model_id).where(
                    conversation_turns.c.turn_id == turn["turn_id"]
                )
            ).mappings().first()
        assert row is not None
        assert row["provider"] == "openai-codex"
        assert row["model_id"] == "gpt-6-luna"
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# Achado #4 [HIGH]: managed beats PROJECT_LOWER_ONLY, PUT locked & effort dict
# ---------------------------------------------------------------------------


def test_redteam_04_managed_wins_project_lower_only_and_put_rejects_locked_keys(
    tmp_path: Path,
) -> None:
    project = tmp_path / "proj"
    _write_json(
        orin_managed_dir() / "managed-settings.json",
        {"effort": {"maxForSubagents": "high"}, "quota": {"policy": {"warnPercent": 60}}},
    )
    _write_json(
        orin_user_dir() / "settings.json",
        {"effort": {"maxForSubagents": "low"}},
    )
    _write_json(
        project / ".orin" / "settings.json",
        {"effort": {"maxForSubagents": "low"}},
    )

    settings = load_effective_settings(project, trusted=True)
    resolved = resolve(settings, "effort.maxForSubagents")
    assert resolved.value == "high"
    assert resolved.layer == "managed"

    security = InMemorySecurityService()
    security.add_pat(
        "test-pat",
        AuthenticatedPrincipal("local-user", "pat-1", frozenset({"api"})),
    )
    app = create_app(
        ApiServices(
            security=security,
            agentic_runtime=AgentRuntimeSettingsStore(tmp_path / "runtime.json"),
            workspace_root=tmp_path / "workspaces",
            capabilities=InstanceCapabilities.for_mode(RuntimeMode.LOCAL),
        )
    )
    client = TestClient(app)
    get_resp = client.get("/v1/settings/orchestration", headers=AUTH)
    assert get_resp.status_code == 200
    items = {item["path"]: item for item in get_resp.json()["items"]}
    assert items["effort.maxForSubagents"]["value"] == "high"
    assert items["effort.maxForSubagents"]["layer"] == "managed"
    assert items["effort.maxForSubagents"]["locked"] is True

    # PUT must reject attempts to overwrite managed-locked keys
    put_resp = client.put(
        "/v1/settings/orchestration",
        headers=AUTH,
        json={"changes": {"effort.maxForSubagents": "low"}},
    )
    assert put_resp.status_code == 403

    put_warn = client.put(
        "/v1/settings/orchestration",
        headers=AUTH,
        json={"changes": {"quota.policy.warnPercent": 90}},
    )
    assert put_warn.status_code == 403


def test_redteam_04_project_effort_dict_preserves_user_scalar_effort(
    tmp_path: Path,
) -> None:
    project = tmp_path / "proj"
    _write_json(orin_user_dir() / "settings.json", {"effort": "high"})
    _write_json(project / ".orin" / "settings.json", {"effort": {"maxForSubagents": "low"}})

    settings = load_effective_settings(project, trusted=False)
    assert settings.data.get("effortLevel") == "high"
    assert settings.provenance.get("effortLevel") == "user"
    assert resolve(settings, "effortLevel").value == "high"
    assert resolve(settings, "effort.maxForSubagents").value == "low"


# ---------------------------------------------------------------------------
# Achado #5 [HIGH]: strip_synthetic_error_from_parts preserves round 1 text & DB sync
# ---------------------------------------------------------------------------


def test_redteam_05_strip_synthetic_error_preserves_round1_text_and_syncs_db(
    tmp_path: Path,
) -> None:
    # Short round-1 legitimate text + leaked round-2 overflow error (<= 400 chars total)
    parts = ["Analisando o arquivo solicitado...", "\n\nPrompt is too long"]
    removed = strip_synthetic_error_from_parts(parts, error_message="Prompt is too long")
    assert removed is True
    assert parts == ["Analisando o arquivo solicitado..."]

    # Concatenated into a single chunk
    single = ["Analisando o arquivo solicitado...\n\nPrompt is too long"]
    removed2 = strip_synthetic_error_from_parts(single, error_message="Prompt is too long")
    assert removed2 is True
    assert single == ["Analisando o arquivo solicitado..."]

    # Generic "context window" phrase in normal prose must NOT false-positive
    assert is_synthetic_overflow_text("A context window define quantos tokens o modelo aceita.") is False
    assert is_synthetic_overflow_text("Request exceeds the context window of the model.") is True

    # Sink discard_synthetic_error_text must also sync cleaned text to PostgresChatStore
    engine = create_engine(f"sqlite:///{(tmp_path / 'overflow_sync.db').as_posix()}")
    upgrade(engine, "head")
    try:
        chat_store = PostgresChatStore(engine, PostgresAgenticActivityStore(engine, "secret"))
        created = chat_store.create(
            user_id="u1",
            message="hi",
            provider="anthropic",
            model_id="claude-fable-5-2",
            idempotency_key="idem-overflow-1",
        )
        turn = chat_store.claim(created.turn_id)
        assert turn is not None
        chat_store.delta(turn, "Analisando o arquivo solicitado...")
        chat_store.delta(turn, "\n\nPrompt is too long")

        sink = _RunnerSink.__new__(_RunnerSink)
        sink.text_parts = ["Analisando o arquivo solicitado...", "\n\nPrompt is too long"]
        sink._round_text_parts = ["\n\nPrompt is too long"]
        sink._runner = SimpleNamespace(
            turn=turn,
            runtime=SimpleNamespace(store=chat_store),
        )
        assert sink.discard_synthetic_error_text(error_message="Prompt is too long") is True
        assert sink.text_parts == ["Analisando o arquivo solicitado..."]

        with engine.connect() as conn:
            content = conn.execute(
                select(conversation_messages.c.content).where(
                    conversation_messages.c.message_id == turn["assistant_message_id"]
                )
            ).scalar_one()
        assert content == "Analisando o arquivo solicitado..."
        assert "Prompt is too long" not in content
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# Achado #6 [HIGH]: AgentFile.effort accepts "ultra"
# ---------------------------------------------------------------------------


def test_redteam_06_agent_file_accepts_ultra_effort(tmp_path: Path) -> None:
    model = AgentFile(
        scope="user",
        description="Codex reviewer",
        prompt="Review carefully.",
        effort="ultra",
    )
    assert model.effort == "ultra"

    security = InMemorySecurityService()
    security.add_pat(
        "test-pat",
        AuthenticatedPrincipal("local-user", "pat-1", frozenset({"api"})),
    )
    app = create_app(
        ApiServices(
            security=security,
            agentic_runtime=AgentRuntimeSettingsStore(tmp_path / "runtime.json"),
            workspace_root=tmp_path / "workspaces",
            capabilities=InstanceCapabilities.for_mode(RuntimeMode.LOCAL),
        )
    )
    client = TestClient(app)
    resp = client.put(
        "/v1/settings/agents/codex-reviewer",
        headers=AUTH,
        json={
            "scope": "user",
            "description": "Codex reviewer",
            "prompt": "Review carefully.",
            "effort": "ultra",
        },
    )
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Achado #7 [MEDIUM]: trust store, host_folders & mutable=True on context routes + worker
# ---------------------------------------------------------------------------


def test_redteam_07_context_window_routes_enforce_capabilities_trust_and_mutable_delete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 1. Server mode (host_folders=False) must reject explicit project_root
    security_srv = InMemorySecurityService()
    security_srv.add_pat(
        "test-pat",
        AuthenticatedPrincipal("user-1", "cred-1", frozenset({"api"})),
    )
    srv_app = create_app(
        ApiServices(
            security=security_srv,
            events=InMemoryClientEventStream(),
            capabilities=InstanceCapabilities.for_mode(RuntimeMode.SERVER),
        )
    )
    srv_client = TestClient(srv_app)
    resp_srv = srv_client.get(
        "/v1/context/windows",
        params={"project_root": str(tmp_path)},
        headers=AUTH,
    )
    assert resp_srv.status_code == 404

    # 2. Local mode must pass trusted=False for untrusted project_root and trusted=True once trusted
    captured_trust: list[bool] = []
    import agentos.api.context_window_routes as cw_routes

    orig_loader = cw_routes._load_window_settings

    def _spy_loader(root, *, trusted: bool = False):
        captured_trust.append(bool(trusted))
        return orig_loader(root, trusted=trusted)

    monkeypatch.setattr(cw_routes, "_load_window_settings", _spy_loader)

    security_loc = InMemorySecurityService()
    security_loc.add_pat(
        "test-pat",
        AuthenticatedPrincipal("user-1", "cred-1", frozenset({"api"})),
    )
    loc_app = create_app(
        ApiServices(
            security=security_loc,
            events=InMemoryClientEventStream(),
            capabilities=InstanceCapabilities.for_mode(RuntimeMode.LOCAL),
        )
    )
    loc_client = TestClient(loc_app)
    proj = tmp_path / "local-proj"
    proj.mkdir()

    r1 = loc_client.get("/v1/context/windows", params={"project_root": str(proj)}, headers=AUTH)
    assert r1.status_code == 200
    assert captured_trust[-1] is False

    WorkspaceTrustStore().trust("user-1", proj)
    r2 = loc_client.get("/v1/context/windows", params={"project_root": str(proj)}, headers=AUTH)
    assert r2.status_code == 200
    assert captured_trust[-1] is True

    # 3. DELETE must pass mutable=True to principal_for (verified via cookie auth without CSRF or spy)
    mutable_flags: list[bool] = []
    from fastapi import FastAPI

    probe_app = FastAPI()

    def _spy_principal_for(request, *, mutable: bool = False, **kwargs):
        mutable_flags.append(bool(mutable))
        return AuthenticatedPrincipal("user-1", "cred-1", frozenset({"api"}))

    cw_routes.register_context_window_routes(
        probe_app,
        ApiServices(
            security=security_loc,
            events=InMemoryClientEventStream(),
            capabilities=InstanceCapabilities.for_mode(RuntimeMode.LOCAL),
        ),
        _spy_principal_for,
    )
    probe_client = TestClient(probe_app)
    del_resp = probe_client.delete("/v1/context/windows/anthropic/claude-fable-5-2")
    assert del_resp.status_code == 200
    assert mutable_flags == [True]

    # 4. ChatWorker._context_window_settings must read turn["workspace_root_path"] and check WorkspaceTrustStore
    untrusted_ws = tmp_path / "conv-ws"
    _write_json(
        untrusted_ws / ".orin" / "settings.json",
        {"context": {"windowOverrides": {"anthropic/claude-fable-5-2": 130_000}}},
    )
    worker = ChatWorker.__new__(ChatWorker)
    worker._workspace_root = None

    observed_load_calls: list[tuple[Path | None, bool]] = []
    import agentos.settings.loader as loader_mod

    orig_load_eff = loader_mod.load_effective_settings

    def _spy_load_eff(project_root, **kw):
        observed_load_calls.append((Path(project_root) if project_root else None, bool(kw.get("trusted"))))
        return orig_load_eff(project_root, **kw)

    monkeypatch.setattr(loader_mod, "load_effective_settings", _spy_load_eff)

    worker._context_window_settings(
        {"user_id": "user-1", "workspace_root_path": str(untrusted_ws)}
    )
    assert observed_load_calls[-1] == (untrusted_ws, False)

    WorkspaceTrustStore().trust("user-1", untrusted_ws)
    worker._context_window_settings(
        {"user_id": "user-1", "workspace_root_path": str(untrusted_ws)}
    )
    assert observed_load_calls[-1] == (untrusted_ws, True)


# ---------------------------------------------------------------------------
# Achado #8 [LOW]: TurnSession.record_usage records subagent's own effort
# ---------------------------------------------------------------------------


def test_redteam_08_subagent_record_usage_uses_subagent_effort_not_parent() -> None:
    session = TurnSession.__new__(TurnSession)
    session.main_agent_id = "agent-main"
    session.turn = {"provider": "anthropic", "model_id": "claude-opus-5-5"}
    session.model_capabilities = ("reasoning", "thinking")
    session.effective_settings = None
    session._active_effort = "high"
    session._active_effort_source = "turn"
    session._agent_efforts = {}
    session._record = lambda *args, **kwargs: None

    usage_calls: list[dict] = []

    class _FakeAgentsStore:
        def record_usage(self, agent_id, provider, model_id, **kwargs):
            usage_calls.append({"agent_id": agent_id, "provider": provider, "model_id": model_id, **kwargs})

    session.agents_store = _FakeAgentsStore()  # type: ignore[assignment]

    # Resolve subagent effort explicitly to "low" via call_effort
    sub_level = session._definition_effort(
        None,
        "claude-opus-5-5",
        "anthropic",
        call_effort="low",
        subagent_type="explore",
        agent_id="agent-sub-1",
    )
    assert sub_level == "low"

    session.record_usage(
        session.turn,
        input_tokens=100,
        output_tokens=50,
        total_tokens=150,
        agent_id="agent-sub-1",
    )
    session.record_usage(
        session.turn,
        input_tokens=200,
        output_tokens=80,
        total_tokens=280,
        agent_id="agent-main",
    )

    assert usage_calls[0]["agent_id"] == "agent-sub-1"
    assert usage_calls[0]["effort"] == "low"
    assert usage_calls[0]["effort_source"] == "call"

    assert usage_calls[1]["agent_id"] == "agent-main"
    assert usage_calls[1]["effort"] == "high"
    assert usage_calls[1]["effort_source"] == "turn"
