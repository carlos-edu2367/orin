"""Atomic, locked writes of settings JSON files."""

from __future__ import annotations

import copy
import json
import os
import stat
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

LOCK_TIMEOUT_SECONDS = 5.0
STALE_LOCK_SECONDS = 30.0
MAX_RULE_LENGTH = 1000
RULE_KINDS = ("allow", "ask", "deny")

# Serialises writers inside this process; the lock file serialises processes.
_process_lock = threading.Lock()


class SettingsWriteError(RuntimeError):
    pass


def _is_directory(path: Path) -> bool:
    try:
        return path.is_dir()
    except OSError:
        return False


def _break_if_stale(lock: Path) -> None:
    """Remove a lock that is older than STALE_LOCK_SECONDS, atomically.

    The lock is first renamed to a unique name: ``os.replace`` succeeds for only
    one stealer, so two waiters can never both delete each other's fresh lock.
    """
    try:
        age = time.time() - lock.stat().st_mtime
    except OSError:
        return  # gone, or cannot be inspected: the caller retries until its deadline
    if age <= STALE_LOCK_SECONDS:
        return
    stolen = lock.with_name(f"{lock.name}.stale.{uuid4().hex}")
    try:
        os.replace(lock, stolen)
    except OSError:
        return  # another waiter already took it
    try:
        if time.time() - stolen.stat().st_mtime <= STALE_LOCK_SECONDS:
            # Someone re-created the lock between our check and our rename: put it back.
            try:
                os.replace(stolen, lock)
                return
            except OSError:
                pass
    except OSError:
        pass
    try:
        stolen.unlink()
    except OSError:
        pass


def _release(lock: Path, token: str) -> None:
    """Remove the lock only if it still carries our token; a stolen lock is not ours to delete."""
    try:
        if lock.read_text(encoding="ascii") != token:
            return
    except (OSError, UnicodeDecodeError):
        return
    try:
        lock.unlink()
    except OSError:
        pass


@contextmanager
def _file_lock(path: Path) -> Iterator[None]:
    lock = path.with_name(path.name + ".lock")
    token = uuid4().hex
    deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
    while True:
        # Checked on every pass: a directory at the lock path can never be acquired.
        if _is_directory(lock):
            raise SettingsWriteError(f"settings lock path is a directory: {lock}")
        try:
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except (FileExistsError, PermissionError):
            # Windows reports a lock in delete-pending state as PermissionError.
            _break_if_stale(lock)
            if time.monotonic() > deadline:
                raise SettingsWriteError(f"settings file is locked by another writer: {path}") from None
            time.sleep(0.02)
            continue
        except OSError as error:
            raise SettingsWriteError(f"cannot create settings lock {lock}: {error}") from error
        try:
            os.write(descriptor, token.encode("ascii"))
        finally:
            os.close(descriptor)
        break
    try:
        yield
    finally:
        _release(lock, token)


def _replace(source: Path, target: Path) -> None:
    for attempt in range(10):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            # Windows refuses the rename while another process has the file open.
            if attempt == 9:
                raise
            time.sleep(0.05)


def _read_object(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        raw = path.read_bytes().decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise SettingsWriteError(f"refusing to overwrite non-UTF-8 file {path}") from error
    if not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as error:
        raise SettingsWriteError(f"refusing to overwrite invalid JSON in {path}: {error.msg}") from error
    if not isinstance(parsed, dict):
        raise SettingsWriteError(f"refusing to overwrite {path}: the top level is not a JSON object")
    return parsed


PRIVATE_FILE_MODE = 0o600  # settings can hold env secrets, so a new file is private by default


def _target_mode(target: Path) -> int:
    try:
        return stat.S_IMODE(target.stat().st_mode)
    except OSError:
        return PRIVATE_FILE_MODE


def update_settings_file(path: Path | str, mutate: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    target = Path(path)
    if target.is_symlink():
        # A dotfile symlink stays a symlink: the real file is what gets updated.
        target = target.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    with _process_lock, _file_lock(target):
        current = _read_object(target)
        updated = copy.deepcopy(current)
        mutate(updated)
        if not isinstance(updated, dict):
            raise SettingsWriteError("settings must remain a JSON object")
        text = json.dumps(updated, indent=2, ensure_ascii=False) + "\n"
        temporary = target.with_name(f".{target.name}.{os.getpid()}.{uuid4().hex[:8]}.tmp")
        mode = _target_mode(target)
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, PRIVATE_FILE_MODE)
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            # Installed with the mode the user chose, so `chmod 600` survives the write.
            os.chmod(temporary, mode)
            _replace(temporary, target)
        finally:
            if temporary.exists():
                try:
                    temporary.unlink()
                except OSError:
                    pass
        return updated


def add_permission_rule(path: Path | str, kind: str, rule: str) -> bool:
    if kind not in RULE_KINDS:
        raise ValueError(f"kind must be one of {', '.join(RULE_KINDS)}")
    clean = str(rule or "").strip()
    if not clean or len(clean) > MAX_RULE_LENGTH or "\n" in clean or "\r" in clean:
        raise ValueError("a permission rule must be one line of at most 1000 characters")
    added = False

    def mutate(data: dict[str, Any]) -> None:
        nonlocal added
        permissions = data.get("permissions")
        if not isinstance(permissions, dict):
            permissions = {}
            data["permissions"] = permissions
        rules = permissions.get(kind)
        if not isinstance(rules, list):
            rules = []
            permissions[kind] = rules
        if clean not in rules:
            rules.append(clean)
            added = True

    update_settings_file(path, mutate)
    return added
