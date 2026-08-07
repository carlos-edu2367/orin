from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import StrEnum
import re
import unicodedata
from typing import TYPE_CHECKING

from agentos.events.models import DataClassification

if TYPE_CHECKING:
    from agentos.resources.models import AuthorizedResourceHandle

MAX_TEXT = 256
MAX_SEGMENTS = 128
MAX_REASON = 128


def _required(value: object, name: str, maximum: int = MAX_TEXT) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-blank string")
    if len(value) > maximum:
        raise ValueError(f"{name} exceeds its maximum length")
    return value


def _aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


class FilesystemEntryKind(StrEnum):
    FILE = "FILE"
    DIRECTORY = "DIRECTORY"
    SYMLINK = "SYMLINK"


class SymlinkPolicy(StrEnum):
    REJECT = "REJECT"
    REQUIRE_CONTAINED_TARGET = "REQUIRE_CONTAINED_TARGET"


class WriteMode(StrEnum):
    CREATE_NEW = "CREATE_NEW"
    REPLACE = "REPLACE"
    APPEND = "APPEND"


class Atomicity(StrEnum):
    REQUIRE_ATOMIC = "REQUIRE_ATOMIC"
    BEST_EFFORT = "BEST_EFFORT"


class OverwritePolicy(StrEnum):
    NEVER = "NEVER"
    IF_VERSION_MATCHES = "IF_VERSION_MATCHES"


class FilesystemOperationKind(StrEnum):
    STAT = "STAT"
    LIST = "LIST"
    READ = "READ"
    CREATE_DIRECTORY = "CREATE_DIRECTORY"
    WRITE = "WRITE"
    MOVE = "MOVE"
    COPY = "COPY"
    REMOVE = "REMOVE"


class EffectState(StrEnum):
    NOT_APPLIED = "NOT_APPLIED"
    APPLIED = "APPLIED"
    UNKNOWN = "UNKNOWN"


class FilesystemErrorCode(StrEnum):
    REJECTED = "REJECTED"
    NOT_FOUND = "NOT_FOUND"
    CONFLICT = "CONFLICT"
    QUOTA_EXCEEDED = "QUOTA_EXCEEDED"
    TIMEOUT = "TIMEOUT"
    CANCELLED = "CANCELLED"
    UNKNOWN_EFFECT = "UNKNOWN_EFFECT"
    INVALID_HANDLE = "INVALID_HANDLE"
    LEASE_EXPIRED = "LEASE_EXPIRED"
    OWNERSHIP_MISMATCH = "OWNERSHIP_MISMATCH"
    CAPABILITY_DENIED = "CAPABILITY_DENIED"
    TYPE_MISMATCH = "TYPE_MISMATCH"
    ATOMICITY_UNSUPPORTED = "ATOMICITY_UNSUPPORTED"
    UNSAFE_ROOT = "UNSAFE_ROOT"
    INVALID_REQUEST = "INVALID_REQUEST"


@dataclass(frozen=True, slots=True)
class FilesystemOperationContext:
    user_id: str
    workspace_id: str
    agent_id: str
    execution_id: str
    correlation_id: str
    purpose: str
    actor: str

    def __post_init__(self) -> None:
        for name in ("user_id", "workspace_id", "agent_id", "execution_id", "correlation_id", "actor"):
            _required(getattr(self, name), name)
        _required(self.purpose, "purpose")
        if any(token in self.workspace_id.lower() for token in ("/", "\\", "..", ":")):
            raise ValueError("workspace_id must be opaque")

    def scope_key(self) -> tuple[str, ...]:
        return (self.user_id, self.workspace_id, self.agent_id, self.execution_id, self.correlation_id, self.purpose, self.actor)

    def __repr__(self) -> str:
        return f"FilesystemOperationContext(user_id={self.user_id!r}, workspace_id={self.workspace_id!r}, agent_id={self.agent_id!r}, execution_id={self.execution_id!r}, correlation_id={self.correlation_id!r}, purpose=<bounded>, actor={self.actor!r})"


@dataclass(frozen=True, slots=True)
class WorkspacePath:
    segments: tuple[str, ...]

    def __post_init__(self) -> None:
        if len(self.segments) > MAX_SEGMENTS:
            raise ValueError("path depth exceeds maximum")
        for segment in self.segments:
            self._validate_segment(segment)
        object.__setattr__(self, "segments", tuple(self.segments))

    @staticmethod
    def _validate_segment(segment: str) -> None:
        if not isinstance(segment, str) or not segment or segment in (".", ".."):
            raise ValueError("path segment is invalid")
        if any(char in segment for char in ("/", "\\", ":", "\x00")) or any(ord(char) < 32 for char in segment):
            raise ValueError("path segment contains a reserved character")
        if segment.startswith(("~", "$", "%")):
            raise ValueError("path segment contains an expansion marker")
        if unicodedata.normalize("NFC", segment) != segment or unicodedata.normalize("NFKC", segment) != segment:
            raise ValueError("path segment is not canonically normalized")
        if len(segment) > MAX_TEXT:
            raise ValueError("path segment is too long")
        if re.fullmatch(r"[. ]+", segment):
            raise ValueError("path segment is ambiguous")

    @classmethod
    def root(cls) -> "WorkspacePath":
        return cls(())

    @classmethod
    def from_segments(cls, *segments: str) -> "WorkspacePath":
        return cls(tuple(segments))

    @classmethod
    def from_string(cls, value: str) -> "WorkspacePath":
        if not isinstance(value, str) or not value or value.startswith(("/", "\\")) or value.endswith(("/", "\\")):
            raise ValueError("path must be relative and non-empty")
        if ":" in value or "//" in value or "\\\\" in value or "?" in value:
            raise ValueError("path contains a reserved namespace")
        return cls(tuple(value.split("/")))

    def as_logical_string(self) -> str:
        return "/".join(self.segments)

    def child(self, segment: str) -> "WorkspacePath":
        return WorkspacePath(self.segments + (segment,))

    def __repr__(self) -> str:
        return f"WorkspacePath(segments={self.segments!r})"


@dataclass(frozen=True, slots=True)
class OpaqueFilesystemHandle:
    _value: str
    binding: str

    def __post_init__(self) -> None:
        _required(self._value, "handle")
        _required(self.binding, "binding")

    def __repr__(self) -> str:
        return "OpaqueFilesystemHandle(<ephemeral>)"

    def __reduce_ex__(self, protocol: int):
        raise TypeError("filesystem handles are ephemeral and not serializable")


@dataclass(frozen=True, slots=True)
class FilesystemEntry:
    path: WorkspacePath
    kind: FilesystemEntryKind
    size_bytes: int
    version: int
    classification: DataClassification = DataClassification.INTERNAL
    modified_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", FilesystemEntryKind(self.kind))
        object.__setattr__(self, "classification", DataClassification(self.classification))
        if self.size_bytes < 0 or self.version < 1:
            raise ValueError("entry size/version is invalid")
        _aware(self.modified_at, "modified_at")


@dataclass(frozen=True, slots=True)
class FilesystemLimits:
    maximum_bytes: int = 1024 * 1024
    maximum_entries: int = 1000
    maximum_depth: int = 32
    timeout: timedelta = timedelta(seconds=30)

    def __post_init__(self) -> None:
        if any(not isinstance(v, int) or v < 0 for v in (self.maximum_bytes, self.maximum_entries, self.maximum_depth)):
            raise ValueError("filesystem limits must be non-negative")
        if self.timeout <= timedelta(0) or self.timeout > timedelta(hours=1):
            raise ValueError("filesystem timeout must be bounded")


@dataclass(frozen=True, slots=True)
class FilesystemError:
    code: FilesystemErrorCode
    reason: str = "filesystem operation failed"
    effect_state: EffectState = EffectState.NOT_APPLIED
    retryable: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "code", FilesystemErrorCode(self.code))
        object.__setattr__(self, "effect_state", EffectState(self.effect_state))
        _required(self.reason, "reason", MAX_REASON)

    def __repr__(self) -> str:
        return f"FilesystemError(code={self.code.value!r}, effect_state={self.effect_state.value!r}, retryable={self.retryable})"

    def __str__(self) -> str:
        return f"filesystem operation failed: {self.code.value}"


@dataclass(frozen=True, slots=True)
class FilesystemOperation:
    operation_id: str
    context: FilesystemOperationContext
    lease_id: str
    resource_handle: "AuthorizedResourceHandle | None"
    kind: FilesystemOperationKind
    source: WorkspacePath
    destination: WorkspacePath | None = None
    limits: FilesystemLimits = FilesystemLimits()
    idempotency_key: str | None = None


@dataclass(frozen=True, slots=True)
class FilesystemReadResult:
    bytes_read: int
    next_offset: int | None
    effect_state: EffectState = EffectState.APPLIED


@dataclass(frozen=True, slots=True)
class FilesystemWriteResult:
    entry: FilesystemEntry
    bytes_written: int
    effect_state: EffectState = EffectState.APPLIED


@dataclass(frozen=True, slots=True)
class FilesystemPage:
    entries: tuple[FilesystemEntry, ...]
    next_cursor: str | None = None


@dataclass(frozen=True, slots=True)
class FilesystemMutationResult:
    entry: FilesystemEntry | None
    affected_entries: int
    effect_state: EffectState = EffectState.APPLIED


__all__ = [name for name in globals() if not name.startswith("_")]
