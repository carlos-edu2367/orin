"""Grep tools: methods of AgentToolset that live in their own module (W4 split)."""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from agentos.agentic.workspace import MAX_SEARCH_RESULTS
from agentos.agentic.tools._shared import AgentToolError, _GLOB_IGNORED_DIRS, _TEXT, _schema
from agentos.agentic.tools.spec import ToolSpec


class GrepTools:
    """Mixed into ``AgentToolset``; ``self`` is the toolset."""

    def search_files(
        self,
        pattern: str | None = None,
        glob: str = "**/*",
        max_results: int = 50,
        ignore_case: bool = True,
        *,
        query: str | None = None,
        path: str | None = None,
        file_pattern: str | None = None,
        is_regex: bool = True,
        case_sensitive: bool | None = None,
        output_mode: str = "content",
        before_context: int = 0,
        after_context: int = 0,
        context_lines: int = 0,
        multiline: bool = False,
        head_limit: int | None = None,
        type: str | None = None,
        offset: int = 0,
    ) -> dict[str, Any]:
        raw_pattern = pattern if (isinstance(pattern, str) and pattern.strip()) else (query if isinstance(query, str) else "")
        if not isinstance(raw_pattern, str) or not raw_pattern.strip() or len(raw_pattern) > 500:
            raise AgentToolError("pattern must be a non-blank string of at most 500 characters")
        mode = str(output_mode or "content").strip().lower()
        if mode not in {"content", "files_with_matches", "count"}:
            raise AgentToolError("output_mode must be 'content', 'files_with_matches', or 'count'.")
        eff_ignore_case = (not bool(case_sensitive)) if case_sensitive is not None else bool(ignore_case)
        flags = re.MULTILINE
        if eff_ignore_case:
            flags |= re.IGNORECASE
        if multiline:
            flags |= re.DOTALL
        expr = raw_pattern if is_regex else re.escape(raw_pattern)
        try:
            regex = re.compile(expr, flags)
        except re.error as error:
            raise AgentToolError(f"invalid regular expression: {error}") from error

        eff_limit = int(head_limit) if (head_limit is not None and int(head_limit) > 0) else int(max_results or 50)
        eff_limit = max(1, min(eff_limit, MAX_SEARCH_RESULTS))
        eff_offset = max(0, int(offset or 0))
        pre_ctx = max(0, int(context_lines if int(context_lines or 0) > 0 else (before_context or 0)))
        post_ctx = max(0, int(context_lines if int(context_lines or 0) > 0 else (after_context or 0)))
        eff_glob = file_pattern.strip() if (isinstance(file_pattern, str) and file_pattern.strip()) else (glob or "**/*")
        clean_type = str(type or "").strip().lstrip(".").lower()

        search_target = self.workspace.resolve(path) if (isinstance(path, str) and path.strip()) else self.workspace.root
        if not search_target.exists():
            raise AgentToolError(f"Path not found: '{path}'.")

        candidate_files: list[Path] = []
        if search_target.is_file():
            candidate_files = [search_target]
        else:
            ignore_policy = getattr(self.workspace, "_ignore_policy", lambda: None)()
            for cur_root, dirs, files in os.walk(search_target):
                cur_path = Path(cur_root)
                kept_dirs: list[str] = []
                for d in dirs:
                    if d in _GLOB_IGNORED_DIRS or d.startswith(".git"):
                        continue
                    d_full = cur_path / d
                    try:
                        d_rel_ws = d_full.relative_to(self.workspace.root).as_posix()
                    except ValueError:
                        continue
                    if ignore_policy is not None and ignore_policy.ignores(d_rel_ws, is_dir=True):
                        continue
                    kept_dirs.append(d)
                dirs[:] = sorted(kept_dirs)
                for fname in sorted(files):
                    full_file = cur_path / fname
                    try:
                        rel_ws = full_file.relative_to(self.workspace.root).as_posix()
                        rel_target = full_file.relative_to(search_target).as_posix()
                    except ValueError:
                        continue
                    if any(part in _GLOB_IGNORED_DIRS for part in Path(rel_ws).parts):
                        continue
                    if ignore_policy is not None and ignore_policy.ignores(rel_ws, is_dir=False):
                        continue
                    if clean_type and full_file.suffix.lstrip(".").lower() != clean_type:
                        continue
                    if not (
                        self._glob_matches_path(rel_ws, eff_glob, allow_basename=True)
                        or self._glob_matches_path(rel_target, eff_glob, allow_basename=True)
                    ):
                        continue
                    candidate_files.append(full_file)

        files_with_matches: list[str] = []
        counts_list: list[dict[str, Any]] = []
        content_matches: list[dict[str, Any]] = []

        for file_path in candidate_files:
            try:
                if file_path.stat().st_size > 2_000_000:
                    continue
                raw_bytes = file_path.read_bytes()
            except OSError:
                continue
            if b"\x00" in raw_bytes[:4096]:
                continue
            text = raw_bytes.decode("utf-8", errors="replace")
            rel_posix = file_path.relative_to(self.workspace.root).as_posix()

            if multiline:
                found = list(regex.finditer(text))
                if not found:
                    continue
                files_with_matches.append(rel_posix)
                counts_list.append({"path": rel_posix, "file": rel_posix, "count": len(found)})
                if mode == "content":
                    file_lines = text.splitlines()
                    for m in found:
                        start_line = text.count("\n", 0, m.start()) + 1
                        end_line = text.count("\n", 0, m.end()) + 1
                        b_ctx = [l[:300] for l in file_lines[max(0, start_line - 1 - pre_ctx) : start_line - 1]] if pre_ctx > 0 else []
                        a_ctx = [l[:300] for l in file_lines[end_line : min(len(file_lines), end_line + post_ctx)]] if post_ctx > 0 else []
                        content_matches.append({
                            "path": rel_posix,
                            "file": rel_posix,
                            "line": start_line,
                            "end_line": end_line,
                            "text": m.group(0)[:300],
                            "before_context": b_ctx,
                            "after_context": a_ctx,
                        })
            else:
                file_lines = text.splitlines()
                file_hits = 0
                for idx, line_text in enumerate(file_lines):
                    if regex.search(line_text):
                        file_hits += 1
                        if mode == "content":
                            b_ctx = [l[:300] for l in file_lines[max(0, idx - pre_ctx) : idx]] if pre_ctx > 0 else []
                            a_ctx = [l[:300] for l in file_lines[idx + 1 : min(len(file_lines), idx + 1 + post_ctx)]] if post_ctx > 0 else []
                            content_matches.append({
                                "path": rel_posix,
                                "file": rel_posix,
                                "line": idx + 1,
                                "text": line_text[:300],
                                "before_context": b_ctx,
                                "after_context": a_ctx,
                            })
                if file_hits > 0:
                    files_with_matches.append(rel_posix)
                    counts_list.append({"path": rel_posix, "file": rel_posix, "count": file_hits})

        if mode == "files_with_matches":
            sliced_files = files_with_matches[eff_offset : eff_offset + eff_limit]
            truncated = len(files_with_matches) > eff_offset + eff_limit
            body = "\n".join(sliced_files) if sliced_files else "[no match]"
            payload = {
                "ok": True,
                "output_mode": mode,
                "pattern": raw_pattern[:200],
                "query": raw_pattern[:200],
                "files": sliced_files[:20],
                "matches": sliced_files[:20],
                "count": len(sliced_files),
                "truncated": truncated,
                "label": raw_pattern[:80],
            }
            return {
                "ok": True,
                "output_mode": mode,
                "pattern": raw_pattern,
                "query": raw_pattern,
                "files": sliced_files,
                "matches": sliced_files,
                "count": len(sliced_files),
                "truncated": truncated,
                "summary": f"Buscou '{raw_pattern[:40]}': {len(sliced_files)} {'arquivo' if len(sliced_files) == 1 else 'arquivos'}",
                "content": body,
                "payload": payload,
            }

        if mode == "count":
            sliced_counts = counts_list[eff_offset : eff_offset + eff_limit]
            truncated = len(counts_list) > eff_offset + eff_limit
            total_occurrences = sum(int(item["count"]) for item in sliced_counts)
            counts_map = {str(item["path"]): int(item["count"]) for item in sliced_counts}
            body = "\n".join(f"{item['path']}:{item['count']}" for item in sliced_counts) if sliced_counts else "[no match]"
            payload = {
                "ok": True,
                "output_mode": mode,
                "pattern": raw_pattern[:200],
                "query": raw_pattern[:200],
                "counts": dict(list(counts_map.items())[:20]),
                "matches": sliced_counts[:8],
                "count": len(sliced_counts),
                "total_matches": total_occurrences,
                "truncated": truncated,
                "label": raw_pattern[:80],
            }
            return {
                "ok": True,
                "output_mode": mode,
                "pattern": raw_pattern,
                "query": raw_pattern,
                "counts": counts_map,
                "matches": sliced_counts,
                "count": len(sliced_counts),
                "total_matches": total_occurrences,
                "truncated": truncated,
                "summary": f"Buscou '{raw_pattern[:40]}': {total_occurrences} {'ocorrência' if total_occurrences == 1 else 'ocorrências'} em {len(sliced_counts)} {'arquivo' if len(sliced_counts) == 1 else 'arquivos'}",
                "content": body,
                "payload": payload,
            }

        sliced_matches = content_matches[eff_offset : eff_offset + eff_limit]
        truncated = len(content_matches) > eff_offset + eff_limit
        if not sliced_matches:
            body = "[no match]"
        elif pre_ctx > 0 or post_ctx > 0:
            blocks: list[str] = []
            for item in sliced_matches:
                block_lines: list[str] = []
                b_lines = item.get("before_context") or []
                a_lines = item.get("after_context") or []
                for idx_b, b_text in enumerate(b_lines):
                    b_line_no = item["line"] - len(b_lines) + idx_b
                    block_lines.append(f"{item['path']}-{b_line_no}: {b_text}")
                block_lines.append(f"{item['path']}:{item['line']}: {item['text']}")
                for idx_a, a_text in enumerate(a_lines):
                    a_line_no = item.get("end_line", item["line"]) + 1 + idx_a
                    block_lines.append(f"{item['path']}+{a_line_no}: {a_text}")
                blocks.append("\n".join(block_lines))
            body = "\n--\n".join(blocks)
        else:
            body = "\n".join(f"{item['path']}:{item['line']}: {item['text']}" for item in sliced_matches)
        payload = {
            "ok": True,
            "output_mode": mode,
            "pattern": raw_pattern[:200],
            "query": raw_pattern[:200],
            "matches": sliced_matches[:4],
            "count": len(sliced_matches),
            "truncated": truncated,
            "label": raw_pattern[:80],
        }
        return {
            "ok": True,
            "output_mode": mode,
            "pattern": raw_pattern,
            "query": raw_pattern,
            "matches": sliced_matches,
            "count": len(sliced_matches),
            "truncated": truncated,
            "summary": f"Buscou '{raw_pattern[:40]}': {len(sliced_matches)} {'ocorrência' if len(sliced_matches) == 1 else 'ocorrências'}",
            "content": body,
            "payload": payload,
        }


# Tool specs, moved from AgentToolset._build_definitions (W4).
SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        key="search_files",
        description="Search file contents in the conversation workspace with a regular expression (Grep parity). Supports output_mode ('content', 'files_with_matches', 'count'), context lines (before_context, after_context, context_lines), multiline matching, and head_limit.",
        legacy_schema=_schema({
            "pattern": {**_TEXT, "description": "Python regular expression."},
            "query": {**_TEXT, "description": "Alias for pattern."},
            "path": {**_TEXT, "description": "Optional workspace-relative directory or file to search within."},
            "glob": {**_TEXT, "description": "Relative glob filter, e.g. '**/*.py'. Defaults to every file."},
            "file_pattern": {**_TEXT, "description": "Alias for glob filter, e.g. '*.py'."},
            "type": {**_TEXT, "description": "Optional file type/extension filter, e.g. 'py', 'ts'."},
            "output_mode": {
                "type": "string",
                "enum": ["content", "files_with_matches", "count"],
                "description": "Output mode: 'content' (matching lines), 'files_with_matches' (file paths only), or 'count' (match counts per file).",
            },
            "before_context": {"type": "integer", "minimum": 0, "maximum": 50, "description": "Number of lines to show before each match (-B)."},
            "after_context": {"type": "integer", "minimum": 0, "maximum": 50, "description": "Number of lines to show after each match (-A)."},
            "context_lines": {"type": "integer", "minimum": 0, "maximum": 50, "description": "Number of lines to show before and after each match (-C)."},
            "multiline": {"type": "boolean", "description": "Allow patterns to span across multiple lines (. matches newline)."},
            "head_limit": {"type": "integer", "minimum": 1, "maximum": 500, "description": "Limit output to the first N entries/matches."},
            "offset": {"type": "integer", "minimum": 0, "description": "Skip the first N matches before applying head_limit/max_results."},
            "max_results": {"type": "integer", "minimum": 1, "maximum": 200},
            "ignore_case": {"type": "boolean"},
            "case_sensitive": {"type": "boolean"},
            "is_regex": {"type": "boolean"},
        }),
        handler="search_files",
        kind="filesystem",
        read_only=True,
        order=90,
    ),
)
