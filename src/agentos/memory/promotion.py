"""Single-writer promotion gate and 14-day expiration for subagent memory proposals (WS-D5 / D-RF10).

Domain-only module: defines the ``PromotionBackend`` protocol and ``MemoryPromotionGate``
with per-scope locking so concurrent sibling subagents never duplicate active entries.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import importlib
import threading
from typing import Any, Literal, Protocol, runtime_checkable

PROPOSAL_EXPIRATION_DAYS = 14
PROPOSAL_EXPIRATION_DELTA = timedelta(days=PROPOSAL_EXPIRATION_DAYS)

_PASS_STRINGS = frozenset({"pass", "passed", "approved", "ok", "true", "yes"})
_FAIL_STRINGS = frozenset({"fail", "failed", "rejected", "false", "no"})

_LOCKS_GUARD = threading.Lock()
_SCOPE_LOCKS: dict[str, threading.Lock] = {}


def _lock_for_scope(scope_key: str) -> threading.Lock:
    with _LOCKS_GUARD:
        lock = _SCOPE_LOCKS.get(scope_key)
        if lock is None:
            lock = threading.Lock()
            _SCOPE_LOCKS[scope_key] = lock
        return lock


def parse_verification_verdict(verdict: bool | str | None) -> bool | None:
    """Normalize a verification verdict to True (pass), False (fail), or None (absent)."""
    if verdict is None:
        return None
    if isinstance(verdict, bool):
        return verdict
    text = str(verdict).strip().lower()
    if not text:
        return None
    if text in _PASS_STRINGS:
        return True
    if text in _FAIL_STRINGS:
        return False
    return None


@dataclass(frozen=True, slots=True)
class ProposedMemoryRecord:
    """Minimal projection of a proposed agent memory waiting for promotion."""

    memory_id: str
    user_id: str
    scope_type: str
    scope_id: str
    project_id: str | None
    agent_key: str | None
    text_hash: str
    fact: str

    @property
    def scope_lock_key(self) -> str:
        return f"{self.user_id}:{self.scope_type}:{self.scope_id}:{self.project_id or ''}:{self.agent_key or ''}"


@dataclass(frozen=True, slots=True)
class PromotionOutcome:
    """Result of attempting to promote a single proposed memory inside its scope lock."""

    memory_id: str
    outcome: Literal["promoted", "deduplicated", "skipped"]
    active_memory_id: str | None = None


@dataclass(frozen=True, slots=True)
class PromotionResult:
    """Summary returned by ``MemoryPromotionGate.on_verified``."""

    promoted_ids: tuple[str, ...] = ()
    deduplicated_ids: tuple[str, ...] = ()
    skipped_ids: tuple[str, ...] = ()

    @property
    def promoted_count(self) -> int:
        return len(self.promoted_ids)


@runtime_checkable
class PromotionBackend(Protocol):
    """Persistence port for listing, promoting, and expiring agent memory proposals."""

    def list_proposed_for_turn(
        self,
        turn_id: str,
        *,
        user_id: str | None = None,
    ) -> Sequence[ProposedMemoryRecord]:
        ...

    def promote_proposal(
        self,
        memory_id: str,
        *,
        actor: str,
        turn_id: str,
        now: datetime,
    ) -> PromotionOutcome:
        ...

    def expire_proposals(
        self,
        older_than: datetime,
        *,
        now: datetime,
        actor: str = "system",
        scope_type: str | None = "agent",
    ) -> Sequence[str]:
        ...


def _resolve_backend(backend_or_engine: Any) -> PromotionBackend:
    if isinstance(backend_or_engine, PromotionBackend):
        return backend_or_engine
    mod = importlib.import_module("agentos.persistence.postgres.memory_promotion")
    return mod.SqlMemoryPromotionBackend(backend_or_engine)


class MemoryPromotionGate:
    """Single-writer promotion gate for subagent proposals with per-scope locking (D-RF10)."""

    def __init__(
        self,
        backend_or_engine: Any,
        *,
        user_id: str | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._backend: PromotionBackend = _resolve_backend(backend_or_engine)
        self._user_id = str(user_id).strip() if user_id and str(user_id).strip() else None
        self._clock = clock or (lambda: datetime.now(UTC))

    def on_verified(
        self,
        turn_id: str,
        verdict: bool | str | None,
        *,
        learn_from_verified_only: bool = True,
        actor: str = "system",
    ) -> PromotionResult:
        """Promote eligible agent proposals for ``turn_id`` according to the verification verdict."""
        clean_turn_id = str(turn_id or "").strip()
        if not clean_turn_id:
            return PromotionResult()

        parsed = parse_verification_verdict(verdict)
        if learn_from_verified_only:
            if parsed is not True:
                return PromotionResult()
        else:
            if parsed is False:
                return PromotionResult()

        proposals = list(self._backend.list_proposed_for_turn(clean_turn_id, user_id=self._user_id))
        if not proposals:
            return PromotionResult()

        grouped: dict[str, list[ProposedMemoryRecord]] = {}
        for item in proposals:
            if item.scope_type != "agent":
                continue
            grouped.setdefault(item.scope_lock_key, []).append(item)

        promoted: list[str] = []
        deduplicated: list[str] = []
        skipped: list[str] = []
        now = self._clock()

        for scope_key in sorted(grouped.keys()):
            lock = _lock_for_scope(scope_key)
            with lock:
                for item in grouped[scope_key]:
                    res = self._backend.promote_proposal(
                        item.memory_id,
                        actor=actor,
                        turn_id=clean_turn_id,
                        now=now,
                    )
                    if res.outcome == "promoted":
                        promoted.append(item.memory_id)
                    elif res.outcome == "deduplicated":
                        deduplicated.append(item.memory_id)
                    else:
                        skipped.append(item.memory_id)

        return PromotionResult(
            promoted_ids=tuple(promoted),
            deduplicated_ids=tuple(deduplicated),
            skipped_ids=tuple(skipped),
        )

    def expire_proposals(
        self,
        older_than: datetime | timedelta | None = None,
        *,
        now: datetime | None = None,
        actor: str = "system",
        scope_type: str | None = "agent",
    ) -> int:
        """Mark unpromoted proposals older than ``older_than`` (default 14 days) as ``invalid`` + event ``expired``."""
        current = now or self._clock()
        if older_than is None:
            cutoff = current - PROPOSAL_EXPIRATION_DELTA
        elif isinstance(older_than, timedelta):
            cutoff = current - older_than
        else:
            cutoff = older_than
        expired_ids = self._backend.expire_proposals(
            cutoff,
            now=current,
            actor=actor,
            scope_type=scope_type,
        )
        return len(expired_ids)


__all__ = [
    "PROPOSAL_EXPIRATION_DAYS",
    "PROPOSAL_EXPIRATION_DELTA",
    "MemoryPromotionGate",
    "PromotionBackend",
    "PromotionOutcome",
    "PromotionResult",
    "ProposedMemoryRecord",
    "parse_verification_verdict",
]
