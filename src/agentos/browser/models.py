from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from types import MappingProxyType
from typing import Mapping


MAX_TEXT = 256


def _required(value: object, field: str, maximum: int = MAX_TEXT) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-blank string")
    if len(value) > maximum:
        raise ValueError(f"{field} exceeds its maximum length")
    return value


def _aware(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")


def _opaque(value: str, field: str = "reference") -> None:
    _required(value, field)
    if any(marker in value for marker in ("\\", "/", "..", "\x00")):
        raise ValueError(f"{field} must be opaque")


class BrowserProfileStatus(StrEnum):
    ACTIVE = "ACTIVE"
    LOCKED = "LOCKED"
    DISABLED = "DISABLED"


class BrowserSessionStatus(StrEnum):
    CREATING = "CREATING"
    READY = "READY"
    BUSY = "BUSY"
    CLOSING = "CLOSING"
    CLOSED = "CLOSED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class BrowserPageStatus(StrEnum):
    OPENING = "OPENING"
    READY = "READY"
    NAVIGATING = "NAVIGATING"
    CLOSED = "CLOSED"
    FAILED = "FAILED"


class BrowserOperationKind(StrEnum):
    OPEN_SESSION = "OPEN_SESSION"
    CLOSE_SESSION = "CLOSE_SESSION"
    OPEN_PAGE = "OPEN_PAGE"
    CLOSE_PAGE = "CLOSE_PAGE"
    NAVIGATE = "NAVIGATE"
    INTERACT = "INTERACT"
    CAPTURE_DOM = "CAPTURE_DOM"
    CAPTURE_SCREENSHOT = "CAPTURE_SCREENSHOT"
    READ_COOKIES = "READ_COOKIES"
    SET_COOKIES = "SET_COOKIES"
    UPLOAD = "UPLOAD"
    DOWNLOAD = "DOWNLOAD"


class EffectState(StrEnum):
    NOT_APPLIED = "NOT_APPLIED"
    APPLIED = "APPLIED"
    UNKNOWN = "UNKNOWN"


class Retryability(StrEnum):
    NEVER = "NEVER"
    SAFE = "SAFE"
    AFTER_RECONCILIATION = "AFTER_RECONCILIATION"


class BrowserErrorCode(StrEnum):
    INVALID_REQUEST = "INVALID_REQUEST"
    UNAUTHORIZED = "UNAUTHORIZED"
    NOT_FOUND = "NOT_FOUND"
    PROFILE_LOCKED = "PROFILE_LOCKED"
    PROFILE_DISABLED = "PROFILE_DISABLED"
    LEASE_EXPIRED = "LEASE_EXPIRED"
    FENCE_REJECTED = "FENCE_REJECTED"
    VERSION_CONFLICT = "VERSION_CONFLICT"
    PAGE_QUOTA_EXCEEDED = "PAGE_QUOTA_EXCEEDED"
    SESSION_CLOSED = "SESSION_CLOSED"
    PAGE_CLOSED = "PAGE_CLOSED"
    POLICY_DENIED = "POLICY_DENIED"
    LIMIT_EXCEEDED = "LIMIT_EXCEEDED"
    CANCELLED = "CANCELLED"
    UNKNOWN = "UNKNOWN"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"


class GrantCapability(StrEnum):
    OPEN_SESSION = "OPEN_SESSION"
    CLOSE_SESSION = "CLOSE_SESSION"
    OPEN_PAGE = "OPEN_PAGE"
    CLOSE_PAGE = "CLOSE_PAGE"
    NAVIGATE = "NAVIGATE"
    INTERACT = "INTERACT"
    READ_DOM = "READ_DOM"
    SCREENSHOT = "SCREENSHOT"
    READ_COOKIES = "READ_COOKIES"
    SET_COOKIES = "SET_COOKIES"
    UPLOAD = "UPLOAD"
    DOWNLOAD = "DOWNLOAD"
    EVALUATE = "EVALUATE"
    CLIPBOARD = "CLIPBOARD"
    CAMERA = "CAMERA"
    GEOLOCATION = "GEOLOCATION"


@dataclass(frozen=True, slots=True)
class BrowserOperationContext:
    user_id: str
    workspace_id: str | None
    agent_id: str
    execution_id: str
    correlation_id: str
    purpose: str
    actor: str

    def __post_init__(self) -> None:
        for name in ("user_id", "agent_id", "execution_id", "correlation_id", "actor"):
            _required(getattr(self, name), name)
        if self.workspace_id is not None:
            _opaque(self.workspace_id, "workspace_id")
        _required(self.purpose, "purpose", 128)

    def scope_key(self) -> tuple[str, ...]:
        return (self.user_id, self.workspace_id or "", self.agent_id, self.execution_id, self.correlation_id, self.purpose, self.actor)

    def binding_key(self) -> tuple[str, ...]:
        return (self.user_id, self.workspace_id or "", self.agent_id, self.execution_id, self.purpose, self.actor)

    def __repr__(self) -> str:
        return f"BrowserOperationContext(user_id={self.user_id!r}, workspace_id={self.workspace_id!r}, agent_id={self.agent_id!r}, execution_id={self.execution_id!r}, correlation_id={self.correlation_id!r}, purpose=<bounded>, actor={self.actor!r})"


@dataclass(frozen=True, slots=True)
class BrowserProfile:
    profile_id: str
    user_id: str
    workspace_id: str | None
    name: str
    policy_ref: str
    storage_state_ref: str | None
    version: int
    status: BrowserProfileStatus

    def __post_init__(self) -> None:
        _opaque(self.profile_id, "profile_id")
        _opaque(self.user_id, "user_id")
        if self.workspace_id is not None:
            _opaque(self.workspace_id, "workspace_id")
        _required(self.name, "name", 96)
        _required(self.policy_ref, "policy_ref")
        if self.storage_state_ref is not None:
            _opaque(self.storage_state_ref, "storage_state_ref")
        if self.version < 1:
            raise ValueError("profile version must be positive")
        object.__setattr__(self, "status", BrowserProfileStatus(self.status))


@dataclass(frozen=True, slots=True)
class BrowserSessionSnapshot:
    session_id: str
    profile_id: str
    context: BrowserOperationContext
    lease_id: str
    worker_ref: str
    status: BrowserSessionStatus
    page_ids: tuple[str, ...]
    version: int
    created_at: datetime
    updated_at: datetime
    expires_at: datetime | None
    profile_snapshot: BrowserProfile | None = None

    def __post_init__(self) -> None:
        for name in ("session_id", "profile_id", "lease_id", "worker_ref"):
            _opaque(getattr(self, name), name)
        object.__setattr__(self, "status", BrowserSessionStatus(self.status))
        object.__setattr__(self, "page_ids", tuple(self.page_ids))
        if self.version < 1:
            raise ValueError("session version must be positive")
        for name in ("created_at", "updated_at"):
            _aware(getattr(self, name), name)
        if self.expires_at is not None:
            _aware(self.expires_at, "expires_at")


@dataclass(frozen=True, slots=True)
class BrowserPageSnapshot:
    page_id: str
    session_id: str
    url: str
    title: str | None
    status: BrowserPageStatus
    version: int
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        _opaque(self.page_id, "page_id")
        _opaque(self.session_id, "session_id")
        _required(self.url, "url", 2048)
        if self.title is not None:
            _required(self.title, "title", 256)
        object.__setattr__(self, "status", BrowserPageStatus(self.status))
        if self.version < 1:
            raise ValueError("page version must be positive")
        _aware(self.created_at, "created_at")
        _aware(self.updated_at, "updated_at")


@dataclass(frozen=True, slots=True)
class BrowserLimits:
    timeout: timedelta
    maximum_pages: int
    maximum_redirects: int
    maximum_dom_bytes: int
    maximum_screenshot_bytes: int
    maximum_upload_bytes: int
    maximum_download_bytes: int
    allowed_download_count: int
    network_policy_ref: str

    def __post_init__(self) -> None:
        if self.timeout <= timedelta(0) or self.timeout > timedelta(hours=1):
            raise ValueError("browser timeout is invalid")
        fields = (self.maximum_pages, self.maximum_redirects, self.maximum_dom_bytes, self.maximum_screenshot_bytes, self.maximum_upload_bytes, self.maximum_download_bytes, self.allowed_download_count)
        if any(not isinstance(value, int) or value < 0 for value in fields):
            raise ValueError("browser limits are invalid")
        if self.maximum_pages < 1:
            raise ValueError("maximum_pages must be positive")
        _required(self.network_policy_ref, "network_policy_ref")


@dataclass(frozen=True, slots=True)
class BrowserArtifactRef:
    artifact_id: str
    version: int
    size_bytes: int
    media_type: str
    classification: str

    def __post_init__(self) -> None:
        _opaque(self.artifact_id, "artifact_id")
        if self.version < 1 or self.size_bytes < 0:
            raise ValueError("artifact reference is invalid")
        _required(self.media_type, "media_type", 128)
        _required(self.classification, "classification", 32)

    def __repr__(self) -> str:
        return f"BrowserArtifactRef(artifact_id={self.artifact_id!r}, version={self.version}, size_bytes={self.size_bytes}, media_type={self.media_type!r}, classification={self.classification!r})"


@dataclass(frozen=True, slots=True)
class AuthorizedFileReference:
    reference_id: str
    context: BrowserOperationContext
    size_bytes: int
    media_type: str
    classification: str

    def __post_init__(self) -> None:
        _opaque(self.reference_id, "file reference")
        if self.size_bytes < 0:
            raise ValueError("file reference size is invalid")
        _required(self.media_type, "media_type", 128)
        _required(self.classification, "classification", 32)

    def __repr__(self) -> str:
        return f"AuthorizedFileReference(reference_id={self.reference_id!r}, size_bytes={self.size_bytes}, media_type={self.media_type!r}, classification={self.classification!r})"


@dataclass(frozen=True, slots=True)
class BrowserCookie:
    name: str
    value_ref: str
    domain: str
    path: str
    secure: bool
    same_site: str
    expires_at: datetime | None

    def __post_init__(self) -> None:
        _required(self.name, "cookie name", 128)
        _opaque(self.value_ref, "secret reference")
        if not self.value_ref.startswith(("secret-ref", "secret:")):
            raise ValueError("cookie values require a secret reference")
        _required(self.domain, "cookie domain", 255)
        if not self.path.startswith("/") or ".." in self.path:
            raise ValueError("cookie path is invalid")
        if self.same_site not in {"STRICT", "LAX", "NONE", "UNSPECIFIED"}:
            raise ValueError("same_site is invalid")
        if self.expires_at is not None:
            _aware(self.expires_at, "expires_at")


@dataclass(frozen=True, slots=True)
class BrowserCookieInput:
    name: str
    value_ref: str
    domain: str
    path: str
    secure: bool
    same_site: str
    expires_at: datetime | None
    allow_inline_value: bool = True

    def __post_init__(self) -> None:
        if not self.allow_inline_value and not self.value_ref.startswith(("secret-ref", "secret:")):
            raise ValueError("cookie values require a secret reference")
        BrowserCookie(self.name, self.value_ref, self.domain, self.path, self.secure, self.same_site, self.expires_at)


@dataclass(frozen=True, slots=True)
class RedactedCookieMetadata:
    name: str
    domain: str
    path: str
    secure: bool
    same_site: str
    expires_at: datetime | None


@dataclass(frozen=True, slots=True)
class BrowserWorkerGrant:
    grant_id: str
    context: BrowserOperationContext
    lease_id: str
    profile_id: str
    session_id: str | None
    capabilities: tuple[GrantCapability, ...]
    expires_at: datetime
    fencing_token: int

    def __post_init__(self) -> None:
        _opaque(self.grant_id, "grant_id")
        _opaque(self.lease_id, "lease_id")
        _opaque(self.profile_id, "profile_id")
        if self.session_id is not None:
            _opaque(self.session_id, "session_id")
        object.__setattr__(self, "capabilities", tuple(GrantCapability(value) for value in self.capabilities))
        _aware(self.expires_at, "expires_at")
        if self.fencing_token < 1:
            raise ValueError("fencing token must be positive")


@dataclass(frozen=True, slots=True)
class BrowserJob:
    job_id: str
    context: BrowserOperationContext
    lease_id: str
    profile_id: str
    profile_version: int
    session_id: str | None
    page_id: str | None
    operation: BrowserOperationKind
    arguments: Mapping[str, object]
    limits: BrowserLimits
    grants: tuple[BrowserWorkerGrant, ...]
    idempotency_key: str
    deadline: datetime
    submitted_at: datetime

    def __post_init__(self) -> None:
        _opaque(self.job_id, "job_id")
        _opaque(self.lease_id, "lease_id")
        _opaque(self.profile_id, "profile_id")
        if self.session_id is not None:
            _opaque(self.session_id, "session_id")
        if self.page_id is not None:
            _opaque(self.page_id, "page_id")
        if self.profile_version < 1:
            raise ValueError("profile version must be positive")
        object.__setattr__(self, "operation", BrowserOperationKind(self.operation))
        object.__setattr__(self, "arguments", MappingProxyType(dict(self.arguments)))
        object.__setattr__(self, "grants", tuple(self.grants))
        _required(self.idempotency_key, "idempotency_key")
        _aware(self.deadline, "deadline")
        _aware(self.submitted_at, "submitted_at")


@dataclass(frozen=True, slots=True)
class BrowserResult:
    kind: str
    session: BrowserSessionSnapshot | None = None
    page: BrowserPageSnapshot | None = None
    artifact_ref: BrowserArtifactRef | None = None
    cookies: tuple[RedactedCookieMetadata, ...] = ()
    page_version: int | None = None
    bytes_count: int = 0


@dataclass(frozen=True, slots=True)
class BrowserJobSucceeded:
    job_id: str
    result: BrowserResult
    effect_state: EffectState = EffectState.APPLIED
    retryability: Retryability = Retryability.NEVER
    usage_operations: int = 1
    usage_bytes: int = 0


@dataclass(frozen=True, slots=True)
class BrowserJobFailed:
    job_id: str
    error_code: BrowserErrorCode
    effect_state: EffectState
    retryability: Retryability
    reason: str = "browser operation failed"

    def __post_init__(self) -> None:
        object.__setattr__(self, "error_code", BrowserErrorCode(self.error_code))
        object.__setattr__(self, "effect_state", EffectState(self.effect_state))
        object.__setattr__(self, "retryability", Retryability(self.retryability))

    def __repr__(self) -> str:
        return f"BrowserJobFailed(job_id=<opaque>, error_code={self.error_code.value!r}, effect_state={self.effect_state.value!r}, retryability={self.retryability.value!r})"


@dataclass(frozen=True, slots=True)
class BrowserJobCancelled:
    job_id: str
    reason: str
    partial_artifact_refs: tuple[BrowserArtifactRef, ...] = ()
    effect_state: EffectState = EffectState.UNKNOWN


@dataclass(frozen=True, slots=True)
class BrowserJobRequest:
    job: BrowserJob
    expected_profile_version: int
    expected_page_version: int | None = None


@dataclass(frozen=True, slots=True)
class BrowserJobAccepted:
    job_id: str
    worker_ref: str


@dataclass(frozen=True, slots=True)
class AuthorizedBrowserJobQuery:
    context: BrowserOperationContext
    job_id: str


@dataclass(frozen=True, slots=True)
class BrowserJobSnapshot:
    job_id: str
    status: str
    outcome: BrowserJobSucceeded | BrowserJobFailed | BrowserJobCancelled | None


@dataclass(frozen=True, slots=True)
class BrowserJobStreamRequest:
    context: BrowserOperationContext
    job_id: str
    after_sequence: int = 0


@dataclass(frozen=True, slots=True)
class BrowserStreamItem:
    job_id: str
    sequence: int
    kind: str
    summary: str


@dataclass(frozen=True, slots=True)
class StreamResult:
    last_sequence: int
    closed: bool = True


@dataclass(frozen=True, slots=True)
class CancelBrowserJob:
    context: BrowserOperationContext
    job_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class CancelBrowserResult:
    accepted: bool
    effect_state: EffectState


@dataclass(frozen=True, slots=True)
class BrowserRejected:
    error_code: str
    reason: str = "browser operation rejected"


@dataclass(frozen=True, slots=True)
class BrowserMutationResult:
    ok: bool
    page: BrowserPageSnapshot | None = None
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class OpenSession:
    marker: bool = True


@dataclass(frozen=True, slots=True)
class CloseSession:
    marker: bool = True


@dataclass(frozen=True, slots=True)
class OpenPage:
    marker: bool = True


@dataclass(frozen=True, slots=True)
class ClosePage:
    marker: bool = True


@dataclass(frozen=True, slots=True)
class Navigate:
    marker: bool = True


@dataclass(frozen=True, slots=True)
class Interact:
    marker: bool = True


@dataclass(frozen=True, slots=True)
class CaptureDom:
    marker: bool = True


@dataclass(frozen=True, slots=True)
class CaptureScreenshot:
    marker: bool = True


@dataclass(frozen=True, slots=True)
class ReadCookies:
    marker: bool = True


@dataclass(frozen=True, slots=True)
class SetCookies:
    marker: bool = True


@dataclass(frozen=True, slots=True)
class Upload:
    marker: bool = True


@dataclass(frozen=True, slots=True)
class Download:
    marker: bool = True


BrowserProfileSnapshot = BrowserProfile
BrowserSession = BrowserSessionSnapshot
BrowserPage = BrowserPageSnapshot
DomSnapshotRef = BrowserArtifactRef
ScreenshotRef = BrowserArtifactRef
UploadRef = BrowserArtifactRef
DownloadRef = BrowserArtifactRef


__all__ = [name for name in globals() if not name.startswith("_")]
