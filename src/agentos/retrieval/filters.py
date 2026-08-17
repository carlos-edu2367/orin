"""What never reaches the index.

The secret denylist is applied before the chunker, not as a result filter. With
a remote embedder configured, indexed content leaves the machine: a ``.env``
inside a chunk would become an API key inside an HTTP request body.

The ``.gitignore`` support is a documented subset — comments, blank lines,
negation with ``!``, directory-only patterns ending in ``/``, and root anchoring
with a leading ``/``. Nested ``.gitignore`` files and ``**`` are not
interpreted; the fixed denylist covers the cases that matter in practice.
"""
from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path


DENIED_SEGMENTS = frozenset({
    ".git", ".hg", ".svn", "node_modules", ".venv", "venv", "__pycache__",
    "dist", "build", ".next", ".turbo", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", "target", "vendor", ".tox", "site-packages",
})

DENIED_NAMES = frozenset({"uv.lock", "package-lock.json", "poetry.lock", "yarn.lock", "Cargo.lock"})

# Prefix/suffix rules for material that must never be embedded. Checked against
# the file name only, so a directory called ``keys`` is not itself excluded.
SECRET_PREFIXES = (".env", "id_rsa", "id_ed25519", "id_ecdsa")
SECRET_SUFFIXES = (".pem", ".key", ".pfx", ".p12", ".keystore", ".jks")
SECRET_EXEMPT_SUFFIXES = (".example", ".sample", ".template")


@dataclass(frozen=True, slots=True)
class _Pattern:
    glob: str
    negated: bool
    directory_only: bool
    anchored: bool


class GitignoreFilter:
    """A bounded reader of the project's root ``.gitignore``."""

    def __init__(self, patterns: tuple[_Pattern, ...]) -> None:
        self._patterns = tuple(patterns)

    @classmethod
    def parse(cls, text: str) -> "GitignoreFilter":
        patterns: list[_Pattern] = []
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            negated = line.startswith("!")
            if negated:
                line = line[1:]
            directory_only = line.endswith("/")
            line = line.rstrip("/")
            anchored = line.startswith("/")
            line = line.lstrip("/")
            if line:
                patterns.append(_Pattern(line, negated, directory_only, anchored))
        return cls(tuple(patterns))

    @classmethod
    def from_root(cls, root: Path) -> "GitignoreFilter":
        candidate = root / ".gitignore"
        try:
            return cls.parse(candidate.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            return cls(())

    def ignores(self, relative_path: str) -> bool:
        decision = False
        segments = relative_path.split("/")
        for pattern in self._patterns:
            if self._matches(pattern, relative_path, segments):
                decision = not pattern.negated
        return decision

    @staticmethod
    def _matches(pattern: _Pattern, relative_path: str, segments: list[str]) -> bool:
        if pattern.anchored:
            return fnmatch(relative_path, pattern.glob) or relative_path.startswith(f"{pattern.glob}/")
        if pattern.directory_only:
            return any(fnmatch(segment, pattern.glob) for segment in segments[:-1])
        return any(fnmatch(segment, pattern.glob) for segment in segments) or fnmatch(relative_path, pattern.glob)


class IndexFilter:
    """The single gate every candidate path passes through before being read."""

    def __init__(self, gitignore: GitignoreFilter) -> None:
        self._gitignore = gitignore

    def rejects(self, relative_path: str) -> bool:
        segments = relative_path.split("/")
        name = segments[-1]
        if any(segment in DENIED_SEGMENTS for segment in segments):
            return True
        if name in DENIED_NAMES:
            return True
        if self._is_secret(name):
            return True
        return self._gitignore.ignores(relative_path)

    @staticmethod
    def _is_secret(name: str) -> bool:
        lowered = name.lower()
        if lowered.endswith(SECRET_EXEMPT_SUFFIXES):
            return False
        return lowered.startswith(SECRET_PREFIXES) or lowered.endswith(SECRET_SUFFIXES)


__all__ = ["DENIED_NAMES", "DENIED_SEGMENTS", "GitignoreFilter", "IndexFilter"]
