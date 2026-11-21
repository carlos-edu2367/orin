from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from agentos.quota.claude_account import ClaudeAccountReader
from agentos.quota.claude_reader import ClaudeUsageCacheReader


FIXTURES = Path(__file__).parent / "fixtures"


def test_claude_usage_cache_reader_builds_windows_and_never_leaks_other_keys(tmp_path: Path):
    claude_json = tmp_path / "claude.json"
    claude_json.write_text(
        (FIXTURES / "claude-json-usage-cache.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    reader = ClaudeUsageCacheReader(ClaudeAccountReader(claude_json))
    snapshots = reader.read()

    expected_observed = datetime.fromtimestamp(1794775200000 / 1000.0, tz=UTC)
    assert snapshots, "expected snapshots from cachedUsageUtilization"

    by_key = {(s.window_kind, s.scope_model): s for s in snapshots}
    assert ("five_hour", None) in by_key
    assert ("seven_day", None) in by_key
    assert ("weekly_scoped", "Fable") in by_key

    five_h = by_key[("five_hour", None)]
    assert five_h.provider == "anthropic-cli"
    assert five_h.used_percent == 32.0
    assert five_h.source == "claude_usage_cache"
    assert five_h.observed_at == expected_observed
    assert five_h.resets_at == datetime(2026, 11, 15, 23, 0, 0, tzinfo=UTC)

    seven_d = by_key[("seven_day", None)]
    assert seven_d.used_percent == 77.0
    assert seven_d.status == "allowed_warning"
    assert seven_d.source == "claude_usage_cache"
    assert seven_d.observed_at == expected_observed
    assert seven_d.resets_at == datetime(2026, 11, 20, 12, 0, 0, tzinfo=UTC)

    fable = by_key[("weekly_scoped", "Fable")]
    assert fable.used_percent == 0.0
    assert fable.scope_model == "Fable"
    assert fable.source == "claude_usage_cache"
    assert fable.observed_at == expected_observed

    for snap in snapshots:
        dumped = json.dumps(snap.raw or {})
        assert "sk-ant-fake-never-expose-me" not in dumped
        assert "accessToken" not in dumped
        assert "secretOtherSection" not in dumped
        assert "must-not-leak" not in dumped


def test_claude_usage_cache_reader_returns_empty_when_cache_missing(tmp_path: Path):
    claude_json = tmp_path / "claude.json"
    claude_json.write_text(json.dumps({"oauthAccount": {"hasExtraUsageEnabled": False}}), encoding="utf-8")

    reader = ClaudeUsageCacheReader(ClaudeAccountReader(claude_json))
    assert reader.read() == []
