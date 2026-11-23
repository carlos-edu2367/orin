from __future__ import annotations

import threading
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import create_engine

from agentos.persistence.postgres.migrate import upgrade
from agentos.persistence.postgres.quota import QuotaSnapshotStore
from agentos.quota.claude_account import ClaudeAccountReader
from agentos.quota.claude_reader import ClaudeUsageCacheReader
from agentos.quota.codex_reader import CodexQuotaReader
from agentos.quota.models import Confidence, QuotaSnapshot
from agentos.quota.poller import QuotaPoller
from agentos.quota.probe_coordinator import (
    OUTCOME_BUSY,
    OUTCOME_DISABLED,
    OUTCOME_NO_PROBE,
    OUTCOME_RAN,
    OUTCOME_THROTTLED,
    OUTCOME_UNAVAILABLE,
    ProbeCoordinator,
    ProbeSettings,
)
from agentos.quota.service import WINDOW_RENEWED, QuotaService

NOW = datetime(2026, 11, 23, 12, 0, 0, tzinfo=UTC)


class FakeProbe:
    def __init__(self, provider: str, source: str, *, snapshots=None, available: bool = True) -> None:
        self.provider = provider
        self.source = source
        self.reads = 0
        self._available = available
        self._snapshots = snapshots
        self.disabled_reason: str | None = None
        self.gate: threading.Event | None = None
        self.entered = threading.Event()

    def available(self) -> bool:
        return self._available

    def read(self) -> list[QuotaSnapshot]:
        self.reads += 1
        self.entered.set()
        if self.gate is not None:
            self.gate.wait(5)
        if self._snapshots is not None:
            return list(self._snapshots)
        return [
            QuotaSnapshot(
                provider=self.provider,
                window_kind="five_hour",
                used_percent=10.0,
                status="allowed",
                resets_at=NOW + timedelta(hours=3),
                source=self.source,
                observed_at=NOW,
            )
        ]


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _inline(task) -> None:
    task()


def _coordinator(probes: Sequence[FakeProbe], recorded: list, clock: Clock, **kwargs) -> ProbeCoordinator:
    return ProbeCoordinator(
        probes,
        record=lambda user, snaps: recorded.append((user, [s.provider for s in snaps])),
        clock=clock,
        spawn=_inline,
        **kwargs,
    )


def test_probe_throttle_allows_one_run_per_minute_per_user_and_subscription():
    clock, recorded = Clock(), []
    probe = FakeProbe("anthropic-cli", "claude_usage_command")
    coordinator = _coordinator([probe], recorded, clock)

    assert coordinator.run("user-1", "anthropic", reason="a") == OUTCOME_RAN
    clock.now += 59
    assert coordinator.run("user-1", "anthropic-cli") == OUTCOME_THROTTLED
    assert coordinator.request("user-1", "anthropic-cli") is False
    # Another user is throttled independently.
    assert coordinator.run("user-2", "anthropic-cli") == OUTCOME_RAN
    clock.now += 2
    assert coordinator.run("user-1", "anthropic-cli") == OUTCOME_RAN
    assert probe.reads == 3
    assert [item[0] for item in recorded] == ["user-1", "user-2", "user-1"]


def test_failed_attempts_also_count_for_the_throttle():
    clock, recorded = Clock(), []
    probe = FakeProbe("openai-codex", "codex_app_server", snapshots=[])
    coordinator = _coordinator([probe], recorded, clock)

    coordinator.run("user-1", "openai-codex")
    clock.now += 10
    assert coordinator.run("user-1", "openai-codex") == OUTCOME_THROTTLED
    assert probe.reads == 1
    assert recorded == []


def test_only_one_probe_in_flight_per_subscription():
    clock, recorded = Clock(), []
    probe = FakeProbe("google-antigravity", "agy_usage_command")
    probe.gate = threading.Event()
    coordinator = _coordinator([probe], recorded, clock)

    outcomes: list[str] = []
    worker = threading.Thread(target=lambda: outcomes.append(coordinator.run("user-1", "agy")))
    worker.start()
    assert probe.entered.wait(5)

    # A different user is not throttled by the first one, but the subscription's lock is held.
    assert coordinator.run("user-2", "google-antigravity") == OUTCOME_BUSY
    assert coordinator.request("user-2", "google-antigravity") is False

    probe.gate.set()
    worker.join(5)
    assert outcomes == [OUTCOME_RAN]
    assert probe.reads == 1


def test_disabled_setting_and_unknown_or_unavailable_probes_do_nothing():
    clock, recorded = Clock(), []
    probe = FakeProbe("anthropic-cli", "claude_usage_command")
    off = _coordinator([probe], recorded, clock, settings_reader=lambda user: ProbeSettings(enabled=False))
    assert off.run("user-1", "anthropic-cli") == OUTCOME_DISABLED
    assert off.request("user-1", "anthropic-cli") is False

    on = _coordinator([probe], recorded, clock)
    assert on.run("user-1", "openai-codex") == OUTCOME_NO_PROBE

    missing = FakeProbe("openai-codex", "codex_app_server", available=False)
    coordinator = _coordinator([missing], recorded, clock)
    assert coordinator.run("user-1", "codex") == OUTCOME_UNAVAILABLE
    assert missing.reads == 0
    assert probe.reads == 0 and recorded == []


def test_recent_probe_seen_in_the_store_skips_the_probe():
    clock, recorded = Clock(), []
    probe = FakeProbe("openai-codex", "codex_app_server")
    coordinator = _coordinator([probe], recorded, clock, recent=lambda user, provider: 20.0)
    assert coordinator.run("user-1", "openai-codex") == OUTCOME_THROTTLED
    assert probe.reads == 0


# --- service integration ---------------------------------------------------------------------


def _service(tmp_path: Path, probes, *, now: datetime = NOW, clock: Clock | None = None, settings=None, db: str = "probe_svc.db"):
    engine = create_engine(f"sqlite:///{(tmp_path / db).as_posix()}")
    upgrade(engine, "head")
    store = QuotaSnapshotStore(engine)
    published: list[tuple[str, str]] = []
    service = QuotaService(
        store,
        claude_reader=ClaudeUsageCacheReader(ClaudeAccountReader(tmp_path / "missing-claude.json")),
        codex_reader=CodexQuotaReader(tmp_path / "missing-codex"),
        clock=lambda: now,
        on_updated=lambda user, provider: published.append((user, provider)),
        active_probes=probes,
        probe_settings_reader=settings or (lambda user: ProbeSettings()),
        probe_clock=clock or Clock(),
        probe_spawn=_inline,
    )
    return service, engine, published


def test_refresh_true_schedules_probes_in_background_and_publishes_quota_updated(tmp_path: Path):
    probes = [
        FakeProbe("anthropic-cli", "claude_usage_command"),
        FakeProbe("openai-codex", "codex_app_server", available=False),
    ]
    service, engine, published = _service(tmp_path, probes)
    try:
        quotas = {q.provider: q for q in service.get_all_quotas("user-1", refresh=True)}

        assert quotas["anthropic-cli"].source == "claude_usage_command"
        assert quotas["anthropic-cli"].confidence == Confidence.MEASURED
        assert ("user-1", "anthropic-cli") in published
        assert probes[0].reads == 1 and probes[1].reads == 0
    finally:
        engine.dispose()


def test_refresh_false_does_not_probe_and_without_probes_nothing_breaks(tmp_path: Path):
    probe = FakeProbe("anthropic-cli", "claude_usage_command")
    service, engine, _published = _service(tmp_path, [probe])
    plain, plain_engine, _ = _service(tmp_path, [], db="plain.db")
    try:
        service.get_all_quotas("user-1")
        assert probe.reads == 0
        assert plain.refresh_active("user-1") == []
        plain.get_all_quotas("user-1", refresh=True)
        assert plain.probe_settings("user-1").enabled is False
    finally:
        engine.dispose()
        plain_engine.dispose()


def test_after_turn_probes_only_the_subscription_used_in_the_turn(tmp_path: Path):
    probes = [
        FakeProbe("anthropic-cli", "claude_usage_command"),
        FakeProbe("openai-codex", "codex_app_server"),
        FakeProbe("google-antigravity", "agy_usage_command"),
    ]
    service, engine, _published = _service(tmp_path, probes)
    try:
        service.after_turn("user-1", "openai-codex")
        assert [p.reads for p in probes] == [0, 1, 0]

        service.after_turn("user-1", "openai-api")  # a provider without a probe is a no-op
        service.after_turn("user-1", None)
        assert [p.reads for p in probes] == [0, 1, 0]
    finally:
        engine.dispose()


def test_renewed_window_is_not_shown_as_current_and_triggers_a_probe(tmp_path: Path):
    probe = FakeProbe("anthropic-cli", "claude_usage_command", snapshots=[])
    service, engine, _published = _service(tmp_path, [probe])
    try:
        old_reset = NOW - timedelta(days=6)
        service.record_snapshots(
            "user-1",
            [
                QuotaSnapshot(
                    provider="anthropic-cli",
                    window_kind="seven_day",
                    used_percent=77.0,
                    status="allowed_warning",
                    resets_at=old_reset,
                    source="claude_usage_cache",
                    observed_at=NOW - timedelta(hours=140),
                ),
                QuotaSnapshot(
                    provider="anthropic-cli",
                    window_kind="five_hour",
                    used_percent=20.0,
                    status="allowed",
                    resets_at=NOW + timedelta(hours=2),
                    source="claude_usage_cache",
                    observed_at=NOW - timedelta(minutes=3),
                ),
            ],
        )

        claude = {q.provider: q for q in service.get_all_quotas("user-1")}["anthropic-cli"]

        by_kind = {w.kind: w for w in claude.windows}
        assert by_kind["seven_day"].status == WINDOW_RENEWED
        assert by_kind["seven_day"].used_percent is None
        assert by_kind["seven_day"].resets_at is None
        assert by_kind["five_hour"].used_percent == 20.0
        assert claude.confidence == Confidence.MEASURED
        assert probe.reads == 1  # the expired window scheduled a probe

        status = service.status_for("user-1", "anthropic-cli")
        assert status.max_percent == 20.0
    finally:
        engine.dispose()


def test_renewed_everything_is_partial_and_status_has_no_stale_percent(tmp_path: Path):
    service, engine, _published = _service(tmp_path, [])
    try:
        service.record_snapshots(
            "user-1",
            [
                QuotaSnapshot(
                    provider="anthropic-cli",
                    window_kind="seven_day",
                    used_percent=77.0,
                    status="allowed_warning",
                    resets_at=NOW - timedelta(days=6),
                    source="claude_usage_cache",
                    observed_at=NOW - timedelta(hours=140),
                )
            ],
        )
        claude = {q.provider: q for q in service.get_all_quotas("user-1")}["anthropic-cli"]
        assert claude.confidence == Confidence.PARTIAL
        assert service.status_for("user-1", "anthropic-cli").max_percent is None
    finally:
        engine.dispose()


def _agy_snapshots() -> list[QuotaSnapshot]:
    def snap(scope: str, kind: str, percent: float, models: list[str]) -> QuotaSnapshot:
        return QuotaSnapshot(
            provider="google-antigravity",
            window_kind=kind,
            used_percent=percent,
            status="allowed",
            resets_at=NOW + timedelta(hours=4 if kind == "five_hour" else 100),
            source="agy_usage_command",
            observed_at=NOW,
            scope_model=scope,
            raw={"models": models},
        )

    gemini = ["Gemini 4 Argon", "Gemini 3.8 Flash"]
    third = ["Claude Opus 5.5", "Claude Sonnet 5.5", "GPT-OSS 120B"]
    return [
        snap("Gemini", "five_hour", 11.0, gemini),
        snap("Gemini", "seven_day", 22.0, gemini),
        snap("Claude e GPT", "five_hour", 33.0, third),
        snap("Claude e GPT", "seven_day", 91.0, third),
    ]


def test_antigravity_with_measured_windows_is_measured_and_status_matches_the_model_group(tmp_path: Path):
    service, engine, _published = _service(tmp_path, [])
    try:
        service.record_snapshots("user-1", _agy_snapshots())
        agy = {q.provider: q for q in service.get_all_quotas("user-1")}["google-antigravity"]
        assert agy.confidence == Confidence.MEASURED
        assert agy.source == "agy_usage_command"
        assert {w.scope_model for w in agy.windows} == {"Gemini", "Claude e GPT"}
        assert agy.windows[0].scope_models

        gemini = service.status_for("user-1", "google-antigravity", "gemini-3.8-flash-low")
        assert gemini.max_percent == 22.0
        assert gemini.confidence == Confidence.MEASURED

        sonnet = service.status_for("user-1", "google-antigravity", "claude-sonnet-5-5-high")
        assert sonnet.max_percent == 91.0

        oss = service.status_for("user-1", "google-antigravity", "gpt-oss-120b-medium")
        assert oss.max_percent == 91.0

        argon = service.status_for("user-1", "google-antigravity", "gemini-4-argon-high")
        assert argon.max_percent == 22.0

        unknown = service.status_for("user-1", "google-antigravity", "kimi-k3")
        assert unknown.max_percent is None
    finally:
        engine.dispose()


def test_antigravity_without_any_reading_stays_unknown(tmp_path: Path):
    service, engine, _published = _service(tmp_path, [])
    try:
        agy = {q.provider: q for q in service.get_all_quotas("user-1")}["google-antigravity"]
        assert agy.confidence == Confidence.UNKNOWN
        assert agy.windows == ()
    finally:
        engine.dispose()


# --- poller cadence ---------------------------------------------------------------------------


class RecordingService:
    def __init__(self, *, enabled: bool = True, minutes: int = 5) -> None:
        self.settings = ProbeSettings(enabled=enabled, interval_minutes=minutes)
        self.passive: list[str] = []
        self.active: list[str] = []
        self.after: list[tuple[str, str | None]] = []

    def refresh_from_files(self, user_id: str):
        self.passive.append(user_id)
        return []

    def probe_settings(self, user_id: str) -> ProbeSettings:
        return self.settings

    def refresh_active(self, user_id: str, providers=None, *, reason: str = "") -> list[str]:
        self.active.append(user_id)
        return []

    def after_turn(self, user_id: str, provider: str | None) -> None:
        self.after.append((user_id, provider))


def test_poller_reads_files_every_tick_and_probes_every_interval_only_with_a_connected_client():
    service, clock = RecordingService(), Clock()
    poller = QuotaPoller(service, clock=clock)

    poller.poll_once()  # nobody connected
    assert service.passive == [] and service.active == []

    poller.client_connected("user-1")
    poller.poll_once()
    assert service.passive == ["user-1"] and service.active == ["user-1"]  # opening probes right away

    for _ in range(4):  # 4 more one-minute ticks: files only
        clock.now += 60
        poller.poll_once()
    assert len(service.passive) == 5 and len(service.active) == 1

    clock.now += 60  # 5 minutes after the first probe
    poller.poll_once()
    assert len(service.passive) == 6 and len(service.active) == 2


def test_poller_honours_the_configured_interval_and_the_off_switch():
    service, clock = RecordingService(minutes=2), Clock()
    poller = QuotaPoller(service, clock=clock)
    poller.client_connected("user-1")
    poller.poll_once()
    clock.now += 119
    poller.poll_once()
    assert len(service.active) == 1
    clock.now += 2
    poller.poll_once()
    assert len(service.active) == 2

    service.settings = ProbeSettings(enabled=False)
    clock.now += 3600
    poller.poll_once()
    assert len(service.active) == 2 and len(service.passive) == 4  # files are still read on every tick


def test_poller_trigger_after_turn_passes_the_provider():
    service = RecordingService()
    poller = QuotaPoller(service)
    poller.trigger_after_turn("user-1", "openai-codex")
    poller.trigger_after_turn("user-1")
    assert service.after == [("user-1", "openai-codex"), ("user-1", None)]
