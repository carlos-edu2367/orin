from __future__ import annotations

import asyncio
import gc
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine

from agentos.api import ApiServices, AuthenticatedPrincipal, InMemoryClientEventStream, InMemorySecurityService, create_app
from agentos.persistence.postgres.migrate import upgrade
from agentos.persistence.postgres.quota import QuotaSnapshotStore
from agentos.providers.cli_adapter import RateLimitState
from agentos.quota.claude_account import ClaudeAccountReader
from agentos.quota.claude_reader import ClaudeUsageCacheReader
from agentos.quota.codex_reader import CodexQuotaReader
from agentos.quota.models import Confidence, OrinUsageSummary, ProviderQuota
from agentos.quota.service import QuotaService

FIXTURES = Path(__file__).resolve().parents[1] / "quota" / "fixtures"


def _make_app(quota_service, *, events=None):
    security = InMemorySecurityService()
    security.add_pat("pat", AuthenticatedPrincipal("user-1", "cred-1", frozenset({"api"})))
    return create_app(
        ApiServices(
            security=security,
            events=events or InMemoryClientEventStream(),
            quota_service=quota_service,
        )
    )


def test_get_v1_quota_returns_subscriptions_windows_confidence_age_and_orin_usage(tmp_path: Path):
    now = datetime.fromtimestamp(1794775200000 / 1000.0, tz=UTC) + timedelta(minutes=8)
    claude_json = tmp_path / "claude.json"
    claude_json.write_text(
        (FIXTURES / "claude-json-usage-cache.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    codex_dir = tmp_path / "codex"
    codex_dir.mkdir()
    (codex_dir / "rollout-2026-11-15T21-30-30-abc.jsonl").write_text(
        (FIXTURES / "rollout-token-count.jsonl").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    engine = create_engine(f"sqlite:///{(tmp_path / 'api_quota.db').as_posix()}")
    upgrade(engine, "head")
    try:
        events = InMemoryClientEventStream()
        store = QuotaSnapshotStore(engine)
        service = QuotaService(
            store,
            claude_reader=ClaudeUsageCacheReader(ClaudeAccountReader(claude_json)),
            codex_reader=CodexQuotaReader(codex_dir),
            clock=lambda: now,
        )
        app = _make_app(service, events=events)
        client = TestClient(app)

        # Trigger initial passive refresh via query param or default behavior
        resp = client.get("/v1/quota?refresh=true", headers={"Authorization": "Bearer pat"})
        assert resp.status_code == 200
        body = resp.json()
        assert "subscriptions" in body
        by_provider = {item["provider"]: item for item in body["subscriptions"]}
        assert set(by_provider) == {"anthropic-cli", "openai-codex", "google-antigravity"}

        claude = by_provider["anthropic-cli"]
        assert claude["confidence"] == "measured"
        assert claude["source"] == "claude_usage_cache"
        assert claude["age_seconds"] == 8 * 60
        assert claude["observed_at"] is not None
        assert "five_hour" in claude["orin_usage"]
        assert "seven_day" in claude["orin_usage"]
        seven_day = [w for w in claude["windows"] if w["kind"] == "seven_day" and w["scope_model"] is None][0]
        assert seven_day["used_percent"] == 77.0

        codex = by_provider["openai-codex"]
        assert codex["confidence"] == "measured"
        assert codex["source"] == "codex_rollout"

        agy = by_provider["google-antigravity"]
        assert agy["confidence"] == "unknown"
        assert agy["windows"] == []

        # Recording a new snapshot publishes a `quota.updated` event into services.events
        state = RateLimitState.from_info(
            {
                "status": "allowed",
                "resetsAt": 1793206800,
                "unifiedWindows": {"five_hour": {"utilization": 0.45, "resetsAt": 1793206800}},
            }
        )
        service.record_rate_limit_state("user-1", "anthropic-cli", state)
        assert any(ev.event_type == "quota.updated" for ev in events._events)
    finally:
        engine.dispose()


def test_get_v1_quota_runs_in_threadpool_and_does_not_block_event_loop():
    slow_seconds = 0.6

    class SlowQuotaService:
        def get_all_quotas(self, user_id: str, *, refresh: bool = False):
            time.sleep(slow_seconds)
            return [
                ProviderQuota(
                    provider="google-antigravity",
                    windows=(),
                    confidence=Confidence.UNKNOWN,
                    observed_at=None,
                    source=None,
                    orin_usage=OrinUsageSummary(),
                )
            ]

    app = _make_app(SlowQuotaService())

    async def _run() -> float:
        transport = ASGITransport(app=app)
        gc.collect()
        was_enabled = gc.isenabled()
        gc.disable()
        try:
            started = time.monotonic()
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                slow = asyncio.ensure_future(
                    client.get("/v1/quota", headers={"Authorization": "Bearer pat"})
                )
                await asyncio.sleep(slow_seconds / 4)
                fast_resp = await client.get("/v1/providers", headers={"Authorization": "Bearer pat"})
                assert fast_resp.status_code in (200, 404, 503)
                fast_done = time.monotonic() - started
                slow_resp = await slow
                assert slow_resp.status_code == 200
                return fast_done
        finally:
            if was_enabled:
                gc.enable()

    elapsed = asyncio.run(_run())
    assert elapsed < slow_seconds * 0.75, f"/v1/quota blocked the event loop ({elapsed:.3f}s)"


def test_get_v1_quota_refresh_returns_without_waiting_for_the_active_probe_and_sse_event_follows(tmp_path: Path):
    import threading

    from agentos.quota.models import QuotaSnapshot
    from agentos.quota.probe_coordinator import ProbeSettings

    now = datetime(2026, 11, 23, 12, 0, tzinfo=UTC)
    release = threading.Event()
    started = threading.Event()

    class SlowProbe:
        provider = "openai-codex"
        source = "codex_app_server"
        disabled_reason = None

        def available(self) -> bool:
            return True

        def read(self) -> list[QuotaSnapshot]:
            started.set()
            release.wait(10)
            return [
                QuotaSnapshot(
                    provider="openai-codex",
                    window_kind="seven_day",
                    used_percent=4.0,
                    status="allowed",
                    resets_at=now + timedelta(days=3),
                    source="codex_app_server",
                    observed_at=now,
                )
            ]

    engine = create_engine(f"sqlite:///{(tmp_path / 'api_probe.db').as_posix()}")
    upgrade(engine, "head")
    try:
        events = InMemoryClientEventStream()
        service = QuotaService(
            QuotaSnapshotStore(engine),
            claude_reader=ClaudeUsageCacheReader(ClaudeAccountReader(tmp_path / "missing.json")),
            codex_reader=CodexQuotaReader(tmp_path / "missing-codex"),
            clock=lambda: now,
            active_probes=[SlowProbe()],
            probe_settings_reader=lambda user: ProbeSettings(),
        )
        client = TestClient(_make_app(service, events=events))

        began = time.monotonic()
        resp = client.get("/v1/quota?refresh=true", headers={"Authorization": "Bearer pat"})
        assert resp.status_code == 200
        assert time.monotonic() - began < 5.0
        assert started.wait(5)
        codex = {item["provider"]: item for item in resp.json()["subscriptions"]}["openai-codex"]
        assert codex["windows"] == []

        release.set()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not any(ev.event_type == "quota.updated" for ev in events._events):
            time.sleep(0.05)
        assert any(ev.event_type == "quota.updated" for ev in events._events)

        after = client.get("/v1/quota", headers={"Authorization": "Bearer pat"}).json()
        codex = {item["provider"]: item for item in after["subscriptions"]}["openai-codex"]
        assert codex["source"] == "codex_app_server"
        assert codex["windows"][0]["used_percent"] == 4.0
        assert "scope_models" in codex["windows"][0]
    finally:
        release.set()
        engine.dispose()
