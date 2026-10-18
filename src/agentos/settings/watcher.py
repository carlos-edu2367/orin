"""Detect settings files that changed between two turns (ConfigChange)."""

from __future__ import annotations

import threading
from collections.abc import Iterable
from pathlib import Path

_Stamp = tuple[int, int] | None
_snapshots: dict[str, dict[Path, _Stamp]] = {}
_lock = threading.Lock()


def _stamp(path: Path) -> _Stamp:
    try:
        stat = path.stat()
    except OSError:
        return None
    return (stat.st_mtime_ns, stat.st_size)


def detect_changes(key: str, paths: Iterable[Path]) -> tuple[Path, ...]:
    current = {Path(path): _stamp(Path(path)) for path in paths}
    with _lock:
        previous = _snapshots.get(key)
        _snapshots[key] = current
    if previous is None:
        return ()
    return tuple(path for path, stamp in current.items() if previous.get(path, None) != stamp)


def reset_snapshots() -> None:
    with _lock:
        _snapshots.clear()


def forget(key: str, paths: Iterable[Path]) -> None:
    """Report these files as changed again on the next check (a ConfigChange a hook refused)."""
    with _lock:
        snapshot = _snapshots.get(key)
        if snapshot is None:
            return
        for path in paths:
            snapshot.pop(Path(path), None)
