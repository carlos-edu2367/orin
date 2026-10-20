"""The liveness lease of an ``orin code`` turn (W6-R2).

A CLI turn runs in the worker, so when the ``orin code`` process dies (Ctrl-C is caught, but TerminateProcess is not)
nothing would stop the turn. The CLI renews a lease while it follows the turn (a heartbeat), and the worker stops the
turn once the lease is older than ``LEASE_SECONDS``. A lease is one empty file per turn under
``<ORIN_DATA_DIR>/cli-leases/<turn_id>``; its modification time is the renewal time.
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path

LEASE_SECONDS = 15
HEARTBEAT_SECONDS = 5
_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def leases_root() -> Path:
    from agentos.installation.paths import orin_paths

    return orin_paths().data / "cli-leases"


def _path(turn_id: str) -> Path:
    if not isinstance(turn_id, str) or not _ID.match(turn_id):
        raise ValueError("a lease needs a turn id of letters, digits, '_' or '-'")
    return leases_root() / turn_id


def renew(turn_id: str) -> None:
    """Create the lease, or touch it when it exists."""
    path = _path(turn_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        os.utime(path, None)
    else:
        with open(path, "a", encoding="utf-8"):
            pass


def expired(turn_id: str, *, ttl: float = LEASE_SECONDS) -> bool:
    """True when the lease is missing or was last renewed more than ``ttl`` seconds ago."""
    path = _path(turn_id)
    try:
        modified = path.stat().st_mtime
    except OSError:
        return True
    return time.time() - modified > ttl


def drop(turn_id: str) -> None:
    """Remove the lease when its turn closes; a lease already gone is fine."""
    try:
        _path(turn_id).unlink()
    except (OSError, ValueError):
        pass


__all__ = ["HEARTBEAT_SECONDS", "LEASE_SECONDS", "drop", "expired", "renew"]
