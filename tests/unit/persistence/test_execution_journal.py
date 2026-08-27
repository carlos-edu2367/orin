from datetime import UTC, datetime

from sqlalchemy import create_engine

from agentos.execution.journal import (
    ExecutionCheckpoint,
    ExecutionEffect,
    ExecutionEffectKind,
    ExecutionEffectRetryability,
    ExecutionEffectState,
    ExecutionJournalScope,
)
from agentos.persistence.postgres.execution_journal import PostgresExecutionJournal
from agentos.persistence.postgres.schema import metadata


def _scope(*, user_id: str = "user-1") -> ExecutionJournalScope:
    return ExecutionJournalScope("execution-1", user_id, "workspace-1", "agent-1", "correlation-1")


def _effect(scope: ExecutionJournalScope) -> ExecutionEffect:
    return ExecutionEffect(
        "effect-1", scope, ExecutionEffectKind.TOOL, "tool:1", "request:1", "idempotency:1",
        ExecutionEffectState.PREPARED, ExecutionEffectRetryability.NEVER, 1,
        datetime(2026, 8, 27, tzinfo=UTC),
    )


def test_journal_keeps_an_unfinished_effect_out_of_the_safe_resume_set() -> None:
    engine = create_engine("sqlite://", future=True)
    metadata.create_all(engine)
    journal = PostgresExecutionJournal(engine)
    scope = _scope()
    effect = journal.prepare(_effect(scope))

    journal.save_checkpoint(ExecutionCheckpoint(
        "checkpoint:unsafe", scope, 1, 3, "context:1", "tool", False,
        datetime(2026, 8, 27, tzinfo=UTC), pending_effect_id=effect.effect_id,
    ))
    journal.mark_in_flight(effect.effect_id, scope, now=datetime(2026, 8, 27, 1, tzinfo=UTC))

    assert journal.latest_safe(scope) is None
    assert journal.unresolved(scope)[0].state is ExecutionEffectState.IN_FLIGHT

    recovered = journal.mark_unresolved_unknown(scope, now=datetime(2026, 8, 27, 2, tzinfo=UTC))

    assert recovered[0].state is ExecutionEffectState.UNKNOWN
    assert journal.unresolved(scope)[0].state is ExecutionEffectState.UNKNOWN


def test_journal_scope_prevents_cross_user_checkpoint_read() -> None:
    engine = create_engine("sqlite://", future=True)
    metadata.create_all(engine)
    journal = PostgresExecutionJournal(engine)
    scope = _scope()
    journal.save_checkpoint(ExecutionCheckpoint(
        "checkpoint:safe", scope, 1, 2, "context:1", "continue", True,
        datetime(2026, 8, 27, tzinfo=UTC),
    ))

    assert journal.latest_safe(_scope(user_id="user-2")) is None
    assert journal.latest_safe(scope).checkpoint_id == "checkpoint:safe"
