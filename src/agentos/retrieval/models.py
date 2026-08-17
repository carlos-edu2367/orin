"""Values exchanged across the retrieval boundary.

Everything here is frozen and validated at construction, so a malformed chunk
cannot reach the index and a malformed hit cannot reach the model.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class Chunk:
    """One indexed span of one file."""

    path: str
    start_line: int
    end_line: int
    symbol: str | None
    kind: str
    text: str

    def __post_init__(self) -> None:
        if not isinstance(self.path, str) or not self.path.strip():
            raise ValueError("path must be a non-blank string")
        if self.start_line < 1:
            raise ValueError("start_line is 1-based")
        if self.end_line < self.start_line:
            raise ValueError("end_line must not precede start_line")
        if self.kind not in {"definition", "block"}:
            raise ValueError("kind must be 'definition' or 'block'")

    @property
    def chunk_id(self) -> str:
        return f"{self.path}:{self.start_line}-{self.end_line}"


@dataclass(frozen=True, slots=True)
class SearchHit:
    path: str
    start_line: int
    end_line: int
    symbol: str | None
    score: float
    text: str

    @property
    def location(self) -> str:
        return f"{self.path}:{self.start_line}-{self.end_line}"


@dataclass(frozen=True, slots=True)
class EmbedderIdentity:
    """What produced the vectors currently stored.

    Persisted with the index. A mismatch on reopen means every stored vector is
    meaningless, so they are discarded rather than silently trusted.
    """

    embedder_id: str
    model: str
    dim: int


@dataclass(frozen=True, slots=True)
class IndexStatus:
    files: int
    chunks: int
    vectors: int
    last_scan_at: datetime | None

    @property
    def mode(self) -> str:
        return "semantic" if self.vectors > 0 else "lexical"


__all__ = ["Chunk", "EmbedderIdentity", "IndexStatus", "SearchHit"]
