from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from typing import NewType

from agentos.events.models import DataClassification

from .security import contains_secret, require_aware, require_text, validate_reference

AgentId = NewType("AgentId", str)
UserId = NewType("UserId", str)
WorkspaceId = NewType("WorkspaceId", str)
CorrelationId = NewType("CorrelationId", str)
AgentConfigVersion = NewType("AgentConfigVersion", int)
Version = NewType("Version", int)


class AgentAdministrativeState(StrEnum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    ARCHIVED = "ARCHIVED"


@dataclass(frozen=True, slots=True)
class OpaqueAgentReference:
    value: str

    def __post_init__(self) -> None:
        validate_reference(self.value)

    def __str__(self) -> str:
        return self.value

    def __repr__(self) -> str:
        return "OpaqueAgentReference(<opaque>)"


@dataclass(frozen=True, slots=True)
class PromptSpecification:
    prompt_ref: OpaqueAgentReference
    prompt_version: int
    instruction_classification: DataClassification

    def __post_init__(self) -> None:
        if not isinstance(self.prompt_ref, OpaqueAgentReference):
            raise ValueError("prompt_ref must be opaque")
        if self.prompt_version < 1:
            raise ValueError("prompt version must be positive")
        object.__setattr__(self, "instruction_classification", DataClassification(self.instruction_classification))


@dataclass(frozen=True, slots=True)
class AgentPresentation:
    avatar_ref: OpaqueAgentReference | None
    color: str | None

    def __post_init__(self) -> None:
        if self.avatar_ref is not None and not isinstance(self.avatar_ref, OpaqueAgentReference):
            raise ValueError("avatar_ref must be opaque")
        if self.color is not None:
            require_text(self.color, "presentation color", maximum=32)
            if contains_secret(self.color):
                raise ValueError("presentation color is invalid")


@dataclass(frozen=True, slots=True)
class WorkspaceAssignment:
    workspace_id: WorkspaceId
    assignment_ref: OpaqueAgentReference
    assigned_by: str
    assigned_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.assignment_ref, OpaqueAgentReference):
            raise ValueError("assignment_ref must be opaque")
        require_text(self.workspace_id, "workspace_id")
        require_text(self.assigned_by, "assigned_by")
        require_aware(self.assigned_at, "assigned_at")


@dataclass(frozen=True, slots=True)
class MemoryScopeReference:
    scope_ref: OpaqueAgentReference
    user_id: UserId
    agent_id: AgentId
    workspace_id: WorkspaceId | None
    classification: DataClassification
    provenance_ref: OpaqueAgentReference
    retention_policy_ref: OpaqueAgentReference

    def __post_init__(self) -> None:
        for reference in (self.scope_ref, self.provenance_ref, self.retention_policy_ref):
            if not isinstance(reference, OpaqueAgentReference):
                raise ValueError("memory scope references must be opaque")
        require_text(self.user_id, "user_id")
        require_text(self.agent_id, "agent_id")
        if self.workspace_id is not None:
            require_text(self.workspace_id, "workspace_id")
        object.__setattr__(self, "classification", DataClassification(self.classification))


@dataclass(frozen=True, slots=True)
class AgentConfiguration:
    agent_id: AgentId
    config_version: AgentConfigVersion
    model_profile_ref: OpaqueAgentReference
    prompt: PromptSpecification
    presentation: AgentPresentation
    tool_grants: tuple[OpaqueAgentReference, ...]
    capability_grants: tuple[OpaqueAgentReference, ...]
    skill_grants: tuple[OpaqueAgentReference, ...]
    execution_policy_ref: OpaqueAgentReference
    context_policy_ref: OpaqueAgentReference
    memory_policy_ref: OpaqueAgentReference
    workspace_assignments: tuple[WorkspaceAssignment, ...]
    created_by: str
    created_at: datetime
    supersedes_version: AgentConfigVersion | None

    def __post_init__(self) -> None:
        for reference in (
            self.model_profile_ref,
            self.execution_policy_ref,
            self.context_policy_ref,
            self.memory_policy_ref,
        ):
            if not isinstance(reference, OpaqueAgentReference):
                raise ValueError("configuration references must be opaque")
        object.__setattr__(self, "tool_grants", tuple(self.tool_grants))
        object.__setattr__(self, "capability_grants", tuple(self.capability_grants))
        object.__setattr__(self, "skill_grants", tuple(self.skill_grants))
        object.__setattr__(self, "workspace_assignments", tuple(self.workspace_assignments))
        require_text(self.agent_id, "agent_id")
        if self.config_version < 1:
            raise ValueError("config_version must be positive")
        require_text(self.created_by, "created_by")
        require_aware(self.created_at, "created_at")
        if self.config_version == 1 and self.supersedes_version is not None:
            raise ValueError("initial configuration cannot supersede a version")
        if self.config_version > 1 and self.supersedes_version != self.config_version - 1:
            raise ValueError("configuration must supersede the immediately previous version")
        for grant in (*self.tool_grants, *self.capability_grants, *self.skill_grants):
            if not isinstance(grant, OpaqueAgentReference):
                raise ValueError("grant references must be opaque")
            validate_reference(str(grant), "grant reference")
        for assignment in self.workspace_assignments:
            if not isinstance(assignment, WorkspaceAssignment):
                raise ValueError("workspace assignments must be typed")


@dataclass(frozen=True, slots=True)
class Agent:
    agent_id: AgentId
    user_id: UserId
    workspace_id: WorkspaceId | None
    owner: str
    display_name: str
    administrative_state: AgentAdministrativeState
    current_config_version: AgentConfigVersion
    private_memory_scope: MemoryScopeReference
    created_by: str
    created_at: datetime
    updated_at: datetime
    suspended_at: datetime | None
    archived_at: datetime | None
    audit_refs: tuple[OpaqueAgentReference, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.private_memory_scope, MemoryScopeReference):
            raise ValueError("private memory scope must be a reference")
        for field, value in (
            ("agent_id", self.agent_id),
            ("user_id", self.user_id),
            ("owner", self.owner),
            ("display_name", self.display_name),
            ("created_by", self.created_by),
        ):
            require_text(value, field, maximum=256 if field != "display_name" else 128)
        if self.workspace_id is not None:
            require_text(self.workspace_id, "workspace_id")
        if self.current_config_version < 1:
            raise ValueError("current_config_version must be positive")
        require_aware(self.created_at, "created_at")
        require_aware(self.updated_at, "updated_at")
        if self.suspended_at is not None:
            require_aware(self.suspended_at, "suspended_at")
        if self.archived_at is not None:
            require_aware(self.archived_at, "archived_at")
        if self.private_memory_scope.user_id != self.user_id or self.private_memory_scope.agent_id != self.agent_id:
            raise ValueError("private memory scope ownership does not match Agent")
        object.__setattr__(self, "audit_refs", tuple(self.audit_refs))
        for reference in self.audit_refs:
            if not isinstance(reference, OpaqueAgentReference):
                raise ValueError("audit references must be opaque")

    def transition_to(self, target: AgentAdministrativeState, *, now: datetime) -> Agent:
        require_aware(now, "now")
        allowed = {
            AgentAdministrativeState.ACTIVE: {AgentAdministrativeState.SUSPENDED, AgentAdministrativeState.ARCHIVED},
            AgentAdministrativeState.SUSPENDED: {AgentAdministrativeState.ACTIVE, AgentAdministrativeState.ARCHIVED},
            AgentAdministrativeState.ARCHIVED: set(),
        }
        if target not in allowed[self.administrative_state]:
            raise ValueError("invalid Agent administrative transition")
        return replace(
            self,
            administrative_state=target,
            updated_at=now,
            suspended_at=now if target is AgentAdministrativeState.SUSPENDED else self.suspended_at,
            archived_at=now if target is AgentAdministrativeState.ARCHIVED else self.archived_at,
        )


@dataclass(frozen=True, slots=True)
class AgentSnapshot:
    agent: Agent
    configuration: AgentConfiguration

    @property
    def agent_id(self) -> AgentId:
        return self.agent.agent_id

    @property
    def config_version(self) -> AgentConfigVersion:
        return self.configuration.config_version


@dataclass(frozen=True, slots=True)
class ResolvedAgentPolicies:
    execution_policy_ref: OpaqueAgentReference
    context_policy_ref: OpaqueAgentReference
    memory_policy_ref: OpaqueAgentReference
    policy_version: Version
    purpose: str
    classification: DataClassification

    def __post_init__(self) -> None:
        for reference in (self.execution_policy_ref, self.context_policy_ref, self.memory_policy_ref):
            if not isinstance(reference, OpaqueAgentReference):
                raise ValueError("resolved policy references must be opaque")
        if self.policy_version < 1:
            raise ValueError("policy_version must be positive")
        require_text(self.purpose, "purpose", maximum=128)
        object.__setattr__(self, "classification", DataClassification(self.classification))


@dataclass(frozen=True, slots=True)
class ResolvedAgent:
    agent_id: AgentId
    user_id: UserId
    workspace_id: WorkspaceId | None
    owner: str
    config_version: AgentConfigVersion
    model_profile_ref: OpaqueAgentReference
    prompt: PromptSpecification
    presentation: AgentPresentation
    tool_grants: tuple[OpaqueAgentReference, ...]
    capability_grants: tuple[OpaqueAgentReference, ...]
    skill_grants: tuple[OpaqueAgentReference, ...]
    private_memory_scope: MemoryScopeReference
    policies: ResolvedAgentPolicies

    def __post_init__(self) -> None:
        if self.config_version < 1:
            raise ValueError("resolved config_version must be positive")
        if not isinstance(self.private_memory_scope, MemoryScopeReference):
            raise ValueError("resolved memory scope must be typed")
        object.__setattr__(self, "tool_grants", tuple(self.tool_grants))
        object.__setattr__(self, "capability_grants", tuple(self.capability_grants))
        object.__setattr__(self, "skill_grants", tuple(self.skill_grants))
        for reference in (
            self.model_profile_ref,
            *self.tool_grants,
            *self.capability_grants,
            *self.skill_grants,
        ):
            if not isinstance(reference, OpaqueAgentReference):
                raise ValueError("resolved references must be opaque")
