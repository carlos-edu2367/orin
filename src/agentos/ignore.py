"""Directories and files that agent-facing filesystem tools should not surface.

Applies to project trees, listings, globs and diffs: dependency and build
output directories (``node_modules``, ``.venv``, ``dist``, ...) are noise a
coding agent should never have to page through, and a project's own
``.gitignore`` is the person's own signal about what does not belong in their
working set. This is shared between the semantic retrieval index
(``agentos.retrieval.filters``, which additionally keeps secrets out of what
gets embedded remotely) and the conversation workspace's own tools
(``agentos.agentic.workspace``), so the two never drift apart on what counts
as noise.
"""
from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path

DENIED_SEGMENTS = frozenset({
    ".git", ".hg", ".svn", "node_modules", ".venv", "venv", "__pycache__",
    "dist", "build", ".next", ".turbo", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", "target", "vendor", ".tox", "site-packages",
    ".aws", ".ssh",
})


@dataclass(frozen=True, slots=True)
class _Pattern:
    glob: str
    negated: bool
    directory_only: bool
    anchored: bool


class GitignoreFilter:
    """A bounded reader of a project's root ``.gitignore``.

    Comments, blank lines, ``!`` negation, directory-only patterns ending in
    ``/``, and root anchoring with a leading ``/`` are supported. Nested
    ``.gitignore`` files and ``**`` are not interpreted; this fixed subset
    covers the cases that matter in practice.
    """

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

    def ignores(self, relative_path: str, *, is_dir: bool = False) -> bool:
        decision = False
        segments = relative_path.split("/")
        for pattern in self._patterns:
            if self._matches(pattern, relative_path, segments, is_dir):
                decision = not pattern.negated
        return decision

    @staticmethod
    def _matches(pattern: _Pattern, relative_path: str, segments: list[str], is_dir: bool) -> bool:
        if pattern.anchored:
            return fnmatch(relative_path, pattern.glob) or relative_path.startswith(f"{pattern.glob}/")
        if pattern.directory_only:
            # A directory-only pattern like ``generated/`` matches the
            # directory itself (the whole path, when it is one) in addition
            # to files found underneath it (their ancestor segments). Every
            # caller checking file paths only (the retrieval index) never
            # passes ``is_dir=True``, so this candidate set is unchanged for
            # them; a caller enumerating directory entries does.
            candidates = segments if is_dir else segments[:-1]
            return any(fnmatch(segment, pattern.glob) for segment in candidates)
        return any(fnmatch(segment, pattern.glob) for segment in segments) or fnmatch(relative_path, pattern.glob)


class PathIgnorePolicy:
    """The single gate a workspace-facing tool checks before surfacing a path.

    Deliberately narrower than ``agentos.retrieval.filters.IndexFilter``: it
    only hides build/dependency noise and honours the project's own
    ``.gitignore``. It does not reject secret-looking filenames, because
    hiding a path from a listing and refusing to read a file the person
    already put in their own project are different decisions.
    """

    def __init__(self, gitignore: GitignoreFilter) -> None:
        self._gitignore = gitignore

    @classmethod
    def for_root(cls, root: Path) -> "PathIgnorePolicy":
        return cls(GitignoreFilter.from_root(root))

    def ignores(self, relative_path: str, *, is_dir: bool = False) -> bool:
        segments = relative_path.split("/")
        if any(segment in DENIED_SEGMENTS for segment in segments):
            return True
        return self._gitignore.ignores(relative_path, is_dir=is_dir)


__all__ = ["DENIED_SEGMENTS", "GitignoreFilter", "PathIgnorePolicy"]
