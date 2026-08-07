from __future__ import annotations

from datetime import timedelta

from agentos.browser.models import *
from agentos.browser.reference import ReferenceBrowserAdapter
from agentos.browser.service import BrowserService


def ctx(execution_id="e"):
    return BrowserOperationContext("u", "ws", "a", execution_id, "c", "browser.session", "agent:a")


def test_stale_page_writer_and_foreign_context_are_rejected() -> None:
    service = BrowserService(adapter=ReferenceBrowserAdapter())
    profile = service.create_profile(ctx(), "p", "safe", "policy:strict")
    session = service.open_session(ctx(), "p", 1, BrowserLimits(timedelta(minutes=1), 2, 1, 512, 512, 512, 512, 1, "network:strict"))
    page = service.open_page(ctx(), session.session_id, session.version, BrowserLimits(timedelta(minutes=1), 2, 1, 512, 512, 512, 512, 1, "network:strict"))
    first = service.navigate(ctx(), page.page_id, page.version, "https://example.com")
    stale = service.navigate(ctx(), page.page_id, page.version, "https://example.com/other")
    foreign = service.inspect_page(ctx(execution_id="other"), page.page_id)
    assert first.ok
    assert stale.error_code == "VERSION_CONFLICT"
    assert foreign.error_code == "UNAUTHORIZED"


def test_expired_resource_lease_blocks_page_inspection() -> None:
    from agentos.resources.service import ResourceManagerService
    manager = ResourceManagerService()
    service = BrowserService(resource_manager=manager, adapter=ReferenceBrowserAdapter())
    profile = service.create_profile(ctx(), "p2", "safe", "policy:strict")
    session = service.open_session(ctx(), "p2", 1, BrowserLimits(timedelta(minutes=1), 1, 1, 512, 512, 512, 512, 1, "network:strict"))
    page = service.open_page(ctx(), session.session_id, session.version, BrowserLimits(timedelta(minutes=1), 1, 1, 512, 512, 512, 512, 1, "network:strict"))
    lease = manager.inspect(context=__import__("agentos.resources.models", fromlist=["ResourceOperationContext"]).ResourceOperationContext(*ctx().scope_key()), lease_id=session.lease_id)
    manager._clock = lambda: lease.expires_at + timedelta(seconds=1)
    assert service.inspect_page(ctx(), page.page_id).error_code == "LEASE_EXPIRED"
