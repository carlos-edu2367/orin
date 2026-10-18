from __future__ import annotations

import os
import time
from pathlib import Path

from agentos.settings.watcher import detect_changes, reset_snapshots


def test_first_call_records_and_later_calls_report_changes(tmp_path: Path) -> None:
    reset_snapshots()
    existing = tmp_path / "a.json"
    existing.write_text("{}", encoding="utf-8")
    missing = tmp_path / "b.json"
    assert detect_changes("k", [existing, missing]) == ()
    assert detect_changes("k", [existing, missing]) == ()
    existing.write_text('{"x": 1}', encoding="utf-8")
    later = time.time() + 5
    os.utime(existing, (later, later))
    missing.write_text("{}", encoding="utf-8")
    changed = detect_changes("k", [existing, missing])
    assert set(changed) == {existing, missing}
    assert detect_changes("k", [existing, missing]) == ()
    missing.unlink()
    assert detect_changes("k", [existing, missing]) == (missing,)


def test_keys_are_independent(tmp_path: Path) -> None:
    reset_snapshots()
    path = tmp_path / "s.json"
    assert detect_changes("one", [path]) == ()
    path.write_text("{}", encoding="utf-8")
    assert detect_changes("two", [path]) == ()
    assert detect_changes("one", [path]) == (path,)
