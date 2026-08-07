from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
import re
from typing import Any

from agentos.events.models import DataClassification, classification_allows


MAX_ID = 255
MAX_NAME = 255
MAX_MEDIA_TYPE = 128
MAX_REASON = 128
MAX_SOURCE_REFS = 16


def _required(value: object, field: str, maximum: int = MAX_ID) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-blank string")
    if len(value) > maximum:
        raise ValueError(f"{field} exceeds its maximum length")
    return value


def _aware(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")


class AccessPurpose(str):
    def __new__(cls, value: str) -> "AccessPurpose":
        _required(value, "purpose", 128)
        if any(char.isspace() for char in value):
            raise ValueError("purpose cannot contain whitespace")
        return str.__new__(cls, value)


class ArtifactCategory(StrEnum):
    UPLOAD = "UPLOAD"
    DOWNLOAD = "DOWNLOAD"
    SCREENSHOT = "SCREENSHOT"
    LOG = "LOG"
    RESULT = "RESULT"
    EXPORT = "EXPORT"
    OTHER = "OTHER"


class ArtifactState(StrEnum):
    STAGING = "STAGING"
    AVAILABLE = "AVAILABLE"
    QUARANTINED = "QUARANTINED"
    EXPIRED = "EXPIRED"
    DELETING = "DELETING"
    DELETED = "DELETED"


class ChecksumAlgorithm(StrEnum):
    SHA256 = "SHA256"


class ArtifactProvenanceKind(StrEnum):
    USER_UPLOAD = "USER_UPLOAD"
    BROWSER_DOWNLOAD = "BROWSER_DOWNLOAD"
    SCREENSHOT = "SCREENSHOT"
    TOOL_OUTPUT = "TOOL_OUTPUT"
    AGENT_RESULT = "AGENT_RESULT"
    LOG_STREAM = "LOG_STREAM"
    IMPORT = "IMPORT"
    DERIVATION = "DERIVATION"


class EffectState(StrEnum):
    NOT_APPLIED = "NOT_APPLIED"
    APPLIED = "APPLIED"
    UNKNOWN = "UNKNOWN"


class Retryability(StrEnum):
    RETRYABLE = "RETRYABLE"
    NON_RETRYABLE = "NON_RETRYABLE"
    AFTER_RECONCILIATION = "AFTER_RECONCILIATION"


class ArtifactErrorCode(StrEnum):
    INVALID_REQUEST = "INVALID_REQUEST"
    UNAUTHORIZED = "UNAUTHORIZED"
    NOT_FOUND = "NOT_FOUND"
    INVALID_HANDLE = "INVALID_HANDLE"
    HANDLE_EXPIRED = "HANDLE_EXPIRED"
    OWNERSHIP_MISMATCH = "OWNERSHIP_MISMATCH"
    NAMESPACE_MISMATCH = "NAMESPACE_MISMATCH"
    OFFSET_CONFLICT = "OFFSET_CONFLICT"
    SIZE_LIMIT_EXCEEDED = "SIZE_LIMIT_EXCEEDED"
    CHECKSUM_MISMATCH = "CHECKSUM_MISMATCH"
    OBJECT_NOT_FOUND = "OBJECT_NOT_FOUND"
    OBJECT_ALREADY_SEALED = "OBJECT_ALREADY_SEALED"
    OBJECT_QUARANTINED = "OBJECT_QUARANTINED"
    QUOTA_EXCEEDED = "QUOTA_EXCEEDED"
    VERSION_CONFLICT = "VERSION_CONFLICT"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    REFERENCE_EXPIRED = "REFERENCE_EXPIRED"
    RETENTION_BLOCKED = "RETENTION_BLOCKED"
    IO_UNAVAILABLE = "IO_UNAVAILABLE"
    TIMEOUT = "TIMEOUT"
    CANCELLED = "CANCELLED"
    INTEGRITY_INDETERMINATE = "INTEGRITY_INDETERMINATE"


class IntegrityState(StrEnum):
    VERIFIED = "VERIFIED"
    MISMATCH = "MISMATCH"
    INDETERMINATE = "INDETERMINATE"


class StorageCapability(StrEnum):
    STREAM_WRITE = "STREAM_WRITE"
    RESUMABLE_WRITE = "RESUMABLE_WRITE"
    RANGE_READ = "RANGE_READ"
    ATOMIC_SEAL = "ATOMIC_SEAL"
    SERVER_SIDE_CHECKSUM = "SERVER_SIDE_CHECKSUM"
    RECOVERABLE_DELETE = "RECOVERABLE_DELETE"
    IMMUTABLE_OBJECT = "IMMUTABLE_OBJECT"


class _OpaqueRef:
    __slots__ = ("value",)

    def __init__(self, value: str) -> None:
        self.value = _required(value, "opaque reference")

    def __str__(self) -> str:
        return self.value

    def __repr__(self) -> str:
        return f"{type(self).__name__}(<opaque>)"

    def __eq__(self, other: object) -> bool:
        return type(self) is type(other) and self.value == other.value

    def __hash__(self) -> int:
        return hash((type(self), self.value))


class OpaqueArtifactRef(_OpaqueRef):
    pass


class OpaqueReadRef(_OpaqueRef):
    pass


class OpaqueWriteSessionRef(_OpaqueRef):
    pass


@dataclass(frozen=True, slots=True)
class ArtifactOperationContext:
    user_id: str
    workspace_id: str | None
    agent_id: str
    execution_id: str
    correlation_id: str
    purpose: AccessPurpose
    actor: str

    def __post_init__(self) -> None:
        for name in ("user_id", "agent_id", "execution_id", "correlation_id", "actor"):
            _required(getattr(self, name), name)
        if self.workspace_id is not None:
            _required(self.workspace_id, "workspace_id")
        object.__setattr__(self, "purpose", AccessPurpose(str(self.purpose)))

    def scope_key(self) -> tuple[str, ...]:
        return (
            self.user_id,
            self.workspace_id or "",
            self.agent_id,
            self.execution_id,
            self.correlation_id,
            str(self.purpose),
            self.actor,
        )

    def __repr__(self) -> str:
        return (
            "ArtifactOperationContext("
            f"user_id={self.user_id!r}, workspace_id={self.workspace_id!r}, "
            f"agent_id={self.agent_id!r}, execution_id={self.execution_id!r}, "
            f"correlation_id={self.correlation_id!r}, purpose=<bounded>, actor={self.actor!r})"
        )


@dataclass(frozen=True, slots=True)
class ArtifactNamespace:
    value: str

    def __post_init__(self) -> None:
        _required(self.value, "namespace", 512)
        if any(char.isspace() for char in self.value) or ".." in self.value:
            raise ValueError("namespace is invalid")

    def __str__(self) -> str:
        return self.value

    def __repr__(self) -> str:
        return "ArtifactNamespace(<opaque>)"


@dataclass(frozen=True, slots=True)
class ContentChecksum:
    algorithm: ChecksumAlgorithm
    digest: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "algorithm", ChecksumAlgorithm(self.algorithm))
        _required(self.digest, "checksum digest", 128)
        if self.algorithm is ChecksumAlgorithm.SHA256 and re.fullmatch(r"[0-9a-fA-F]{64}", self.digest) is None:
            raise ValueError("SHA256 digest must contain 64 hexadecimal characters")

    def __repr__(self) -> str:
        return f"ContentChecksum(algorithm={self.algorithm.value!r}, digest=<opaque>)"


@dataclass(frozen=True, slots=True)
class ArtifactProvenance:
    source_kind: ArtifactProvenanceKind
    source_refs: tuple[str, ...]
    created_by: ArtifactOperationContext

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_kind", ArtifactProvenanceKind(self.source_kind))
        refs = tuple(_required(ref, "source_ref") for ref in self.source_refs)
        if len(refs) > MAX_SOURCE_REFS:
            raise ValueError("source_refs exceed the public maximum")
        object.__setattr__(self, "source_refs", refs)


@dataclass(frozen=True, slots=True)
class ArtifactMetadata:
    artifact_id: str
    namespace: ArtifactNamespace
    logical_name: str
    category: ArtifactCategory
    media_type: str
    declared_media_type: str | None
    size_bytes: int
    checksum: ContentChecksum
    classification: DataClassification
    provenance: ArtifactProvenance
    retention_policy_ref: str
    state: ArtifactState
    version: int
    created_at: datetime
    available_at: datetime | None
    expires_at: datetime | None

    def __post_init__(self) -> None:
        _required(self.artifact_id, "artifact_id")
        _required(self.logical_name, "logical_name", MAX_NAME)
        _required(self.media_type, "media_type", MAX_MEDIA_TYPE)
        if self.declared_media_type is not None:
            _required(self.declared_media_type, "declared_media_type", MAX_MEDIA_TYPE)
        if self.size_bytes < 0:
            raise ValueError("size_bytes cannot be negative")
        if self.version < 1:
            raise ValueError("version must be positive")
        object.__setattr__(self, "category", ArtifactCategory(self.category))
        object.__setattr__(self, "classification", DataClassification(self.classification))
        object.__setattr__(self, "state", ArtifactState(self.state))
        _aware(self.created_at, "created_at")
        for value, field in ((self.available_at, "available_at"), (self.expires_at, "expires_at")):
            if value is not None:
                _aware(value, field)

    def __repr__(self) -> str:
        return (
            "ArtifactMetadata("
            f"artifact_id={self.artifact_id!r}, category={self.category.value!r}, "
            f"state={self.state.value!r}, version={self.version}, size_bytes={self.size_bytes}, "
            f"classification={self.classification.value!r})"
        )


@dataclass(frozen=True, slots=True)
class ArtifactReference:
    artifact_id: str
    version: int
    user_id: str
    workspace_id: str | None
    category: ArtifactCategory
    size_bytes: int
    checksum: ContentChecksum
    classification: DataClassification
    authorization_ref: str
    purpose: AccessPurpose
    expires_at: datetime | None

    def __post_init__(self) -> None:
        _required(self.artifact_id, "artifact_id")
        _required(self.user_id, "user_id")
        _required(self.authorization_ref, "authorization_ref")
        if self.workspace_id is not None:
            _required(self.workspace_id, "workspace_id")
        if self.version < 1 or self.size_bytes < 0:
            raise ValueError("reference version/size is invalid")
        object.__setattr__(self, "category", ArtifactCategory(self.category))
        object.__setattr__(self, "classification", DataClassification(self.classification))
        object.__setattr__(self, "purpose", AccessPurpose(str(self.purpose)))
        if self.expires_at is not None:
            _aware(self.expires_at, "expires_at")

    def __repr__(self) -> str:
        return (
            "ArtifactReference("
            f"artifact_id={self.artifact_id!r}, version={self.version}, "
            f"category={self.category.value!r}, size_bytes={self.size_bytes}, "
            f"authorization_ref=<opaque>)"
        )


@dataclass(frozen=True, slots=True)
class ArtifactGrant:
    grant_id: str
    artifact_id: str
    user_id: str
    workspace_id: str | None
    agent_id: str
    execution_id: str
    purpose: AccessPurpose
    classification_ceiling: DataClassification
    version: int
    expires_at: datetime | None
    revoked_at: datetime | None

    def __post_init__(self) -> None:
        for name in ("grant_id", "artifact_id", "user_id", "agent_id", "execution_id"):
            _required(getattr(self, name), name)
        object.__setattr__(self, "purpose", AccessPurpose(str(self.purpose)))
        object.__setattr__(self, "classification_ceiling", DataClassification(self.classification_ceiling))
        if self.version < 1:
            raise ValueError("grant version must be positive")
        if self.expires_at is not None:
            _aware(self.expires_at, "expires_at")
        if self.revoked_at is not None:
            _aware(self.revoked_at, "revoked_at")

    def allows(
        self,
        context: ArtifactOperationContext,
        *,
        version: int,
        classification: DataClassification,
        now: datetime,
    ) -> bool:
        if self.revoked_at is not None and now >= self.revoked_at:
            return False
        if self.expires_at is not None and now >= self.expires_at:
            return False
        return (
            self.user_id == context.user_id
            and self.workspace_id == context.workspace_id
            and self.agent_id == context.agent_id
            and self.execution_id == context.execution_id
            and self.purpose == context.purpose
            and self.version == version
            and classification_allows(self.classification_ceiling, DataClassification(classification))
        )


@dataclass(frozen=True, slots=True)
class ArtifactError(Exception):
    code: ArtifactErrorCode
    retryability: Retryability
    effect_state: EffectState
    message: str = "artifact operation failed"

    def __post_init__(self) -> None:
        object.__setattr__(self, "code", ArtifactErrorCode(self.code))
        object.__setattr__(self, "retryability", Retryability(self.retryability))
        object.__setattr__(self, "effect_state", EffectState(self.effect_state))
        object.__setattr__(self, "message", _required(self.message, "message", MAX_REASON))
        Exception.__init__(self, self.message)

    def __repr__(self) -> str:
        return f"ArtifactError(code={self.code.value!r}, effect_state={self.effect_state.value!r})"


__all__ = [
    "AccessPurpose", "ArtifactCategory", "ArtifactError", "ArtifactErrorCode", "ArtifactGrant",
    "ArtifactMetadata", "ArtifactNamespace", "ArtifactOperationContext", "ArtifactProvenance",
    "ArtifactProvenanceKind", "ArtifactReference", "ArtifactState", "ChecksumAlgorithm",
    "ContentChecksum", "DataClassification", "EffectState", "IntegrityState", "OpaqueArtifactRef",
    "OpaqueReadRef", "OpaqueWriteSessionRef", "Retryability", "StorageCapability",
]
