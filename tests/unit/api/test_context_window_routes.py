from __future__ import annotations

import asyncio
import gc
import time
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine

from agentos.api import (
    ApiServices,
    AuthenticatedPrincipal,
    InMemoryClientEventStream,
    InMemorySecurityService,
    create_app,
)
from agentos.persistence.postgres.context_observations import ContextObservationStore
from agentos.persistence.postgres.migrate import upgrade
from agentos.quota.claude_account import ClaudeAccountReader

FIXTURES = Path(__file__).resolve().parents[1] / "providers" / "claude_cli" / "fixtures"


class _FakeCatalog:
    def list(self, ctx, provider_name=None, favorites_only=False):
        return [
            SimpleNamespace(
                provider="anthropic",
                model_id="claude-fable-5-2",
                display_name="Claude Fable 5.2 (Assinatura CLI)",
                context_window=1_000_000,
                favorite=True,
            ),
            SimpleNamespace(
                provider="anthropic",
                model_id="claude-opus-5-5",
                display_name="Claude Opus 5.5 (Assinatura CLI)",
                context_window=1_000_000,
                favorite=True,
            ),
        ]


def _make_app(obs_store, *, provider_catalog=None, claude_reader=None):
    security = InMemorySecurityService()
    security.add_pat("pat", AuthenticatedPrincipal("user-1", "cred-1", frozenset({"api"})))
    services = ApiServices(
        security=security,
        events=InMemoryClientEventStream(),
        provider_catalog=provider_catalog,
        context_observations=obs_store,
    )
    if claude_reader is not None:
        services.claude_account_reader = claude_reader
    return create_app(services)


def test_get_and_delete_context_windows(tmp_path: Path, monkeypatch) -> None:
    engine = create_engine(f"sqlite:///{(tmp_path / 'ctx_api.db').as_posix()}")
    upgrade(engine, "head")
    try:
        t0 = datetime(2026, 11, 21, 11, 0, tzinfo=UTC)
        obs = ContextObservationStore(engine, clock=lambda: t0)
        obs.learn(
            "user-1",
            "anthropic",
            "claude-fable-5-2",
            200_000,
            "error_raw",
            first_message_tokens=205_123,
        )
        reader = ClaudeAccountReader(FIXTURES / "claude-json-no-credits.json")
        monkeypatch.setattr(
            "agentos.api.context_window_routes._load_window_settings",
            lambda _root: ({"anthropic/claude-opus-5-5": 500_000}, False),
        )
        app = _make_app(obs, provider_catalog=_FakeCatalog(), claude_reader=reader)
        client = TestClient(app)

        resp = client.get("/v1/context/windows", headers={"Authorization": "Bearer pat"})
        assert resp.status_code == 200
        body = resp.json()
        assert "windows" in body
        by_key = {(w["provider"], w["model_id"]): w for w in body["windows"]}

        fable = by_key[("anthropic", "claude-fable-5-2")]
        assert fable["catalog_tokens"] == 1_000_000
        assert fable["learned"]["tokens"] == 200_000
        assert fable["learned"]["source"] == "error_raw"
        assert fable["learned"]["observed_at"] is not None
        assert fable["override_tokens"] is None
        assert fable["effective_tokens"] == 200_000
        assert fable["effective_source"] == "learned"
        assert isinstance(fable["effective_detail"], str) and fable["effective_detail"]

        opus = by_key[("anthropic", "claude-opus-5-5")]
        assert opus["override_tokens"] == 500_000
        assert opus["effective_tokens"] == 500_000
        assert opus["effective_source"] == "override"
        assert opus["learned"] is None

        # Forget learned observation
        del_resp = client.delete(
            "/v1/context/windows/anthropic/claude-fable-5-2",
            headers={"Authorization": "Bearer pat"},
        )
        assert del_resp.status_code == 200
        assert del_resp.json()["forgotten"] is True

        # Now Fable falls back to rule 3 (account_credits -> 200_000) and learned is null
        resp2 = client.get("/v1/context/windows", headers={"Authorization": "Bearer pat"})
        assert resp2.status_code == 200
        by_key2 = {(w["provider"], w["model_id"]): w for w in resp2.json()["windows"]}
        fable2 = by_key2[("anthropic", "claude-fable-5-2")]
        assert fable2["learned"] is None
        assert fable2["effective_tokens"] == 200_000
        assert fable2["effective_source"] == "account_credits"
    finally:
        engine.dispose()


def test_context_windows_routes_do_not_stall_event_loop() -> None:
    class _SlowObsStore:
        def list(self, user_id: str):
            time.sleep(0.8)
            return []

        def invalidate(self, user_id: str, provider: str, model_id: str):
            time.sleep(0.8)
            return True

    app = _make_app(_SlowObsStore())

    async def _run() -> float:
        transport = ASGITransport(app=app)
        gc.collect()
        gc_was = gc.isenabled()
        gc.disable()
        try:
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                slow_task = asyncio.create_task(
                    client.get("/v1/context/windows", headers={"Authorization": "Bearer pat"})
                )
                await asyncio.sleep(0.05)
                t0 = time.perf_counter()
                fast_resp = await client.get("/v1/quota", headers={"Authorization": "Bearer pat"})
                elapsed = time.perf_counter() - t0
                slow_resp = await slow_task
                assert fast_resp.status_code == 200
                assert slow_resp.status_code == 200
                return elapsed
        finally:
            if gc_was:
                gc.enable()

    elapsed = asyncio.run(_run())
    assert elapsed < 0.4, f"GET /v1/context/windows stalled the event loop ({elapsed:.2f}s)"
