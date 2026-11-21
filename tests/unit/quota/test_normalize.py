from __future__ import annotations

from datetime import UTC, datetime

from agentos.providers.cli_adapter import RateLimitState
from agentos.quota.normalize import (
    normalize_percent,
    normalize_provider_name,
    normalize_resets_at,
    normalize_window_kind,
    snapshots_from_rate_limit_state,
)


def test_normalize_percent_fraction_vs_percent_scales():
    assert normalize_percent(0.45, scale="fraction") == 45.0
    assert normalize_percent(1.0, scale="fraction") == 100.0
    assert normalize_percent(0.0, scale="fraction") == 0.0
    assert normalize_percent(77, scale="percent") == 77.0
    assert normalize_percent(1.0, scale="percent") == 1.0
    assert normalize_percent(0, scale="percent") == 0.0
    # Auto mode handles fractions (0 < v < 1) and percents (0..100)
    assert normalize_percent(0.45) == 45.0
    assert normalize_percent(77) == 77.0
    # Out-of-range values clamp cleanly
    assert normalize_percent(120, scale="percent") == 100.0
    assert normalize_percent(-5, scale="percent") == 0.0
    assert normalize_percent(None) is None
    assert normalize_percent(True) is None
    assert normalize_percent(float("nan")) is None


def test_normalize_window_kind_minutes_and_names():
    assert normalize_window_kind(window_minutes=300) == "five_hour"
    assert normalize_window_kind(window_minutes=10080) == "seven_day"
    assert normalize_window_kind("five_hour") == "five_hour"
    assert normalize_window_kind("5h") == "five_hour"
    assert normalize_window_kind("seven_day") == "seven_day"
    assert normalize_window_kind("7d") == "seven_day"
    assert normalize_window_kind("weekly_all") == "seven_day"
    assert normalize_window_kind("weekly_scoped") == "weekly_scoped"
    assert normalize_window_kind("weekly") == "weekly"
    assert normalize_window_kind(None) == "unknown"


def test_normalize_resets_at_epoch_seconds_ms_and_iso():
    from_seconds = normalize_resets_at(1793206800)
    from_ms = normalize_resets_at(1793206800_000)
    expected = datetime.fromtimestamp(1793206800, tz=UTC)
    assert from_seconds == expected
    assert from_ms == expected

    from_iso = normalize_resets_at("2026-11-20T12:00:00Z")
    assert from_iso == datetime(2026, 11, 20, 12, 0, 0, tzinfo=UTC)
    assert normalize_resets_at(None) is None
    assert normalize_resets_at(-10) is None
    assert normalize_resets_at("not-a-date") is None


def test_normalize_provider_name():
    assert normalize_provider_name("anthropic") == "anthropic-cli"
    assert normalize_provider_name("anthropic-cli") == "anthropic-cli"
    assert normalize_provider_name("claude-cli") == "anthropic-cli"
    assert normalize_provider_name("openai-codex") == "openai-codex"
    assert normalize_provider_name("codex") == "openai-codex"
    assert normalize_provider_name("google-antigravity") == "google-antigravity"
    assert normalize_provider_name("agy") == "google-antigravity"


def test_rate_limit_state_from_info_extracts_unified_windows_and_normalizes():
    info = {
        "status": "allowed",
        "resetsAt": 1793206800,
        "unifiedWindows": {
            "five_hour": {"utilization": 0.45, "resetsAt": 1793206800},
            "seven_day": {"utilization": 0.34, "resetsAt": 1793451600},
        },
    }
    state = RateLimitState.from_info(info)
    assert state.status == "allowed"
    assert state.resets_at == 1793206800.0
    assert len(state.windows) == 2

    observed = datetime(2026, 11, 21, 12, 0, 0, tzinfo=UTC)
    snapshots = snapshots_from_rate_limit_state("anthropic", state, observed_at=observed)
    by_kind = {snap.window_kind: snap for snap in snapshots}
    assert set(by_kind) == {"five_hour", "seven_day"}
    assert by_kind["five_hour"].used_percent == 45.0
    assert by_kind["five_hour"].provider == "anthropic-cli"
    assert by_kind["five_hour"].source == "rate_limit_event"
    assert by_kind["five_hour"].resets_at == datetime.fromtimestamp(1793206800, tz=UTC)
    assert by_kind["seven_day"].used_percent == 34.0
    assert by_kind["seven_day"].resets_at == datetime.fromtimestamp(1793451600, tz=UTC)


def test_rate_limit_state_rejected_utilization_one_becomes_100():
    state = RateLimitState.from_info(
        {
            "status": "rejected",
            "rateLimitType": "five_hour",
            "resetsAt": 1793206800,
            "unifiedWindows": {
                "five_hour": {"utilization": 1.0, "resetsAt": 1793206800},
            },
        }
    )
    snapshots = snapshots_from_rate_limit_state("anthropic-cli", state)
    assert len(snapshots) == 1
    assert snapshots[0].window_kind == "five_hour"
    assert snapshots[0].used_percent == 100.0
    assert snapshots[0].status == "rejected"
