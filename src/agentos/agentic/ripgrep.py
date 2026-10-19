"""ripgrep for Grep, with Orin's Python search as the fallback (W4).

The binary is used only from an explicit place: ``ORIN_RIPGREP_PATH`` (set by the operator) or the copy
bundled with a frozen Orin (``<runtime>/_internal/bin``). It is never looked up on ``PATH`` and never
taken from inside the workspace: a repository must not be able to choose the program that runs, and the
results must not depend on what the machine happens to have installed. Whenever ripgrep cannot answer
(no binary, a pattern its regex engine rejects, a timeout, any error), the caller falls back to Python.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from collections.abc import Iterable
from pathlib import Path
from typing import Any

RIPGREP_TIMEOUT_SECONDS = 30
MAX_RIPGREP_EVENTS = 20_000
_NAME = "rg.exe" if os.name == "nt" else "rg"


def _usable(candidate: Path, workspace_root: Path | None) -> bool:
    if not candidate.is_absolute() or not candidate.is_file():
        return False
    if workspace_root is not None:
        try:
            candidate.resolve().relative_to(Path(workspace_root).resolve())
            return False  # a binary inside the workspace is repository-controlled
        except (OSError, ValueError):
            pass
    return True


def ripgrep_binary(workspace_root: Path | None = None) -> Path | None:
    """The ripgrep to use, or None (the Python search then answers)."""
    configured = os.environ.get("ORIN_RIPGREP_PATH", "").strip()
    if configured:
        candidate = Path(configured)
        return candidate if _usable(candidate, workspace_root) else None
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        candidate = Path(bundle) / "bin" / _NAME
        if _usable(candidate, workspace_root):
            return candidate
    return None


def run_ripgrep(arguments: Iterable[str], *, cwd: Path) -> list[dict[str, Any]] | None:
    """The JSON events of one ripgrep run, or None when it failed (exit 2, timeout, too much output)."""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    try:
        process = subprocess.Popen(
            list(arguments), cwd=str(cwd), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, creationflags=flags,
        )
    except OSError:
        return None
    timer = threading.Timer(RIPGREP_TIMEOUT_SECONDS, process.kill)
    timer.start()
    events: list[dict[str, Any]] = []
    overflow = False
    try:
        assert process.stdout is not None
        for raw in process.stdout:
            if len(events) >= MAX_RIPGREP_EVENTS:
                overflow = True
                process.kill()
                break
            try:
                event = json.loads(raw)
            except ValueError:
                continue
            if isinstance(event, dict) and event.get("type") in {"match", "context"}:
                events.append(event)
        code = process.wait()
    finally:
        timer.cancel()
        if process.stdout is not None:
            process.stdout.close()
    if overflow or code not in (0, 1):
        return None
    return events


def event_path(event: dict[str, Any]) -> str | None:
    path = (event.get("data") or {}).get("path") or {}
    text = path.get("text")
    return text if isinstance(text, str) else None  # non-UTF-8 paths come as bytes: skipped


def event_text(event: dict[str, Any]) -> str:
    lines = (event.get("data") or {}).get("lines") or {}
    text = lines.get("text")
    return text if isinstance(text, str) else ""
