"""Composable WritePolicy interface and base identity policy for memory writes (WS-D2)."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any, Protocol, runtime_checkable

from .conflict import ConflictResolver
from .conflict_adjudicator import ConflictAdjudicator
from .models_write import MemoryWriteContext, WriteDecision
from .quarantine import QuarantinePolicy


@runtime_checkable
class WritePolicy(Protocol):
    """Decision hook invoked before persisting a memory candidate."""

    def decide(self, candidate: Any, context: MemoryWriteContext) -> WriteDecision:
        ...


class IdentityWritePolicy:
    """Default no-op policy that preserves standard add/active behaviour until D3/D4 plug in."""

    def decide(self, candidate: Any, context: MemoryWriteContext) -> WriteDecision:
        _ = (candidate, context)
        return WriteDecision(action="add", target_id=None, status="active", reason="identity")


class WritePolicyChain:
    """Runs policies in order; the first non-default decision wins."""

    def __init__(self, policies: Sequence[WritePolicy] = ()) -> None:
        self._policies: tuple[WritePolicy, ...] = tuple(policies)

    @property
    def policies(self) -> tuple[WritePolicy, ...]:
        return self._policies

    def decide(self, candidate: Any, context: MemoryWriteContext) -> WriteDecision:
        for policy in self._policies:
            decision = policy.decide(candidate, context)
            if (
                decision.action != "add"
                or decision.status != "active"
                or decision.target_id is not None
            ):
                return decision
        return WriteDecision(action="add", target_id=None, status="active", reason="identity")


def default_write_policy(
    *,
    quarantine_enabled: bool | Callable[[], bool] = True,
    settings_provider: Callable[[], Any] | None = None,
    on_quarantined: Callable[[Any, MemoryWriteContext, WriteDecision], None] | None = None,
    adjudicator: ConflictAdjudicator | None = None,
    llm_adjudication_enabled: bool | Callable[[], bool] | None = None,
) -> WritePolicyChain:
    """Return the default composable memory write policy chain (Quarantine -> Conflict)."""
    return WritePolicyChain(
        (
            QuarantinePolicy(
                enabled=quarantine_enabled,
                settings_provider=settings_provider,
                on_quarantined=on_quarantined,
            ),
            ConflictResolver(
                adjudicator=adjudicator,
                llm_adjudication_enabled=llm_adjudication_enabled,
            ),
        )
    )


__all__ = [
    "IdentityWritePolicy",
    "QuarantinePolicy",
    "WritePolicy",
    "WritePolicyChain",
    "default_write_policy",
]
