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
import os
import re
from threading import Lock
import time
from typing import Callable, Mapping
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

BROWSER_CAPABILITY_VARIABLE = "AGENTOS_BROWSER_CAPABILITY"
BROWSER_CAPABILITIES = ("read", "interact", "full")
# "interact" matches the normal interactive browser capability. Form
# submission controls are available there too, but their side effect remains
# behind the explicit preview/confirmation handshake.
DEFAULT_BROWSER_CAPABILITY = "interact"


def browser_capability_from_environment() -> str:
    """The browser capability level for this local install.

    There is no per-conversation UI for this yet, so every conversation on
    this install shares one level, set once via ``AGENTOS_BROWSER_CAPABILITY``
    (one of "read", "interact", "full"). An unset or unrecognized value keeps
    today's behavior instead of silently narrowing or widening it.
    """
    raw = os.environ.get(BROWSER_CAPABILITY_VARIABLE, "").strip().lower()
    return raw if raw in BROWSER_CAPABILITIES else DEFAULT_BROWSER_CAPABILITY
_SENSITIVE_NAME = r"(?:api[_-]?key|access[_-]?token|refresh[_-]?token|token|password|secret|authorization|cookie|credential)"
_PRIVATE_KEY_BLOCK = re.compile(r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----", re.IGNORECASE | re.DOTALL)
_AUTHORIZATION_VALUE = re.compile(rf"(?i)(\bauthorization\b\s*[:=]\s*)(?:bearer\s+)?[^\r\n,.;!?]+")
_SENSITIVE_ASSIGNMENT = re.compile(rf"(?i)([\"']?\b{_SENSITIVE_NAME}\b[\"']?\s*[:=]\s*)[^\r\n,.;!?]+")
_JWT_VALUE = re.compile(r"(?<![A-Za-z0-9_-])(eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,})(?![A-Za-z0-9_-])")


def _netloc(parsed) -> str:
    host = (parsed.hostname or "").lower()
    if ":" in host:
        host = f"[{host}]"
    if parsed.port:
        host += f":{parsed.port}"
    return host


def _safe_display_url(value: str) -> str:
    """Credential- and query-free URL, safe to show in the UI, activity log or ledger."""
    parsed = urlsplit(value)
    return urlunsplit((parsed.scheme.lower(), _netloc(parsed), parsed.path or "/", "", ""))


def _cache_key_url(value: str) -> str:
    """Identifies "the same page" for the tab cache.

    Unlike ``_safe_display_url`` this keeps the query string: a search or an
    item id is a different page from another one at the same path, and must
    not share a cached observation with it.
    """
    parsed = urlsplit(value)
    return urlunsplit((parsed.scheme.lower(), _netloc(parsed), parsed.path or "/", parsed.query, ""))


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


def conversation_browser_for(turn: Mapping[str, object]) -> object | None:
    """Build an isolated browser host only when the optional engine exists."""
    from agentos.browser.conversation_worker import IsolatedConversationBrowser, playwright_available

    if not playwright_available():
        return None
    return IsolatedConversationBrowser(capability=browser_capability_from_environment())


class AgentBrowserView:
    """Presents one agent's own tab in a browser shared across a turn.

    Concurrent subagents used to fight over one shared page: this is what
    gives each ``agent_key`` its own isolated ``Page`` on the host side (see
    ``conversation_worker._AgentPageState``), while ``agent_tools.py`` keeps
    calling ``navigate``/``click``/... with the exact same signatures it
    always has — the scoping is invisible to it.

    Deliberately has no ``close()``: an individual agent's view must never be
    able to tear down a browser session other agents, or a later turn, are
    still using.
    """

    def __init__(self, browser: object, agent_key: str) -> None:
        self._browser = browser
        self._agent_key = agent_key

    def navigate(self, url: str) -> dict[str, object]:
        return self._browser.navigate(url, agent_key=self._agent_key)

    def observe(self) -> dict[str, object]:
        return self._browser.observe(agent_key=self._agent_key)

    def click(self, selector: str, confirmed: bool = False) -> dict[str, object]:
        if confirmed:
            return self._browser.click(selector, True, agent_key=self._agent_key)
        return self._browser.click(selector, agent_key=self._agent_key)

    def fill(self, selector: str, text: str) -> dict[str, object]:
        return self._browser.fill(selector, text, agent_key=self._agent_key)

    def press(self, selector: str, key: str, confirmed: bool = False) -> dict[str, object]:
        if confirmed:
            return self._browser.press(selector, key, True, agent_key=self._agent_key)
        return self._browser.press(selector, key, agent_key=self._agent_key)

    def select(self, selector: str, values: list[str]) -> dict[str, object]:
        return self._browser.select(selector, values, agent_key=self._agent_key)

    def check(self, selector: str, checked: bool) -> dict[str, object]:
        return self._browser.check(selector, checked, agent_key=self._agent_key)

    def screenshot(self) -> dict[str, object]:
        return self._browser.screenshot(agent_key=self._agent_key)

    def submit(self, selector: str, confirmed: bool) -> dict[str, object]:
        return self._browser.submit(selector, confirmed, agent_key=self._agent_key)

    def back(self) -> dict[str, object]:
        return self._browser.back(agent_key=self._agent_key)

    def scroll(self, direction: str) -> dict[str, object]:
        return self._browser.scroll(direction, agent_key=self._agent_key)

    def wait_for(self, selector: str, state: str) -> dict[str, object]:
        return self._browser.wait_for(selector, state, agent_key=self._agent_key)

    def render(self, url: str) -> str:
        return str(self._browser.navigate(url, agent_key=self._agent_key).get("html") or "")


def _close_quietly(browser: object | None) -> None:
    if browser is None:
        return
    closer = getattr(browser, "close", None)
    if callable(closer):
        try:
            closer()
        except Exception:
            pass


class _RegistryEntry:
    __slots__ = ("browser", "last_used")

    def __init__(self, browser: object, last_used: float) -> None:
        self.browser = browser
        self.last_used = last_used


class ConversationBrowserRegistry:
    """Keeps one browser process alive per conversation across turns.

    A browser built fresh every turn starts from a blank tab each message,
    which makes a login or any multi-step flow impossible to carry across a
    conversation. This hands out the same browser for repeated turns of one
    conversation, and only tears it down once it has sat idle past
    ``idle_seconds`` or once the number of live sessions exceeds
    ``max_sessions`` (evicting the least-recently-used one first) — so a
    conversation nobody returns to does not leak a Chromium process forever.

    Eviction is opportunistic: it runs on every ``acquire`` call rather than
    on a background timer, which is enough for a local, single-user app and
    needs no extra thread.
    """

    def __init__(
        self,
        *,
        factory: Callable[[Mapping[str, object]], object | None],
        idle_seconds: float = 1_800,
        max_sessions: int = 6,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._factory = factory
        self._idle_seconds = idle_seconds
        self._max_sessions = max_sessions
        self._clock = clock
        self._lock = Lock()
        self._entries: dict[str, _RegistryEntry] = {}

    def acquire(self, turn: Mapping[str, object]) -> object | None:
        conversation_id = str(turn.get("conversation_id") or "")
        with self._lock:
            self._evict_idle_locked()
            entry = self._entries.get(conversation_id)
            if entry is not None:
                entry.last_used = self._clock()
                return entry.browser
            if not conversation_id:
                # Nothing to key a session by: behave exactly like before
                # this registry existed, one unmanaged browser per call.
                return self._factory(turn)
            browser = self._factory(turn)
            if browser is not None:
                self._evict_lru_locked_to_make_room()
                self._entries[conversation_id] = _RegistryEntry(browser, self._clock())
            return browser

    def release(self, turn: Mapping[str, object]) -> None:
        """Refresh the conversation's idle clock at the end of a turn.

        A turn can run long while actively using the browser; without this,
        the idle clock would only advance at the *start* of a turn and a
        long-running one could be evicted by an unrelated conversation's
        cleanup while still in progress.
        """
        conversation_id = str(turn.get("conversation_id") or "")
        with self._lock:
            entry = self._entries.get(conversation_id)
            if entry is not None:
                entry.last_used = self._clock()

    def discard(self, conversation_id: str) -> None:
        with self._lock:
            entry = self._entries.pop(str(conversation_id), None)
        _close_quietly(entry.browser if entry is not None else None)

    def close_all(self) -> None:
        with self._lock:
            entries = list(self._entries.values())
            self._entries.clear()
        for entry in entries:
            _close_quietly(entry.browser)

    def _evict_idle_locked(self) -> None:
        now = self._clock()
        stale = [key for key, entry in self._entries.items() if now - entry.last_used > self._idle_seconds]
        for key in stale:
            _close_quietly(self._entries.pop(key).browser)

    def _evict_lru_locked_to_make_room(self) -> None:
        while len(self._entries) >= self._max_sessions:
            oldest_key = min(self._entries, key=lambda key: self._entries[key].last_used)
            _close_quietly(self._entries.pop(oldest_key).browser)


__all__ = [
    "AgentBrowserView", "BROWSER_TIMEOUT", "ConversationBrowser", "ConversationBrowserRegistry",
    "MemoryArtifactOutput", "conversation_browser_for",
]
