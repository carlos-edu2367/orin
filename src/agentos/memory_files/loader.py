"""ORIN.md / CLAUDE.md instruction files.

Layers, in load order (later text wins when instructions conflict): managed, user,
project (repository root down to the working directory, then rules without
``paths:``), local. A file may import others with ``@path``, five levels deep at
most. Rules with ``paths:`` and the instruction files of subdirectories load when
a matching file is read.
"""

from __future__ import annotations

import itertools
import os
import re
import threading
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MAX_IMPORT_DEPTH = 5
MAX_FILE_BYTES = 64 * 1024
MAX_TOTAL_CHARS = 40_000
MAX_RULE_FILES = 64
DIRECTORY_FILES = ("AGENTS.md", "CONVENTIONS.md", "CLAUDE.md", ".claude/CLAUDE.md", "ORIN.md", ".orin/ORIN.md")
LOCAL_FILES = ("CLAUDE.local.md", "ORIN.local.md")
RULE_DIRS = (".claude/rules", ".orin/rules")
TEXT_SUFFIXES = frozenset({".md", ".markdown", ".txt", ".rst"})
MANAGED, USER, PROJECT, LOCAL = "Managed", "User", "Project", "Local"
_LABELS = {MANAGED: "managed", USER: "user", PROJECT: "project", LOCAL: "local"}
_FRONTMATTER = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.DOTALL)
_IMPORT = re.compile(r"(?<![\w`@])@([^\s`'\"<>|*?@]+)")
_FENCE = re.compile(r"^\s*(```|~~~)")


@dataclass(frozen=True, slots=True)
class MemoryFile:
    path: Path
    memory_type: str
    load_reason: str
    content: str
    globs: tuple[str, ...] = ()
    parent: Path | None = None


def _is_remote(text: str) -> bool:
    """A UNC, device or extended-length path. Resolving one opens it, which can reach a network host."""
    return text.replace("/", "\\").startswith("\\\\")


def _resolved(path: Path) -> Path:
    """``Path.resolve()`` for local paths. A remote path is never opened: it is returned as given (made absolute)."""
    if _is_remote(str(path)):
        return Path(os.path.abspath(path))
    try:
        return Path(path).resolve()
    except OSError:
        return Path(path).absolute()


def _key(path: Path) -> str:
    return os.path.normcase(str(_resolved(path)))


def _read_text(path: Path) -> str | None:
    try:
        if not path.is_file():
            return None
        with path.open("rb") as handle:
            data = handle.read(MAX_FILE_BYTES + 1)
    except OSError:
        return None
    if b"\x00" in data:
        return None
    text = data[:MAX_FILE_BYTES].decode("utf-8", errors="replace").strip()
    return text or None


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """A ``---`` block of ``key: value`` lines (values may be lists), and the text after it."""
    match = _FRONTMATTER.match(text)
    if not match:
        return {}, text
    meta: dict[str, Any] = {}
    current: list[str] | None = None
    for raw in match.group(1).splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("- ") and current is not None:
            current.append(_unquote(stripped[2:]))
            continue
        if ":" not in stripped:
            current = None
            continue
        key, _, value = stripped.partition(":")
        key, value = key.strip(), value.strip()
        if not key:
            continue
        if not value:
            current = []
            meta[key] = current
        elif value.startswith("[") and value.endswith("]"):
            meta[key] = [_unquote(part) for part in value[1:-1].split(",") if part.strip()]
            current = None
        else:
            meta[key] = _unquote(value)
            current = None
    return meta, text[match.end():]


def frontmatter_list(meta: Mapping[str, Any], key: str) -> tuple[str, ...]:
    value = meta.get(key)
    if isinstance(value, list):
        items = value
    elif isinstance(value, str):
        items = value.split(",")
    else:
        return ()
    return tuple(item.strip() for item in items if isinstance(item, str) and item.strip())


def frontmatter_bool(meta: Mapping[str, Any], key: str, default: bool) -> bool:
    value = meta.get(key)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "yes", "1"}:
            return True
        if lowered in {"false", "no", "0"}:
            return False
    return default


def _glob_tokens(pattern: str) -> list[tuple[str, str]]:
    """``**/`` = any directories, ``**`` = anything, ``*`` = one segment's characters, ``?`` = one of them."""
    text = pattern.strip().replace("\\", "/")
    if text.startswith("./"):
        text = text[2:]
    tokens: list[tuple[str, str]] = []
    index = 0
    while index < len(text):
        if text.startswith("**/", index):
            tokens.append(("optdir", ""))
            index += 3
        elif text.startswith("**", index):
            tokens.append(("any", ""))
            index += 2
        elif text[index] == "*":
            tokens.append(("star", ""))
            index += 1
        elif text[index] == "?":
            tokens.append(("one", ""))
            index += 1
        else:
            tokens.append(("lit", text[index]))
            index += 1
    return tokens


def _glob_accepts(tokens: Sequence[tuple[str, str]], candidate: str) -> bool:
    """Match by tracking every position the pattern can have reached, one token at a time.

    No backtracking, so the cost is at most (tokens x path length) whatever the pattern is: a hostile
    ``paths:`` pattern in a repository cannot stall the turn that reads a file.
    """
    fold = os.name == "nt"
    text = candidate.lower() if fold else candidate
    size = len(text)
    next_slash = [size] * (size + 1)
    for index in range(size - 1, -1, -1):
        next_slash[index] = index if text[index] == "/" else next_slash[index + 1]
    reach: set[int] = {0}
    for kind, char in tokens:
        if not reach:
            return False
        low = min(reach)
        if kind == "lit":
            wanted = char.lower() if fold else char
            reach = {p + 1 for p in reach if p < size and text[p] == wanted}
        elif kind == "one":
            reach = {p + 1 for p in reach if p < size and text[p] != "/"}
        elif kind == "star":
            # Consumes any run of characters that holds no "/": each position covers up to the next slash.
            spread: set[int] = set()
            covered = -1
            for p in sorted(reach):
                start, end = max(p, covered + 1), next_slash[p]
                if start <= end:
                    spread.update(range(start, end + 1))
                    covered = end
            reach = spread
        elif kind == "any":
            reach = set(range(low, size + 1))
        else:  # optdir: (?:.*/)?
            reach = reach | {index + 1 for index in range(low, size) if text[index] == "/"}
    return size in reach


def glob_match(pattern: str, path: str) -> bool:
    """gitignore-style: ``**`` spans directories; a pattern without ``/`` matches the file name anywhere."""
    if not pattern or len(pattern) > 500 or len(path) > 4000:
        return False
    candidate = path.replace("\\", "/")
    tokens = _glob_tokens(pattern)
    if _glob_accepts(tokens, candidate):
        return True
    return "/" not in pattern.replace("\\", "/") and _glob_accepts(tokens, candidate.rsplit("/", 1)[-1])


def _import_targets(content: str) -> list[str]:
    targets: list[str] = []
    in_fence = False
    for line in content.splitlines():
        if _FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        for match in _IMPORT.finditer(re.sub(r"`[^`]*`", "", line)):
            target = match.group(1).rstrip(".,;:)]}")
            if target and any(mark in target for mark in ("/", "\\", ".")):
                targets.append(target)
    return targets


def _resolve_import(target: str, importer: Path) -> Path | None:
    """The file an ``@target`` names, or None for a remote target (refused for every layer, before any I/O)."""
    if target.startswith(("~/", "~\\")):
        resolved = Path.home() / target[2:]
    else:
        candidate = Path(target)
        resolved = candidate if candidate.is_absolute() else importer.parent / candidate
    return None if _is_remote(str(resolved)) else resolved


def instructions_payload(item: MemoryFile, *, trigger: Path | None = None) -> dict[str, object]:
    """InstructionsLoaded hook input for one file."""
    payload: dict[str, object] = {"file_path": str(item.path), "memory_type": item.memory_type, "load_reason": item.load_reason}
    if item.globs:
        payload["globs"] = list(item.globs)
    if item.parent is not None:
        payload["parent_file_path"] = str(item.parent)
    if trigger is not None:
        payload["trigger_file_path"] = str(trigger)
    return payload


class MemoryFiles:
    """The instruction files of one session: loaded at start, extended as files are read. Thread-safe."""

    def __init__(
        self,
        workspace_root: Path | str,
        *,
        trusted: bool,
        excludes: Sequence[str] = (),
        repository_excludes: Sequence[str] = (),
        walk_up: bool = False,
        user_files: Sequence[Path] = (),
        managed_files: Sequence[Path] = (),
        managed_text: str = "",
        user_rule_dirs: Sequence[Path] = (),
    ) -> None:
        self.root = Path(workspace_root)
        self.trusted = bool(trusted)
        # ``excludes`` come from the operator (user, CLI, managed) and apply to every non-managed file.
        # ``repository_excludes`` come from the repository and apply only to its own files: a repository may
        # hide what it adds, never the user's or the organisation's instructions.
        self.excludes = tuple(item for item in excludes if isinstance(item, str) and item.strip())[:64]
        self.repository_excludes = tuple(item for item in repository_excludes if isinstance(item, str) and item.strip())[:64]
        self._lock = threading.Lock()
        self._seen: set[str] = set()
        self.files: list[MemoryFile] = []
        self.conditional: list[MemoryFile] = []
        self._project_root = self._find_project_root(walk_up)
        for path in managed_files:
            self.files += self._add(Path(path), MANAGED, "session_start")
        if str(managed_text or "").strip():
            self.files.append(MemoryFile(Path("<managed settings: claudeMd>"), MANAGED, "session_start", str(managed_text).strip()))
        for path in user_files:
            self.files += self._add(Path(path), USER, "session_start")
        for directory in user_rule_dirs:
            self._load_rules(Path(directory), USER)
        for directory in self._directory_chain():
            for name in DIRECTORY_FILES:
                self.files += self._add_repository(directory / name, PROJECT, "session_start")
        for rule_dir in RULE_DIRS:
            self._load_rules(self.root / rule_dir, PROJECT)
        for name in LOCAL_FILES:
            self.files += self._add_repository(self.root / name, LOCAL, "session_start")

    def _find_project_root(self, walk_up: bool) -> Path:
        if not walk_up:
            return self.root
        current = self.root
        for _ in range(64):
            try:
                if (current / ".git").exists():
                    return current
            except OSError:
                break
            if current.parent == current:
                break
            current = current.parent
        return self.root

    def _directory_chain(self) -> list[Path]:
        chain = [self.root]
        current = self.root
        while current != self._project_root and current.parent != current and len(chain) < 64:
            current = current.parent
            chain.append(current)
        return list(reversed(chain))

    def _inside_project(self, path: Path) -> bool:
        """Decided lexically first: a path outside the project is refused with no filesystem access.

        Only a path that lies inside lexically is resolved, to catch a symlink or junction that leads out.
        """
        try:
            lexical = os.path.normcase(os.path.abspath(path))
            base = os.path.normcase(os.path.abspath(self._project_root))
            if os.path.commonpath([lexical, base]) != base:
                return False
        except (OSError, ValueError):  # different drives have no common path
            return False
        try:
            Path(path).resolve().relative_to(self._project_root.resolve())
            return True
        except (OSError, ValueError):
            return False

    def _excluded(self, path: Path, memory_type: str) -> bool:
        patterns = self.excludes + (self.repository_excludes if memory_type in {PROJECT, LOCAL} else ())
        if not patterns:
            return False
        absolute = Path(path).absolute().as_posix()
        try:
            relative = _resolved(Path(path)).relative_to(self.root.resolve()).as_posix()
        except (OSError, ValueError):
            relative = ""
        return any(glob_match(pattern, absolute) or (relative and glob_match(pattern, relative)) for pattern in patterns)

    def _add_repository(self, path: Path, memory_type: str, reason: str) -> list[MemoryFile]:
        # A repository file (or link) that resolves outside the project is only read once the workspace is trusted.
        if not self.trusted and not self._inside_project(path):
            return []
        return self._add(path, memory_type, reason)

    def _add(self, path: Path, memory_type: str, reason: str, *, parent: Path | None = None, depth: int = 0, content: str | None = None) -> list[MemoryFile]:
        key = _key(path)
        if key in self._seen:
            return []
        if memory_type != MANAGED and self._excluded(path, memory_type):
            return []
        text = content if content is not None else _read_text(path)
        if text is None:
            return []
        self._seen.add(key)
        _meta, body = split_frontmatter(text)
        body = body.strip()
        if not body:
            return []
        added = [MemoryFile(Path(path), memory_type, reason, body, (), parent)]
        if depth < MAX_IMPORT_DEPTH:
            for target in _import_targets(body):
                resolved = _resolve_import(target, Path(path))
                if resolved is None or resolved.suffix.lower() not in TEXT_SUFFIXES:
                    continue
                if memory_type in {PROJECT, LOCAL} and not self.trusted and not self._inside_project(resolved):
                    continue
                added += self._add(resolved, memory_type, "include", parent=Path(path), depth=depth + 1)
        return added

    def _load_rules(self, directory: Path, memory_type: str) -> None:
        try:
            if not directory.is_dir():
                return
            candidates = sorted(itertools.islice(directory.rglob("*.md"), 512))[:MAX_RULE_FILES]
        except OSError:
            return
        for path in candidates:
            if memory_type == PROJECT and not self.trusted and not self._inside_project(path):
                continue
            text = _read_text(path)
            if text is None:
                continue
            meta, body = split_frontmatter(text)
            globs = frontmatter_list(meta, "paths")
            if globs:
                if body.strip():
                    self.conditional.append(MemoryFile(path, memory_type, "path_glob_match", body.strip(), globs))
                continue
            self.files += self._add(path, memory_type, "session_start", content=body)

    def on_file_read(self, path: Path | str) -> list[MemoryFile]:
        """Memory a read of ``path`` brings in: subdirectory instruction files, and rules whose paths match."""
        try:
            target = Path(path).resolve()
            relative = target.relative_to(self.root.resolve())
        except (OSError, ValueError):
            return []
        added: list[MemoryFile] = []
        with self._lock:
            directory = self.root
            for part in relative.parts[:-1]:
                directory = directory / part
                for name in DIRECTORY_FILES:
                    added += self._add_repository(directory / name, PROJECT, "nested_traversal")
            rel = relative.as_posix()
            for rule in list(self.conditional):
                if any(glob_match(pattern, rel) for pattern in rule.globs):
                    self.conditional.remove(rule)
                    added.append(rule)
            self.files.extend(added)
        return added

    def _display(self, item: MemoryFile) -> str:
        try:
            label = item.path.resolve().relative_to(self.root.resolve()).as_posix()
        except (OSError, ValueError):
            label = item.path.name or str(item.path)
        via = f", imported by {item.parent.name}" if item.parent is not None else ""
        return f"{label} ({_LABELS.get(item.memory_type, 'project')}{via})"

    def render(self, files: Iterable[MemoryFile] | None = None, *, limit: int = MAX_TOTAL_CHARS) -> str:
        items = list(self.files if files is None else files)
        parts: list[str] = []
        remaining = max(0, int(limit))
        for item in items:
            if remaining <= 0:
                parts.append("[more instruction files were left out: size limit]")
                break
            body = item.content[:remaining]
            parts.append(f"### {self._display(item)}\n{body}")
            remaining -= len(body)
        return "\n\n".join(parts)


__all__ = [
    "LOCAL", "MANAGED", "MAX_IMPORT_DEPTH", "MAX_TOTAL_CHARS", "PROJECT", "USER", "MemoryFile", "MemoryFiles",
    "frontmatter_bool", "frontmatter_list", "glob_match", "instructions_payload", "split_frontmatter",
]
