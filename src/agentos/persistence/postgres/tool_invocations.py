"""Durable, redacted tool invocation state for worker restart recovery."""
from __future__ import annotations

from sqlalchemy import insert, select, update
from sqlalchemy.engine import Engine

from agentos.tool_runtime.models import (
    EffectState, SensitiveOperationContext, ToolCancelled, ToolError, ToolErrorCode,
    ToolFailed, ToolInvocationSnapshot, ToolInvocationState, ToolRef, ToolSucceeded,
)

from .schema import tool_invocations


class PostgresToolInvocationStore:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def save(self, snapshot: ToolInvocationSnapshot, fingerprint: str) -> None:
        context = snapshot.context
        outcome = snapshot.outcome
        if isinstance(outcome, ToolSucceeded):
            public_outcome = {"kind": "succeeded", "result_ref": outcome.result_ref}
        elif isinstance(outcome, ToolFailed):
            public_outcome = {"kind": "failed", "code": outcome.error.code.value, "reason": outcome.error.reason[:256], "effect_state": outcome.error.effect_state.value}
        elif isinstance(outcome, ToolCancelled):
            public_outcome = {"kind": "cancelled", "reason": outcome.reason[:256]}
        else:
            public_outcome = None
        values = {
            "invocation_id": snapshot.invocation_id, "user_id": context.user_id, "workspace_id": context.workspace_id,
            "agent_id": context.agent_id, "execution_id": context.execution_id, "correlation_id": context.correlation_id,
            "purpose": context.purpose, "actor": context.actor, "tool_id": snapshot.tool_ref.tool_id,
            "tool_version": snapshot.tool_ref.version, "state": snapshot.state.value, "fingerprint": fingerprint,
            "outcome": public_outcome, "started_at": snapshot.started_at, "finished_at": snapshot.finished_at,
        }
        with self._engine.begin() as connection:
            existing = connection.execute(select(tool_invocations.c.invocation_id).where(tool_invocations.c.invocation_id == snapshot.invocation_id)).scalar_one_or_none()
            if existing is None:
                connection.execute(insert(tool_invocations).values(**values))
            else:
                connection.execute(update(tool_invocations).where(tool_invocations.c.invocation_id == snapshot.invocation_id).values(**{key: value for key, value in values.items() if key != "invocation_id"}))

    def load(self, invocation_id: str, context: SensitiveOperationContext):
        with self._engine.connect() as connection:
            row = connection.execute(select(tool_invocations).where(tool_invocations.c.invocation_id == invocation_id, tool_invocations.c.user_id == context.user_id, tool_invocations.c.execution_id == context.execution_id)).mappings().first()
        if row is None:
            return None
        stored_context = SensitiveOperationContext(row["user_id"], row["workspace_id"], row["agent_id"], row["execution_id"], row["correlation_id"], row["purpose"], row["actor"])
        outcome_data = row["outcome"] or {}
        outcome = None
        if outcome_data.get("kind") == "succeeded":
            outcome = ToolSucceeded(invocation_id, {}, result_ref=outcome_data.get("result_ref"))
        elif outcome_data.get("kind") == "failed":
            outcome = ToolFailed(invocation_id, ToolError(ToolErrorCode(outcome_data.get("code", "EXECUTION_FAILED")), effect_state=EffectState(outcome_data.get("effect_state", "UNKNOWN")), reason=str(outcome_data.get("reason", "tool invocation failed"))))
        elif outcome_data.get("kind") == "cancelled":
            outcome = ToolCancelled(invocation_id, str(outcome_data.get("reason", "tool invocation cancelled")))
        snapshot = ToolInvocationSnapshot(invocation_id, ToolRef(row["tool_id"], int(row["tool_version"])), stored_context, ToolInvocationState(row["state"]), outcome, started_at=row["started_at"], finished_at=row["finished_at"])
        return snapshot, str(row["fingerprint"])


__all__ = ["PostgresToolInvocationStore"]
