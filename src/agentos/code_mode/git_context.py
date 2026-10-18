"""A short git status for the Code prompt, without running anything the repository configured."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

MAX_STATUS_LINES = 40
GIT_TIMEOUT_SECONDS = 2.0


def git_status_summary(root: Path | str, *, timeout: float = GIT_TIMEOUT_SECONDS, max_lines: int = MAX_STATUS_LINES) -> str:
    """``git status --short --branch`` of a repository, or "" (no repository, no git, an error or a timeout).

    Repository configuration can run code during a status (fsmonitor hooks, clean/process filters), so
    callers only use this for a trusted workspace; fsmonitor and the untracked cache are disabled here anyway.
    """
    base = Path(root)
    try:
        if not (base / ".git").exists():
            return ""
    except OSError:
        return ""
    git = shutil.which("git")
    if not git:
        return ""
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0"}
    extra: dict[str, object] = {}
    if os.name == "nt":
        extra["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        completed = subprocess.run(
            [git, "-c", "core.fsmonitor=false", "-c", "core.untrackedCache=false", "status", "--short", "--branch"],
            cwd=str(base), capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, stdin=subprocess.DEVNULL, env=env, check=False, **extra,
        )
    except (OSError, subprocess.SubprocessError, ValueError):
        return ""
    if completed.returncode != 0:
        return ""
    lines = [line.rstrip() for line in completed.stdout.splitlines() if line.strip()]
    if len(lines) > max_lines:
        lines = [*lines[:max_lines], f"... {len(lines) - max_lines} more"]
    return "\n".join(lines)


__all__ = ["GIT_TIMEOUT_SECONDS", "MAX_STATUS_LINES", "git_status_summary"]
