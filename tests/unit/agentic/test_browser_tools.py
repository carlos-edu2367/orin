from __future__ import annotations

from datetime import UTC, datetime
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
import time

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


class ActiveAdapter(FakeAdapter):
    def __init__(self) -> None:
        super().__init__()
        self._active = 0
        self._active_lock = Lock()
        self.maximum_active = 0

    def execute(self, job):
        with self._active_lock:
            self._active += 1
            self.maximum_active = max(self.maximum_active, self._active)
        try:
            time.sleep(0.01)
            return super().execute(job)
        finally:
            with self._active_lock:
                self._active -= 1


class InvalidResultAdapter(FakeAdapter):
    def __init__(self, *, navigation_kind: str = "PAGE", missing_page: bool = False, missing_artifact: bool = False) -> None:
        super().__init__()
        self.navigation_kind = navigation_kind
        self.missing_page = missing_page
        self.missing_artifact = missing_artifact

    def execute(self, job):
        if job.operation is BrowserOperationKind.NAVIGATE:
            self.jobs.append(job.operation)
            snapshot = BrowserPageSnapshot(job.page_id, job.session_id, str(job.arguments["url"]), "Title", BrowserPageStatus.READY, 1, job.submitted_at, datetime.now(UTC))
            return BrowserResult(self.navigation_kind, page=None if self.missing_page else snapshot, page_version=1)
        if self.missing_artifact:
            self.jobs.append(job.operation)
            return BrowserResult("DOM", bytes_count=0)
        return super().execute(job)


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


def test_render_serializes_the_shared_page_for_concurrent_calls() -> None:
    adapter = ActiveAdapter()
    browser = ConversationBrowser(adapter, user_id="user-1", workspace_id="workspace-1", agent_id="agent-1", execution_id="execution-1")

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(browser.render, ("https://example.test/a", "https://example.test/b")))

    assert all("rendered content" in result for result in results)
    assert adapter.maximum_active == 1


def test_render_rejects_unexpected_navigation_results() -> None:
    browser = ConversationBrowser(InvalidResultAdapter(navigation_kind="DOM"), user_id="user-1", workspace_id="workspace-1", agent_id="agent-1", execution_id="execution-1")

    with pytest.raises(RuntimeError, match="PAGE"):
        browser.render("https://example.test/page")


def test_render_rejects_a_page_result_without_a_page_snapshot() -> None:
    browser = ConversationBrowser(InvalidResultAdapter(missing_page=True), user_id="user-1", workspace_id="workspace-1", agent_id="agent-1", execution_id="execution-1")

    with pytest.raises(RuntimeError, match="page snapshot"):
        browser.render("https://example.test/page")


def test_render_rejects_a_dom_result_without_an_artifact() -> None:
    browser = ConversationBrowser(InvalidResultAdapter(missing_artifact=True), user_id="user-1", workspace_id="workspace-1", agent_id="agent-1", execution_id="execution-1")

    with pytest.raises(RuntimeError, match="artifact"):
        browser.render("https://example.test/page")


def test_render_clears_stale_memory_before_a_failed_capture() -> None:
    class SequenceAdapter(FakeAdapter):
        def __init__(self) -> None:
            super().__init__()
            self.capture_count = 0

        def execute(self, job):
            if job.operation is BrowserOperationKind.CAPTURE_DOM:
                self.capture_count += 1
                if self.capture_count == 2:
                    return BrowserJobFailed(job.job_id, BrowserErrorCode.LIMIT_EXCEEDED, EffectState.NOT_APPLIED, Retryability.NEVER)
            return super().execute(job)

    browser = ConversationBrowser(SequenceAdapter(), user_id="user-1", workspace_id="workspace-1", agent_id="agent-1", execution_id="execution-1")
    browser.render("https://example.test/first")

    with pytest.raises(RuntimeError):
        browser.render("https://example.test/second")
    assert browser.output.data == b""


def test_memory_output_enforces_limits_and_abort_semantics() -> None:
    output = MemoryArtifactOutput()
    sink = output.begin("CAPTURE_DOM", object(), object(), 3)
    sink.write(b"abc")

    with pytest.raises(ValueError, match="maximum"):
        sink.write(b"d")

    output.abort(sink)
    assert output.data == b""
    with pytest.raises(ValueError, match="aborted"):
        output.commit(sink, "text/html")


def test_close_cleans_up_the_session_and_shuts_down_the_adapter() -> None:
    class ClosableAdapter(FakeAdapter):
        def __init__(self) -> None:
            super().__init__()
            self.cleaned = 0
            self.closed = 0

        def cleanup(self, session_id: str) -> bool:
            self.cleaned += 1
            return True

        def close(self) -> None:
            self.closed += 1

    adapter = ClosableAdapter()
    browser = ConversationBrowser(adapter, user_id="user-1", workspace_id="workspace-1", agent_id="agent-1", execution_id="execution-1")

    browser.close()

    assert adapter.cleaned == 1
    assert adapter.closed == 1
