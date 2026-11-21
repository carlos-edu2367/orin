"""Cross-platform POSIX-style glob matcher without OS-dependent fnmatch quirks (WS-A3)."""

from __future__ import annotations

from collections.abc import Iterable
from functools import lru_cache
import re

_MULTI_SLASH_RE = re.compile(r"/+")
_MULTI_GLOBSTAR_RE = re.compile(r"\*\*(?:/\*\*)+")


def normalize_glob_path(raw: str) -> str:
    """Normalize a workspace path or glob pattern to clean POSIX separators.

    Converts ``\\`` to ``/``, collapses repeated ``/``, strips leading ``./``
    prefixes (preserving dotfiles such as ``.env``, ``.orin/...``, and
    ``.github/...``), and strips leading/trailing ``/``.
    """
    text = str(raw or "").strip().replace("\\", "/")
    if not text:
        return ""
    text = _MULTI_SLASH_RE.sub("/", text)
    while text.startswith("./"):
        text = text[2:]
    text = text.strip("/")
    return text


def _compile_glob_pattern(pattern: str, *, case_sensitive: bool) -> re.Pattern[str]:
    norm = normalize_glob_path(pattern)
    norm = _MULTI_GLOBSTAR_RE.sub("**", norm)
    parts: list[str] = ["^"]
    i = 0
    n = len(norm)
    while i < n:
        if norm[i : i + 4] == "/**/":
            parts.append("/(?:.+/)?")
            i += 4
        elif norm[i : i + 3] == "**/" and (i == 0 or norm[i - 1] == "/"):
            parts.append("(?:.+/)?")
            i += 3
        elif norm[i : i + 3] == "/**" and i + 3 == n:
            parts.append("/.*")
            i += 3
        elif norm[i : i + 2] == "**":
            parts.append(".*")
            i += 2
        elif norm[i] == "*":
            parts.append("[^/]*")
            i += 1
        elif norm[i] == "?":
            parts.append("[^/]")
            i += 1
        elif norm[i] == "[":
            j = i + 1
            if j < n and norm[j] in "!^":
                j += 1
            if j < n and norm[j] == "]":
                j += 1
            while j < n and norm[j] != "]":
                j += 1
            if j >= n:
                parts.append(r"\[")
                i += 1
            else:
                body = norm[i + 1 : j]
                if body.startswith("!"):
                    body = "^/" + body[1:]
                elif body.startswith("^"):
                    body = "^/" + body[1:]
                parts.append(f"[{body}]")
                i = j + 1
        else:
            parts.append(re.escape(norm[i]))
            i += 1
    parts.append("$")
    flags = 0 if case_sensitive else re.IGNORECASE
    return re.compile("".join(parts), flags)


@lru_cache(maxsize=512)
def _cached_compile(pattern: str, case_sensitive: bool) -> re.Pattern[str]:
    return _compile_glob_pattern(pattern, case_sensitive=case_sensitive)


def match_glob(path: str, pattern: str, *, case_sensitive: bool = True) -> bool:
    """Return True if ``path`` matches POSIX glob ``pattern`` across platforms."""
    norm_path = normalize_glob_path(path)
    norm_pattern = normalize_glob_path(pattern)
    if not norm_path or not norm_pattern:
        return False
    return _cached_compile(norm_pattern, case_sensitive).match(norm_path) is not None


def matches_any_glob(
    path: str,
    patterns: Iterable[str],
    *,
    case_sensitive: bool = True,
) -> bool:
    """Return True if ``path`` matches any glob in ``patterns``."""
    norm_path = normalize_glob_path(path)
    if not norm_path:
        return False
    for pattern in patterns:
        norm_pattern = normalize_glob_path(pattern)
        if not norm_pattern:
            continue
        if _cached_compile(norm_pattern, case_sensitive).match(norm_path) is not None:
            return True
    return False


__all__ = ["match_glob", "matches_any_glob", "normalize_glob_path"]
