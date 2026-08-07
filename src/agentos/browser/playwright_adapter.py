from __future__ import annotations

from datetime import datetime, timezone
import hashlib

from .models import BrowserArtifactRef, BrowserErrorCode, BrowserJob, BrowserJobFailed, BrowserJobSucceeded, BrowserOperationKind, BrowserPageSnapshot, BrowserPageStatus, BrowserResult, EffectState, RedactedCookieMetadata, Retryability
from .security import NetworkPolicy, NetworkPolicyError, validate_url


class PlaywrightBrowserAdapter:
    """Optional engine boundary. The domain and worker never import Playwright types."""

    def __init__(self, *, artifact_output=None, browser_name: str = "chromium") -> None:
        if browser_name not in {"chromium", "firefox", "webkit"}:
            raise ValueError("unsupported browser engine")
        self.artifact_output = artifact_output
        self.browser_name = browser_name
        self._playwright = None
        self._browser = None
        self._contexts = {}
        self._pages = {}

    @staticmethod
    def is_available() -> bool:
        try:
            import importlib.util
            return importlib.util.find_spec("playwright") is not None
        except (ImportError, ValueError):
            return False

    def execute(self, job: BrowserJob):
        if not self.is_available():
            raise RuntimeError("Playwright capability is not installed")
        if self._playwright is None:
            from playwright.sync_api import sync_playwright
            self._playwright = sync_playwright().start()
            browser_type = getattr(self._playwright, self.browser_name)
            self._browser = browser_type.launch(headless=True)
        context = self._contexts.get(job.session_id)
        if context is None:
            context = self._browser.new_context()
            self._contexts[job.session_id] = context
        page = self._pages.get(job.page_id)
        if page is None:
            page = context.new_page()
            self._pages[job.page_id] = page
        if job.operation is BrowserOperationKind.NAVIGATE:
            try:
                url = validate_url(str(job.arguments["url"]), NetworkPolicy())
                page.goto(url, timeout=int(job.limits.timeout.total_seconds() * 1000), wait_until="domcontentloaded")
            except (KeyError, NetworkPolicyError, Exception) as exc:
                if isinstance(exc, NetworkPolicyError):
                    return BrowserJobFailed(job.job_id, BrowserErrorCode.POLICY_DENIED, EffectState.NOT_APPLIED, Retryability.NEVER)
                return BrowserJobFailed(job.job_id, BrowserErrorCode.UNKNOWN, EffectState.UNKNOWN, Retryability.AFTER_RECONCILIATION)
            snapshot = BrowserPageSnapshot(job.page_id or "page", job.session_id or "session", url, str(page.title())[:256], BrowserPageStatus.READY, 1, job.submitted_at, datetime.now(timezone.utc))
            return BrowserResult("PAGE", page=snapshot, page_version=1)
        if job.operation is BrowserOperationKind.CAPTURE_DOM:
            data = page.content().encode("utf-8")
            if len(data) > job.limits.maximum_dom_bytes:
                return BrowserJobFailed(job.job_id, BrowserErrorCode.LIMIT_EXCEEDED, EffectState.NOT_APPLIED, Retryability.NEVER)
            return BrowserResult("DOM", bytes_count=len(data), artifact_ref=self._write_artifact(job, data, "text/html"))
        if job.operation is BrowserOperationKind.CAPTURE_SCREENSHOT:
            data = page.screenshot(type="png")
            if len(data) > job.limits.maximum_screenshot_bytes:
                return BrowserJobFailed(job.job_id, BrowserErrorCode.LIMIT_EXCEEDED, EffectState.NOT_APPLIED, Retryability.NEVER)
            return BrowserResult("SCREENSHOT", bytes_count=len(data), artifact_ref=self._write_artifact(job, data, "image/png"))
        if job.operation is BrowserOperationKind.READ_COOKIES:
            cookies = tuple(RedactedCookieMetadata(str(item.get("name", ""))[:128], str(item.get("domain", ""))[:255], str(item.get("path", "/"))[:256], bool(item.get("secure", False)), str(item.get("sameSite", "UNSPECIFIED")).upper(), None) for item in context.cookies())
            return BrowserResult("COOKIES", cookies=cookies)
        return BrowserResult("INTERACTION", page_version=1)

    def _write_artifact(self, job: BrowserJob, data: bytes, media_type: str) -> BrowserArtifactRef | None:
        if self.artifact_output is None:
            return None
        grant = job.grants[0]
        sink = self.artifact_output.begin(job.operation.value, job.context, grant, len(data))
        try:
            sink.write(data)
            return self.artifact_output.commit(sink, media_type)
        except Exception:
            self.artifact_output.abort(sink)
            raise

    def cleanup(self, session_id: str) -> bool:
        context = self._contexts.pop(session_id, None)
        if context is not None:
            context.close()
        for page_id, page in tuple(self._pages.items()):
            if page_id.startswith(session_id):
                self._pages.pop(page_id, None)
        return True


__all__ = ["PlaywrightBrowserAdapter"]
