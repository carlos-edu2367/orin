"""User `@file` mention extraction, expansion (`<system-reminder>`), and workspace file autocomplete."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
from typing import Any, Mapping, MutableMapping, Sequence

from agentos.agentic.agent_tools import ReadFileRecord
from agentos.agentic.workspace import ConversationWorkspace, WorkspaceError
from agentos.ignore import PathIgnorePolicy
from agentos.pathsafety import resolve_contained


MAX_MENTIONED_FILES = 5
MAX_MENTION_LINES = 400
MAX_MENTION_CHARS_PER_FILE = 8_000
MAX_WORKSPACE_FILE_SCAN = 2_000

# Matches `@path/to/file.ext` when `@` is at start of text or preceded by whitespace or open punctuation.
# Avoids matching email addresses (`foo@bar.com`).
_FILE_MENTION_RE = re.compile(
    r"(?:^|(?<=[\s(\[{\"'`]))@([A-Za-z0-9_./\\-]+)"
)
_TRAILING_PUNCTUATION = ".,;:!?)]}'\"`"


def _as_workspace(workspace: Any) -> ConversationWorkspace:
    if isinstance(workspace, ConversationWorkspace):
        return workspace
    if isinstance(workspace, (Path, str)):
        return ConversationWorkspace.at_root(Path(workspace).resolve())
    root = getattr(workspace, "root", workspace)
    return ConversationWorkspace.at_root(Path(root).resolve())


def extract_file_mentions(text: str) -> list[str]:
    """Extract deduplicated relative paths from `@path/to/file` tokens in ``text``."""
    if not isinstance(text, str) or "@" not in text:
        return []
    mentions: list[str] = []
    seen: set[str] = set()
    for match in _FILE_MENTION_RE.finditer(text):
        raw_token = match.group(1).strip(_TRAILING_PUNCTUATION)
        normalized = raw_token.replace("\\", "/").lstrip("/")
        while normalized.endswith(".") and not normalized.endswith(".."):
            normalized = normalized[:-1]
        if not normalized or normalized in {".", ".."}:
            continue
        segments = [seg for seg in normalized.split("/") if seg not in ("", ".")]
        if not segments or any(seg == ".." for seg in segments):
            continue
        clean_path = "/".join(segments)
        if clean_path not in seen:
            seen.add(clean_path)
            mentions.append(clean_path)
    return mentions


def expand_file_mentions(
    text: str,
    workspace: Any,
    read_file_state: MutableMapping[str, Any] | None = None,
    *,
    max_files: int = MAX_MENTIONED_FILES,
    max_lines: int = MAX_MENTION_LINES,
) -> str:
    """Resolve `@file` mentions in ``text``, pre-populate ``read_file_state``, and return a ``<system-reminder>`` block."""
    mentions = extract_file_mentions(text)
    if not mentions:
        return ""
    ws = _as_workspace(workspace)
    sections: list[str] = []
    loaded_count = 0

    for candidate in mentions:
        if loaded_count >= max_files:
            break
        try:
            target = ws.resolve(candidate)
        except WorkspaceError:
            continue
        if not target.exists():
            continue

        rel_path = ws.relative(target)
        if target.is_file():
            try:
                stat = target.stat()
                raw_bytes = target.read_bytes()
            except OSError:
                continue
            digest = hashlib.sha256(raw_bytes).hexdigest()
            full_text = raw_bytes.decode("utf-8", "replace")
            all_lines = full_text.splitlines()
            total_lines = len(all_lines)
            bounded_limit = max(1, min(int(max_lines), total_lines or 1))
            selected_lines = all_lines[:bounded_limit]
            numbered = "\n".join(f"{idx} | {line}" for idx, line in enumerate(selected_lines, start=1))
            if len(numbered) > MAX_MENTION_CHARS_PER_FILE:
                numbered = numbered[:MAX_MENTION_CHARS_PER_FILE] + "\n... [truncated]"

            if read_file_state is not None:
                record = ReadFileRecord(
                    mtime_ns=stat.st_mtime_ns,
                    sha256=digest,
                    offset=1,
                    limit=bounded_limit,
                    total_lines=total_lines,
                )
                read_file_state[rel_path] = record
                if candidate != rel_path:
                    read_file_state[candidate] = record

            line_range = f"1-{len(selected_lines)} of {total_lines}" if total_lines else "empty file"
            sections.append(f"### @{rel_path} (lines {line_range})\n{numbered}")
            loaded_count += 1
        elif target.is_dir():
            try:
                entries = ws.list_entries(rel_path if rel_path != "." else "", depth=2)[:40]
            except Exception:
                continue
            listing = "\n".join(f"- {item['kind']}: {item['path']}" for item in entries) or "(empty directory)"
            sections.append(f"### @{rel_path}/ (directory)\n{listing}")
            loaded_count += 1

    if not sections:
        return ""

    body = "\n\n".join(sections)
    return (
        "<system-reminder>\n"
        "The user referenced the following workspace file(s) with @-mentions. "
        "Their contents have been pre-loaded and registered in read_file_state so you can edit them directly:\n\n"
        f"{body}\n"
        "</system-reminder>"
    )


def inject_file_mentions_into_history(
    history: Sequence[Mapping[str, Any]],
    workspace: Any,
    read_file_state: MutableMapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Append the `<system-reminder>` for any `@file` mentions to the latest user message in ``history``."""
    updated = [dict(item) for item in history]
    for index in range(len(updated) - 1, -1, -1):
        if updated[index].get("role") != "user":
            continue
        original = updated[index].get("content", "")
        if isinstance(original, str):
            if "<system-reminder>" in original and "referenced the following workspace file(s)" in original:
                return updated
            reminder = expand_file_mentions(original, workspace, read_file_state)
            if reminder:
                updated[index]["content"] = f"{original}\n\n{reminder}" if original else reminder
        elif isinstance(original, list):
            text_parts = [
                str(block.get("text") or "")
                for block in original
                if isinstance(block, Mapping) and block.get("type") == "text"
            ]
            combined = "\n".join(text_parts)
            if "<system-reminder>" in combined and "referenced the following workspace file(s)" in combined:
                return updated
            reminder = expand_file_mentions(combined, workspace, read_file_state)
            if reminder:
                updated[index]["content"] = [*original, {"type": "text", "text": reminder}]
        return updated
    return updated


def list_workspace_files(workspace: Any, query: str = "", *, limit: int = 20) -> list[str]:
    """List workspace-relative file paths filtered and ranked by ``query`` for `@file` autocomplete."""
    ws = _as_workspace(workspace)
    root = ws.root
    if not root.is_dir():
        return []
    bounded_limit = max(1, min(int(limit), 100))
    needle = (query or "").strip().replace("\\", "/").lstrip("/").lower()
    ignore = PathIgnorePolicy.for_root(root)

    candidates: list[str] = []
    for current_dir, dirnames, filenames in os.walk(root):
        current = Path(current_dir)
        dirnames[:] = sorted(
            name
            for name in dirnames
            if name not in {".git", ".orin"}
            and not ignore.ignores(ws.relative(current / name), is_dir=True)
        )
        for filename in sorted(filenames):
            if len(candidates) >= MAX_WORKSPACE_FILE_SCAN:
                break
            item = current / filename
            resolved = resolve_contained(item, root)
            if resolved is None or not resolved.is_file():
                continue
            rel = ws.relative(resolved)
            if rel.startswith(".git/") or rel.startswith(".orin/") or ignore.ignores(rel):
                continue
            candidates.append(rel)
        if len(candidates) >= MAX_WORKSPACE_FILE_SCAN:
            break

    if not needle:
        return candidates[:bounded_limit]

    def rank_key(path: str) -> tuple[int, int, str]:
        lower_path = path.lower()
        basename = lower_path.rsplit("/", 1)[-1]
        if basename == needle or lower_path == needle:
            return (0, len(path), lower_path)
        if basename.startswith(needle):
            return (1, len(path), lower_path)
        if lower_path.startswith(needle):
            return (2, len(path), lower_path)
        return (3, len(path), lower_path)

    matched = [path for path in candidates if needle in path.lower()]
    matched.sort(key=rank_key)
    return matched[:bounded_limit]


__all__ = [
    "MAX_MENTIONED_FILES",
    "MAX_MENTION_LINES",
    "expand_file_mentions",
    "extract_file_mentions",
    "inject_file_mentions_into_history",
    "list_workspace_files",
]
