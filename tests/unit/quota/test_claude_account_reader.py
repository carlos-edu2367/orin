from __future__ import annotations

import json
import os
from pathlib import Path

from agentos.quota.claude_account import ClaudeAccountReader, ClaudeAccountState


FIXTURES = Path(__file__).parent / "fixtures"


def test_claude_account_reader_extracts_only_allowed_keys_and_never_exposes_tokens(tmp_path: Path):
    target = tmp_path / "pasta com espaço e acento" / "claude.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    raw_fixture = (FIXTURES / "claude-json-usage-cache.json").read_text(encoding="utf-8")
    target.write_text(raw_fixture, encoding="utf-8")

    reader = ClaudeAccountReader(target)
    state = reader.read()

    assert isinstance(state, ClaudeAccountState)
    assert state.extra_usage_enabled is False
    assert state.overage_models == frozenset({"Fable", "Fable 5", "Fable 5.1", "Fable 5.2"})
    assert state.fetched_at_ms == 1794775200000
    assert isinstance(state.usage_cache, dict)

    serialized = json.dumps(
        {
            "extra_usage_enabled": state.extra_usage_enabled,
            "overage_models": sorted(state.overage_models),
            "usage_cache": state.usage_cache,
            "fetched_at_ms": state.fetched_at_ms,
        }
    )
    assert "sk-ant-fake-never-expose-me" not in serialized
    assert "accessToken" not in serialized
    assert "must-not-leak" not in serialized
    assert "secretOtherSection" not in serialized
    assert "unrelated_flag" not in serialized


def test_claude_account_reader_caches_by_mtime_and_size(tmp_path: Path):
    target = tmp_path / "claude.json"
    target.write_text(
        json.dumps({"oauthAccount": {"hasExtraUsageEnabled": False}}),
        encoding="utf-8",
    )

    reader = ClaudeAccountReader(target)
    first = reader.read()
    second = reader.read()
    assert first is second
    assert first.extra_usage_enabled is False

    # Change size + mtime to force cache invalidation
    target.write_text(
        json.dumps(
            {
                "oauthAccount": {"hasExtraUsageEnabled": True},
                "cachedGrowthBookFeatures": {
                    "tengu_usage_overage_included_models": ["Fable 5.2"]
                },
            }
        ),
        encoding="utf-8",
    )
    st = target.stat()
    os.utime(target, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))

    third = reader.read()
    assert third is not first
    assert third.extra_usage_enabled is True
    assert third.overage_models == frozenset({"Fable 5.2"})


def test_claude_account_reader_tolerates_missing_or_corrupt_file(tmp_path: Path):
    missing = ClaudeAccountReader(tmp_path / "does-not-exist.json").read()
    assert missing == ClaudeAccountState(
        extra_usage_enabled=None,
        overage_models=frozenset(),
        usage_cache=None,
        fetched_at_ms=None,
    )

    corrupt = tmp_path / "corrupt.json"
    corrupt.write_text("{not valid json", encoding="utf-8")
    state = ClaudeAccountReader(corrupt).read()
    assert state == ClaudeAccountState(
        extra_usage_enabled=None,
        overage_models=frozenset(),
        usage_cache=None,
        fetched_at_ms=None,
    )


def test_claude_account_reader_handles_5mb_file_safely(tmp_path: Path):
    target = tmp_path / "large-claude.json"
    filler = "x" * (5 * 1024 * 1024)
    payload = {
        "oauthAccount": {
            "hasExtraUsageEnabled": True,
            "accessToken": "sk-ant-large-secret",
        },
        "cachedGrowthBookFeatures": {
            "tengu_usage_overage_included_models": ["Fable 5.2"],
        },
        "cachedUsageUtilization": {
            "fetchedAtMs": 1794775200000,
            "utilization": {
                "seven_day": {"utilization": 55, "resets_at": "2026-11-20T12:00:00Z"},
            },
        },
        "hugeHistoryBlob": filler,
    }
    target.write_text(json.dumps(payload), encoding="utf-8")
    assert target.stat().st_size > 5 * 1024 * 1024

    state = ClaudeAccountReader(target).read()
    assert state.extra_usage_enabled is True
    assert state.overage_models == frozenset({"Fable 5.2"})
    assert state.fetched_at_ms == 1794775200000
    assert state.usage_cache is not None
    assert "hugeHistoryBlob" not in json.dumps(state.usage_cache)
    assert "sk-ant-large-secret" not in json.dumps(state.usage_cache)


def test_claude_account_reader_rejects_file_over_size_limit(tmp_path: Path):
    target = tmp_path / "too-big.json"
    target.write_text('{"oauthAccount":{"hasExtraUsageEnabled":true}}', encoding="utf-8")
    state = ClaudeAccountReader(target, max_bytes=10).read()
    assert state.extra_usage_enabled is None
    assert state.usage_cache is None
