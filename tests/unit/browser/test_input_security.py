from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from agentos.browser.integration import InMemoryBrowserInputResolver
from agentos.browser.models import BrowserOperationContext, BrowserWorkerGrant, GrantCapability


def test_input_requires_matching_purpose_and_scope() -> None:
    context = BrowserOperationContext("u", "ws", "a", "e", "c", "browser.upload", "agent:a")
    foreign = BrowserOperationContext("other", "ws", "a", "e", "c", "browser.upload", "agent:a")
    grant = BrowserWorkerGrant("g", context, "lease", "p", "s", (GrantCapability.UPLOAD,), datetime.now(timezone.utc) + timedelta(minutes=1), 1)
    resolver = InMemoryBrowserInputResolver({"ref": (b"ok", foreign)})
    with pytest.raises(ValueError):
        resolver.open("ref", grant)
