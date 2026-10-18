"""Path specifiers for Read/Edit/Write rules, and the protected-path list.

Specifiers: ``//abs`` absolute, ``~/x`` home, ``/x`` / ``./x`` / ``x/y``
relative to the workspace root, a pattern without ``/`` matches a name at any
depth, a trailing ``/`` matches everything below, ``**`` crosses
directories. Case-insensitive on Windows.
"""

from __future__ import annotations

import os
import re
from pathlib import Path, PurePosixPath

PROTECTED_FILE_NAMES: frozenset[str] = frozenset({
    ".bashrc", ".bash_profile", ".bash_login", ".bash_aliases", ".bash_logout", ".zshrc", ".zprofile",
    ".profile", ".envrc", ".ripgreprc", ".mcp.json", ".claude.json", ".npmrc", ".yarnrc", ".yarnrc.yml",
    ".pnp.cjs", ".pnp.loader.mjs", ".pnpmfile.cjs", "bunfig.toml", ".bunfig.toml", ".bazelrc",
    ".bazelversion", ".bazeliskrc", ".pre-commit-config.yaml", "lefthook.yml", ".lefthook.yml",
    ".netrc", ".git-credentials",
})
# Inside .orin/ these are working areas the agent may write freely.
_ORIN_WRITABLE = ("plans", "worktrees", "tool-outputs", "logs")

_CASE_INSENSITIVE = os.name == "nt"


def glob_to_regex(pattern: str, *, case_insensitive: bool) -> re.Pattern[str]:
    out: list[str] = []
    i = 0
    n = len(pattern)
    while i < n:
        ch = pattern[i]
        if ch == "*":
            if pattern[i:i + 3] == "**/":
                out.append("(?:.*/)?")
                i += 3
                continue
            if pattern[i:i + 2] == "**":
                out.append(".*")
                i += 2
                continue
            out.append("[^/]*")
            i += 1
            continue
        if ch == "?":
            out.append("[^/]")
            i += 1
            continue
        if ch == "[":
            end = pattern.find("]", i + 1)
            if end == -1:
                out.append(re.escape(ch))
                i += 1
                continue
            body = pattern[i + 1:end]
            if body.startswith("!"):
                body = "^" + body[1:]
            out.append("[" + body.replace("\\", "\\\\") + "]")
            i = end + 1
            continue
        out.append(re.escape(ch))
        i += 1
    flags = re.IGNORECASE if case_insensitive else 0
    return re.compile("^" + "".join(out) + "$", flags | re.DOTALL)


def _posix(path: Path) -> str:
    return PurePosixPath(Path(os.path.abspath(path)).as_posix()).as_posix()


def _resolved(path: Path) -> Path:
    try:
        return path.resolve()
    except OSError:
        return Path(os.path.abspath(path))


_DRIVE_PREFIX = re.compile(r"^[A-Za-z]:")


def workspace_path(raw: str, root: Path) -> Path:
    """Target of a tool path, resolved the way ``ConversationWorkspace.resolve`` does.

    Separators are normalized and leading separators dropped, so ``/.env``,
    ``\.env`` and ``//.env`` name the workspace file the tool will open.
    Drive-qualified paths keep their plain meaning.
    """
    text = str(raw).strip()
    if _DRIVE_PREFIX.match(text):
        candidate = Path(text)
        return candidate if candidate.is_absolute() else root / candidate
    segments = [segment for segment in text.replace("\\", "/").split("/") if segment not in ("", ".")]
    return root.joinpath(*segments) if segments else root


def _ntfs_name(part: str) -> str:
    """The name NTFS opens for a component: a stream suffix is ignored, trailing dots and spaces are not part of it."""
    return part.split(":", 1)[0].rstrip(" .")


def _relative(target: Path, base: Path) -> str | None:
    target_text = _posix(_resolved(target))
    base_text = _posix(_resolved(base)).rstrip("/")
    compare_target = target_text.lower() if _CASE_INSENSITIVE else target_text
    compare_base = base_text.lower() if _CASE_INSENSITIVE else base_text
    if compare_target == compare_base:
        return ""
    if compare_target.startswith(compare_base + "/"):
        relative = target_text[len(base_text) + 1:]
        if _CASE_INSENSITIVE:
            relative = "/".join(name for name in (_ntfs_name(part) for part in relative.split("/")) if name)
        return relative
    return None


def path_rule_matches(spec: str, target: Path, *, workspace_root: Path, home: Path) -> bool:
    pattern = str(spec or "").strip().replace("\\", "/")
    if pattern in {"", "*", "**"}:
        return True
    if pattern.startswith("//"):
        absolute = pattern[2:]
        if not re.match(r"^[A-Za-z]:/", absolute):
            absolute = "/" + absolute
        rx = glob_to_regex(absolute.rstrip("/") + ("/**" if pattern.endswith("/") else ""), case_insensitive=_CASE_INSENSITIVE)
        return bool(rx.match(_posix(_resolved(target))))
    if pattern.startswith("~/"):
        base, pattern = home, pattern[2:]
    elif pattern.startswith("/"):
        base, pattern = workspace_root, pattern[1:]
    elif pattern.startswith("./"):
        base, pattern = workspace_root, pattern[2:]
    else:
        base = workspace_root
    relative = _relative(target, base)
    if relative is None:
        return False
    if pattern.endswith("/"):
        pattern = pattern + "**"
    rx = glob_to_regex(pattern, case_insensitive=_CASE_INSENSITIVE)
    if "/" not in pattern:
        return any(rx.match(part) for part in relative.split("/") if part)
    if pattern.endswith("/**"):
        # "dir/**" also covers the directory itself: a search rooted at it reads its contents.
        if glob_to_regex(pattern[:-3], case_insensitive=_CASE_INSENSITIVE).match(relative):
            return True
    return bool(rx.match(relative))


def is_protected_path(target: Path, *, workspace_root: Path) -> bool:
    relative = _relative(target, workspace_root)
    if relative is None or relative == "":
        return False
    parts = [part for part in relative.split("/") if part]
    lowered = [part.lower() for part in parts] if _CASE_INSENSITIVE else parts
    if lowered[-1] in PROTECTED_FILE_NAMES or parts[-1] in PROTECTED_FILE_NAMES:
        return True
    if ".git" in lowered:
        return True
    if lowered[0] == ".claude":
        return True
    if lowered[0] == ".orin":
        return not (len(lowered) >= 2 and lowered[1] in _ORIN_WRITABLE)
    return False
