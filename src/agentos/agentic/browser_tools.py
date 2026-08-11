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
import re
from threading import Lock
from typing import Mapping
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from agentos.browser.models import (
    BrowserArtifactRef,
    BrowserJob,
    BrowserJobFailed,
    BrowserLimits,
    BrowserOperationContext,
    BrowserOperationKind,
    BrowserResult,
    BrowserWorkerGrant,
    GrantCapability,
)


BROWSER_TIMEOUT = timedelta(seconds=30)
MAX_DOM_BYTES = 2_000_000
MAX_SCREENSHOT_BYTES = 4_000_000
_SENSITIVE_NAME = r"(?:api[_-]?key|access[_-]?token|refresh[_-]?token|token|password|secret|authorization|cookie|credential)"
_PRIVATE_KEY_BLOCK = re.compile(r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----", re.IGNORECASE | re.DOTALL)
_AUTHORIZATION_VALUE = re.compile(rf"(?i)(\bauthorization\b\s*[:=]\s*)(?:bearer\s+)?[^\r\n,.;!?]+")
_SENSITIVE_ASSIGNMENT = re.compile(rf"(?i)([\"']?\b{_SENSITIVE_NAME}\b[\"']?\s*[:=]\s*)[^\r\n,.;!?]+")
_JWT_VALUE = re.compile(r"(?<![A-Za-z0-9_-])(eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,})(?![A-Za-z0-9_-])")


def _safe_display_url(value: str) -> str:
    parsed = urlsplit(value)
    host = (parsed.hostname or "").lower()
    if ":" in host:
        host = f"[{host}]"
    netloc = host
    if parsed.port:
        netloc += f":{parsed.port}"
    return urlunsplit((parsed.scheme.lower(), netloc, parsed.path or "/", "", ""))


def sanitize_page_text(value: str) -> str:
    value = _PRIVATE_KEY_BLOCK.sub("[REDACTED PRIVATE KEY]", value)
    value = _AUTHORIZATION_VALUE.sub(r"\1[REDACTED]", value)
    value = _SENSITIVE_ASSIGNMENT.sub(r"\1[REDACTED]", value)
    return _JWT_VALUE.sub("[REDACTED TOKEN]", value)


class _MemorySink:
    def __init__(self, maximum_bytes: int) -> None:
        self.buffer = bytearray()
        self.maximum_bytes = maximum_bytes
        self.aborted = False
        self.committed = False

    def write(self, data: bytes) -> int:
        if self.aborted:
            raise ValueError("cannot write to an aborted artifact")
        if self.committed:
            raise ValueError("cannot write to a committed artifact")
        if len(self.buffer) + len(data) > self.maximum_bytes:
            raise ValueError("artifact exceeds maximum bytes")
        self.buffer.extend(data)
        return len(data)


class MemoryArtifactOutput:
    """Collects a capture in memory so the turn can read it without storage."""

    def __init__(self) -> None:
        self.data = b""

    def begin(self, kind: str, context: object, grant: object, maximum_bytes: int) -> _MemorySink:
        if not isinstance(maximum_bytes, int) or maximum_bytes < 0:
            raise ValueError("maximum bytes must be non-negative")
        return _MemorySink(maximum_bytes)

    def commit(self, sink: _MemorySink, media_type: str) -> BrowserArtifactRef:
        if sink.aborted:
            raise ValueError("cannot commit an aborted artifact")
        if sink.committed:
            raise ValueError("artifact is already committed")
        sink.committed = True
        self.data = bytes(sink.buffer)
        return BrowserArtifactRef(f"memory:{uuid4().hex}", 1, len(self.data), media_type, "INTERNAL")

    def abort(self, sink: _MemorySink) -> None:
        sink.aborted = True
        sink.buffer.clear()
        self.data = b""

    def reset(self) -> None:
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
        self._render_lock = Lock()
        self._closed = False

    def _job(self, operation: BrowserOperationKind, arguments: Mapping[str, object]) -> BrowserJob:
        now = datetime.now(UTC)
        return BrowserJob(
            f"job-{uuid4().hex}", self._context, self._grant.lease_id, "profile-conversation", 1,
            self._session_id, self._page_id, operation, dict(arguments), self._limits, (self._grant,),
            f"idempotency-{uuid4().hex}", now + BROWSER_TIMEOUT, now,
        )

    def _run(self, operation: BrowserOperationKind, arguments: Mapping[str, object], expected_kind: str) -> BrowserResult:
        outcome = self.adapter.execute(self._job(operation, arguments))
        if isinstance(outcome, BrowserJobFailed):
            raise RuntimeError(f"browser refused the operation: {outcome.error_code.value}")
        if not isinstance(outcome, BrowserResult) or outcome.kind != expected_kind:
            raise RuntimeError(f"browser returned an unexpected {operation.value} result; expected {expected_kind}")
        return outcome

    def render(self, url: str) -> str:
        """Navigate and return the rendered HTML of the page."""
        with self._render_lock:
            self.output.reset()
            page = self._run(BrowserOperationKind.NAVIGATE, {"url": url}, "PAGE")
            if page.page is None:
                raise RuntimeError("browser returned PAGE without a page snapshot")
            dom = self._run(BrowserOperationKind.CAPTURE_DOM, {}, "DOM")
            if dom.artifact_ref is None:
                raise RuntimeError("browser returned DOM without an artifact")
            if dom.artifact_ref.size_bytes != len(self.output.data):
                raise RuntimeError("browser returned DOM without matching artifact data")
            return self.output.data.decode("utf-8", "replace")

    def close(self) -> None:
        with self._render_lock:
            if self._closed:
                return
            self._closed = True
            try:
                self.adapter.cleanup(self._session_id)
            except Exception:
                pass
            shutdown = getattr(self.adapter, "close", None)
            if callable(shutdown):
                try:
                    shutdown()
                except Exception:
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
