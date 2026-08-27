"""Authorized SQL adapter for durable execution recovery records."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Mapping

from sqlalchemy import select, update
from sqlalchemy.engine import Engine

from agentos.execution.journal import (
    ExecutionCheckpoint,
    ExecutionEffect,
    ExecutionEffectKind,
    ExecutionEffectRetryability,
    ExecutionEffectState,
    ExecutionJournalScope,
)

from .schema import execution_checkpoints, execution_effects


def _scope_where(table, scope: ExecutionJournalScope):
    workspace = (
        table.c.workspace_id.is_(None)
        if scope.workspace_id is None
        else table.c.workspace_id == str(scope.workspace_id)
    )
    return (
        table.c.execution_id == str(scope.execution_id),
        table.c.user_id == str(scope.user_id),
        workspace,
        table.c.agent_id == scope.agent_id,
        table.c.correlation_id == scope.correlation_id,
    )


def _utc(value: datetime) -> datetime:
    # SQLite intentionally drops timezone offsets for DateTime columns. The
    # journal only writes UTC instants, so restore that invariant at the
    # adapter boundary while PostgreSQL values pass through unchanged.
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _effect(row) -> ExecutionEffect:
    scope = ExecutionJournalScope(
        execution_id=str(row["execution_id"]), user_id=str(row["user_id"]),
        workspace_id=row["workspace_id"], agent_id=str(row["agent_id"]),
        correlation_id=str(row["correlation_id"]),
    )
    return ExecutionEffect(
        effect_id=str(row["effect_id"]), scope=scope,
        kind=ExecutionEffectKind(str(row["kind"])), invocation_ref=str(row["invocation_ref"]),
        request_ref=str(row["request_ref"]), idempotency_key=str(row["idempotency_key"]),
        state=ExecutionEffectState(str(row["state"])),
        retryability=ExecutionEffectRetryability(str(row["retryability"])),
        attempt=int(row["attempt"]), prepared_at=_utc(row["prepared_at"]), version=int(row["version"]),
        result_ref=row["result_ref"], error_code=row["error_code"],
        started_at=_utc(row["started_at"]) if row["started_at"] is not None else None,
        resolved_at=_utc(row["resolved_at"]) if row["resolved_at"] is not None else None,
        reconciliation_ref=row["reconciliation_ref"],
    )


def _checkpoint(row) -> ExecutionCheckpoint:
    scope = ExecutionJournalScope(
        execution_id=str(row["execution_id"]), user_id=str(row["user_id"]),
        workspace_id=row["workspace_id"], agent_id=str(row["agent_id"]),
        correlation_id=str(row["correlation_id"]),
    )
    return ExecutionCheckpoint(
        checkpoint_id=str(row["checkpoint_id"]), scope=scope, sequence=int(row["sequence"]),
        execution_state_version=int(row["execution_state_version"]),
        context_manifest_ref=str(row["context_manifest_ref"]), next_decision=str(row["next_decision"]),
        is_safe=bool(row["is_safe"]), pending_effect_id=row["pending_effect_id"],
        snapshot=dict(row["snapshot"] or {}), created_at=_utc(row["created_at"]),
    )


class PostgresExecutionJournal:
    """Journal whose every read repeats the complete execution ownership scope."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def prepare(self, effect: ExecutionEffect) -> ExecutionEffect:
        with self._engine.begin() as connection:
            existing = connection.execute(select(execution_effects).where(
                *_scope_where(execution_effects, effect.scope),
                execution_effects.c.idempotency_key == effect.idempotency_key,
            )).mappings().first()
            if existing is not None:
                return _effect(existing)
            connection.execute(execution_effects.insert().values(
                effect_id=effect.effect_id, execution_id=str(effect.scope.execution_id), user_id=str(effect.scope.user_id),
                workspace_id=str(effect.scope.workspace_id) if effect.scope.workspace_id is not None else None,
                agent_id=effect.scope.agent_id, correlation_id=effect.scope.correlation_id,
                kind=effect.kind.value, invocation_ref=effect.invocation_ref, request_ref=effect.request_ref,
                result_ref=effect.result_ref, idempotency_key=effect.idempotency_key, state=effect.state.value,
                retryability=effect.retryability.value, attempt=effect.attempt, result=None,
                error_code=effect.error_code, prepared_at=effect.prepared_at, started_at=effect.started_at,
                resolved_at=effect.resolved_at, reconciliation_ref=effect.reconciliation_ref, version=effect.version,
            ))
        return effect

    def mark_in_flight(self, effect_id: str, scope: ExecutionJournalScope, *, now: datetime) -> ExecutionEffect:
        with self._engine.begin() as connection:
            row = connection.execute(select(execution_effects).where(
                *_scope_where(execution_effects, scope), execution_effects.c.effect_id == effect_id,
            )).mappings().one()
            if row["state"] == ExecutionEffectState.PREPARED.value:
                connection.execute(update(execution_effects).where(
                    execution_effects.c.effect_id == effect_id, execution_effects.c.version == row["version"],
                ).values(state=ExecutionEffectState.IN_FLIGHT.value, started_at=now, version=int(row["version"]) + 1))
                row = connection.execute(select(execution_effects).where(execution_effects.c.effect_id == effect_id)).mappings().one()
            return _effect(row)

    def resolve(self, effect_id: str, scope: ExecutionJournalScope, *, state: ExecutionEffectState, now: datetime, result_ref: str | None = None, error_code: str | None = None, private_result: Mapping[str, object] | None = None) -> ExecutionEffect:
        if state not in {ExecutionEffectState.APPLIED, ExecutionEffectState.NOT_APPLIED, ExecutionEffectState.UNKNOWN}:
            raise ValueError("effect resolution must be terminal")
        with self._engine.begin() as connection:
            row = connection.execute(select(execution_effects).where(
                *_scope_where(execution_effects, scope), execution_effects.c.effect_id == effect_id,
            )).mappings().one()
            if row["state"] not in {ExecutionEffectState.APPLIED.value, ExecutionEffectState.NOT_APPLIED.value, ExecutionEffectState.UNKNOWN.value}:
                connection.execute(update(execution_effects).where(
                    execution_effects.c.effect_id == effect_id, execution_effects.c.version == row["version"],
                ).values(state=state.value, result_ref=result_ref, error_code=error_code, result=dict(private_result or {}) or None,
                         resolved_at=now, version=int(row["version"]) + 1))
                row = connection.execute(select(execution_effects).where(execution_effects.c.effect_id == effect_id)).mappings().one()
            return _effect(row)

    def save_checkpoint(self, checkpoint: ExecutionCheckpoint) -> ExecutionCheckpoint:
        with self._engine.begin() as connection:
            existing = connection.execute(select(execution_checkpoints).where(
                *_scope_where(execution_checkpoints, checkpoint.scope), execution_checkpoints.c.sequence == checkpoint.sequence,
            )).mappings().first()
            if existing is not None:
                return _checkpoint(existing)
            connection.execute(execution_checkpoints.insert().values(
                checkpoint_id=checkpoint.checkpoint_id, execution_id=str(checkpoint.scope.execution_id), user_id=str(checkpoint.scope.user_id),
                workspace_id=str(checkpoint.scope.workspace_id) if checkpoint.scope.workspace_id is not None else None,
                agent_id=checkpoint.scope.agent_id, correlation_id=checkpoint.scope.correlation_id,
                sequence=checkpoint.sequence, execution_state_version=checkpoint.execution_state_version,
                context_manifest_ref=checkpoint.context_manifest_ref, next_decision=checkpoint.next_decision,
                pending_effect_id=checkpoint.pending_effect_id, is_safe=checkpoint.is_safe,
                snapshot=dict(checkpoint.snapshot or {}), created_at=checkpoint.created_at,
            ))
        return checkpoint

    def latest_safe(self, scope: ExecutionJournalScope) -> ExecutionCheckpoint | None:
        with self._engine.connect() as connection:
            row = connection.execute(select(execution_checkpoints).where(
                *_scope_where(execution_checkpoints, scope), execution_checkpoints.c.is_safe.is_(True),
            ).order_by(execution_checkpoints.c.sequence.desc())).mappings().first()
        return _checkpoint(row) if row is not None else None

    def next_checkpoint_sequence(self, scope: ExecutionJournalScope) -> int:
        with self._engine.connect() as connection:
            last = connection.execute(select(execution_checkpoints.c.sequence).where(
                *_scope_where(execution_checkpoints, scope),
            ).order_by(execution_checkpoints.c.sequence.desc()).limit(1)).scalar_one_or_none()
        return int(last or 0) + 1

    def unresolved(self, scope: ExecutionJournalScope) -> tuple[ExecutionEffect, ...]:
        active = (ExecutionEffectState.PREPARED.value, ExecutionEffectState.IN_FLIGHT.value, ExecutionEffectState.UNKNOWN.value)
        with self._engine.connect() as connection:
            rows = connection.execute(select(execution_effects).where(
                *_scope_where(execution_effects, scope), execution_effects.c.state.in_(active),
            ).order_by(execution_effects.c.prepared_at, execution_effects.c.effect_id)).mappings().all()
        return tuple(_effect(row) for row in rows)

    def mark_unresolved_unknown(self, scope: ExecutionJournalScope, *, now: datetime) -> tuple[ExecutionEffect, ...]:
        resolved = []
        for effect in self.unresolved(scope):
            if effect.state is ExecutionEffectState.UNKNOWN:
                resolved.append(effect)
            elif effect.state is ExecutionEffectState.PREPARED and effect.started_at is None:
                # No gateway handoff was recorded: it is provably safe to make
                # a later recovery decision from the preceding checkpoint.
                resolved.append(self.resolve(effect.effect_id, scope, state=ExecutionEffectState.NOT_APPLIED, now=now, error_code="RECOVERY_NOT_STARTED"))
            else:
                resolved.append(self.resolve(effect.effect_id, scope, state=ExecutionEffectState.UNKNOWN, now=now, error_code="RECOVERY_RECONCILIATION_REQUIRED"))
        return tuple(resolved)


__all__ = ["PostgresExecutionJournal"]
