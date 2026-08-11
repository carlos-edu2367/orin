"""Production ExecutionApplication/ResourceApplication adapters (frontend Fase 0).

Bridges the HTTP-facing command/query dicts used by ``agentos.api.gateway`` to
``agentos.execution.control.ExecutionControlService`` over the real Postgres
persistence, via the existing RFC 601 <-> legacy execution port bridge
(``agentos.persistence.execution_compat.ExecutionTransactionalPersistenceAdapter``).

Design notes (see "Decisões locais" in docs/frontend/IMPLEMENTATION_PLAN.md,
Fase 0), because none of this is guessed:

- The real persistence adapter scopes every read/write to an exact
  ``(user_id, workspace_id, agent_id, execution_id, correlation_id, purpose,
  actor)`` tuple (``PostgresTransactionalPersistence._scope_filters``).
  ``purpose``/``actor`` are fixed per adapter instance already;
  ``correlation_id`` is made deterministic per execution by the gateway
  (see ``agentos.api.gateway.context``).
- The gateway does not know an execution's ``agent_id``/``workspace_id`` on
  control/input requests (the client only sends ``execution_id`` in the
  path). ``_resolve_scope`` looks those up directly from
  ``persistence_records``, scoped to the caller's own ``user_id`` so a
  request for someone else's execution resolves to "not found" rather than
  leaking existence.
- There is no public DTO/permission for ``display_text`` yet (Fase 0 does
  not touch result-ref resolution; see the separate result-resolution
  work), so ``result.display_text`` is intentionally omitted here.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.engine import Engine

from agentos.execution.control import ExecutionControlService
from agentos.execution.models import (
    CancellationReason,
    CancellationReasonCode,
    Execution,
    ExecutionLimits,
    Ownership,
    TaskSnapshot,
    ExecutionFailure,
    ExecutionState,
    FailureReason,
)
from agentos.execution.ports import (
    Accepted,
    AlreadyApplied,
    CancelExecution,
    CommandResult,
    Conflict,
    CreateExecution,
    ExecutionCommandContext,
    ExecutionNotFoundError,
    Indeterminate,
    PauseExecution,
    ProvideExecutionInput,
    Rejected,
    RejectionReason,
    ResumeExecution,
    UnauthorizedExecutionError,
    TransitionExecution,
)
from agentos.persistence.execution_compat import ExecutionTransactionalPersistenceAdapter

from .adapter import PostgresTransactionalPersistence
from .schema import persistence_records

from agentos.api.contracts import (
    ApplicationConflictError,
    ApplicationIndeterminateError,
    ApplicationNotFoundError,
    ApplicationValidationError,
)

_PURPOSE = "execution.persist"
_ACTOR = "api-gateway"

_VALIDATION_REASONS = {RejectionReason.VERSION_REQUIRED, RejectionReason.INVALID_COMMAND}
_CONFLICT_REASONS = {
    RejectionReason.INVALID_TRANSITION,
    RejectionReason.TERMINAL_IMMUTABLE,
    RejectionReason.IDEMPOTENCY_CONFLICT,
    RejectionReason.PERSISTENCE_REJECTED,
}


def _correlation_id(execution_id: str) -> str:
    return f"corr_{execution_id}"


def _as_int(value: object) -> int | None:
    return None if value is None else int(value)


def _as_decimal(value: object) -> Decimal | None:
    return None if value is None else Decimal(str(value))


@dataclass(frozen=True, slots=True)
class _Scope:
    user_id: str
    workspace_id: str | None
    agent_id: str
    correlation_id: str


class _ScopeResolver:
    """Resolves the real ownership tuple for an execution the caller owns."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def resolve(self, execution_id: str, user_id: str) -> _Scope:
        with self._engine.connect() as connection:
            row = connection.execute(
                select(persistence_records.c.workspace_id, persistence_records.c.agent_id).where(
                    persistence_records.c.record_ref == execution_id,
                    persistence_records.c.record_type == "execution",
                    persistence_records.c.user_id == user_id,
                )
            ).mappings().first()
        if row is None:
            raise ExecutionNotFoundError(execution_id)
        return _Scope(user_id, row["workspace_id"], row["agent_id"], _correlation_id(execution_id))


def _result_to_receipt(result: CommandResult, execution_id: str) -> dict[str, object]:
    if isinstance(result, Accepted):
        return {"outcome": "accepted", "execution_id": execution_id, "state_version": int(result.resulting_version)}
    if isinstance(result, AlreadyApplied):
        return {"outcome": "already_applied", "execution_id": execution_id, "state_version": int(result.resulting_version)}
    if isinstance(result, Conflict):
        raise ApplicationConflictError(execution_id, int(result.current_version))
    if isinstance(result, Indeterminate):
        raise ApplicationIndeterminateError(execution_id, str(result.transaction_id))
    if isinstance(result, Rejected):
        if result.reason is RejectionReason.UNAUTHORIZED:
            raise UnauthorizedExecutionError(execution_id)
        if result.reason in _VALIDATION_REASONS:
            raise ApplicationValidationError(f"execution {execution_id} rejected: {result.reason.value}")
        if result.reason in _CONFLICT_REASONS:
            raise ApplicationConflictError(execution_id, 0)
        raise ApplicationValidationError(f"execution {execution_id} rejected: {result.reason.value}")
    raise AssertionError("unknown command result")


class ExecutionApplicationAdapter:
    """Production ``ExecutionApplication`` (frontend Fase 0)."""

    def __init__(self, engine: Engine) -> None:
        persistence = ExecutionTransactionalPersistenceAdapter(
            PostgresTransactionalPersistence(engine), actor=_ACTOR, purpose=_PURPOSE
        )
        self._control = ExecutionControlService(persistence)
        self._scope = _ScopeResolver(engine)

    def create(self, command: dict[str, object]) -> dict[str, object]:
        raw_context = command["context"]
        execution_id = str(raw_context["execution_id"])
        correlation_id = _correlation_id(execution_id)
        context = ExecutionCommandContext(
            user_id=str(raw_context["user_id"]),
            workspace_id=raw_context.get("workspace_id"),
            agent_id=str(raw_context["agent_id"]),
            execution_id=execution_id,
            correlation_id=correlation_id,
            purpose=_PURPOSE,
        )
        limits = command.get("limits") or {}
        execution = Execution.create(
            execution_id=execution_id,
            ownership=Ownership(context.user_id, context.workspace_id),
            agent_id=context.agent_id,
            task=TaskSnapshot(str(command["task_ref"]), 1),
            correlation_id=correlation_id,
            limits=ExecutionLimits(
                max_duration_seconds=_as_int(limits.get("max_duration_seconds")),
                max_iterations=_as_int(limits.get("max_iterations")),
                max_cost=_as_decimal(limits.get("max_cost")),
                max_provider_tokens=_as_int(limits.get("max_provider_tokens")),
            ),
            now=command["requested_at"],
            agent_config_version=command.get("expected_agent_version"),
        )
        result = self._control.create(CreateExecution(
            context=context,
            command_id=f"cmd_{uuid4().hex}",
            idempotency_key=str(command["idempotency_key"]),
            expected_version=None,
            requested_at=command["requested_at"],
            execution=execution,
        ))
        return _result_to_receipt(result, execution_id)

    def control(self, command: dict[str, object]) -> dict[str, object]:
        raw_context = command["context"]
        execution_id = str(raw_context["execution_id"])
        try:
            scope = self._scope.resolve(execution_id, str(raw_context["user_id"]))
        except ExecutionNotFoundError as error:
            raise ApplicationNotFoundError(execution_id) from error
        context = ExecutionCommandContext(scope.user_id, scope.workspace_id, scope.agent_id, execution_id, scope.correlation_id, _PURPOSE)
        expected_version = command.get("expected_state_version")
        base = dict(
            context=context,
            command_id=f"cmd_{uuid4().hex}",
            idempotency_key=str(command["idempotency_key"]),
            expected_version=None if expected_version is None else int(expected_version),
            requested_at=command["requested_at"],
        )
        action = str(command["action"])
        if action == "PAUSE":
            result = self._control.request_pause(PauseExecution(**base))
        elif action == "RESUME":
            result = self._control.request_resume(ResumeExecution(**base))
        elif action == "CANCEL":
            reason_text = str(command.get("reason") or "user_requested")
            result = self._control.request_cancel(CancelExecution(
                reason=CancellationReason(CancellationReasonCode.USER_REQUESTED, reason_text), **base
            ))
        else:
            raise ApplicationValidationError(f"unsupported control action: {action}")
        return _result_to_receipt(result, execution_id)

    def provide_input(self, command: dict[str, object]) -> dict[str, object]:
        raw_context = command["context"]
        execution_id = str(raw_context["execution_id"])
        try:
            scope = self._scope.resolve(execution_id, str(raw_context["user_id"]))
        except ExecutionNotFoundError as error:
            raise ApplicationNotFoundError(execution_id) from error
        context = ExecutionCommandContext(scope.user_id, scope.workspace_id, scope.agent_id, execution_id, scope.correlation_id, _PURPOSE)
        expected_version = command.get("expected_state_version")
        result = self._control.provide_input(ProvideExecutionInput(
            context=context,
            command_id=f"cmd_{uuid4().hex}",
            idempotency_key=str(command["idempotency_key"]),
            expected_version=None if expected_version is None else int(expected_version),
            requested_at=command["requested_at"],
            input_ref=str(command["input_ref"]),
        ))
        return _result_to_receipt(result, execution_id)

    def transition(self, command: dict[str, object]) -> dict[str, object]:
        """Trusted worker transition; not registered as an HTTP control action."""
        execution_id, user_id = str(command["execution_id"]), str(command["user_id"])
        try:
            scope = self._scope.resolve(execution_id, user_id)
        except ExecutionNotFoundError as error:
            raise ApplicationNotFoundError(execution_id) from error
        target = ExecutionState(str(command["target_state"]))
        failure = ExecutionFailure(FailureReason.RUNTIME_ERROR) if target is ExecutionState.FAILED else None
        result = self._control.transition(TransitionExecution(
            context=ExecutionCommandContext(scope.user_id, scope.workspace_id, scope.agent_id, execution_id, scope.correlation_id, _PURPOSE),
            command_id=f"worker_{uuid4().hex}", idempotency_key=str(command["idempotency_key"]),
            expected_version=int(command["expected_state_version"]), requested_at=command["requested_at"],
            target_state=target, reason_code=str(command["reason_code"]),
            result_ref=str(command["result_ref"]) if target is ExecutionState.COMPLETED else None, failure=failure,
        ))
        return _result_to_receipt(result, execution_id)


class ExecutionQueryAdapter:
    """Production ``ResourceApplication`` for executions, projecting the public ``ExecutionView``."""

    def __init__(self, engine: Engine) -> None:
        persistence = ExecutionTransactionalPersistenceAdapter(
            PostgresTransactionalPersistence(engine), actor=_ACTOR, purpose=_PURPOSE
        )
        self._control = ExecutionControlService(persistence)
        self._scope = _ScopeResolver(engine)
        self._engine = engine

    def get(self, query: dict[str, object]) -> dict[str, object]:
        execution_id = str(query["resource_id"])
        user_id = str(query["user_id"])
        try:
            scope = self._scope.resolve(execution_id, user_id)
            context = ExecutionCommandContext(scope.user_id, scope.workspace_id, scope.agent_id, execution_id, scope.correlation_id, _PURPOSE)
            execution = self._control.load(context)
        except (ExecutionNotFoundError, UnauthorizedExecutionError) as error:
            raise ApplicationNotFoundError(execution_id) from error
        return _to_execution_view(execution)

    def list(self, query: dict[str, object]) -> dict[str, object]:
        user_id = str(query["user_id"])
        with self._engine.connect() as connection:
            rows = connection.execute(
                select(persistence_records.c.record_ref).where(
                    persistence_records.c.record_type == "execution",
                    persistence_records.c.user_id == user_id,
                ).order_by(persistence_records.c.record_ref)
            ).scalars().all()
        views = []
        for execution_id in rows:
            try:
                views.append(self.get({"resource_id": execution_id, "user_id": user_id, "purpose": "executions.read"}))
            except ApplicationNotFoundError:
                continue
        return {"items": views, "cursor": None}


def _to_execution_view(execution: Execution) -> dict[str, object]:
    return {
        "execution_id": str(execution.execution_id),
        "agent_id": str(execution.agent_id),
        "state": execution.state.value,
        "state_version": int(execution.state_version),
        "parent_execution_id": str(execution.parent_execution_id) if execution.parent_execution_id else None,
        "created_at": execution.created_at.isoformat(),
        "updated_at": execution.updated_at.isoformat(),
        "finished_at": execution.finished_at.isoformat() if execution.finished_at else None,
        "result": {"result_ref": str(execution.result.result_ref)} if execution.result else None,
        "failure": {"code": execution.failure.code.value} if execution.failure else None,
    }


__all__ = ["ExecutionApplicationAdapter", "ExecutionQueryAdapter"]
