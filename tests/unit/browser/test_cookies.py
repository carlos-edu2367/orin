from __future__ import annotations

from datetime import datetime, timedelta, timezone

from agentos.browser.models import BrowserCookie, BrowserCookieInput, BrowserOperationContext, BrowserWorkerGrant, GrantCapability
from agentos.browser.reference import ReferenceBrowserAdapter
from agentos.browser.worker import BrowserWorker


def test_read_cookies_returns_only_redacted_metadata() -> None:
    context = BrowserOperationContext("u", "ws", "a", "e", "c", "browser.cookies", "agent:a")
    now = datetime.now(timezone.utc)
    grant = BrowserWorkerGrant("g", context, "lease", "p", "s", (GrantCapability.READ_COOKIES,), now + timedelta(minutes=1), 1)
    adapter = ReferenceBrowserAdapter(cookies=(BrowserCookie("sid", "secret-ref", "example.com", "/", True, "LAX", None),))
    outcome = BrowserWorker(adapter).execute(__import__("agentos.browser.models", fromlist=["BrowserJob"]).__dict__["BrowserJob"]("j", context, "lease", "p", 1, "s", "p1", __import__("agentos.browser.models", fromlist=["BrowserOperationKind"]).__dict__["BrowserOperationKind"].READ_COOKIES, {}, __import__("agentos.browser.models", fromlist=["BrowserLimits"]).__dict__["BrowserLimits"](timedelta(seconds=5), 1, 1, 100, 100, 100, 100, 0, "network:strict"), (grant,), "i", now + timedelta(seconds=5), now))
    assert "secret-ref" not in repr(outcome)
    assert outcome.result.cookies[0].name == "sid"
