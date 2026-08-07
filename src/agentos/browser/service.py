from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from threading import RLock
from uuid import uuid4

from .models import *
from .ports import BrowserJobSink
from .reference import ReferenceBrowserAdapter
from .security import NetworkPolicy, NetworkPolicyError, same_context, validate_url
from .worker import BrowserWorker


class BrowserService:
    """Domain authority for Browser profiles, live sessions, pages and jobs."""

    def __init__(self, *, resource_manager=None, adapter=None, worker=None, clock=None) -> None:
        self.resource_manager = resource_manager
        self.adapter = adapter or ReferenceBrowserAdapter()
        self.worker = worker or BrowserWorker(self.adapter, clock=clock)
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._lock = RLock()
        self._profiles: dict[str, BrowserProfile] = {}
        self._sessions: dict[str, BrowserSessionSnapshot] = {}
        self._pages: dict[str, BrowserPageSnapshot] = {}
        self._session_limits: dict[str, BrowserLimits] = {}
        self._lease_fences: dict[str, int] = {}
        self._jobs: dict[str, tuple[BrowserJob, BrowserJobSucceeded | BrowserJobFailed | BrowserJobCancelled | None, str]] = {}
        self._idempotency: dict[tuple[tuple[str, ...], str], str] = {}
        self._session_idempotency: dict[tuple[tuple[str, ...], str], str] = {}
        self._events: list[dict[str, object]] = []

    @property
    def events(self) -> tuple[dict[str, object], ...]:
        return tuple(self._events)

    def _now(self) -> datetime:
        return self._clock()

    def create_profile(self, context: BrowserOperationContext, profile_id: str, name: str, policy_ref: str, storage_state_ref: str | None = None) -> BrowserProfile:
        with self._lock:
            if profile_id in self._profiles:
                return self._profiles[profile_id]
            profile = BrowserProfile(profile_id, context.user_id, context.workspace_id, name, policy_ref, storage_state_ref, 1, BrowserProfileStatus.ACTIVE)
            self._profiles[profile_id] = profile
            return profile

    def update_profile(self, context: BrowserOperationContext, profile_id: str, *, expected_version: int, status: BrowserProfileStatus | None = None, name: str | None = None) -> BrowserProfile | BrowserRejected:
        with self._lock:
            profile = self._profiles.get(profile_id)
            if profile is None:
                return BrowserRejected(BrowserErrorCode.NOT_FOUND.value)
            if profile.user_id != context.user_id or profile.workspace_id != context.workspace_id:
                return BrowserRejected(BrowserErrorCode.UNAUTHORIZED.value)
            if profile.version != expected_version:
                return BrowserRejected(BrowserErrorCode.VERSION_CONFLICT.value)
            updated = replace(profile, status=status or profile.status, name=name or profile.name, version=profile.version + 1)
            self._profiles[profile_id] = updated
            return updated

    def _profile_for(self, context: BrowserOperationContext, profile_id: str, expected_version: int) -> BrowserProfile | BrowserRejected:
        profile = self._profiles.get(profile_id)
        if profile is None or profile.user_id != context.user_id or profile.workspace_id != context.workspace_id:
            return BrowserRejected(BrowserErrorCode.UNAUTHORIZED.value)
        if profile.version != expected_version:
            return BrowserRejected(BrowserErrorCode.VERSION_CONFLICT.value)
        if profile.status is BrowserProfileStatus.LOCKED:
            return BrowserRejected(BrowserErrorCode.PROFILE_LOCKED.value)
        if profile.status is BrowserProfileStatus.DISABLED:
            return BrowserRejected(BrowserErrorCode.PROFILE_DISABLED.value)
        return profile

    def _acquire_browser_lease(self, context: BrowserOperationContext, limits: BrowserLimits) -> tuple[str, int] | BrowserRejected:
        if self.resource_manager is None:
            return "browser-lease-" + uuid4().hex, 1
        from agentos.resources.models import ResourceBudget, ResourceCapability, ResourceLeaseRequest, ResourceOperationContext, ResourceType
        resource_context = ResourceOperationContext(*context.scope_key())
        lease = self.resource_manager.acquire(ResourceLeaseRequest("browser-request-" + uuid4().hex, resource_context, ResourceType.BROWSER, (ResourceCapability.BROWSER_SESSION,), (ResourceCapability.BROWSER_SESSION, ResourceCapability.INSPECT), ResourceBudget(100, limits.maximum_download_bytes, limits.timeout), limits.timeout, "browser-idem-" + uuid4().hex))
        if not hasattr(lease, "lease_id"):
            return BrowserRejected(getattr(getattr(lease, "code", None), "value", BrowserErrorCode.UNAUTHORIZED.value))
        return lease.lease_id, lease.fencing_token

    def open_session(self, context: BrowserOperationContext, profile_id: str, expected_profile_version: int, limits: BrowserLimits, idempotency_key: str | None = None) -> BrowserSessionSnapshot | BrowserRejected:
        with self._lock:
            if idempotency_key is not None:
                existing_id = self._session_idempotency.get((context.scope_key(), idempotency_key))
                if existing_id is not None:
                    return self._sessions[existing_id]
            profile = self._profile_for(context, profile_id, expected_profile_version)
            if isinstance(profile, BrowserRejected):
                return profile
            acquired = self._acquire_browser_lease(context, limits)
            if isinstance(acquired, BrowserRejected):
                return acquired
            lease_id, fence = acquired
            now = self._now()
            session = BrowserSessionSnapshot("session-" + uuid4().hex, profile.profile_id, context, lease_id, "browser-worker-" + uuid4().hex[:8], BrowserSessionStatus.READY, (), 1, now, now, now + limits.timeout, profile)
            self._sessions[session.session_id] = session
            self._session_limits[session.session_id] = limits
            self._lease_fences[session.lease_id] = fence
            if idempotency_key is not None:
                self._session_idempotency[(context.scope_key(), idempotency_key)] = session.session_id
            self._event("BrowserOpened", session)
            return session

    def open_page(self, context: BrowserOperationContext, session_id: str, expected_session_version: int, limits: BrowserLimits) -> BrowserPageSnapshot | BrowserRejected:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return BrowserRejected(BrowserErrorCode.NOT_FOUND.value)
            if not same_context(session.context, context):
                return BrowserRejected(BrowserErrorCode.UNAUTHORIZED.value)
            lease_error = self._lease_error(session, context)
            if lease_error is not None:
                return lease_error
            if session.version != expected_session_version:
                return BrowserRejected(BrowserErrorCode.VERSION_CONFLICT.value)
            if session.status in (BrowserSessionStatus.CLOSED, BrowserSessionStatus.FAILED, BrowserSessionStatus.CANCELLED):
                return BrowserRejected(BrowserErrorCode.SESSION_CLOSED.value)
            maximum_pages = self._session_limits.get(session_id, limits).maximum_pages
            if len(session.page_ids) >= maximum_pages:
                return BrowserRejected(BrowserErrorCode.PAGE_QUOTA_EXCEEDED.value)
            now = self._now()
            page = BrowserPageSnapshot("page-" + uuid4().hex, session_id, "about:blank", None, BrowserPageStatus.READY, 1, now, now)
            self._pages[page.page_id] = page
            self._sessions[session_id] = replace(session, page_ids=session.page_ids + (page.page_id,), updated_at=now)
            self._event("BrowserPageOpened", page)
            return page

    def inspect_page(self, context: BrowserOperationContext, page_id: str) -> BrowserPageSnapshot | BrowserRejected:
        with self._lock:
            page = self._pages.get(page_id)
            if page is None or page.session_id not in self._sessions:
                return BrowserRejected(BrowserErrorCode.NOT_FOUND.value)
            session = self._sessions[page.session_id]
            if not same_context(session.context, context):
                return BrowserRejected(BrowserErrorCode.UNAUTHORIZED.value)
            lease_error = self._lease_error(session, context)
            if lease_error is not None:
                return lease_error
            if page.status is BrowserPageStatus.CLOSED:
                return BrowserRejected(BrowserErrorCode.PAGE_CLOSED.value)
            return page

    def navigate(self, context: BrowserOperationContext, page_id: str, expected_page_version: int, url: str) -> BrowserMutationResult:
        with self._lock:
            current = self.inspect_page(context, page_id)
            if isinstance(current, BrowserRejected):
                return BrowserMutationResult(False, error_code=current.error_code)
            if current.version != expected_page_version:
                return BrowserMutationResult(False, error_code=BrowserErrorCode.VERSION_CONFLICT.value)
            try:
                safe_url = validate_url(url, NetworkPolicy())
            except NetworkPolicyError:
                return BrowserMutationResult(False, error_code=BrowserErrorCode.POLICY_DENIED.value)
            now = self._now()
            updated = replace(current, url=safe_url, title="Reference page", status=BrowserPageStatus.READY, version=current.version + 1, updated_at=now)
            self._pages[page_id] = updated
            self._event("BrowserNavigationFinished", updated)
            return BrowserMutationResult(True, updated)

    def close_session(self, context: BrowserOperationContext, session_id: str, lease_id: str, reason: str) -> BrowserSessionSnapshot | BrowserRejected:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return BrowserRejected(BrowserErrorCode.NOT_FOUND.value)
            if not same_context(session.context, context) or session.lease_id != lease_id:
                return BrowserRejected(BrowserErrorCode.UNAUTHORIZED.value)
            if session.status is BrowserSessionStatus.CLOSED:
                return session
            if self.resource_manager is not None:
                from agentos.resources.models import ReleaseResourceLease, ResourceOperationContext
                released = self.resource_manager.release(ReleaseResourceLease("browser-release-" + uuid4().hex, session.lease_id, ResourceOperationContext(*context.scope_key()), self._lease_fences.get(session.lease_id, 1), reason, "browser-release-" + session.lease_id))
                if hasattr(released, "code") or getattr(getattr(released, "effect_state", None), "value", "APPLIED") != "APPLIED":
                    return BrowserRejected(BrowserErrorCode.RECOVERY_REQUIRED.value)
            now = self._now()
            for page_id in session.page_ids:
                if page_id in self._pages:
                    self._pages[page_id] = replace(self._pages[page_id], status=BrowserPageStatus.CLOSED, version=self._pages[page_id].version + 1, updated_at=now)
                    self._event("BrowserPageClosed", self._pages[page_id])
            self.adapter.cleanup(session_id)
            closed = replace(session, status=BrowserSessionStatus.CLOSED, version=session.version + 1, updated_at=now)
            self._sessions[session_id] = closed
            self._event("BrowserClosed", closed)
            return closed

    def make_request(self, context: BrowserOperationContext, session: BrowserSessionSnapshot, operation: BrowserOperationKind, arguments: dict[str, object], idempotency_key: str, *, page: BrowserPageSnapshot | None = None) -> BrowserJobRequest:
        now = self._now()
        capability = {BrowserOperationKind.NAVIGATE: GrantCapability.NAVIGATE, BrowserOperationKind.CAPTURE_DOM: GrantCapability.READ_DOM, BrowserOperationKind.CAPTURE_SCREENSHOT: GrantCapability.SCREENSHOT, BrowserOperationKind.DOWNLOAD: GrantCapability.DOWNLOAD, BrowserOperationKind.UPLOAD: GrantCapability.UPLOAD, BrowserOperationKind.READ_COOKIES: GrantCapability.READ_COOKIES}.get(operation, GrantCapability.INTERACT)
        grant = BrowserWorkerGrant("grant-" + uuid4().hex, context, session.lease_id, session.profile_id, session.session_id, (capability,), session.expires_at, self._lease_fences.get(session.lease_id, 1))
        job = BrowserJob("job-" + uuid4().hex, context, session.lease_id, session.profile_id, session.profile_snapshot.version if session.profile_snapshot else 1, session.session_id, page.page_id if page else None, operation, arguments, self._session_limits[session.session_id], (grant,), idempotency_key, min(session.expires_at, now + self._session_limits[session.session_id].timeout), now)
        return BrowserJobRequest(job, job.profile_version, page.version if page else None)

    def submit(self, request: BrowserJobRequest) -> BrowserJobAccepted | BrowserRejected:
        with self._lock:
            job = request.job
            key = (job.context.scope_key(), job.idempotency_key)
            prior = self._idempotency.get(key)
            if prior is not None:
                return BrowserJobAccepted(prior, "browser-worker")
            if request.expected_profile_version != job.profile_version:
                return BrowserRejected(BrowserErrorCode.VERSION_CONFLICT.value)
            outcome = self.worker.execute(job)
            status = "CANCELLED" if isinstance(outcome, BrowserJobCancelled) else "FAILED" if isinstance(outcome, BrowserJobFailed) else "SUCCEEDED"
            self._jobs[job.job_id] = (job, outcome, status)
            self._idempotency[key] = job.job_id
            self._events.append({"event_type": "BrowserJobFailed" if isinstance(outcome, BrowserJobFailed) else "BrowserNavigationFinished", "job_id": job.job_id, "sequence": len(self._events) + 1})
            return BrowserJobAccepted(job.job_id, "browser-worker")

    def inspect(self, query: AuthorizedBrowserJobQuery) -> BrowserJobSnapshot | BrowserRejected:
        with self._lock:
            record = self._jobs.get(query.job_id)
            if record is None:
                return BrowserRejected(BrowserErrorCode.NOT_FOUND.value)
            job, outcome, status = record
            if not same_context(job.context, query.context):
                return BrowserRejected(BrowserErrorCode.UNAUTHORIZED.value)
            return BrowserJobSnapshot(job.job_id, status, outcome)

    def stream(self, request: BrowserJobStreamRequest, sink: BrowserJobSink) -> StreamResult | BrowserRejected:
        snapshot = self.inspect(AuthorizedBrowserJobQuery(request.context, request.job_id))
        if isinstance(snapshot, BrowserRejected):
            return snapshot
        item = BrowserStreamItem(request.job_id, max(request.after_sequence + 1, 1), "TERMINAL", snapshot.status)
        sink.emit(item)
        return StreamResult(item.sequence, True)

    def request_cancel(self, request: CancelBrowserJob) -> CancelBrowserResult | BrowserRejected:
        with self._lock:
            record = self._jobs.get(request.job_id)
            if record is None:
                return BrowserRejected(BrowserErrorCode.NOT_FOUND.value)
            job, outcome, status = record
            if not same_context(job.context, request.context):
                return BrowserRejected(BrowserErrorCode.UNAUTHORIZED.value)
            if not isinstance(outcome, BrowserJobCancelled):
                self._jobs[request.job_id] = (job, BrowserJobCancelled(job.job_id, request.reason), "CANCELLED")
            return CancelBrowserResult(True, EffectState.APPLIED)

    def _event(self, event_type: str, value: object) -> None:
        item = {"event_type": event_type}
        if isinstance(value, BrowserSessionSnapshot):
            item.update({"session_id": value.session_id, "status": value.status.value, "version": value.version})
        elif isinstance(value, BrowserPageSnapshot):
            item.update({"page_id": value.page_id, "session_id": value.session_id, "status": value.status.value, "version": value.version})
        self._events.append(item)

    def _lease_error(self, session: BrowserSessionSnapshot, context: BrowserOperationContext) -> BrowserRejected | None:
        if session.expires_at is not None and self._now() >= session.expires_at:
            return BrowserRejected(BrowserErrorCode.LEASE_EXPIRED.value)
        if self.resource_manager is None:
            return None
        from agentos.resources.models import ResourceOperationContext
        lease = self.resource_manager.inspect(context=ResourceOperationContext(*context.scope_key()), lease_id=session.lease_id)
        if hasattr(lease, "lease_id"):
            if getattr(getattr(lease, "state", None), "value", "LEASED") != "LEASED":
                return BrowserRejected(BrowserErrorCode.LEASE_EXPIRED.value)
            return None
        code = getattr(getattr(lease, "code", None), "value", BrowserErrorCode.LEASE_EXPIRED.value)
        return BrowserRejected(code)


__all__ = ["BrowserService"]
