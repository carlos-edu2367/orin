from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

from agentos.quota.codex_reader import CodexQuotaReader


FIXTURES = Path(__file__).parent / "fixtures"


def test_codex_reader_extracts_latest_five_hour_and_seven_day_windows(tmp_path: Path):
    day_dir = tmp_path / "sessões codex" / "2026" / "11" / "15"
    day_dir.mkdir(parents=True, exist_ok=True)
    rollout = day_dir / "rollout-2026-11-15T21-30-30-abc.jsonl"
    rollout.write_text((FIXTURES / "rollout-token-count.jsonl").read_text(encoding="utf-8"), encoding="utf-8")

    reader = CodexQuotaReader(tmp_path / "sessões codex")
    snapshots = reader.read()

    by_kind = {snap.window_kind: snap for snap in snapshots}
    assert set(by_kind) == {"five_hour", "seven_day"}
    assert by_kind["five_hour"].provider == "openai-codex"
    assert by_kind["five_hour"].used_percent == 28.5
    assert by_kind["five_hour"].source == "codex_rollout"
    assert by_kind["five_hour"].resets_at == datetime.fromtimestamp(1794781200, tz=UTC)
    assert by_kind["seven_day"].used_percent == 61.0
    assert by_kind["seven_day"].resets_at == datetime.fromtimestamp(1795300000, tz=UTC)
    assert by_kind["seven_day"].status == "allowed"


def test_codex_reader_reads_only_last_2mb_of_3mb_rollout(tmp_path: Path):
    rollout = tmp_path / "rollout-2026-11-15T22-00-00-large.jsonl"
    early_event = json.dumps(
        {
            "timestamp": "2026-11-15T20:00:00.000Z",
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "rate_limits": {
                    "primary": {"used_percent": 99.0, "window_minutes": 300, "resets_at": 1794780000},
                },
            },
        }
    )
    # 3 MB of noise lines after the early event
    noise_line = json.dumps({"type": "event_msg", "payload": {"type": "other", "blob": "a" * 1000}}) + "\n"
    noise_count = (3 * 1024 * 1024) // len(noise_line) + 10
    with rollout.open("w", encoding="utf-8") as fh:
        fh.write(early_event + "\n")
        for _ in range(noise_count):
            fh.write(noise_line)
    assert rollout.stat().st_size > 3 * 1024 * 1024

    # Since the early event sits more than 2 MB before EOF, the tail reader must NOT read it
    reader = CodexQuotaReader(tmp_path, max_tail_bytes=2 * 1024 * 1024)
    assert reader.read() == []

    # Appending a fresh event at the end IS found within the last 2 MB
    latest_event = json.dumps(
        {
            "timestamp": "2026-11-15T22:00:00.000Z",
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "rate_limits": {
                    "primary": {"used_percent": 15.0, "window_minutes": 300, "resets_at": 1794789999},
                },
            },
        }
    )
    with rollout.open("a", encoding="utf-8") as fh:
        fh.write(latest_event + "\n")

    snapshots = reader.read()
    assert len(snapshots) == 1
    assert snapshots[0].window_kind == "five_hour"
    assert snapshots[0].used_percent == 15.0


def test_codex_reader_only_inspects_three_most_recent_files(tmp_path: Path):
    base_ts = 1794000000.0
    # Oldest file (4th by mtime) has rate_limits; the 3 newer files do not
    oldest = tmp_path / "rollout-1-oldest.jsonl"
    oldest.write_text((FIXTURES / "rollout-token-count.jsonl").read_text(encoding="utf-8"), encoding="utf-8")
    os.utime(oldest, (base_ts, base_ts))

    for idx in range(1, 4):
        f = tmp_path / f"rollout-{idx + 1}-newer.jsonl"
        f.write_text(json.dumps({"type": "session_meta", "payload": {"id": f"s{idx}"}}) + "\n", encoding="utf-8")
        ts = base_ts + idx * 100
        os.utime(f, (ts, ts))

    assert CodexQuotaReader(tmp_path).read() == []


def test_codex_reader_empty_missing_or_unexpected_format_returns_empty(tmp_path: Path):
    assert CodexQuotaReader(tmp_path / "missing").read() == []
    assert CodexQuotaReader(tmp_path).read() == []

    bad = tmp_path / "rollout-bad.jsonl"
    bad.write_text(
        "not json\n"
        + json.dumps({"type": "event_msg", "payload": {"type": "token_count", "rate_limits": "unexpected"}})
        + "\n",
        encoding="utf-8",
    )
    assert CodexQuotaReader(tmp_path).read() == []
