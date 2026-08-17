"""Split a file into chunks that respect definition boundaries.

This is the heuristic implementation. It costs no dependencies and captures the
two things retrieval actually needs: a symbol name, and a span that does not cut
a definition in half. A tree-sitter implementation can replace it behind the
``Chunker`` protocol without touching any caller.
"""
from __future__ import annotations

import re

from .models import Chunk
from .symbols import language_for


MAX_CHUNK_LINES = 120
OVERLAP_LINES = 20
MAX_CHUNKS_PER_FILE = 400

_DEFINITION_PATTERNS: dict[str, re.Pattern[str]] = {
    "python": re.compile(r"^\s*(?:async\s+)?(?:def|class)\s+(?P<name>\w+)"),
    "javascript": re.compile(
        r"^\s*(?:export\s+(?:default\s+)?)?(?:"
        r"(?:async\s+)?function\s*\*?\s*(?P<fn>\w+)"
        r"|class\s+(?P<cls>\w+)"
        r"|(?:const|let|var)\s+(?P<var>\w+)\s*(?::[^=]+)?=\s*(?:async\s*)?(?:\(|function)"
        r")"
    ),
    "go": re.compile(r"^\s*func\s+(?:\([^)]*\)\s*)?(?P<name>\w+)"),
    "rust": re.compile(r"^\s*(?:pub\s+)?(?:async\s+)?(?:fn|struct|enum|trait|impl)\s+(?P<name>\w+)"),
    "java": re.compile(r"^\s*(?:public|private|protected)\s+(?:static\s+)?[\w<>\[\], ]+\s+(?P<name>\w+)\s*\("),
    "csharp": re.compile(r"^\s*(?:public|private|protected|internal)\s+(?:static\s+)?[\w<>\[\], ]+\s+(?P<name>\w+)\s*\("),
    "ruby": re.compile(r"^\s*(?:def|class|module)\s+(?P<name>[\w.]+)"),
    "php": re.compile(r"^\s*(?:public\s+|private\s+|protected\s+)?function\s+(?P<name>\w+)"),
    "markdown": re.compile(r"^#{1,6}\s+(?P<name>.+?)\s*$"),
}
_DEFINITION_PATTERNS["typescript"] = _DEFINITION_PATTERNS["javascript"]


def _symbol(match: re.Match[str]) -> str | None:
    for value in match.groupdict().values():
        if value:
            return value.strip()
    return None


class HeuristicChunker:
    """Chunker built from per-language definition patterns plus a window fallback."""

    def split(self, path: str, text: str) -> list[Chunk]:
        if not text.strip():
            return []
        lines = text.splitlines()
        if not lines:
            return []
        pattern = _DEFINITION_PATTERNS.get(language_for(path) or "")
        boundaries = self._boundaries(lines, pattern)
        chunks: list[Chunk] = []
        for index, (start, symbol) in enumerate(boundaries):
            end = boundaries[index + 1][0] - 1 if index + 1 < len(boundaries) else len(lines)
            chunks.extend(self._emit(path, lines, start, end, symbol))
            if len(chunks) >= MAX_CHUNKS_PER_FILE:
                return chunks[:MAX_CHUNKS_PER_FILE]
        return chunks

    @staticmethod
    def _boundaries(lines: list[str], pattern: re.Pattern[str] | None) -> list[tuple[int, str | None]]:
        """Segment starts as 1-based line numbers, each with its symbol.

        A leading segment is always present so a file's header — imports,
        licence, module docstring — is indexed rather than dropped.
        """
        found: list[tuple[int, str | None]] = []
        if pattern is not None:
            for number, line in enumerate(lines, start=1):
                match = pattern.match(line)
                if match is not None:
                    found.append((number, _symbol(match)))
        if not found:
            return [(1, None)]
        if found[0][0] > 1:
            return [(1, None), *found]
        return found

    @staticmethod
    def _emit(path: str, lines: list[str], start: int, end: int, symbol: str | None) -> list[Chunk]:
        """One segment as one chunk, or as overlapping windows when it is long."""
        kind = "definition" if symbol is not None else "block"
        emitted: list[Chunk] = []
        cursor = start
        step = MAX_CHUNK_LINES - OVERLAP_LINES
        while cursor <= end:
            window_end = min(cursor + MAX_CHUNK_LINES - 1, end)
            body = "\n".join(lines[cursor - 1 : window_end])
            if body.strip():
                emitted.append(Chunk(path=path, start_line=cursor, end_line=window_end, symbol=symbol, kind=kind, text=body))
            if window_end >= end:
                break
            cursor += step
        return emitted


__all__ = ["MAX_CHUNKS_PER_FILE", "MAX_CHUNK_LINES", "OVERLAP_LINES", "HeuristicChunker"]
