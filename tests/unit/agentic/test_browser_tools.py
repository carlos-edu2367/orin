from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agentos.agentic.browser_tools import ConversationBrowser, MemoryArtifactOutput
from agentos.browser.models import (
    BrowserArtifactRef,
    BrowserErrorCode,
    BrowserJobFailed,
    BrowserOperationKind,
    BrowserPageSnapshot,
    BrowserPageStatus,
    BrowserResult,
    EffectState,
    Retryability,
)


class FakeAdapter:
    """Stands in for PlaywrightBrowserAdapter without launching a browser."""

    def __init__(self, *, fail: bool = False) -> None:
        self.artifact_output = None
        self.jobs: list[BrowserOperationKind] = []
        self._fail = fail

    def execute(self, job):
        self.jobs.append(job.operation)
        if self._fail:
            return BrowserJobFailed(job.job_id, BrowserErrorCode.POLICY_DENIED, EffectState.NOT_APPLIED, Retryability.NEVER)
        if job.operation is BrowserOperationKind.NAVIGATE:
            snapshot = BrowserPageSnapshot(job.page_id, job.session_id, str(job.arguments["url"]), "Title", BrowserPageStatus.READY, 1, job.submitted_at, datetime.now(UTC))
            return BrowserResult("PAGE", page=snapshot, page_version=1)
        data = b"<html><body>rendered content</body></html>"
        grant = job.grants[0]
        sink = self.artifact_output.begin(job.operation.value, job.context, grant, len(data))
        sink.write(data)
        return BrowserResult("DOM", bytes_count=len(data), artifact_ref=self.artifact_output.commit(sink, "text/html"))

    def cleanup(self, session_id: str) -> bool:
        return True


def _browser(**kwargs) -> ConversationBrowser:
    return ConversationBrowser(FakeAdapter(**kwargs), user_id="user-1", workspace_id="workspace-1", agent_id="agent-1", execution_id="execution-1")


def test_render_navigates_then_captures_the_dom() -> None:
    browser = _browser()

    html = browser.render("https://example.test/page")

    assert "rendered content" in html
    assert browser.adapter.jobs == [BrowserOperationKind.NAVIGATE, BrowserOperationKind.CAPTURE_DOM]


def test_render_reports_a_refused_navigation_instead_of_returning_empty_html() -> None:
    with pytest.raises(RuntimeError):
        _browser(fail=True).render("https://example.test/page")


def test_the_artifact_output_returns_the_bytes_it_was_given() -> None:
    output = MemoryArtifactOutput()
    sink = output.begin("CAPTURE_DOM", object(), object(), 32)
    sink.write(b"abc")
    reference = output.commit(sink, "text/html")

    assert isinstance(reference, BrowserArtifactRef)
    assert output.data == b"abc"
    assert reference.size_bytes == 3
