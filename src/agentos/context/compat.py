from __future__ import annotations

from agentos.execution.models import ExecutionState
from agentos.runtime.models import (
    ContextAssemblyRequest as RuntimeAssemblyRequest,
    ContextSnapshot as RuntimeSnapshot,
    ContextTurnUpdate as RuntimeTurnUpdate,
)

from .models import (
    ContextAssemblyRequest,
    ContextBudget,
    ContextDisposition,
    ContextOperationContext,
    ContextReference,
    ContextTurnUpdate,
    TaskSnapshot,
    TurnReference,
)


class RuntimeContextManagerAdapter:
    """Adapts the legacy Runtime reference contract to canonical RFC 104 types."""

    def __init__(self, manager, *, default_budget: ContextBudget | None = None) -> None:
        self._manager = manager
        self._default_budget = default_budget or ContextBudget(maximum_input_tokens=4096)

    def assemble(self, request: RuntimeAssemblyRequest) -> RuntimeSnapshot:
        snapshot = self._manager.assemble(
            ContextAssemblyRequest(
                context=self._context(request.context),
                turn=request.turn,
                task=TaskSnapshot(reference=ContextReference(str(request.task_ref))),
                model_requirements_ref=request.model_requirements_ref,
                budget=self._default_budget,
                prior_manifest_ref=(
                    ContextReference(str(request.prior_manifest_ref))
                    if request.prior_manifest_ref is not None
                    else None
                ),
            )
        )
        return RuntimeSnapshot(snapshot.context_ref, snapshot.manifest_ref)

    def apply_turn(self, request: RuntimeTurnUpdate) -> RuntimeSnapshot:
        references = []
        if request.provider_result_ref is not None:
            references.append(
                TurnReference(
                    reference=ContextReference(str(request.provider_result_ref)),
                )
            )
        if request.action_result_ref is not None:
            references.append(
                TurnReference(
                    reference=ContextReference(str(request.action_result_ref)),
                )
            )
        previous_manifest_ref = request.manifest_ref or request.context_ref
        snapshot = self._manager.apply_turn(
            ContextTurnUpdate(
                context=self._context(request.context),
                expected_turn=request.turn,
                previous_manifest_ref=ContextReference(str(previous_manifest_ref)),
                new_messages=tuple(references),
            )
        )
        return RuntimeSnapshot(snapshot.context_ref, snapshot.manifest_ref)

    def finalize(self, execution_id, disposition) -> None:
        self._manager.finalize(execution_id, _disposition(disposition))

    @staticmethod
    def _context(runtime_context) -> ContextOperationContext:
        return ContextOperationContext(
            user_id=runtime_context.user_id,
            workspace_id=runtime_context.workspace_id,
            agent_id=runtime_context.agent_id,
            execution_id=runtime_context.execution_id,
            correlation_id=runtime_context.correlation_id,
            purpose=runtime_context.purpose,
        )


def _disposition(value) -> ContextDisposition:
    if isinstance(value, ContextDisposition):
        return value
    if value is ExecutionState.PAUSED:
        return ContextDisposition.PRESERVE_REFERENCES
    if value is ExecutionState.WAITING_USER:
        return ContextDisposition.PRESERVE_MANIFEST
    return ContextDisposition.DISCARD


__all__ = ["RuntimeContextManagerAdapter"]
