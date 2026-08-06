from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
import json
from typing import Mapping

from .models import MAX_EDGES, MAX_NODES, OrchestrationPlanDraft


class OrchestratorValidationError(ValueError):
    """Sanitized public validation failure."""


class OrchestratorAccessDenied(PermissionError):
    """Sanitized public ownership failure."""


class OrchestratorIdempotencyConflict(ValueError):
    """The same key was used for a different bounded intent."""


class OrchestratorVersionConflict(ValueError):
    """An optimistic plan version is stale."""


def sanitize_error(_: object) -> str:
    return "orchestration operation rejected"


def require_owner(*, expected_user_id: str, expected_workspace_id: str | None, actual_user_id: str, actual_workspace_id: str | None) -> None:
    if expected_user_id != actual_user_id or expected_workspace_id != actual_workspace_id:
        raise OrchestratorAccessDenied("orchestration access denied")


def _plain(value: object, depth: int = 0) -> object:
    if depth > 12:
        raise OrchestratorValidationError("fingerprint exceeds its bound")
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if value is None or isinstance(value, (bool, int, float, str)):
        if isinstance(value, str) and len(value) > 256:
            raise OrchestratorValidationError("fingerprint contains oversized text")
        return value
    if hasattr(value, "value") and not isinstance(value, Mapping):
        return _plain(value.value, depth + 1)
    if is_dataclass(value):
        return {key: _plain(item, depth + 1) for key, item in asdict(value).items()}
    if isinstance(value, Mapping):
        if len(value) > 512:
            raise OrchestratorValidationError("fingerprint contains too many entries")
        return {str(key): _plain(item, depth + 1) for key, item in sorted(value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, (tuple, list)):
        if len(value) > 512:
            raise OrchestratorValidationError("fingerprint contains too many items")
        return [_plain(item, depth + 1) for item in value]
    raise OrchestratorValidationError("fingerprint contains unsupported data")


def fingerprint(value: object) -> str:
    encoded = json.dumps(_plain(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return sha256(encoded).hexdigest()


def validate_plan(plan: OrchestrationPlanDraft) -> tuple[str, ...]:
    if len(plan.nodes) == 0 or len(plan.nodes) > MAX_NODES:
        raise OrchestratorValidationError("plan node count is invalid")
    if len(plan.dependencies) > MAX_EDGES:
        raise OrchestratorValidationError("plan dependency count is invalid")
    node_ids = [str(node.work_id) for node in plan.nodes]
    if len(set(node_ids)) != len(node_ids):
        raise OrchestratorValidationError("plan work identifiers are not unique")
    known = set(node_ids)
    edges: set[tuple[str, str]] = set()
    graph: dict[str, set[str]] = {node_id: set() for node_id in node_ids}
    for edge in plan.dependencies:
        source, target = str(edge.predecessor_work_id), str(edge.successor_work_id)
        if source not in known or target not in known or source == target:
            raise OrchestratorValidationError("plan dependency is invalid")
        if (source, target) in edges:
            raise OrchestratorValidationError("plan dependency is duplicated")
        edges.add((source, target))
        graph[source].add(target)
    indegree = {node_id: 0 for node_id in node_ids}
    for targets in graph.values():
        for target in targets:
            indegree[target] += 1
    ready = [node_id for node_id, count in indegree.items() if count == 0]
    visited: list[str] = []
    while ready:
        node_id = ready.pop()
        visited.append(node_id)
        for target in graph[node_id]:
            indegree[target] -= 1
            if indegree[target] == 0:
                ready.append(target)
    if len(visited) != len(node_ids):
        raise OrchestratorValidationError("plan dependencies contain a cycle")
    for node in plan.nodes:
        if node.failure_handler_work_id is not None and str(node.failure_handler_work_id) not in known:
            raise OrchestratorValidationError("failure handler is not part of the plan")
    return tuple(visited)


__all__ = [
    "OrchestratorAccessDenied", "OrchestratorIdempotencyConflict", "OrchestratorValidationError",
    "OrchestratorVersionConflict", "fingerprint", "require_owner", "sanitize_error", "validate_plan",
]
