from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import create_engine

from agentos.persistence.postgres.migrate import upgrade
from agentos.persistence.postgres.quota import QuotaSnapshotStore
from agentos.providers.cli_adapter import RateLimitState
from agentos.quota.claude_account import ClaudeAccountReader
from agentos.quota.claude_reader import ClaudeUsageCacheReader
from agentos.quota.codex_reader import CodexQuotaReader
from agentos.quota.models import Confidence
from agentos.quota.poller import QuotaPoller
from agentos.quota.service import QuotaService

FIXTURES = Path(__file__).parent / "fixtures"


def _make_service(
    tmp_path: Path,
    *,
    now: datetime,
    claude_json: Path | None = None,
    codex_dir: Path | None = None,
    on_updated=None,
) -> tuple[QuotaService, object]:
    engine = create_engine(f"sqlite:///{(tmp_path / 'quota_svc.db').as_posix()}")
    upgrade(engine, "head")
    store = QuotaSnapshotStore(engine)
    claude_reader = ClaudeUsageCacheReader(
        ClaudeAccountReader(claude_json or (tmp_path / "missing-claude.json"))
    )
    codex_reader = CodexQuotaReader(codex_dir or (tmp_path / "missing-codex"))
    service = QuotaService(
        store,
        claude_reader=claude_reader,
        codex_reader=codex_reader,
        clock=lambda: now,
        on_updated=on_updated,
    )
    return service, engine


def test_quota_service_records_unified_windows_and_antigravity_unknown(tmp_path: Path):
    now = datetime(2026, 11, 21, 12, 0, 0, tzinfo=UTC)
    published: list[tuple[str, str]] = []
    service, engine = _make_service(
        tmp_path,
        now=now,
        on_updated=lambda user_id, provider: published.append((user_id, provider)),
    )
    try:
        state = RateLimitState.from_info(
            {
                "status": "allowed",
                "resetsAt": 1793206800,
                "unifiedWindows": {
                    "five_hour": {"utilization": 0.45, "resetsAt": 1793206800},
                    "seven_day": {"utilization": 0.34, "resetsAt": 1793451600},
                },
            }
        )
        snaps = service.record_rate_limit_state("user-1", "anthropic", state)
        assert len(snaps) == 2
        assert ("user-1", "anthropic-cli") in published

        quotas = {q.provider: q for q in service.get_all_quotas("user-1")}
        assert set(quotas) == {"anthropic-cli", "openai-codex", "google-antigravity"}

        claude = quotas["anthropic-cli"]
        assert claude.confidence == Confidence.MEASURED
        by_kind = {w.kind: w for w in claude.windows}
        assert by_kind["five_hour"].used_percent == 45.0
        assert by_kind["seven_day"].used_percent == 34.0

        agy = quotas["google-antigravity"]
        assert agy.confidence == Confidence.UNKNOWN
        assert agy.windows == ()

        status = service.status_for("user-1", "anthropic-cli")
        assert status.max_percent == 45.0
        assert status.rejected is False
        assert status.confidence == Confidence.MEASURED
    finally:
        engine.dispose()


def test_quota_service_antigravity_429_error_marks_rejected_with_reset(tmp_path: Path):
    now = datetime(2026, 11, 21, 12, 0, 0, tzinfo=UTC)
    service, engine = _make_service(tmp_path, now=now)
    try:
        resets_at = now + timedelta(minutes=30)
        service.record_quota_error(
            "user-1",
            "google-antigravity",
            resets_at=resets_at,
            message="429 RESOURCE_EXHAUSTED",
        )

        status = service.status_for("user-1", "google-antigravity")
        assert status.rejected is True
        assert status.resets_at == resets_at
        assert status.max_percent is None
        assert status.confidence == Confidence.PARTIAL

        quotas = {q.provider: q for q in service.get_all_quotas("user-1")}
        agy = quotas["google-antigravity"]
        assert agy.windows[0].status == "rejected"
        assert agy.windows[0].used_percent is None
        assert agy.source == "error"
    finally:
        engine.dispose()


def test_quota_service_refreshes_claude_and_codex_files_with_age(tmp_path: Path):
    now = datetime.fromtimestamp(1794775200000 / 1000.0, tz=UTC) + timedelta(minutes=12)
    claude_json = tmp_path / "claude.json"
    claude_json.write_text(
        (FIXTURES / "claude-json-usage-cache.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    codex_dir = tmp_path / "codex_sessions"
    codex_dir.mkdir()
    (codex_dir / "rollout-2026-11-15T21-30-30-test.jsonl").write_text(
        (FIXTURES / "rollout-token-count.jsonl").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    service, engine = _make_service(
        tmp_path,
        now=now,
        claude_json=claude_json,
        codex_dir=codex_dir,
    )
    try:
        written = service.refresh_from_files("user-1")
        assert len(written) >= 4

        quotas = {q.provider: q for q in service.get_all_quotas("user-1")}
        claude = quotas["anthropic-cli"]
        assert claude.source == "claude_usage_cache"
        assert claude.age_seconds == 12 * 60
        seven_d = [w for w in claude.windows if w.kind == "seven_day" and w.scope_model is None][0]
        assert seven_d.used_percent == 77.0

        codex = quotas["openai-codex"]
        assert codex.source == "codex_rollout"
        assert codex.confidence == Confidence.MEASURED
        codex_by_kind = {w.kind: w for w in codex.windows}
        assert codex_by_kind["five_hour"].used_percent == 28.5
        assert codex_by_kind["seven_day"].used_percent == 61.0
    finally:
        engine.dispose()


def test_quota_poller_polls_only_when_sse_clients_connected_and_on_demand():
    polled_users: list[str] = []
    polled_event = threading.Event()

    class FakeService:
        def refresh_from_files(self, user_id: str):
            polled_users.append(user_id)
            polled_event.set()
            return []

    poller = QuotaPoller(FakeService(), interval_seconds=0.05)
    poller.start()
    try:
        # Without SSE clients, nothing is polled automatically
        assert not polled_event.wait(timeout=0.12)
        assert polled_users == []

        # Registering an SSE client triggers polling for that user
        poller.client_connected("user-1")
        assert polled_event.wait(timeout=2.0)
        assert "user-1" in polled_users

        # Disconnecting stops polling; trigger_after_turn still refreshes on demand
        poller.client_disconnected("user-1")
        before = len(polled_users)
        poller.trigger_after_turn("user-2")
        assert "user-2" in polled_users[before:]
    finally:
        poller.stop()
