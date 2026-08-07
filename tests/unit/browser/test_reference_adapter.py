from __future__ import annotations

from datetime import datetime, timedelta, timezone

from agentos.browser.models import *
from agentos.browser.reference import ReferenceBrowserAdapter
from agentos.browser.worker import BrowserWorker


def ctx() -> BrowserOperationContext:
    return BrowserOperationContext("u", "ws", "a", "e", "c", "browser.navigate", "agent:a")


def make_job(operation: BrowserOperationKind, args: dict[str, object], caps: tuple[GrantCapability, ...]) -> BrowserJob:
    now = datetime.now(timezone.utc)
    grant = BrowserWorkerGrant("g", ctx(), "lease", "profile", None, caps, now + timedelta(minutes=1), 1)
    return BrowserJob("job-" + operation.value, ctx(), "lease", "profile", 1, "session", "page", operation, args, BrowserLimits(timedelta(seconds=10), 2, 2, 1024, 1024, 1024, 1024, 1, "network:strict"), (grant,), "idem-" + operation.value, now + timedelta(seconds=10), now)


def test_reference_adapter_navigates_and_captures_bounded_artifacts() -> None:
    worker = BrowserWorker(ReferenceBrowserAdapter())
    navigate = worker.execute(make_job(BrowserOperationKind.NAVIGATE, {"url": "https://example.com"}, (GrantCapability.NAVIGATE,)))
    assert isinstance(navigate, BrowserJobSucceeded)
    dom = worker.execute(make_job(BrowserOperationKind.CAPTURE_DOM, {}, (GrantCapability.READ_DOM,)))
    assert isinstance(dom, BrowserJobSucceeded)
    assert dom.result.kind == "DOM"


def test_external_effect_can_return_unknown_without_false_success() -> None:
    worker = BrowserWorker(ReferenceBrowserAdapter(unknown_operations={BrowserOperationKind.DOWNLOAD}))
    outcome = worker.execute(make_job(BrowserOperationKind.DOWNLOAD, {"url": "https://example.com/file"}, (GrantCapability.DOWNLOAD,)))
    assert isinstance(outcome, BrowserJobFailed)
    assert outcome.effect_state is EffectState.UNKNOWN
    assert outcome.retryability is Retryability.AFTER_RECONCILIATION


def test_artifact_limits_are_effective() -> None:
    worker = BrowserWorker(ReferenceBrowserAdapter())
    job = make_job(BrowserOperationKind.CAPTURE_DOM, {}, (GrantCapability.READ_DOM,))
    job = BrowserJob(job.job_id, job.context, job.lease_id, job.profile_id, job.profile_version, job.session_id, job.page_id, job.operation, job.arguments, BrowserLimits(job.limits.timeout, 2, 2, 1, 1, 1, 1, 1, "network:strict"), job.grants, job.idempotency_key, job.deadline, job.submitted_at)
    outcome = worker.execute(job)
    assert isinstance(outcome, BrowserJobFailed)
    assert outcome.error_code is BrowserErrorCode.LIMIT_EXCEEDED
