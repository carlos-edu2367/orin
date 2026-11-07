"""Which Python the agent's shell can really run, for the system prompt's environment section.

On Windows ``python`` is often the Microsoft Store App Execution Alias: a 0-byte stub under
``...\\Microsoft\\WindowsApps\\`` that only prints "Python was not found". Advertising Orin's own interpreter
version next to it sends the agent into commands that cannot work, so the stub is detected and replaced by what runs:
the ``py`` launcher, ``uv run python`` or the project's ``.venv``. The answer is cached for the process.
"""
from __future__ import annotations

import functools
import os
import platform
import re
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

STORE_NOTE = "`python` on PATH is the Microsoft Store alias (does not run); use {use}."
_VERSION_TIMEOUT_SECONDS = 5.0


def is_store_stub(path: str | None) -> bool:
    """True for the Microsoft Store alias: under ``\\Microsoft\\WindowsApps\\`` or a zero-size file."""
    if not path:
        return False
    normalised = str(path).replace("/", "\\").lower()
    if "\\microsoft\\windowsapps\\" in normalised:
        return True
    try:
        return os.path.isfile(path) and os.path.getsize(path) == 0
    except OSError:
        return False


def _version_of(argv: list[str]) -> str:
    """``3.13.1`` from ``<argv> --version`` (empty when it does not run)."""
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
            [*argv, "--version"], capture_output=True, text=True, timeout=_VERSION_TIMEOUT_SECONDS, check=False,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    words = (completed.stdout or completed.stderr or "").split()
    return words[1] if completed.returncode == 0 and len(words) >= 2 and words[0].lower() == "python" else ""


def resolve_python(
    *, which: Callable[[str], str | None] = shutil.which, version_of: Callable[[list[str]], str] = _version_of,
    project_root: Path | None = None,
) -> tuple[str, str]:
    """``(python text for the prompt, note or "")``; see the module docstring."""
    found = which("python") or which("python3")
    if found and not is_store_stub(found):
        version = version_of([found])
        return (f"{version} (`python` on PATH)" if version else platform.python_version()), ""
    stub = bool(found)
    candidates: list[tuple[list[str], str]] = []
    if which("py"):
        candidates.append((["py"], "`py`"))
    if which("uv"):
        candidates.append((["uv", "run", "--no-project", "python"], "`uv run python`"))
    venv = (project_root or Path.cwd()) / ".venv" / ("Scripts" if os.name == "nt" else "bin") / ("python.exe" if os.name == "nt" else "python")
    if venv.is_file():
        candidates.append(([str(venv)], f"the project's .venv interpreter (`{venv.as_posix()}`)"))
    for argv, label in candidates:
        version = version_of(argv)
        if version:
            note = STORE_NOTE.format(use=label.replace("`py`", "`py …`")) if stub else ""
            return f"{version} (via {label})", note
    if stub:
        return "none runnable", STORE_NOTE.format(use="`uv run python …` or `py …` if installed")
    return platform.python_version() + " (Orin's own interpreter; `python` is not on PATH)", ""


@functools.lru_cache(maxsize=1)
def cached_python() -> tuple[str, str]:
    return resolve_python()


# Git Bash (MSYS) rewrites POSIX-looking arguments for native Windows programs.
GIT_BASH_PATH_NOTE = "Git Bash rewrites POSIX paths for Windows programs; pass Windows-style paths with forward slashes (C:/tmp/q.sql)."


def environment_notes(environment: "dict[str, str]") -> list[str]:
    """Extra one-line guidance for the prompt's environment section (empty when nothing applies)."""
    notes = [environment.get("python_note", "")]
    if environment.get("shell_kind") == "git_bash":
        notes.append(GIT_BASH_PATH_NOTE)
    return [note for note in notes if note]


_NATIVE_PROGRAMS = frozenset({
    "node", "python", "python3", "py", "deno", "bun", "npm", "npx", "pnpm", "yarn", "uv", "pip", "pip3", "cargo",
    "go", "dotnet", "java", "ruby", "php", "docker", "powershell", "pwsh",
})
_SEGMENT_SPLIT = re.compile(r"&&|\|\||[;|\n]")
_POSIX_ABSOLUTE = re.compile(r"""^["']?/(?:[a-zA-Z]/|(?:tmp|var|home|usr|etc|opt|mnt)(?:/|["']?$))""")
GIT_BASH_PATH_HINT = "Hint: " + GIT_BASH_PATH_NOTE


def posix_path_hint(command: str, *, windows: bool | None = None) -> str:
    """The hint for a failed Git Bash command that hands ``/c/...`` or ``/tmp/...`` to a native Windows program, else ``""``."""
    if not (os.name == "nt" if windows is None else windows):
        return ""
    for segment in _SEGMENT_SPLIT.split(command):
        words = segment.split()
        while words and re.fullmatch(r"\w+=\S*", words[0]):  # leading VAR=value assignments
            words.pop(0)
        if not words:
            continue
        program = words[0].strip("\"'").replace("\\", "/").rsplit("/", 1)[-1].lower().removesuffix(".exe")
        if program in _NATIVE_PROGRAMS and any(_POSIX_ABSOLUTE.match(word) for word in words[1:]):
            return GIT_BASH_PATH_HINT
    return ""
