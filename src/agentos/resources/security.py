from __future__ import annotations

from .models import ResourceOperationContext


def validate_actor(context: ResourceOperationContext) -> None:
    if context.actor not in (f"user:{context.user_id}", f"agent:{context.agent_id}", f"system:{context.user_id}"):
        raise PermissionError("actor is not bound to resource context")


def same_binding(left: ResourceOperationContext, right: ResourceOperationContext) -> bool:
    return left.binding_key() == right.binding_key()


__all__ = ["same_binding", "validate_actor"]
