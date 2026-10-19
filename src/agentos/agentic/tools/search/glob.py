"""Glob tools: methods of AgentToolset that live in their own module (W4 split)."""
from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from agentos.agentic.workspace import MAX_SEARCH_RESULTS, MAX_LIST_DEPTH
from agentos.agentic.tools._shared import AgentToolError, _GLOB_IGNORED_DIRS, _TEXT, _schema
from agentos.agentic.tools.spec import ToolSpec, ToolInput


class GlobTools:
    """Mixed into ``AgentToolset``; ``self`` is the toolset."""

    @staticmethod
    def _compile_glob_regex(pattern: str) -> re.Pattern[str]:
        norm = pattern.strip().replace("\\", "/")
        while norm.startswith("./"):
            norm = norm[2:]
        norm = norm.lstrip("/")
        parts: list[str] = ["^"]
        i = 0
        n = len(norm)
        while i < n:
            if norm[i : i + 3] == "**/":
                parts.append("(?:.+/)?")
                i += 3
            elif norm[i : i + 3] == "/**":
                parts.append("(?:/.*)?")
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
                if j < n:
                    inner = norm[i + 1 : j].replace("\\", "\\\\")
                    if inner.startswith("!"):
                        inner = "^" + inner[1:]
                    parts.append(f"[{inner}]")
                    i = j + 1
                else:
                    parts.append(re.escape(norm[i]))
                    i += 1
            else:
                parts.append(re.escape(norm[i]))
                i += 1
        parts.append("$")
        return re.compile("".join(parts))

    @classmethod
    def _glob_matches_path(cls, rel_posix: str, pattern: str, *, allow_basename: bool = False) -> bool:
        clean_pattern = (pattern or "**/*").strip().replace("\\", "/")
        if clean_pattern in {"", "*", "**", "**/*"}:
            return True
        regex = cls._compile_glob_regex(clean_pattern)
        if regex.match(rel_posix):
            return True
        if allow_basename and "/" not in clean_pattern:
            basename = rel_posix.rsplit("/", 1)[-1]
            if regex.match(basename):
                return True
        return False

    def glob_files(
        self,
        pattern: str,
        path: str | None = None,
        max_results: int = 200,
    ) -> dict[str, Any]:
        if not isinstance(pattern, str) or not pattern.strip():
            raise AgentToolError("pattern must be a non-blank glob string.")
        started = time.monotonic()
        clean_pattern = pattern.strip()
        cap = max(1, min(int(max_results or 200), 1000))
        base_root = self.workspace.resolve(path) if (isinstance(path, str) and path.strip()) else self.workspace.root
        if not base_root.exists() or not base_root.is_dir():
            raise AgentToolError(f"Directory not found: '{path}'.")
        ignore_policy = getattr(self.workspace, "_ignore_policy", lambda: None)()
        matched: list[tuple[int, str]] = []
        for cur_root, dirs, files in os.walk(base_root):
            cur_path = Path(cur_root)
            kept_dirs: list[str] = []
            for d in dirs:
                if d in _GLOB_IGNORED_DIRS or d.startswith(".git"):
                    continue
                d_full = cur_path / d
                try:
                    d_rel_ws = self.workspace.key(d_full)
                except ValueError:
                    continue
                if ignore_policy is not None and ignore_policy.ignores(d_rel_ws, is_dir=True):
                    continue
                kept_dirs.append(d)
            dirs[:] = kept_dirs
            for fname in files:
                full_file = cur_path / fname
                try:
                    rel_ws = self.workspace.key(full_file)
                    rel_base = full_file.relative_to(base_root).as_posix()
                except ValueError:
                    continue
                if any(part in _GLOB_IGNORED_DIRS for part in Path(rel_ws).parts):
                    continue
                if ignore_policy is not None and ignore_policy.ignores(rel_ws, is_dir=False):
                    continue
                if self._glob_matches_path(rel_base, clean_pattern, allow_basename=False):
                    try:
                        mtime_ns = full_file.stat().st_mtime_ns
                    except OSError:
                        mtime_ns = 0
                    matched.append((mtime_ns, rel_ws))
        matched.sort(key=lambda item: (-item[0], item[1]))
        truncated = len(matched) > cap
        matches = [rel for _, rel in matched[:cap]]
        body = "\n".join(matches) if matches else "[no match]"
        payload: dict[str, Any] = {
            "ok": True,
            "pattern": clean_pattern[:200],
            "matches": matches[:25],
            "count": len(matches),
            "truncated": truncated,
            "label": clean_pattern[:80],
        }
        return {
            "ok": True,
            "pattern": clean_pattern,
            "matches": matches,
            "count": len(matches),
            "truncated": truncated,
            "summary": f"Buscou arquivos '{clean_pattern[:60]}': {len(matches)} {'resultado' if len(matches) == 1 else 'resultados'}",
            "content": body,
            "payload": payload,
            "tool_output": {
                "durationMs": int((time.monotonic() - started) * 1000),
                "numFiles": len(matches),
                "filenames": [str(self.workspace.root / rel) for rel in matches],
                "truncated": truncated,
                "totalMatches": len(matched),
                "countIsComplete": True,
            },
        }

    def list_files(self, path: str = "", depth: int = 1, pattern: str = "") -> dict[str, Any]:
        if isinstance(pattern, str) and pattern.strip():
            matches = self.workspace.glob(pattern, max_results=MAX_SEARCH_RESULTS)
            body = "\n".join(matches) if matches else "[no match]"
            return {
                "summary": f"Buscou arquivos '{pattern[:60]}': {len(matches)} {'resultado' if len(matches) == 1 else 'resultados'}",
                "content": body,
                "payload": {"pattern": pattern[:200], "count": len(matches), "label": pattern[:80]},
            }
        entries = self.workspace.list_entries(path, depth=int(depth))
        if not entries:
            listing = "[empty directory]"
        else:
            listing = "\n".join(f"{item['kind'][:1]} {item['path']}" for item in entries)
        return {
            "summary": f"Listou {len(entries)} {'item' if len(entries) == 1 else 'itens'}",
            "content": listing,
            "payload": {"path": path or "/", "count": len(entries), "depth": int(depth), "label": path or "/"},
        }


class GlobInput(ToolInput):
    pattern: str = Field(description="Glob pattern.")
    path: str | None = Field(None, description="Directory to search in. Defaults to the workspace root.")


class GlobOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    durationMs: int
    numFiles: int
    filenames: list[str]
    truncated: bool
    totalMatches: int | None = None
    countIsComplete: bool | None = None


# Tool specs, moved from AgentToolset._build_definitions (W4).
SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        key="list_files",
        description="List files and directories in the conversation workspace. Use depth to see a whole subtree in one call, or "
        "pattern for a glob match (e.g. '**/*.tsx') when you need every file of one kind regardless of depth or directory shape — path and depth are ignored when pattern is set.",
        legacy_schema=_schema({
            "path": {**_TEXT, "description": "Workspace-relative directory; omit for the root. Ignored when pattern is set."},
            "depth": {"type": "integer", "minimum": 1, "maximum": MAX_LIST_DEPTH, "description": "How many directory levels to descend. Defaults to 1. Ignored when pattern is set."},
            "pattern": {**_TEXT, "description": "Glob pattern relative to the workspace, e.g. '**/*.py' or 'src/**/*.tsx'. When set, lists matching paths instead of one directory."},
        }),
        handler="list_files",
        kind="filesystem",
        read_only=True,
        order=70,
    ),
    ToolSpec(
        key="glob_files",
        description="Fast file pattern matching across the workspace (e.g. '**/*.py', 'src/**/*.tsx'), sorted by modification time (newest first).",
        legacy_schema=_schema({
            "pattern": {**_TEXT, "description": "Glob pattern to match files against (e.g. '**/*.py', 'src/**/*.ts')."},
            "path": {**_TEXT, "description": "Optional workspace-relative subdirectory to search within."},
            "max_results": {"type": "integer", "minimum": 1, "maximum": 1000, "description": "Maximum number of file paths to return (default 200)."},
        }, ("pattern",)),
        handler="glob_files",
        kind="filesystem",
        input_model=GlobInput,
        output_model=GlobOutput,
        public_description="Find files by glob pattern (e.g. '**/*.py'), newest first.",
        read_only=True,
        order=80,
    ),
)
