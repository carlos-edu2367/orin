"""Grep tools: methods of AgentToolset that live in their own module (W4 split)."""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from agentos.agentic.ripgrep import event_path, event_text, ripgrep_binary, run_ripgrep
from agentos.agentic.workspace import MAX_SEARCH_RESULTS
from agentos.agentic.tools._shared import AgentToolError, _GLOB_IGNORED_DIRS, _TEXT, _schema
from agentos.agentic.tools.spec import ToolSpec, ToolInput
from agentos.ignore import DENIED_SEGMENTS


def _grep_output(
    mode: str, *, filenames: list[str], body: str, num_matches: int | None, limit: int, offset: int, truncated: bool,
) -> dict[str, Any]:
    """Claude Code's Grep result for one call."""
    output: dict[str, Any] = {"mode": mode, "numFiles": len(filenames), "filenames": filenames}
    if mode != "files_with_matches":
        output["content"] = body
        output["numLines"] = 0 if body == "[no match]" else len(body.splitlines())
    if num_matches is not None:
        output["numMatches"] = num_matches
    if truncated:
        output["appliedLimit"] = limit
    if offset:
        output["appliedOffset"] = offset
    return output


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
        line_numbers: bool = True,
        only_matching: bool = False,
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

        ripgrep = ripgrep_binary(self.workspace.root)
        rg_found = None
        if ripgrep is not None:
            rg_found = self._grep_with_ripgrep(
                ripgrep, expr=expr if is_regex else raw_pattern, fixed=not is_regex, ignore_case=eff_ignore_case,
                multiline=bool(multiline), search_target=search_target, glob=eff_glob, file_type=clean_type,
                pre_ctx=pre_ctx, post_ctx=post_ctx, only_matching=bool(only_matching),
            )
        candidate_files: list[Path] = []
        if rg_found is not None:
            pass  # ripgrep already searched; the Python scan below sees no candidates
        elif search_target.is_file():
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
                            pieces = [found.group(0) for found in regex.finditer(line_text)] if only_matching else [line_text]
                            for piece in pieces:
                                content_matches.append({
                                    "path": rel_posix,
                                    "file": rel_posix,
                                    "line": idx + 1,
                                    "text": piece[:300],
                                    "before_context": b_ctx,
                                    "after_context": a_ctx,
                                })
                if file_hits > 0:
                    files_with_matches.append(rel_posix)
                    counts_list.append({"path": rel_posix, "file": rel_posix, "count": file_hits})

        if rg_found is not None:
            files_with_matches, counts_list, content_matches = rg_found

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
                "tool_output": _grep_output("files_with_matches", filenames=sliced_files, body=body, num_matches=None, limit=eff_limit, offset=eff_offset, truncated=truncated),
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
                "tool_output": _grep_output("count", filenames=[str(item["path"]) for item in sliced_counts], body=body, num_matches=total_occurrences, limit=eff_limit, offset=eff_offset, truncated=truncated),
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
                    block_lines.append(f"{item['path']}-{b_line_no}: {b_text}" if line_numbers else f"{item['path']}- {b_text}")
                block_lines.append(f"{item['path']}:{item['line']}: {item['text']}" if line_numbers else f"{item['path']}: {item['text']}")
                for idx_a, a_text in enumerate(a_lines):
                    a_line_no = item.get("end_line", item["line"]) + 1 + idx_a
                    block_lines.append(f"{item['path']}+{a_line_no}: {a_text}" if line_numbers else f"{item['path']}+ {a_text}")
                blocks.append("\n".join(block_lines))
            body = "\n--\n".join(blocks)
        else:
            body = "\n".join(
                f"{item['path']}:{item['line']}: {item['text']}" if line_numbers else f"{item['path']}: {item['text']}"
                for item in sliced_matches
            )
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
            "tool_output": _grep_output("content", filenames=list(dict.fromkeys(str(item["path"]) for item in sliced_matches)), body=body, num_matches=len(sliced_matches), limit=eff_limit, offset=eff_offset, truncated=truncated),
        }

    def _grep_with_ripgrep(
        self,
        binary: Path,
        *,
        expr: str,
        fixed: bool,
        ignore_case: bool,
        multiline: bool,
        search_target: Path,
        glob: str,
        file_type: str,
        pre_ctx: int,
        post_ctx: int,
        only_matching: bool,
    ) -> tuple[list[str], list[dict[str, Any]], list[dict[str, Any]]] | None:
        """The same three lists the Python scan builds, from ripgrep; None makes the caller fall back."""
        # --no-config: no RIPGREP_CONFIG_PATH; --no-require-git: .gitignore counts outside a git checkout too,
        # as Orin's own ignore policy does; the globs exclude what the Python search never looks at.
        arguments = [
            str(binary), "--json", "--no-config", "--no-require-git", "--hidden", "--sort", "path",
            "--max-filesize", "2M", "--max-columns", "2000", "-i" if ignore_case else "-s",
        ]
        if fixed:
            arguments.append("-F")
        for ignored in sorted(_GLOB_IGNORED_DIRS | DENIED_SEGMENTS | {".git"}):
            arguments += ["--glob", f"!{ignored}"]
        if multiline:
            arguments += ["-U", "--multiline-dotall"]
        if glob and glob not in {"**/*", "**", "*"}:
            arguments += ["--glob", glob]
        if file_type:
            arguments += ["--type", file_type]
        if pre_ctx:
            arguments += ["-B", str(pre_ctx)]
        if post_ctx:
            arguments += ["-A", str(post_ctx)]
        arguments += ["--", expr, str(search_target)]
        events = run_ripgrep(arguments, cwd=self.workspace.root)
        if events is None:
            return None
        root = self.workspace.root.resolve()
        by_file: dict[str, list[dict[str, Any]]] = {}
        for event in events:
            raw = event_path(event)
            if raw is None:
                continue
            try:
                rel = Path(raw).resolve().relative_to(root).as_posix()
            except (OSError, ValueError):
                continue  # outside the workspace (a link ripgrep followed): never reported
            by_file.setdefault(rel, []).append(event)
        files_with_matches: list[str] = []
        counts_list: list[dict[str, Any]] = []
        content_matches: list[dict[str, Any]] = []
        for rel, file_events in by_file.items():
            lines: dict[int, str] = {}
            for event in file_events:
                number = int((event.get("data") or {}).get("line_number") or 0)
                for offset, text in enumerate(event_text(event).splitlines()):
                    lines.setdefault(number + offset, text)
            matches = [event for event in file_events if event.get("type") == "match"]
            if not matches:
                continue
            files_with_matches.append(rel)
            hits = sum(len((event.get("data") or {}).get("submatches") or [1]) for event in matches) if multiline else len(matches)
            counts_list.append({"path": rel, "file": rel, "count": hits})
            for event in matches:
                data = event.get("data") or {}
                start = int(data.get("line_number") or 0)
                text = event_text(event)
                end = start + max(0, len(text.rstrip("\n").splitlines()) - 1)
                submatches = [str((item.get("match") or {}).get("text") or "") for item in data.get("submatches") or []]
                pieces = submatches if (only_matching or multiline) and submatches else [text.rstrip("\r\n")]
                before = [lines[number][:300] for number in range(max(1, start - pre_ctx), start) if number in lines] if pre_ctx else []
                after = [lines[number][:300] for number in range(end + 1, end + 1 + post_ctx) if number in lines] if post_ctx else []
                for piece in pieces:
                    item = {"path": rel, "file": rel, "line": start, "text": piece[:300],
                            "before_context": before, "after_context": after}
                    if end != start:
                        item["end_line"] = end
                    content_matches.append(item)
        return files_with_matches, counts_list, content_matches


class GrepInput(ToolInput):
    pattern: str = Field(description="Regular expression to search for.")
    path: str | None = Field(None, description="File or directory to search.")
    glob: str | None = Field(None, description="Only search files matching this glob.")
    type: str | None = Field(None, description="Only search this file type, e.g. 'py'.")
    output_mode: Literal["content", "files_with_matches", "count"] | None = None
    after: int | None = Field(None, alias="-A", ge=0, le=50, description="Lines after each match.")
    before: int | None = Field(None, alias="-B", ge=0, le=50, description="Lines before each match.")
    around: int | None = Field(None, alias="-C", ge=0, le=50, description="Lines around each match.")
    ignore_case: bool | None = Field(None, alias="-i", description="Case-insensitive search.")
    line_numbers: bool | None = Field(None, alias="-n", description="Show line numbers (default true).")
    only_matching: bool | None = Field(None, alias="-o", description="Show only the matched text.")
    context: int | None = Field(None, ge=0, le=50, description="Lines around each match (same as -C).")
    multiline: bool | None = Field(None, description="Let patterns span lines.")
    head_limit: int | None = Field(None, ge=1, le=500, description="Return at most this many entries.")
    offset: int | None = Field(None, ge=0, description="Skip this many entries first.")


class GrepOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["content", "files_with_matches", "count"]
    numFiles: int
    filenames: list[str]
    content: str | None = None
    numLines: int | None = None
    numMatches: int | None = None
    appliedLimit: int | None = None
    appliedOffset: int | None = None


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
        input_model=GrepInput,
        output_model=GrepOutput,
        public_description='Search file contents with a regular expression.',
        read_only=True,
        order=90,
    ),
)
