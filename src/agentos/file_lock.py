"""A cross-process lock on a file's ``.lock`` sibling (W3-R23, W6-R7): one writer at a time across Orin and its `orin code` runs.

``file_lock(path)`` yields True when the lock is held and False when it could not be taken within ``wait_seconds``;
the caller then changes nothing. The lock is released when the block ends, even when it raises.
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def file_lock(path: Path | str, wait_seconds: float = 5.0) -> Iterator[bool]:
    target = Path(path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        handle = open(target.with_name(target.name + ".lock"), "a+b")  # noqa: SIM115 - held for the block
    except OSError:
        yield False
        return
    locked = False
    deadline = time.monotonic() + wait_seconds
    try:
        while not locked:
            try:
                if os.name == "nt":
                    import msvcrt

                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
            except OSError:
                if time.monotonic() >= deadline:
                    break
                time.sleep(0.01)
        yield locked
    finally:
        if locked:
            try:
                if os.name == "nt":
                    import msvcrt

                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        handle.close()


__all__ = ["file_lock"]
