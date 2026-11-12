"""Which conversation wrote a file, so a long command does not take credit for another chat's files.

A command's artifacts come from a before/after diff of the workspace. When two conversations share a
folder, a command that runs for minutes sees every file the other conversation wrote in that time and
used to list them as its own. Every tool that writes (write/edit, and each command once its own diff is
known) claims its paths here; a command then drops the paths another conversation claimed while it ran.

The registry lives in the worker process, where every conversation's turns run as threads. When two
commands overlap, the one that ends first keeps the shared files; a hung command ends last, so the
files go back to the chat that really wrote them.
"""
from __future__ import annotations

import os
import threading
import time
from collections.abc import Iterable
from pathlib import Path

CLAIM_TTL_SECONDS = 6 * 3600
MAX_CLAIMS = 20_000

_lock = threading.Lock()
_claims: dict[str, tuple[str, float]] = {}


def _key(root: Path | str, relative: str) -> str:
    return os.path.normcase(os.path.normpath(os.path.join(str(root), str(relative).replace("\\", "/"))))


def _prune(now: float) -> None:
    if len(_claims) <= MAX_CLAIMS:
        stale = [key for key, (_, at) in _claims.items() if now - at > CLAIM_TTL_SECONDS]
        for key in stale:
            del _claims[key]
        return
    ordered = sorted(_claims.items(), key=lambda item: item[1][1])
    for key, _ in ordered[: len(_claims) - MAX_CLAIMS // 2]:
        del _claims[key]


def claim(root: Path | str, paths: Iterable[str], owner: str | None, *, now: float | None = None) -> None:
    """Record that ``owner`` (a conversation id) wrote these workspace-relative paths just now."""
    if not owner:
        return
    moment = time.monotonic() if now is None else now
    with _lock:
        for relative in paths:
            if relative:
                _claims[_key(root, relative)] = (owner, moment)
        if len(_claims) > 512:
            _prune(moment)


def foreign(root: Path | str, paths: Iterable[str], owner: str | None, *, since: float) -> set[str]:
    """The paths another conversation claimed at or after ``since`` (monotonic seconds)."""
    if not owner:
        return set()
    found: set[str] = set()
    with _lock:
        for relative in paths:
            entry = _claims.get(_key(root, relative))
            if entry is not None and entry[0] != owner and entry[1] >= since:
                found.add(relative)
    return found


def reset() -> None:
    """Forget every claim (tests)."""
    with _lock:
        _claims.clear()
