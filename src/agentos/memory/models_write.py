"""Write-path models, normalization, and hashing for agent memories (WS-D2)."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
from typing import Literal

SourceKind = Literal["user", "agent", "tool", "web", "file", "consolidator"]
MemoryStatus = Literal["active", "proposed", "quarantined", "superseded", "invalid", "deleted"]
WriteAction = Literal["add", "supersede", "conflict", "noop", "quarantine"]

_TRAILING_PUNCT_RE = re.compile(r"[\s.,;:!?…]+$")


def normalize_memory_text(text: str) -> str:
    """Normalize a memory fact for hashing: lowercase, collapsed spaces, no trailing punctuation."""
    collapsed = " ".join(str(text or "").lower().split())
    return _TRAILING_PUNCT_RE.sub("", collapsed).strip()


def compute_text_hash(text: str) -> str:
    """Return a 64-char hex blake2b digest (digest_size=32) of the normalized text."""
    normalized = normalize_memory_text(text)
    return hashlib.blake2b(normalized.encode("utf-8"), digest_size=32).hexdigest()


def source_kind_from_legacy_source(source: str | None) -> tuple[str, int]:
    """Map legacy ``agent_memories.source`` values to ``(source_kind, trust)``."""
    raw = str(source or "").strip().lower()
    if raw in {"user_explicit", "user"}:
        return ("user", 2)
    if raw in {"consolidation", "consolidator"}:
        return ("consolidator", 1)
    if raw in {"tool", "web", "file"}:
        return (raw, 0)
    return ("agent", 1)


@dataclass(frozen=True, slots=True)
class MemoryWriteContext:
    source_kind: str = "agent"
    source_ref: str | None = None
    actor: str = "system"
    agent_key: str | None = None
    conversation_id: str | None = None
    turn_id: str | None = None
    trust: int = 1


@dataclass(frozen=True, slots=True)
class MemoryWriteCandidate:
    fact: str
    text_hash: str
    kind: str = "fact"
    scope_type: str = "user"
    scope_id: str = ""
    project_id: str | None = None
    agent_key: str | None = None
    confidence: float = 1.0
    tags: tuple[str, ...] = ()
    supersedes_id: str | None = None


@dataclass(frozen=True, slots=True)
class WriteDecision:
    action: Literal["add", "supersede", "conflict", "noop", "quarantine"]
    target_id: str | None = None
    status: str = "active"
    reason: str = ""


__all__ = [
    "MemoryStatus",
    "MemoryWriteCandidate",
    "MemoryWriteContext",
    "SourceKind",
    "WriteAction",
    "WriteDecision",
    "compute_text_hash",
    "normalize_memory_text",
    "source_kind_from_legacy_source",
]
