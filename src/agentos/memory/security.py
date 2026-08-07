from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
import hashlib
import json
import re
from threading import RLock
from typing import Mapping

from agentos.events.models import DataClassification
from agentos.events.security import clearance_allows

from .models import (
    BoundedMemoryContent,
    MemoryAccessDenied,
    MemoryArtifactReference,
    MemoryGrant,
    MemoryOperationContext,
    MemoryProvenance,
    MemoryRecord,
    MemoryReference,
    MemoryScope,
    MemoryStatus,
)


_FORBIDDEN_CONTENT = re.compile(
    r"(?ix)(?:bearer\s+[a-z0-9._~+/=-]{8,}|"
    r"(?:api[_-]?key|password|secret|credential|access[_-]?token|refresh[_-]?token|token)\s*[:=]\s*[^\s,;]+|"
    r"(?:system\s+prompt|developer\s+message|chain\s+of\s+thought|ignore\s+(?:all|the)\s+(?:previous|policy)))"
)


def validate_memory_content(content: object) -> None:
    if isinstance(content, BoundedMemoryContent):
        value = content.value
    elif isinstance(content, str):
        value = content
    elif isinstance(content, MemoryArtifactReference):
        return
    else:
        raise ValueError("content must be bounded text or an opaque artifact reference")
    if not value.strip() or len(value) > 4096:
        raise ValueError("content is outside its bounded limit")
    if _FORBIDDEN_CONTENT.search(value):
        raise ValueError("content violates the memory data policy")


def validate_provenance(provenance: MemoryProvenance) -> None:
    if not isinstance(provenance, MemoryProvenance):
        raise ValueError("provenance is required")
    if not provenance.source_refs or not provenance.integrity_ref:
        raise ValueError("provenance integrity is required")


def validate_scope(
    scope: MemoryScope,
    *,
    user_id: str,
    workspace_id: str | None,
    owner_agent_id: str | None,
) -> None:
    try:
        scope = MemoryScope(scope)
    except (TypeError, ValueError) as exc:
        raise ValueError("scope is invalid") from exc
    if not user_id.strip():
        raise ValueError("user_id is required")
    if scope is MemoryScope.PRIVATE and not owner_agent_id:
        raise ValueError("PRIVATE memory requires owner_agent_id")
    if scope is MemoryScope.WORKSPACE and not workspace_id:
        raise ValueError("WORKSPACE memory requires workspace_id")
    if scope is MemoryScope.USER and workspace_id is not None:
        raise ValueError("USER memory cannot be born inside a Workspace")


def _plain(value: object) -> object:
    if is_dataclass(value):
        return {key: _plain(item) for key, item in asdict(value).items()}
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, DataClassification):
        return value.value
    if hasattr(value, "value") and type(value).__module__.startswith("agentos."):
        return str(value.value)
    return value


def fingerprint_command(command: object) -> str:
    payload = _plain(command)
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _now(value: datetime | None) -> datetime:
    if value is not None:
        return value
    return datetime.now(timezone.utc)


class InMemoryMemoryAuthorizationPolicy:
    """Reference authorization policy with explicit active agents and grants."""

    def __init__(self) -> None:
        self._active_agents: set[tuple[str, str]] = set()
        self._workspace_access: set[tuple[str, str, str]] = set()
        self._grants: dict[str, MemoryGrant] = {}
        self._lock = RLock()

    def register_agent(self, user_id: str, agent_id: str, *, active: bool = True) -> None:
        with self._lock:
            key = (str(user_id), str(agent_id))
            if active:
                self._active_agents.add(key)
            else:
                self._active_agents.discard(key)

    def suspend_agent(self, user_id: str, agent_id: str) -> None:
        self.register_agent(user_id, agent_id, active=False)

    def is_agent_active(self, user_id: str, agent_id: str) -> bool:
        with self._lock:
            return (str(user_id), str(agent_id)) in self._active_agents

    def has_workspace_access(self, user_id: str, workspace_id: str, agent_id: str) -> bool:
        with self._lock:
            return (str(user_id), str(workspace_id), str(agent_id)) in self._workspace_access

    def register_workspace_access(self, user_id: str, workspace_id: str, agent_id: str) -> None:
        with self._lock:
            self._workspace_access.add((str(user_id), str(workspace_id), str(agent_id)))

    def register_grant(self, grant: MemoryGrant) -> None:
        with self._lock:
            self._grants[str(grant.grant_id)] = grant

    def revoke_grant(self, grant_id: str) -> None:
        with self._lock:
            grant = self._grants.get(str(grant_id))
            if grant is not None:
                self._grants[str(grant_id)] = MemoryGrant(
                    grant_id=grant.grant_id,
                    memory_id=grant.memory_id,
                    user_id=grant.user_id,
                    source_agent_id=grant.source_agent_id,
                    target_agent_id=grant.target_agent_id,
                    target_execution_id=grant.target_execution_id,
                    purpose=grant.purpose,
                    classification_ceiling=grant.classification_ceiling,
                    expires_at=grant.expires_at,
                    maximum_uses=grant.maximum_uses,
                    redelegation=False,
                    revoked=True,
                    uses=grant.uses,
                )

    def authorize(
        self,
        context: MemoryOperationContext,
        record: MemoryRecord | None,
        *,
        operation: str,
        reference: MemoryReference | None = None,
        classification_ceiling: DataClassification | None = None,
        grant_refs: tuple[str, ...] = (),
        consume_grant: bool = True,
        now: datetime | None = None,
    ) -> None:
        moment = _now(now)
        ceiling = DataClassification.RESTRICTED if classification_ceiling is None else DataClassification(classification_ceiling)
        with self._lock:
            if (str(context.user_id), str(context.agent_id)) not in self._active_agents:
                raise MemoryAccessDenied()
            if record is None:
                return
            if str(record.user_id) != str(context.user_id):
                raise MemoryAccessDenied()
            if not clearance_allows(ceiling.value, record.classification.value):
                raise MemoryAccessDenied("CLASSIFICATION_DENIED")
            if reference is not None:
                self._validate_reference(context, record, reference, moment)
            if str(record.status) != MemoryStatus.ACTIVE.value:
                raise MemoryAccessDenied("REFERENCE_UNAVAILABLE")

            owner_access = self._owner_access(context, record)
            grant = self._find_grant(context, record, grant_refs, moment, ceiling)
            if not owner_access and grant is None:
                raise MemoryAccessDenied()
            if record.scope is MemoryScope.WORKSPACE and not owner_access:
                raise MemoryAccessDenied()
            if not owner_access and grant is not None and consume_grant:
                self._consume(grant)

    def _owner_access(self, context: MemoryOperationContext, record: MemoryRecord) -> bool:
        if record.scope is MemoryScope.PRIVATE:
            return (
                str(record.owner_agent_id) == str(context.agent_id)
                and str(record.workspace_id) == str(context.workspace_id)
            )
        if record.scope is MemoryScope.WORKSPACE:
            return (
                record.workspace_id is not None
                and str(record.workspace_id) == str(context.workspace_id)
                and (str(context.user_id), str(record.workspace_id), str(context.agent_id)) in self._workspace_access
            )
        return record.workspace_id is None and context.workspace_id is None

    def _validate_reference(
        self,
        context: MemoryOperationContext,
        record: MemoryRecord,
        reference: MemoryReference,
        now: datetime,
    ) -> None:
        if (
            str(reference.memory_id) != str(record.memory_id)
            or int(reference.version) != int(record.version)
            or str(reference.user_id) != str(record.user_id)
            or reference.workspace_id != record.workspace_id
            or (reference.permitted_agent_id is not None and str(reference.permitted_agent_id) != str(context.agent_id))
            or str(reference.purpose) != str(context.purpose)
            or (reference.expires_at is not None and reference.expires_at <= now)
        ):
            raise MemoryAccessDenied("REFERENCE_UNAVAILABLE")

    def _find_grant(
        self,
        context: MemoryOperationContext,
        record: MemoryRecord,
        grant_refs: tuple[str, ...],
        now: datetime,
        ceiling: DataClassification,
    ) -> MemoryGrant | None:
        for grant_ref in grant_refs:
            grant = self._grants.get(str(grant_ref))
            if grant is None:
                continue
            if (
                grant.revoked
                or grant.expires_at <= now
                or grant.uses >= grant.maximum_uses
                or str(grant.memory_id) != str(record.memory_id)
                or str(grant.user_id) != str(record.user_id)
                or str(grant.target_agent_id) != str(context.agent_id)
                or str(grant.target_execution_id) != str(context.execution_id)
                or str(grant.purpose) != str(context.purpose)
                or not clearance_allows(grant.classification_ceiling.value, record.classification.value)
                or not clearance_allows(ceiling.value, record.classification.value)
            ):
                continue
            return grant
        return None

    def _consume(self, grant: MemoryGrant) -> None:
        self._grants[str(grant.grant_id)] = MemoryGrant(
            grant_id=grant.grant_id,
            memory_id=grant.memory_id,
            user_id=grant.user_id,
            source_agent_id=grant.source_agent_id,
            target_agent_id=grant.target_agent_id,
            target_execution_id=grant.target_execution_id,
            purpose=grant.purpose,
            classification_ceiling=grant.classification_ceiling,
            expires_at=grant.expires_at,
            maximum_uses=grant.maximum_uses,
            redelegation=False,
            revoked=grant.revoked,
            uses=grant.uses + 1,
        )


__all__ = [
    "InMemoryMemoryAuthorizationPolicy",
    "fingerprint_command",
    "validate_memory_content",
    "validate_provenance",
    "validate_scope",
]
