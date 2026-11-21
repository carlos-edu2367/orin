from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import create_engine

from agentos.agentic.events import AgentActivityEventType
from agentos.agentic.native_turn import _RunnerSink
from agentos.agentic.session import TurnSession
from agentos.persistence.postgres.migrate import upgrade
from agentos.persistence.postgres.quota import QuotaSnapshotStore
from agentos.providers.antigravity_cli.errors import classify_antigravity_error
from agentos.providers.claude_cli.errors import classify_result_error_info
from agentos.providers.cli_adapter import (
    CliErrorKind,
    CliResult,
    CliUsage,
    RateLimitState,
    set_quota_error_reporter,
)
from agentos.providers.codex_cli.errors import classify_codex_error
from agentos.quota.models import Confidence, QuotaSnapshot, QuotaStatus
from agentos.quota.policy import QuotaPolicy
from agentos.quota.service import QuotaService
from agentos.settings.loader import EffectiveSettings, SettingsLayer
from agentos.workers.scheduler import _maybe_prune_activity_events


def test_native_sink_forwards_rate_limit_to_quota_sink():
    received: list[tuple[str, RateLimitState]] = []
    runtime = SimpleNamespace(
        quota_sink=lambda provider, state: received.append((provider, state)),
    )
    runner = SimpleNamespace(
        runtime=runtime,
        turn={"provider": "anthropic"},
    )
    sink = object.__new__(_RunnerSink)
    sink._runner = runner

    state = RateLimitState.from_info(
        {
            "status": "allowed",
            "unifiedWindows": {
                "five_hour": {"utilization": 0.45, "resetsAt": 1793206800},
                "seven_day": {"utilization": 0.34, "resetsAt": 1793451600},
            },
        }
    )
    sink.on_rate_limit(state)
    assert len(received) == 1
    assert received[0][0] == "anthropic"
    assert received[0][1] == state


def test_cli_error_classifiers_report_rate_limit_to_quota_error_hook(tmp_path: Path):
    now = datetime(2026, 11, 21, 12, 0, 0, tzinfo=UTC)
    engine = create_engine(f"sqlite:///{(tmp_path / 'cli_err.db').as_posix()}")
    upgrade(engine, "head")
    try:
        store = QuotaSnapshotStore(engine)
        service = QuotaService(store, clock=lambda: now)

        def _reporter(
            provider: str,
            retry_after_seconds: float | None,
            message: str,
            resets_at: float | None,
        ) -> None:
            service.record_quota_error(
                "u1",
                provider,
                retry_after_seconds=retry_after_seconds,
                message=message,
                resets_at=resets_at,
            )

        set_quota_error_reporter(_reporter)
        try:
            # Antigravity 429
            agy_res = CliResult(
                subtype="error",
                is_error=True,
                result_text="429 RESOURCE_EXHAUSTED: try again in 15 minutes",
                stop_reason="error",
                num_turns=1,
                duration_ms=10,
                session_id="s1",
                api_error_status=429,
                usage=CliUsage(),
            )
            agy_err = classify_antigravity_error(agy_res, now=now.timestamp())
            assert agy_err.kind == CliErrorKind.RATE_LIMITED
            agy_status = service.status_for("u1", "google-antigravity")
            assert agy_status.rejected is True
            assert agy_status.resets_at == now + timedelta(minutes=15)

            # Codex 429
            codex_res = CliResult(
                subtype="error",
                is_error=True,
                result_text="429 usage limit reached, try again in 5 minutes",
                stop_reason="error",
                num_turns=1,
                duration_ms=10,
                session_id="s2",
                api_error_status=429,
                usage=CliUsage(),
            )
            codex_err = classify_codex_error(codex_res, now=now.timestamp())
            assert codex_err.kind == CliErrorKind.RATE_LIMITED
            assert service.status_for("u1", "openai-codex").rejected is True

            # Claude 429
            claude_res = CliResult(
                subtype="error",
                is_error=True,
                result_text="API Error: 429 rate limit exceeded",
                stop_reason="error",
                num_turns=1,
                duration_ms=10,
                session_id="s3",
                api_error_status=429,
                usage=CliUsage(),
            )
            claude_rl = RateLimitState.from_info(
                {
                    "status": "rejected",
                    "rateLimitType": "five_hour",
                    "resetsAt": (now + timedelta(hours=1)).timestamp(),
                }
            )
            claude_err = classify_result_error_info(claude_res, claude_rl, now=now.timestamp())
            assert claude_err.kind == CliErrorKind.RATE_LIMITED
            assert service.status_for("u1", "anthropic-cli").rejected is True
        finally:
            set_quota_error_reporter(None)
    finally:
        engine.dispose()


def test_scheduler_prune_cleans_quota_snapshots_older_than_14_days(tmp_path: Path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'sched_prune.db').as_posix()}")
    upgrade(engine, "head")
    try:
        qstore = QuotaSnapshotStore(engine)
        now = datetime.now(UTC)
        qstore.add_many(
            "u1",
            [
                QuotaSnapshot(
                    provider="anthropic-cli",
                    window_kind="five_hour",
                    used_percent=10.0,
                    status="allowed",
                    resets_at=None,
                    source="rate_limit_event",
                    observed_at=now - timedelta(days=20),
                ),
                QuotaSnapshot(
                    provider="anthropic-cli",
                    window_kind="seven_day",
                    used_percent=50.0,
                    status="allowed",
                    resets_at=None,
                    source="rate_limit_event",
                    observed_at=now - timedelta(days=2),
                ),
            ],
        )

        class FakeChatStore:
            def __init__(self, eng):
                self._engine = eng

            def prune_transient_activity_events(self, *, batch_size: int, max_batches: int) -> int:
                return 0

        next_mono = _maybe_prune_activity_events(
            FakeChatStore(engine),  # type: ignore[arg-type]
            now_mono=100.0,
            next_prune_mono=0.0,
            quota_store=qstore,
        )
        assert next_mono > 100.0
        remaining = qstore.latest("u1", provider="anthropic-cli")
        assert [s.window_kind for s in remaining] == ["seven_day"]
    finally:
        engine.dispose()


def test_session_quota_gate_between_turns_fallback_and_event_type():
    assert AgentActivityEventType.QUOTA_UPDATED == "quota.updated"
    assert AgentActivityEventType.QUOTA_FALLBACK_APPLIED == "quota.fallback_applied"

    now = datetime(2026, 11, 21, 12, 0, 0, tzinfo=UTC)

    class StubSvc:
        def status_for(self, user_id: str, provider: str, model_id: str | None = None) -> QuotaStatus:
            if provider == "anthropic-cli":
                return QuotaStatus(100.0, True, Confidence.MEASURED, now, now + timedelta(hours=1))
            return QuotaStatus(20.0, False, Confidence.MEASURED, now, None)

    recorded: list[tuple[AgentActivityEventType, str, dict]] = []
    session = object.__new__(TurnSession)
    session.turn = {
        "user_id": "u1",
        "provider": "anthropic",
        "model_id": "claude-sonnet-5-5",
    }
    data = {
        "quota": {
            "policy": {"enabled": True},
            "fallback": {"enabled": True, "chain": ["openai-codex/gpt-6-luna"]},
        }
    }
    session.effective_settings = EffectiveSettings(
        data=data,
        provenance={},
        layers=(SettingsLayer(name="user", path=None, data=data),),
        errors=(),
        trusted=True,
    )
    session.quota_gate = QuotaPolicy(StubSvc())
    session._record = lambda event_type, summary, payload=None: recorded.append(
        (event_type, summary, dict(payload or {}))
    )

    decision = session.apply_quota_fallback_before_turn(has_pending_tool_effects=False)
    assert decision is not None
    assert session.turn["provider"] == "openai-codex"
    assert session.turn["model_id"] == "gpt-6-luna"
    assert len(recorded) == 1
    assert recorded[0][0] == AgentActivityEventType.QUOTA_FALLBACK_APPLIED
    assert recorded[0][2]["to_provider"] == "openai-codex"
    assert recorded[0][2]["to_model"] == "gpt-6-luna"
