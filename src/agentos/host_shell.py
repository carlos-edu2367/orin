"""Host shell discovery and POSIX/Windows path conversion for the Bash tool."""
from __future__ import annotations

import functools
import os
import re
import shutil
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

_POSIX_DRIVE_RE = re.compile(r"^/([a-zA-Z])(?:/(.*))?$")
_WINDOWS_DRIVE_RE = re.compile(r"^([a-zA-Z]):(?:[\\/](.*))?$")


@dataclass(frozen=True, slots=True)
class HostShell:
    kind: Literal["git_bash", "cmd", "sh"]
    dialect: Literal["posix", "cmd"]
    executable: str
    label: str


def _is_windows() -> bool:
    return os.name == "nt"


def _discover_git_bash(
    environ: Mapping[str, str],
    which: Callable[[str], str | None],
) -> Path | None:
    explicit = (environ.get("ORIN_GIT_BASH_PATH") or "").strip()
    if explicit:
        if explicit.lower() == "none":
            return None
        candidate = Path(explicit)
        if candidate.is_file():
            return candidate

    claude_explicit = (environ.get("CLAUDE_CODE_GIT_BASH_PATH") or "").strip()
    if claude_explicit:
        candidate = Path(claude_explicit)
        if candidate.is_file():
            return candidate

    git_hit = which("git")
    if git_hit:
        for parent in list(Path(git_hit).parents)[:3]:
            candidate = parent / "bin" / "bash.exe"
            if candidate.is_file():
                return candidate

    roots: list[Path] = []
    for key in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)"):
        value = (environ.get(key) or "").strip()
        if value:
            roots.append(Path(value))
    local_app_data = (environ.get("LOCALAPPDATA") or "").strip()
    if local_app_data:
        roots.append(Path(local_app_data) / "Programs")
    for root in roots:
        candidate = root / "Git" / "bin" / "bash.exe"
        if candidate.is_file():
            return candidate
    return None


@functools.lru_cache(maxsize=1)
def find_git_bash() -> Path | None:
    if not _is_windows():
        return None
    return _discover_git_bash(os.environ, shutil.which)


def current_host_shell() -> HostShell:
    if not _is_windows():
        return HostShell("sh", "posix", "/bin/sh", "sh")
    exe = find_git_bash()
    if exe is not None:
        return HostShell("git_bash", "posix", str(exe), f"bash (Git Bash, {exe})")
    return HostShell(
        "cmd",
        "cmd",
        os.environ.get("COMSPEC") or "cmd.exe",
        "cmd.exe (Git Bash unavailable)",
    )


def shell_argv(command: str, shell: HostShell) -> list[str] | None:
    if shell.kind == "git_bash":
        return [shell.executable, "-c", command]
    return None


def posix_to_windows_path(text: str) -> str:
    if text == "~":
        return str(Path.home())
    if text.startswith("~/"):
        rest = text[2:].replace("/", "\\")
        return str(Path.home() / rest) if rest else str(Path.home())
    match = _POSIX_DRIVE_RE.match(text)
    if match:
        drive = match.group(1).upper()
        rest = (match.group(2) or "").replace("/", "\\")
        return f"{drive}:\\{rest}" if rest else f"{drive}:\\"
    return text


def windows_to_posix_path(path: str | Path) -> str:
    raw = str(path)
    match = _WINDOWS_DRIVE_RE.match(raw)
    if match:
        drive = match.group(1).lower()
        rest = (match.group(2) or "").replace("\\", "/")
        return f"/{drive}/{rest}" if rest else f"/{drive}"
    return raw.replace("\\", "/")
