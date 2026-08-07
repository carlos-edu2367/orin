from __future__ import annotations

from datetime import datetime, timezone
from dataclasses import replace

from .models import *
from .ports import BrowserAdapter, BrowserArtifactOutput, BrowserInputResolver, SecretReferencePort
from .security import validate_grants


_CAPABILITY_BY_OPERATION = {
    BrowserOperationKind.OPEN_SESSION: GrantCapability.OPEN_SESSION,
    BrowserOperationKind.CLOSE_SESSION: GrantCapability.CLOSE_SESSION,
    BrowserOperationKind.OPEN_PAGE: GrantCapability.OPEN_PAGE,
    BrowserOperationKind.CLOSE_PAGE: GrantCapability.CLOSE_PAGE,
    BrowserOperationKind.NAVIGATE: GrantCapability.NAVIGATE,
    BrowserOperationKind.INTERACT: GrantCapability.INTERACT,
    BrowserOperationKind.CAPTURE_DOM: GrantCapability.READ_DOM,
    BrowserOperationKind.CAPTURE_SCREENSHOT: GrantCapability.SCREENSHOT,
    BrowserOperationKind.READ_COOKIES: GrantCapability.READ_COOKIES,
    BrowserOperationKind.SET_COOKIES: GrantCapability.SET_COOKIES,
    BrowserOperationKind.UPLOAD: GrantCapability.UPLOAD,
    BrowserOperationKind.DOWNLOAD: GrantCapability.DOWNLOAD,
}


class BrowserWorker:
    """Dedicated worker boundary; it receives no domain storage or ownership query port."""

    def __init__(self, adapter: BrowserAdapter, *, artifact_output: BrowserArtifactOutput | None = None, input_resolver: BrowserInputResolver | None = None, secret_port: SecretReferencePort | None = None, clock=None) -> None:
        self.adapter = adapter
        self.artifact_output = artifact_output
        self.input_resolver = input_resolver
        self.secret_port = secret_port
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def execute(self, job: BrowserJob) -> BrowserJobSucceeded | BrowserJobFailed | BrowserJobCancelled:
        now = self._clock()
        if now >= job.deadline:
            return BrowserJobFailed(job.job_id, BrowserErrorCode.INVALID_REQUEST, EffectState.NOT_APPLIED, Retryability.NEVER, "deadline exceeded")
        capability = _CAPABILITY_BY_OPERATION[job.operation]
        if job.operation is BrowserOperationKind.INTERACT:
            dangerous = {"evaluate": GrantCapability.EVALUATE, "javascript": GrantCapability.EVALUATE, "clipboard": GrantCapability.CLIPBOARD, "camera": GrantCapability.CAMERA, "geolocation": GrantCapability.GEOLOCATION}
            requested_action = str(job.arguments.get("action", "")).lower()
            capability = dangerous.get(requested_action, capability)
        try:
            validate_grants(job.grants, job.context, job.lease_id, capability, now=now)
        except ValueError:
            return BrowserJobFailed(job.job_id, BrowserErrorCode.UNAUTHORIZED, EffectState.NOT_APPLIED, Retryability.NEVER)
        if job.operation is BrowserOperationKind.UPLOAD and self.input_resolver is not None:
            try:
                source = self.input_resolver.open(str(job.arguments.get("input_ref", "")), validate_grants(job.grants, job.context, job.lease_id, GrantCapability.UPLOAD, now=now))
                data = source.read(job.limits.maximum_upload_bytes + 1)
                if len(data) > job.limits.maximum_upload_bytes:
                    return BrowserJobFailed(job.job_id, BrowserErrorCode.LIMIT_EXCEEDED, EffectState.NOT_APPLIED, Retryability.NEVER)
                job = replace(job, arguments={**job.arguments, "size_bytes": len(data)})
            except ValueError:
                return BrowserJobFailed(job.job_id, BrowserErrorCode.UNAUTHORIZED, EffectState.NOT_APPLIED, Retryability.NEVER)
        result = self.adapter.execute(job)
        if isinstance(result, BrowserJobFailed):
            return result
        limit = {
            BrowserOperationKind.CAPTURE_DOM: job.limits.maximum_dom_bytes,
            BrowserOperationKind.CAPTURE_SCREENSHOT: job.limits.maximum_screenshot_bytes,
            BrowserOperationKind.UPLOAD: job.limits.maximum_upload_bytes,
            BrowserOperationKind.DOWNLOAD: job.limits.maximum_download_bytes,
        }.get(job.operation)
        if limit is not None and result.bytes_count > limit:
            return BrowserJobFailed(job.job_id, BrowserErrorCode.LIMIT_EXCEEDED, EffectState.NOT_APPLIED, Retryability.NEVER)
        if job.operation is BrowserOperationKind.DOWNLOAD and int(job.arguments.get("download_count", 1)) > job.limits.allowed_download_count:
            return BrowserJobFailed(job.job_id, BrowserErrorCode.LIMIT_EXCEEDED, EffectState.NOT_APPLIED, Retryability.NEVER)
        if self.artifact_output is not None and result.artifact_ref is not None and hasattr(self.adapter, "artifact_bytes"):
            sink = self.artifact_output.begin(job.operation.value, job.context, job.grants[0], limit if limit is not None else result.bytes_count)
            try:
                data = self.adapter.artifact_bytes(result.kind)
                if len(data) > (limit if limit is not None else result.bytes_count):
                    self.artifact_output.abort(sink)
                    return BrowserJobFailed(job.job_id, BrowserErrorCode.LIMIT_EXCEEDED, EffectState.NOT_APPLIED, Retryability.NEVER)
                sink.write(data)
                artifact_ref = self.artifact_output.commit(sink, "text/html" if result.kind == "DOM" else "image/png" if result.kind == "SCREENSHOT" else "application/octet-stream")
                result = replace(result, artifact_ref=artifact_ref, bytes_count=len(data))
            except Exception:
                self.artifact_output.abort(sink)
                return BrowserJobFailed(job.job_id, BrowserErrorCode.UNKNOWN, EffectState.UNKNOWN, Retryability.AFTER_RECONCILIATION)
        return BrowserJobSucceeded(job.job_id, result, EffectState.APPLIED, Retryability.NEVER, 1, result.bytes_count)


__all__ = ["BrowserWorker"]
