"""A rewind and the turns of one workspace folder never overlap (W4-R11).

Every chat that resolves to one folder counts its running turns under that folder's key (a project's chats share
the project's folder). A rewind takes the folder exclusively: it is refused while any turn of the folder runs or
another rewind holds it, and a turn that starts while a rewind holds the folder waits for the rewind to finish.

The state lives in this process. The turns of a second Orin process on the same folder are seen only through
their database state, which the routes still check.
"""
from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

_CONDITION = threading.Condition()
_TURNS: dict[str, int] = {}
_REWINDING: set[str] = set()


class WorkspaceBusy(RuntimeError):
    """A turn of this folder is running, or another rewind holds it."""


def folder_key(root: Path | str) -> str:
    return os.path.normcase(str(Path(root).resolve()))


def enter_turn(root: Path | str) -> str:
    """Count a running turn of ``root``; waits while a rewind holds the folder. Returns the key for ``exit_turn``."""
    key = folder_key(root)
    with _CONDITION:
        while key in _REWINDING:
            _CONDITION.wait()
        _TURNS[key] = _TURNS.get(key, 0) + 1
    return key


def exit_turn(key: str) -> None:
    with _CONDITION:
        count = _TURNS.get(key, 0) - 1
        if count > 0:
            _TURNS[key] = count
        else:
            _TURNS.pop(key, None)
        _CONDITION.notify_all()


@contextmanager
def exclusive_folder(root: Path | str) -> Iterator[None]:
    """Hold ``root`` for one rewind. Raises WorkspaceBusy when a turn of it runs or another rewind holds it."""
    key = folder_key(root)
    with _CONDITION:
        if key in _REWINDING or _TURNS.get(key, 0) > 0:
            raise WorkspaceBusy(key)
        _REWINDING.add(key)
    try:
        yield
    finally:
        with _CONDITION:
            _REWINDING.discard(key)
            _CONDITION.notify_all()


__all__ = ["WorkspaceBusy", "enter_turn", "exclusive_folder", "exit_turn", "folder_key"]
