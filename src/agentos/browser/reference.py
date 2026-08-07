from __future__ import annotations

from datetime import datetime, timezone

from .models import *
from .security import NetworkPolicy, NetworkPolicyError, sanitize_url, validate_url


class ReferenceBrowserAdapter:
    """Deterministic engine used to prove Browser contracts without a browser process."""

    def __init__(self, *, unknown_operations: set[BrowserOperationKind] | None = None, cookies: tuple[BrowserCookie, ...] = ()) -> None:
        self.unknown_operations = set(unknown_operations or ())
        self.cookies = tuple(cookies)
        self.executed_operations: list[BrowserOperationKind] = []
        self._closed_sessions: set[str] = set()

    def execute(self, job: BrowserJob) -> BrowserResult | BrowserJobFailed:
        self.executed_operations.append(job.operation)
        if job.operation in self.unknown_operations:
            return BrowserJobFailed(job.job_id, BrowserErrorCode.UNKNOWN, EffectState.UNKNOWN, Retryability.AFTER_RECONCILIATION, "external effect could not be observed")
        if job.operation is BrowserOperationKind.NAVIGATE:
            try:
                url = validate_url(str(job.arguments.get("url", "")), NetworkPolicy())
            except NetworkPolicyError:
                return BrowserJobFailed(job.job_id, BrowserErrorCode.POLICY_DENIED, EffectState.NOT_APPLIED, Retryability.NEVER)
            page = BrowserPageSnapshot(job.page_id or "page-reference", job.session_id or "session-reference", url, "Reference page", BrowserPageStatus.READY, 1, job.submitted_at, datetime.now(timezone.utc))
            return BrowserResult("PAGE", page=page, page_version=page.version)
        if job.operation is BrowserOperationKind.CAPTURE_DOM:
            return BrowserResult("DOM", artifact_ref=BrowserArtifactRef("artifact-dom", 1, 32, "text/html", "INTERNAL"), bytes_count=32)
        if job.operation is BrowserOperationKind.CAPTURE_SCREENSHOT:
            return BrowserResult("SCREENSHOT", artifact_ref=BrowserArtifactRef("artifact-screenshot", 1, 16, "image/png", "INTERNAL"), bytes_count=16)
        if job.operation is BrowserOperationKind.DOWNLOAD:
            return BrowserResult("DOWNLOAD", artifact_ref=BrowserArtifactRef("artifact-download", 1, 4, "application/octet-stream", "INTERNAL"), bytes_count=4)
        if job.operation is BrowserOperationKind.READ_COOKIES:
            metadata = tuple(RedactedCookieMetadata(cookie.name, cookie.domain, cookie.path, cookie.secure, cookie.same_site, cookie.expires_at) for cookie in self.cookies)
            return BrowserResult("COOKIES", cookies=metadata)
        if job.operation in (BrowserOperationKind.OPEN_SESSION, BrowserOperationKind.OPEN_PAGE):
            return BrowserResult("OPENED")
        if job.operation in (BrowserOperationKind.CLOSE_SESSION, BrowserOperationKind.CLOSE_PAGE):
            return BrowserResult("CLOSED")
        if job.operation is BrowserOperationKind.UPLOAD:
            return BrowserResult("UPLOAD", page_version=1, bytes_count=int(job.arguments.get("size_bytes", 0)))
        return BrowserResult("INTERACTION", page_version=1)

    def cleanup(self, session_id: str) -> bool:
        self._closed_sessions.add(session_id)
        return True

    def artifact_bytes(self, kind: str) -> bytes:
        return {"DOM": b"<html><body>reference</body></html>", "SCREENSHOT": b"reference-png", "DOWNLOAD": b"data"}.get(kind, b"")


__all__ = ["ReferenceBrowserAdapter"]
