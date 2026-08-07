from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from agentos.browser.models import (
    BrowserArtifactRef,
    BrowserCookie,
    BrowserCookieInput,
    BrowserLimits,
    BrowserOperationContext,
    BrowserOperationKind,
    BrowserPageStatus,
    BrowserProfile,
    BrowserProfileStatus,
    BrowserSessionStatus,
    BrowserJob,
    BrowserProfileSnapshot,
    DomSnapshotRef,
    ScreenshotRef,
    UploadRef,
    DownloadRef,
    OpenSession,
    CloseSession,
    OpenPage,
    ClosePage,
    Navigate,
    Interact,
    CaptureDom,
    CaptureScreenshot,
    ReadCookies,
    SetCookies,
    Upload,
    Download,
)


def context(**changes: object) -> BrowserOperationContext:
    values = {
        "user_id": "user-1",
        "workspace_id": "workspace-1",
        "agent_id": "agent-1",
        "execution_id": "execution-1",
        "correlation_id": "correlation-1",
        "purpose": "browser.navigate",
        "actor": "agent:agent-1",
    }
    values.update(changes)
    return BrowserOperationContext(**values)


def test_context_is_complete_and_redacted() -> None:
    operation_context = context()
    assert operation_context.binding_key() == ("user-1", "workspace-1", "agent-1", "execution-1", "browser.navigate", "agent:agent-1")
    assert "browser.navigate" not in repr(operation_context)
    with pytest.raises(ValueError):
        context(actor="")
    with pytest.raises(ValueError):
        context(workspace_id="../escape")


def test_public_models_are_immutable_and_do_not_hold_native_or_content_data() -> None:
    profile = BrowserProfile("profile-1", "user-1", "workspace-1", "safe", "policy:strict", None, 1, BrowserProfileStatus.ACTIVE)
    assert profile.status is BrowserProfileStatus.ACTIVE
    with pytest.raises(Exception):
        profile.name = "changed"  # type: ignore[misc]
    artifact = BrowserArtifactRef("artifact-1", 1, 12, "text/html", "INTERNAL")
    assert "artifact-1" in repr(artifact)
    assert "native" not in repr(artifact).lower()
    assert BrowserProfileSnapshot is BrowserProfile
    assert DomSnapshotRef is BrowserArtifactRef
    assert ScreenshotRef is BrowserArtifactRef
    assert UploadRef is BrowserArtifactRef
    assert DownloadRef is BrowserArtifactRef


def test_limits_and_operations_are_bounded() -> None:
    limits = BrowserLimits(
        timeout=timedelta(seconds=20),
        maximum_pages=2,
        maximum_redirects=3,
        maximum_dom_bytes=1024,
        maximum_screenshot_bytes=2048,
        maximum_upload_bytes=4096,
        maximum_download_bytes=8192,
        allowed_download_count=1,
        network_policy_ref="network:strict",
    )
    assert limits.maximum_pages == 2
    assert BrowserSessionStatus.READY.value == "READY"
    assert BrowserPageStatus.NAVIGATING.value == "NAVIGATING"
    with pytest.raises(ValueError):
        BrowserLimits(timedelta(hours=2), 1, 1, 1, 1, 1, 1, 1, "network:strict")


def test_cookie_input_requires_secret_reference_not_value() -> None:
    cookie = BrowserCookieInput("sid", "secret-ref-1", "example.com", "/", True, "LAX", None)
    assert cookie.value_ref == "secret-ref-1"
    with pytest.raises(ValueError):
        BrowserCookieInput("sid", "inline-secret-value", "example.com", "/", True, "LAX", None, allow_inline_value=False)
    with pytest.raises(ValueError):
        BrowserCookie("sid", "inline-secret-value", "example.com", "/", True, "LAX", None)


def test_job_contains_context_lease_snapshot_operation_and_deadline() -> None:
    now = datetime.now(timezone.utc)
    job = BrowserJob(
        "job-1", context(), "lease-1", "profile-1", 1, None, None,
        BrowserOperationKind.OPEN_SESSION, {"profile_id": "profile-1"},
        BrowserLimits(timedelta(seconds=10), 1, 1, 100, 100, 100, 100, 0, "network:strict"),
        (), "idem-1", now + timedelta(seconds=10), now,
    )
    assert job.operation is BrowserOperationKind.OPEN_SESSION
    assert job.deadline > now
    with pytest.raises(TypeError):
        job.arguments["profile_id"] = "changed"  # type: ignore[index]
    assert all(cls() is not None for cls in (OpenSession, CloseSession, OpenPage, ClosePage, Navigate, Interact, CaptureDom, CaptureScreenshot, ReadCookies, SetCookies, Upload, Download))
