from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from agentos.browser.models import BrowserOperationContext, BrowserWorkerGrant, GrantCapability
from agentos.browser.security import validate_grants


def ctx() -> BrowserOperationContext:
    return BrowserOperationContext("u", "ws", "a", "e", "c", "browser.navigate", "agent:a")


def test_minimum_grants_are_required_and_expiry_is_enforced() -> None:
    now = datetime.now(timezone.utc)
    grant = BrowserWorkerGrant("grant-1", ctx(), "lease-1", "profile-1", None, (GrantCapability.NAVIGATE,), now + timedelta(seconds=5), 1)
    validate_grants((grant,), ctx(), "lease-1", GrantCapability.NAVIGATE, now=now)
    with pytest.raises(ValueError):
        validate_grants((grant,), ctx(), "lease-2", GrantCapability.NAVIGATE, now=now)


def test_dangerous_capabilities_are_denied_without_explicit_grant() -> None:
    now = datetime.now(timezone.utc)
    grant = BrowserWorkerGrant("grant-1", ctx(), "lease-1", "profile-1", None, (GrantCapability.NAVIGATE,), now + timedelta(seconds=5), 1)
    with pytest.raises(ValueError):
        validate_grants((grant,), ctx(), "lease-1", GrantCapability.EVALUATE, now=now)
