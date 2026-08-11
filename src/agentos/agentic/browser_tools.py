"""Rendered-page reading for the conversational agent.

The browser domain in ``agentos.browser`` is built around durable sessions,
leases and artifact references, which is the right shape for a long-running
automation job and the wrong shape for one chat turn. This module is the thin
adaptation: it builds the ``BrowserJob`` values the adapter expects, collects
the captured DOM in memory instead of in artifact storage, and hands plain text
back to the model.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Mapping
from uuid import uuid4

from agentos.browser.models import (
    BrowserArtifactRef,
    BrowserJob,
    BrowserJobFailed,
    BrowserLimits,
    BrowserOperationContext,
    BrowserOperationKind,
    BrowserWorkerGrant,
    GrantCapability,
)


BROWSER_TIMEOUT = timedelta(seconds=30)
MAX_DOM_BYTES = 2_000_000
MAX_SCREENSHOT_BYTES = 4_000_000


class _MemorySink:
    def __init__(self) -> None:
        self.buffer = bytearray()

    def write(self, data: bytes) -> int:
        self.buffer.extend(data)
        return len(data)


class MemoryArtifactOutput:
    """Collects a capture in memory so the turn can read it without storage."""

    def __init__(self) -> None:
        self.data = b""

    def begin(self, kind: str, context: object, grant: object, maximum_bytes: int) -> _MemorySink:
        return _MemorySink()

    def commit(self, sink: _MemorySink, media_type: str) -> BrowserArtifactRef:
        self.data = bytes(sink.buffer)
        return BrowserArtifactRef(f"memory:{uuid4().hex}", 1, len(self.data), media_type, "INTERNAL")

    def abort(self, sink: _MemorySink) -> None:
        self.data = b""


class ConversationBrowser:
    """One headless page, alive for the duration of a turn."""

    def __init__(self, adapter, *, user_id: str, workspace_id: str, agent_id: str, execution_id: str) -> None:
        self.adapter = adapter
        self.output = MemoryArtifactOutput()
        self.adapter.artifact_output = self.output
        self._session_id = f"session-{uuid4().hex}"
        self._page_id = f"page-{uuid4().hex}"
        self._context = BrowserOperationContext(user_id, workspace_id, agent_id, execution_id, f"correlation-{uuid4().hex}", "browser.page", f"agent:{agent_id}")
        self._limits = BrowserLimits(BROWSER_TIMEOUT, 1, 5, MAX_DOM_BYTES, MAX_SCREENSHOT_BYTES, 0, 0, 0, "network:strict")
        self._grant = BrowserWorkerGrant(
            f"grant-{uuid4().hex}", self._context, f"lease-{uuid4().hex}", "profile-conversation", self._session_id,
            (GrantCapability.NAVIGATE, GrantCapability.READ_DOM), datetime.now(UTC) + BROWSER_TIMEOUT, 1,
        )

    def _job(self, operation: BrowserOperationKind, arguments: Mapping[str, object]) -> BrowserJob:
        now = datetime.now(UTC)
        return BrowserJob(
            f"job-{uuid4().hex}", self._context, self._grant.lease_id, "profile-conversation", 1,
            self._session_id, self._page_id, operation, dict(arguments), self._limits, (self._grant,),
            f"idempotency-{uuid4().hex}", now + BROWSER_TIMEOUT, now,
        )

    def _run(self, operation: BrowserOperationKind, arguments: Mapping[str, object]):
        outcome = self.adapter.execute(self._job(operation, arguments))
        if isinstance(outcome, BrowserJobFailed):
            raise RuntimeError(f"browser refused the operation: {outcome.error_code.value}")
        return outcome

    def render(self, url: str) -> str:
        """Navigate and return the rendered HTML of the page."""
        self._run(BrowserOperationKind.NAVIGATE, {"url": url})
        self._run(BrowserOperationKind.CAPTURE_DOM, {})
        return self.output.data.decode("utf-8", "replace")

    def close(self) -> None:
        try:
            self.adapter.cleanup(self._session_id)
        except Exception:
            # Cleanup is best effort; a stuck engine must not fail the turn.
            pass


def conversation_browser_for(turn: Mapping[str, object]) -> ConversationBrowser | None:
    """Build a browser only when the optional engine is actually installed."""
    from agentos.browser.playwright_adapter import PlaywrightBrowserAdapter

    if not PlaywrightBrowserAdapter.is_available():
        return None
    return ConversationBrowser(
        PlaywrightBrowserAdapter(),
        user_id=str(turn.get("user_id") or "user"),
        workspace_id=str(turn.get("workspace_id") or turn.get("conversation_id") or "workspace"),
        agent_id=str(turn.get("agent_id") or "agent"),
        execution_id=str(turn.get("execution_id") or "execution"),
    )


__all__ = ["BROWSER_TIMEOUT", "ConversationBrowser", "MemoryArtifactOutput", "conversation_browser_for"]
