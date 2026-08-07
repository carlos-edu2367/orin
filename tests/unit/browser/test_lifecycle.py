from __future__ import annotations

from datetime import datetime, timedelta, timezone

from agentos.browser.models import *
from agentos.browser.reference import ReferenceBrowserAdapter
from agentos.browser.service import BrowserService


def ctx(**changes):
    values = {"user_id": "u", "workspace_id": "ws", "agent_id": "a", "execution_id": "e", "correlation_id": "c", "purpose": "browser.session", "actor": "agent:a"}
    values.update(changes)
    return BrowserOperationContext(**values)


def limits(maximum_pages=2):
    return BrowserLimits(timedelta(minutes=5), maximum_pages, 3, 4096, 4096, 4096, 4096, 2, "network:strict")


def test_profile_snapshot_is_stable_and_status_is_enforced() -> None:
    service = BrowserService(adapter=ReferenceBrowserAdapter())
    profile = service.create_profile(ctx(), "profile-1", "safe", "policy:strict")
    session = service.open_session(ctx(), profile.profile_id, profile.version, limits())
    service.update_profile(ctx(), profile.profile_id, expected_version=1, status=BrowserProfileStatus.LOCKED)
    assert session.profile_snapshot.status is BrowserProfileStatus.ACTIVE
    assert service.open_session(ctx(), profile.profile_id, 2, limits()).error_code == "PROFILE_LOCKED"


def test_page_quota_ttl_and_cascade_close_are_effective() -> None:
    service = BrowserService(adapter=ReferenceBrowserAdapter())
    profile = service.create_profile(ctx(), "profile-1", "safe", "policy:strict")
    session = service.open_session(ctx(), profile.profile_id, 1, limits(maximum_pages=1))
    page = service.open_page(ctx(), session.session_id, session.version, limits(maximum_pages=1))
    rejected = service.open_page(ctx(), session.session_id, session.version, limits(maximum_pages=1))
    assert rejected.error_code == "PAGE_QUOTA_EXCEEDED"
    closed = service.close_session(ctx(), session.session_id, session.lease_id, "done")
    assert closed.status is BrowserSessionStatus.CLOSED
    assert service.inspect_page(ctx(), page.page_id).error_code == "PAGE_CLOSED"


def test_browser_uses_resource_manager_binding() -> None:
    from agentos.resources.service import ResourceManagerService
    manager = ResourceManagerService()
    service = BrowserService(resource_manager=manager, adapter=ReferenceBrowserAdapter())
    profile = service.create_profile(ctx(), "profile-1", "safe", "policy:strict")
    session = service.open_session(ctx(), profile.profile_id, 1, limits())
    assert session.lease_id
    assert any(lease.resource_type.value == "BROWSER" for lease in manager.active_leases())


def test_service_navigation_enforces_network_policy() -> None:
    service = BrowserService(adapter=ReferenceBrowserAdapter())
    profile = service.create_profile(ctx(purpose="browser.navigate"), "profile-network", "safe", "policy:strict")
    session = service.open_session(ctx(purpose="browser.navigate"), profile.profile_id, 1, limits())
    page = service.open_page(ctx(purpose="browser.navigate"), session.session_id, 1, limits())
    result = service.navigate(ctx(purpose="browser.navigate"), page.page_id, page.version, "https://127.0.0.1/")
    assert result.error_code == "POLICY_DENIED"


def test_open_session_is_idempotent_and_close_releases_resource_lease() -> None:
    from agentos.resources.service import ResourceManagerService
    manager = ResourceManagerService()
    service = BrowserService(resource_manager=manager, adapter=ReferenceBrowserAdapter())
    profile = service.create_profile(ctx(), "profile-idem", "safe", "policy:strict")
    first = service.open_session(ctx(), profile.profile_id, 1, limits())
    second = service.open_session(ctx(), profile.profile_id, 1, limits(), idempotency_key="session-key")
    third = service.open_session(ctx(), profile.profile_id, 1, limits(), idempotency_key="session-key")
    assert second.session_id == third.session_id
    service.close_session(ctx(), first.session_id, first.lease_id, "done")
    assert all(lease.lease_id != first.lease_id for lease in manager.active_leases())
