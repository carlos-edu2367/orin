from __future__ import annotations

from datetime import datetime, timedelta, timezone
from dataclasses import replace

from agentos.browser.models import BrowserJob, BrowserLimits, BrowserOperationContext, BrowserOperationKind, BrowserWorkerGrant, GrantCapability, BrowserJobFailed, BrowserJobSucceeded, EffectState
from agentos.browser.reference import ReferenceBrowserAdapter
from agentos.browser.worker import BrowserWorker
from agentos.browser.integration import InMemoryBrowserArtifactOutput, InMemoryBrowserInputResolver


def ctx() -> BrowserOperationContext:
    return BrowserOperationContext("u", "ws", "a", "e", "c", "browser.navigate", "agent:a")


def job(grants=(GrantCapability.NAVIGATE,)) -> BrowserJob:
    now = datetime.now(timezone.utc)
    grant = BrowserWorkerGrant("g", ctx(), "lease-1", "profile-1", None, tuple(grants), now + timedelta(minutes=1), 1)
    return BrowserJob("j", ctx(), "lease-1", "profile-1", 1, None, None, BrowserOperationKind.NAVIGATE, {"url": "https://example.com"}, BrowserLimits(timedelta(seconds=5), 1, 1, 100, 100, 100, 100, 0, "network:strict"), (grant,), "idem", now + timedelta(seconds=5), now)


def test_worker_rejects_missing_grant_before_engine_execution() -> None:
    worker = BrowserWorker(ReferenceBrowserAdapter())
    outcome = worker.execute(job((GrantCapability.READ_DOM,)))
    assert isinstance(outcome, BrowserJobFailed)
    assert outcome.effect_state is EffectState.NOT_APPLIED
    assert worker.adapter.executed_operations == []


def test_web_content_is_data_only() -> None:
    worker = BrowserWorker(ReferenceBrowserAdapter())
    outcome = worker.execute(job())
    assert outcome.result is not None
    assert "grant" not in repr(outcome.result).lower()


def test_dangerous_interaction_requires_granular_capability() -> None:
    worker = BrowserWorker(ReferenceBrowserAdapter())
    outcome = worker.execute(replace(job((GrantCapability.INTERACT,)), arguments={"action": "evaluate"}))
    assert isinstance(outcome, BrowserJobFailed)
    assert outcome.error_code.value == "UNAUTHORIZED"


def test_worker_materializes_browser_artifact_through_output_port() -> None:
    context = ctx()
    now = datetime.now(timezone.utc)
    grant = BrowserWorkerGrant("g2", context, "lease-1", "profile-1", "session-1", (GrantCapability.READ_DOM,), now + timedelta(minutes=1), 1)
    job_value = BrowserJob("dom-job", context, "lease-1", "profile-1", 1, "session-1", "page-1", BrowserOperationKind.CAPTURE_DOM, {}, BrowserLimits(timedelta(seconds=5), 1, 1, 100, 100, 100, 100, 0, "network:strict"), (grant,), "dom-idem", now + timedelta(seconds=5), now)
    output = InMemoryBrowserArtifactOutput(maximum_bytes=100)
    outcome = BrowserWorker(ReferenceBrowserAdapter(), artifact_output=output).execute(job_value)
    assert isinstance(outcome, BrowserJobSucceeded)
    assert outcome.result.artifact_ref is not None
    assert output.read(outcome.result.artifact_ref)
