"""Atomic, locked writes of settings JSON files."""

from __future__ import annotations

import copy
import json
import os
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


@contextmanager
def _file_lock(path: Path) -> Iterator[None]:
    lock = path.with_name(path.name + ".lock")
    deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
    while True:
        try:
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                age = time.time() - lock.stat().st_mtime
            except OSError:
                continue
            if age > STALE_LOCK_SECONDS:
                try:
                    lock.unlink()
                except OSError:
                    pass
                continue
            if time.monotonic() > deadline:
                raise SettingsWriteError(f"settings file is locked by another writer: {path}") from None
            time.sleep(0.02)
            continue
        try:
            os.write(descriptor, str(os.getpid()).encode("ascii"))
        finally:
            os.close(descriptor)
        break
    try:
        yield
    finally:
        try:
            lock.unlink()
        except OSError:
            pass


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


def update_settings_file(path: Path | str, mutate: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with _process_lock, _file_lock(target):
        current = _read_object(target)
        updated = copy.deepcopy(current)
        mutate(updated)
        if not isinstance(updated, dict):
            raise SettingsWriteError("settings must remain a JSON object")
        text = json.dumps(updated, indent=2, ensure_ascii=False) + "\n"
        temporary = target.with_name(f".{target.name}.{os.getpid()}.{uuid4().hex[:8]}.tmp")
        try:
            with open(temporary, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
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
