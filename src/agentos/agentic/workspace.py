"""Per-conversation workspace sandbox for agent-facing tools.

Every filesystem and terminal tool the model can reach is confined to one
directory below ``root``. Paths are resolved and then re-checked against the
sandbox after symlink resolution, so a crafted path can neither escape upward
nor follow a link outside the conversation's own directory.
"""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import re


MAX_READ_BYTES = 200_000
MAX_WRITE_BYTES = 1_000_000
MAX_SNAPSHOT_FILES = 1_000
MAX_SEARCH_RESULTS = 200
MAX_SEARCH_FILE_BYTES = 2_000_000
MAX_SEARCH_LINE_CHARS = 400
MAX_READ_LINES = 800
MAX_LIST_DEPTH = 5

_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9._-]+$")
_PROJECT_WORKSPACE_ID = re.compile(r"^workspace:[A-Za-z0-9._-]+$")


class WorkspaceError(ValueError):
    """A path was rejected before any filesystem call was attempted."""


def _directory_name(workspace_id: str) -> str:
    if _SAFE_SEGMENT.fullmatch(workspace_id):
        return workspace_id
    if _PROJECT_WORKSPACE_ID.fullmatch(workspace_id):
        return f"workspace-{sha256(workspace_id.encode('utf-8')).hexdigest()}"
    raise WorkspaceError("conversation_id is not a safe directory name")


class ConversationWorkspace:
    def __init__(self, root: Path | str, conversation_id: str) -> None:
        self.root = Path(root).resolve() / _directory_name(conversation_id)
        self.root.mkdir(parents=True, exist_ok=True)

    def resolve(self, path: str) -> Path:
        if not isinstance(path, str) or not path.strip():
            raise WorkspaceError("path must be a non-blank string")
        candidate = path.strip().replace("\\", "/").lstrip("/")
        if not candidate or len(candidate) > 512:
            raise WorkspaceError("path must be a bounded workspace-relative value")
        segments = [segment for segment in candidate.split("/") if segment not in ("", ".")]
        if any(segment == ".." for segment in segments):
            raise WorkspaceError("path must not traverse outside the workspace")
        if not segments:
            return self.root
        target = self.root.joinpath(*segments)
        # ``resolve`` follows symlinks; comparing the resolved value is what
        # actually rules out a link planted inside the workspace that points out.
        resolved = target.resolve() if target.exists() else target.parent.resolve() / target.name
        try:
            resolved.relative_to(self.root)
        except ValueError as error:
            raise WorkspaceError("path must stay inside the workspace") from error
        return resolved

    def relative(self, path: Path) -> str:
        try:
            return path.relative_to(self.root).as_posix()
        except ValueError:
            return path.name

    def read_text(self, path: str) -> tuple[str, bool]:
        target = self.resolve(path)
        if not target.is_file():
            raise WorkspaceError(f"'{path}' is not a file in this workspace")
        data = target.read_bytes()[: MAX_READ_BYTES + 1]
        truncated = len(data) > MAX_READ_BYTES
        return data[:MAX_READ_BYTES].decode("utf-8", "replace"), truncated

    def read_lines(self, path: str, *, offset: int = 1, limit: int = 400) -> tuple[list[str], int, int, bool]:
        """Return a bounded line window plus the total, so the model can page.

        ``read_text`` stays as the whole-file accessor used by ``edit_file``;
        this is the accessor used by the model, which must be able to ask for
        the part of a file it has not seen yet.
        """
        content, truncated = self.read_text(path)
        lines = content.splitlines()
        start = max(1, int(offset))
        window = max(1, min(int(limit), MAX_READ_LINES))
        return lines[start - 1: start - 1 + window], start, len(lines), truncated

    def write_text(self, path: str, content: str) -> int:
        if not isinstance(content, str):
            raise WorkspaceError("content must be text")
        payload = content.encode("utf-8")
        if len(payload) > MAX_WRITE_BYTES:
            raise WorkspaceError("content exceeds the workspace write limit")
        target = self.resolve(path)
        if target == self.root:
            raise WorkspaceError("path must name a file")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        return len(payload)

    def list_entries(self, path: str = "", *, depth: int = 1) -> list[dict[str, object]]:
        target = self.resolve(path) if path.strip() else self.root
        if not target.is_dir():
            raise WorkspaceError(f"'{path}' is not a directory in this workspace")
        levels = max(1, min(int(depth), MAX_LIST_DEPTH))
        entries: list[dict[str, object]] = []
        self._collect(target, levels, entries)
        return entries

    def _collect(self, directory: Path, levels: int, entries: list[dict[str, object]]) -> None:
        for item in sorted(directory.iterdir(), key=lambda value: (value.is_file(), value.name.lower())):
            if len(entries) >= 500:
                return
            is_file = item.is_file()
            entries.append({
                "path": self.relative(item),
                "kind": "file" if is_file else "directory",
                "size_bytes": item.stat().st_size if is_file else None,
            })
            if not is_file and levels > 1:
                self._collect(item, levels - 1, entries)

    def search(self, pattern: str, *, glob: str = "**/*", max_results: int = 50, ignore_case: bool = True) -> list[dict[str, object]]:
        """Scan workspace text files for a regular expression.

        The glob is validated before it reaches ``Path.glob`` because a pattern
        containing ``..`` would otherwise walk out of the sandbox that every
        other method is careful to enforce.
        """
        if not isinstance(pattern, str) or not pattern.strip():
            raise WorkspaceError("pattern must be a non-blank string")
        if not isinstance(glob, str) or not glob.strip() or ".." in glob or glob.startswith("/") or "\\" in glob:
            raise WorkspaceError("glob must be a relative pattern without '..'")
        try:
            expression = re.compile(pattern, re.IGNORECASE if ignore_case else 0)
        except re.error as error:
            raise WorkspaceError(f"pattern is not a valid regular expression: {error}") from error
        limit = max(1, min(int(max_results), MAX_SEARCH_RESULTS))
        matches: list[dict[str, object]] = []
        for item in sorted(self.root.glob(glob)):
            if len(matches) >= limit:
                break
            try:
                resolved = item.resolve()
                relative = resolved.relative_to(self.root).as_posix()
                if not resolved.is_file() or resolved.stat().st_size > MAX_SEARCH_FILE_BYTES:
                    continue
                text = resolved.read_bytes()[:MAX_SEARCH_FILE_BYTES].decode("utf-8", "replace")
            except (OSError, ValueError):
                continue
            for number, line in enumerate(text.splitlines(), start=1):
                if len(matches) >= limit:
                    break
                if expression.search(line):
                    matches.append({"path": relative, "line": number, "text": line.strip()[:MAX_SEARCH_LINE_CHARS]})
        return matches

    def file_snapshot(self) -> dict[str, dict[str, int]]:
        """Return bounded metadata for regular files that remain in this workspace."""
        snapshot: dict[str, dict[str, int]] = {}
        for item in self.root.rglob("*"):
            if len(snapshot) >= MAX_SNAPSHOT_FILES:
                break
            try:
                resolved = item.resolve()
                relative = resolved.relative_to(self.root).as_posix()
                if not resolved.is_file():
                    continue
                stat = resolved.stat()
            except (OSError, ValueError):
                continue
            snapshot[relative] = {"size_bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns}
        return snapshot

    def changed_files(self, before: dict[str, dict[str, int]]) -> list[dict[str, object]]:
        after = self.file_snapshot()
        return [
            {"path": path, "size_bytes": metadata["size_bytes"]}
            for path, metadata in after.items()
            if before.get(path) != metadata
        ]

    def file_metadata(self, path: str) -> dict[str, object]:
        target = self.resolve(path)
        if not target.is_file():
            raise WorkspaceError(f"'{path}' is not a file in this workspace")
        return {"path": self.relative(target), "size_bytes": target.stat().st_size}


__all__ = [
    "ConversationWorkspace",
    "MAX_LIST_DEPTH",
    "MAX_READ_BYTES",
    "MAX_READ_LINES",
    "MAX_SEARCH_FILE_BYTES",
    "MAX_SEARCH_LINE_CHARS",
    "MAX_SEARCH_RESULTS",
    "MAX_SNAPSHOT_FILES",
    "MAX_WRITE_BYTES",
    "WorkspaceError",
]
