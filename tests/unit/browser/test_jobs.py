from __future__ import annotations

from datetime import timedelta

from agentos.browser.models import *
from agentos.browser.reference import ReferenceBrowserAdapter
from agentos.browser.service import BrowserService


def ctx():
    return BrowserOperationContext("u", "ws", "a", "e", "c", "browser.navigate", "agent:a")


def test_submit_inspect_stream_cancel_and_idempotency() -> None:
    service = BrowserService(adapter=ReferenceBrowserAdapter())
    profile = service.create_profile(ctx(), "p", "safe", "policy:strict")
    session = service.open_session(ctx(), "p", 1, BrowserLimits(timedelta(minutes=1), 1, 2, 1024, 1024, 1024, 1024, 0, "network:strict"))
    request = service.make_request(ctx(), session, BrowserOperationKind.NAVIGATE, {"url": "https://example.com"}, "idem-job")
    accepted = service.submit(request)
    assert accepted.job_id == service.submit(request).job_id
    snapshot = service.inspect(AuthorizedBrowserJobQuery(ctx(), accepted.job_id))
    assert snapshot.job_id == accepted.job_id
    streamed = service.stream(BrowserJobStreamRequest(ctx(), accepted.job_id, 0), _CollectSink())
    assert streamed.last_sequence >= 1
    cancelled = service.request_cancel(CancelBrowserJob(ctx(), accepted.job_id, "caller"))
    assert cancelled.accepted


class _CollectSink:
    def __init__(self): self.items = []
    def emit(self, item): self.items.append(item); return "ACCEPTED"
