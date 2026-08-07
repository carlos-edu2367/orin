from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import NewType

from agentos.filesystem.models import WorkspacePath


MAX_TEXT = 256
MAX_REASON = 96
MAX_CHUNK_BYTES = 1024 * 1024

TerminalSessionId = NewType("TerminalSessionId", str)
TerminalCommandId = NewType("TerminalCommandId", str)
TerminalRequestId = NewType("TerminalRequestId", str)
ResourceLeaseId = NewType("ResourceLeaseId", str)
SecretReference = NewType("SecretReference", str)
ShellProfileRef = NewType("ShellProfileRef", str)
NetworkPolicyRef = NewType("NetworkPolicyRef", str)
ResultReference = NewType("ResultReference", str)


def _required(value: object, name: str, maximum: int = MAX_TEXT) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-blank string")
    if len(value) > maximum:
        raise ValueError(f"{name} exceeds its maximum length")
    return value


def _aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


class TerminalSessionStatus(StrEnum):
    CREATING = "CREATING"
    READY = "READY"
    RUNNING = "RUNNING"
    EXITED = "EXITED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    CLOSED = "CLOSED"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"


class OutputChannel(StrEnum):
    STDOUT = "STDOUT"
    STDERR = "STDERR"
    CONTROL = "CONTROL"


class BufferTruncation(StrEnum):
    NONE = "NONE"
    HEAD_DROPPED = "HEAD_DROPPED"
    REDACTED = "REDACTED"


class TerminalEffectState(StrEnum):
    NOT_APPLIED = "NOT_APPLIED"
    APPLIED = "APPLIED"
    UNKNOWN = "UNKNOWN"


class Retryability(StrEnum):
    NEVER = "NEVER"
    SAFE = "SAFE"
    AFTER_RECONCILIATION = "AFTER_RECONCILIATION"


class TerminalErrorCode(StrEnum):
    INVALID_REQUEST = "INVALID_REQUEST"
    UNAUTHORIZED = "UNAUTHORIZED"
    NOT_FOUND = "NOT_FOUND"
    LEASE_INVALID = "LEASE_INVALID"
    FENCE_REJECTED = "FENCE_REJECTED"
    SESSION_STATE_REJECTED = "SESSION_STATE_REJECTED"
    COMMAND_ACTIVE = "COMMAND_ACTIVE"
    COMMAND_NOT_FOUND = "COMMAND_NOT_FOUND"
    LIMIT_EXCEEDED = "LIMIT_EXCEEDED"
    CWD_REJECTED = "CWD_REJECTED"
    POLICY_DENIED = "POLICY_DENIED"
    ADAPTER_FAILURE = "ADAPTER_FAILURE"
    CLEANUP_FAILED = "CLEANUP_FAILED"
    UNKNOWN_EFFECT = "UNKNOWN_EFFECT"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"


class CancellationReason(StrEnum):
    USER_REQUESTED = "USER_REQUESTED"
    POLICY_REQUESTED = "POLICY_REQUESTED"
    TIMEOUT = "TIMEOUT"
    LEASE_REVOKED = "LEASE_REVOKED"
    RECOVERY_ABORTED = "RECOVERY_ABORTED"


class TerminationStage(StrEnum):
    COOPERATIVE = "COOPERATIVE"
    INTERRUPT = "INTERRUPT"
    TERMINATE = "TERMINATE"
    KILL = "KILL"
    ALREADY_EXITED = "ALREADY_EXITED"
    UNKNOWN = "UNKNOWN"


class StreamDisposition(StrEnum):
    CONTINUE = "CONTINUE"
    PAUSE = "PAUSE"
    STOP = "STOP"


@dataclass(frozen=True, slots=True)
class TerminalOperationContext:
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
        if any(marker in self.workspace_id for marker in ("/", "\\", ":", "..")):
            raise ValueError("workspace_id must be opaque")
        _required(self.purpose, "purpose", 128)

    def scope_key(self) -> tuple[str, ...]:
        return (self.user_id, self.workspace_id, self.agent_id, self.execution_id, self.correlation_id, self.purpose, self.actor)

    def binding_key(self) -> tuple[str, ...]:
        return (self.user_id, self.workspace_id, self.agent_id, self.execution_id, self.purpose, self.actor)

    def __repr__(self) -> str:
        return (
            "TerminalOperationContext("
            f"user_id={self.user_id!r}, workspace_id={self.workspace_id!r}, "
            f"agent_id={self.agent_id!r}, execution_id={self.execution_id!r}, "
            f"correlation_id={self.correlation_id!r}, purpose=<bounded>, actor={self.actor!r})"
        )


@dataclass(frozen=True, slots=True)
class TerminalLimits:
    session_ttl: timedelta
    command_timeout: timedelta
    maximum_processes: int
    maximum_memory_bytes: int
    maximum_cpu_time: timedelta
    maximum_output_bytes: int
    maximum_input_bytes: int
    maximum_buffer_bytes: int
    network_policy_ref: NetworkPolicyRef

    def __post_init__(self) -> None:
        if self.session_ttl <= timedelta(0) or self.session_ttl > timedelta(hours=1):
            raise ValueError("session_ttl is invalid")
        if self.command_timeout <= timedelta(0) or self.command_timeout > self.session_ttl:
            raise ValueError("command_timeout is invalid")
        if self.maximum_cpu_time <= timedelta(0) or self.maximum_cpu_time > self.session_ttl:
            raise ValueError("maximum_cpu_time is invalid")
        for name, value in (("maximum_processes", self.maximum_processes), ("maximum_memory_bytes", self.maximum_memory_bytes), ("maximum_output_bytes", self.maximum_output_bytes), ("maximum_input_bytes", self.maximum_input_bytes), ("maximum_buffer_bytes", self.maximum_buffer_bytes)):
            if not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} is invalid")
        if self.maximum_processes < 1 or self.maximum_output_bytes < 1 or self.maximum_buffer_bytes < 1:
            raise ValueError("terminal limits must allow a process and output buffer")
        _required(self.network_policy_ref, "network_policy_ref")


@dataclass(frozen=True, slots=True)
class TerminalBuffer:
    first_sequence: int | None
    last_sequence: int | None
    retained_bytes: int
    dropped_bytes: int
    maximum_bytes: int
    truncation: BufferTruncation = BufferTruncation.NONE

    def __post_init__(self) -> None:
        for name, value in (("retained_bytes", self.retained_bytes), ("dropped_bytes", self.dropped_bytes), ("maximum_bytes", self.maximum_bytes)):
            if not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} is invalid")
        if self.maximum_bytes < 1 or self.retained_bytes > self.maximum_bytes:
            raise ValueError("buffer accounting is invalid")
        if (self.first_sequence is None) != (self.last_sequence is None):
            raise ValueError("buffer sequence range is invalid")
        if self.first_sequence is not None and (self.first_sequence < 1 or self.last_sequence < self.first_sequence):
            raise ValueError("buffer sequence range is invalid")
        object.__setattr__(self, "truncation", BufferTruncation(self.truncation))


@dataclass(frozen=True, slots=True)
class TerminalOutputChunk:
    session_id: TerminalSessionId | str
    command_id: TerminalCommandId | str
    sequence: int
    channel: OutputChannel | str
    bytes: bytes
    occurred_at: datetime

    def __post_init__(self) -> None:
        _required(self.session_id, "session_id")
        _required(self.command_id, "command_id")
        if self.sequence < 1:
            raise ValueError("sequence must be positive")
        if not isinstance(self.bytes, bytes) or len(self.bytes) > MAX_CHUNK_BYTES:
            raise ValueError("output chunk is invalid or too large")
        _aware(self.occurred_at, "occurred_at")
        object.__setattr__(self, "channel", OutputChannel(self.channel))

    def __repr__(self) -> str:
        return f"TerminalOutputChunk(session_id={self.session_id!r}, command_id={self.command_id!r}, sequence={self.sequence}, channel={self.channel.value!r}, bytes=<redacted>)"


@dataclass(frozen=True, slots=True)
class TerminalCommand:
    command_id: TerminalCommandId | str
    session_id: TerminalSessionId | str
    context: TerminalOperationContext
    command: str
    requested_cwd: WorkspacePath | None
    environment_refs: tuple[SecretReference | str, ...]
    timeout: timedelta
    maximum_output_bytes: int
    idempotency_key: str | None

    def __post_init__(self) -> None:
        _required(self.command, "command", 16 * 1024)
        _required(self.command_id, "command_id")
        _required(self.session_id, "session_id")
        if self.timeout <= timedelta(0) or self.timeout > timedelta(hours=1):
            raise ValueError("command timeout is invalid")
        if self.maximum_output_bytes < 1:
            raise ValueError("maximum_output_bytes must be positive")
        refs = tuple(self.environment_refs)
        for ref in refs:
            _required(ref, "environment reference")
        object.__setattr__(self, "environment_refs", refs)
        if self.idempotency_key is not None:
            _required(self.idempotency_key, "idempotency_key")

    def __repr__(self) -> str:
        return f"TerminalCommand(command_id={self.command_id!r}, session_id={self.session_id!r}, context=<scoped>, command=<sensitive>, requested_cwd={self.requested_cwd!r}, environment_refs=<redacted>)"


@dataclass(frozen=True, slots=True)
class TerminalSessionSnapshot:
    id: TerminalSessionId | str
    cwd: WorkspacePath
    pid: int | None
    status: TerminalSessionStatus | str
    owner: str
    workspace: str
    agent_id: str
    execution_id: str
    correlation_id: str
    purpose: str
    buffer: TerminalBuffer
    lease_id: ResourceLeaseId | str
    current_command_id: TerminalCommandId | str | None
    policy_version: int
    created_at: datetime
    last_activity_at: datetime
    expires_at: datetime
    output_ref: ResultReference | str | None = None

    def __post_init__(self) -> None:
        for name in ("id", "owner", "workspace", "agent_id", "execution_id", "correlation_id", "lease_id"):
            _required(getattr(self, name), name)
        _required(self.purpose, "purpose", 128)
        if self.policy_version < 1:
            raise ValueError("policy_version must be positive")
        _aware(self.created_at, "created_at")
        _aware(self.last_activity_at, "last_activity_at")
        _aware(self.expires_at, "expires_at")
        if self.expires_at <= self.created_at:
            raise ValueError("session expiry is invalid")
        object.__setattr__(self, "status", TerminalSessionStatus(self.status))


@dataclass(frozen=True, slots=True)
class CreateTerminalSession:
    request_id: TerminalRequestId | str
    context: TerminalOperationContext
    lease_id: ResourceLeaseId | str
    initial_cwd: WorkspacePath
    shell_profile: ShellProfileRef | str
    environment_refs: tuple[SecretReference | str, ...]
    limits: TerminalLimits
    idempotency_key: str

    def __post_init__(self) -> None:
        for name in ("request_id", "lease_id", "shell_profile", "idempotency_key"):
            _required(getattr(self, name), name)
        object.__setattr__(self, "environment_refs", tuple(self.environment_refs))


@dataclass(frozen=True, slots=True)
class ExecuteTerminalCommand:
    command: TerminalCommand
    expected_session_status: TerminalSessionStatus | str = TerminalSessionStatus.READY

    def __post_init__(self) -> None:
        object.__setattr__(self, "expected_session_status", TerminalSessionStatus(self.expected_session_status))


@dataclass(frozen=True, slots=True)
class WriteTerminalInput:
    request_id: TerminalRequestId | str
    context: TerminalOperationContext
    lease_id: ResourceLeaseId | str
    session_id: TerminalSessionId | str
    command_id: TerminalCommandId | str
    input: bytes
    end_of_input: bool
    input_sequence: int
    idempotency_key: str

    def __post_init__(self) -> None:
        for name in ("request_id", "lease_id", "session_id", "command_id", "idempotency_key"):
            _required(getattr(self, name), name)
        if not isinstance(self.input, bytes) or len(self.input) > 1024 * 1024:
            raise ValueError("input is invalid or too large")
        if self.input_sequence < 1:
            raise ValueError("input_sequence must be positive")


@dataclass(frozen=True, slots=True)
class StreamTerminalOutput:
    request_id: TerminalRequestId | str
    context: TerminalOperationContext
    lease_id: ResourceLeaseId | str
    session_id: TerminalSessionId | str
    command_id: TerminalCommandId | str | None
    after_sequence: int
    maximum_chunks: int
    maximum_bytes: int
    timeout: timedelta

    def __post_init__(self) -> None:
        for name in ("request_id", "lease_id", "session_id"):
            _required(getattr(self, name), name)
        if self.after_sequence < 0 or self.maximum_chunks < 1 or self.maximum_bytes < 1 or self.timeout <= timedelta(0):
            raise ValueError("stream limits are invalid")


@dataclass(frozen=True, slots=True)
class AuthorizedTerminalQuery:
    context: TerminalOperationContext
    lease_id: ResourceLeaseId | str
    session_id: TerminalSessionId | str
    include_buffer_metadata: bool = True
    include_current_command: bool = True


@dataclass(frozen=True, slots=True)
class CancelTerminalCommand:
    request_id: TerminalRequestId | str
    context: TerminalOperationContext
    lease_id: ResourceLeaseId | str
    session_id: TerminalSessionId | str
    command_id: TerminalCommandId | str
    reason: CancellationReason | str
    cancellation_deadline: datetime
    idempotency_key: str

    def __post_init__(self) -> None:
        for name in ("request_id", "lease_id", "session_id", "command_id", "idempotency_key"):
            _required(getattr(self, name), name)
        object.__setattr__(self, "reason", CancellationReason(self.reason))
        _aware(self.cancellation_deadline, "cancellation_deadline")


@dataclass(frozen=True, slots=True)
class CloseTerminalSession:
    request_id: TerminalRequestId | str
    context: TerminalOperationContext
    lease_id: ResourceLeaseId | str
    session_id: TerminalSessionId | str
    expected_status: TerminalSessionStatus | str
    reason: str
    cleanup_deadline: datetime
    idempotency_key: str

    def __post_init__(self) -> None:
        for name in ("request_id", "lease_id", "session_id", "reason", "idempotency_key"):
            _required(getattr(self, name), name)
        object.__setattr__(self, "expected_status", TerminalSessionStatus(self.expected_status))
        _aware(self.cleanup_deadline, "cleanup_deadline")


@dataclass(frozen=True, slots=True)
class TerminalError:
    code: TerminalErrorCode | str
    retryability: Retryability | str = Retryability.NEVER
    effect_state: TerminalEffectState | str = TerminalEffectState.NOT_APPLIED
    reason_code: str = "terminal operation rejected"

    def __post_init__(self) -> None:
        object.__setattr__(self, "code", TerminalErrorCode(self.code))
        object.__setattr__(self, "retryability", Retryability(self.retryability))
        object.__setattr__(self, "effect_state", TerminalEffectState(self.effect_state))
        _required(self.reason_code, "reason_code", MAX_REASON)

    def __repr__(self) -> str:
        return f"TerminalError(code={self.code.value!r}, retryability={self.retryability.value!r}, effect_state={self.effect_state.value!r})"


@dataclass(frozen=True, slots=True)
class TerminalCommandAccepted:
    command_id: TerminalCommandId | str
    session_id: TerminalSessionId | str
    accepted_at: datetime


@dataclass(frozen=True, slots=True)
class InputWriteResult:
    session_id: TerminalSessionId | str
    command_id: TerminalCommandId | str
    input_sequence: int
    accepted_bytes: int
    effect_state: TerminalEffectState = TerminalEffectState.APPLIED


@dataclass(frozen=True, slots=True)
class StreamResult:
    session_id: TerminalSessionId | str
    command_id: TerminalCommandId | str | None
    chunks_emitted: int
    bytes_emitted: int
    next_sequence: int | None
    truncated: bool = False
    timed_out: bool = False
    effect_state: TerminalEffectState = TerminalEffectState.APPLIED


@dataclass(frozen=True, slots=True)
class SignalReceipt:
    command_id: TerminalCommandId | str
    stage: TerminationStage | str
    effect_state: TerminalEffectState = TerminalEffectState.APPLIED


@dataclass(frozen=True, slots=True)
class ProcessExitState:
    command_id: TerminalCommandId | str
    exited: bool
    exit_code: int | None
    effect_state: TerminalEffectState = TerminalEffectState.APPLIED


@dataclass(frozen=True, slots=True)
class TerminationResult:
    session_id: TerminalSessionId | str
    stage: TerminationStage | str
    tree_terminated: bool
    effect_state: TerminalEffectState = TerminalEffectState.APPLIED


@dataclass(frozen=True, slots=True)
class ProcessTreeSnapshot:
    session_id: TerminalSessionId | str
    owned_processes: int
    live_processes: int
    ownership_confirmed: bool
    effect_state: TerminalEffectState = TerminalEffectState.APPLIED


@dataclass(frozen=True, slots=True)
class ResourceUsage:
    duration: timedelta = timedelta(0)
    cpu_time: timedelta = timedelta(0)
    output_bytes: int = 0
    input_bytes: int = 0
    processes: int = 1


@dataclass(frozen=True, slots=True)
class CommandExited:
    command_id: TerminalCommandId | str
    exit_code: int
    final_cwd: WorkspacePath
    output_ref: ResultReference | str | None
    usage: ResourceUsage
    effect_state: TerminalEffectState = TerminalEffectState.APPLIED


@dataclass(frozen=True, slots=True)
class CommandFailed:
    command_id: TerminalCommandId | str
    error: TerminalError
    effect_state: TerminalEffectState
    output_ref: ResultReference | str | None = None


@dataclass(frozen=True, slots=True)
class CommandCancelled:
    command_id: TerminalCommandId | str
    termination_stage: TerminationStage | str
    output_ref: ResultReference | str | None = None
    effect_state: TerminalEffectState = TerminalEffectState.APPLIED


TerminalCommandOutcome = CommandExited | CommandFailed | CommandCancelled


@dataclass(frozen=True, slots=True)
class CancelTerminalResult:
    session_id: TerminalSessionId | str
    command_id: TerminalCommandId | str
    stage: TerminationStage | str
    effect_state: TerminalEffectState


@dataclass(frozen=True, slots=True)
class CloseTerminalResult:
    session_id: TerminalSessionId | str
    status: TerminalSessionStatus | str
    effect_state: TerminalEffectState
    lease_released: bool


__all__ = [name for name in globals() if not name.startswith("_")]
