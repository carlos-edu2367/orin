"""Isolated Playwright host used by one conversational turn.

The chat worker can ask this process for a bounded observation or a typed page
interaction, but it never owns a Playwright object.  The child receives only
the command pipe: it has no database, Redis, workspace or provider objects.
"""
from __future__ import annotations

import base64
import importlib.util
import math
from multiprocessing.connection import Connection
from multiprocessing.context import BaseContext
import multiprocessing
import re
from threading import Lock
import time
from typing import Any, Mapping

from .security import NetworkPolicy, NetworkPolicyError, validate_url


DEFAULT_TIMEOUT_MS = 20_000
PAGE_SETTLE_TIMEOUT_MS = 3_000
SCREENSHOT_TIMEOUT_MS = 4_000
# A single navigate can pay every best-effort wait in sequence: the initial
# goto, then the load wait, then the network-idle wait, then the screenshot.
# The parent's per-call timeout is derived from this so the two budgets can
# never drift apart the way a hand-picked parent timeout previously did (the
# parent aborted a still-recovering child before the child's own timeout
# handling could return a graceful error).
WORST_CASE_OPERATION_MS = DEFAULT_TIMEOUT_MS + PAGE_SETTLE_TIMEOUT_MS * 2 + SCREENSHOT_TIMEOUT_MS
PARENT_TIMEOUT_MARGIN_MS = 8_000
MAX_SCREENSHOT_BYTES = 4_000_000
MAX_HTML_BYTES = 2_000_000
MAX_SELECTOR_LENGTH = 256
MAX_INPUT_LENGTH = 4_000
MAX_INVENTORY_ELEMENTS = 150
# One tab for the main agent plus up to MAX_SUBAGENTS_PER_TURN (session.py)
# running concurrently, with a little slack.
MAX_AGENT_PAGES = 6
_PRESS_KEYS = frozenset({"Escape", "Tab", "Space", "ArrowDown", "ArrowUp", "ArrowLeft", "ArrowRight", "Backspace", "Delete", "Home", "End", "PageDown", "PageUp", "Enter"})
# Enter can submit a form. The host keeps the final side effect behind the
# same explicit confirmation used by ``browser_submit``; capability levels
# control the network policy, not whether the browser can operate a page.
SCROLL_PX = 800
WAIT_FOR_TIMEOUT_MS = 10_000
_WAIT_STATES = frozenset({"visible", "hidden", "attached", "detached"})
_REF_PATTERN = re.compile(r"^e[0-9]{1,6}$")


class _AgentPageState:
    """One agent's isolated tab: its own Page, plus its own navigate-cache.

    Concurrent subagents used to fight over a single shared Page — one
    agent's navigation would yank the tab out from under another agent's
    click. Keying this per ``agent_key`` gives each agent its own DOM and its
    own repeated-navigation cache, while all pages still share one
    BrowserContext (so cookies and localStorage behave like real browser tabs
    in the same profile).
    """

    __slots__ = ("page", "last_navigation_target", "last_navigation_observation")

    def __init__(self, page: object) -> None:
        self.page = page
        self.last_navigation_target: str | None = None
        self.last_navigation_observation: dict[str, object] | None = None

# Tags every visible interactive element with a stable ``data-orin-ref``
# attribute and returns one description line per element. Refs are reassigned
# on every observation, so they are only valid until the next one.
_ELEMENT_INVENTORY_SCRIPT = """
() => {
  document.querySelectorAll('[data-orin-ref]').forEach((el) => el.removeAttribute('data-orin-ref'));
  const isVisible = (el) => {
    const rect = el.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return false;
    const style = window.getComputedStyle(el);
    return style.visibility !== 'hidden' && style.display !== 'none';
  };
  const nodes = document.querySelectorAll(
    'a[href], button, input, textarea, select, [role="button"], [role="link"], [onclick], [contenteditable="true"]'
  );
  const items = [];
  let index = 0;
  for (const el of nodes) {
    if (items.length >= 150) break;
    if (!isVisible(el)) continue;
    index += 1;
    const ref = 'e' + index;
    el.setAttribute('data-orin-ref', ref);
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute('type') || '').toLowerCase();
    const label = (el.innerText || el.value || el.getAttribute('aria-label') || el.getAttribute('placeholder') || el.getAttribute('alt') || '').replace(/\\s+/g, ' ').trim().slice(0, 80);
    const extra = [];
    const href = el.getAttribute('href');
    if (tag === 'a' && href) extra.push('href=' + href.slice(0, 120));
    if (type) extra.push('type=' + type);
    const name = el.getAttribute('name');
    if (name) extra.push('name=' + name);
    const tagWithType = type ? tag + '[' + type + ']' : tag;
    items.push('[' + ref + '] ' + tagWithType + ' "' + label + '"' + (extra.length ? ' ' + extra.join(' ') : ''));
  }
  return items;
}
"""


def playwright_available() -> bool:
    return importlib.util.find_spec("playwright") is not None


def default_parent_timeout_seconds() -> float:
    """The per-request timeout the parent process should use.

    Kept strictly above ``WORST_CASE_OPERATION_MS`` so the parent never aborts
    a child that is still inside its own, recoverable timeout handling.
    """
    return math.ceil((WORST_CASE_OPERATION_MS + PARENT_TIMEOUT_MARGIN_MS) / 1000)


def _selector(value: object) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_SELECTOR_LENGTH:
        raise ValueError("selector must be a non-blank CSS selector up to 256 characters")
    text = value.strip()
    if text.startswith("ref:"):
        ref = text[4:]
        if not _REF_PATTERN.fullmatch(ref):
            raise ValueError("ref selector must look like ref:e12, taken from the latest observation's element list")
        return f'[data-orin-ref="{ref}"]'
    return text


def _single(page, selector: object):
    css = _selector(selector)
    locator = page.locator(css)
    count = locator.count()
    if count == 0:
        raise ValueError(f"selector matched no visible element ({css!r}); observe the page again and use one of its [eN] references or a more specific selector")
    if count > 1:
        raise ValueError(f"selector matched {count} elements ({css!r}); narrow it or use a ref:eN from the latest observation's element list")
    return locator


def _element_inventory(page) -> list[str]:
    try:
        items = page.evaluate(_ELEMENT_INVENTORY_SCRIPT)
    except Exception:
        return []
    if not isinstance(items, list):
        return []
    return [str(item) for item in items[:MAX_INVENTORY_ELEMENTS]]


def _is_form_submission_control(locator, tag: str, input_type: str) -> bool:
    """Keep submit/reset controls behind the explicit approval boundary."""
    if tag == "input":
        return input_type in {"image", "reset", "submit"}
    if tag != "button":
        return False
    if input_type in {"reset", "submit"}:
        return True
    # A button without a type defaults to submit only when it belongs to a
    # form. Outside a form it is a normal JavaScript/UI action and is allowed.
    return not input_type and bool(locator.evaluate("el => Boolean(el.form)"))


_FORM_PREVIEW_SCRIPT = """
el => {
  const form = el.closest('form');
  if (!form) return { action: null, method: null, fields: [] };
  const fields = [];
  for (const field of form.elements) {
    if (!field.name || fields.length >= 50) continue;
    const type = (field.type || field.tagName || '').toLowerCase();
    if (type === 'password') { fields.push({ name: field.name, type: 'password', value: '[hidden]' }); continue; }
    if (type === 'checkbox' || type === 'radio') { fields.push({ name: field.name, type, value: field.checked }); continue; }
    fields.push({ name: field.name, type, value: String(field.value || '').slice(0, 200) });
  }
  return { action: form.getAttribute('action'), method: (form.method || 'get').toUpperCase(), fields };
}
"""


def _describe_form(locator) -> dict[str, object]:
    """Read-only preview of the form a submit control belongs to.

    Used by the "submit" action's confirmation step: it must be possible to
    see exactly what would be sent before anything actually is.
    """
    try:
        info = locator.evaluate(_FORM_PREVIEW_SCRIPT)
    except Exception:
        return {"action": None, "method": None, "fields": []}
    return info if isinstance(info, Mapping) else {"action": None, "method": None, "fields": []}


def _capture_screenshot(page, timeout_error) -> tuple[bytes, str, str]:
    """Best-effort visual capture.

    Playwright waits for fonts/animations while taking a screenshot, and a
    complex or third-party page can exceed the timeout here or produce an
    oversized PNG. Neither must cost the caller the HTML, which is the whole
    point of keeping this apart from ``page.content()``: on any failure this
    returns an empty image and a human-readable reason instead of raising.

    Returns ``(image_bytes, media_type, error)``; ``error`` is empty on success.
    """
    try:
        image = page.screenshot(type="png", timeout=SCREENSHOT_TIMEOUT_MS)
    except timeout_error:
        return b"", "image/png", "screenshot timed out"
    if len(image) <= MAX_SCREENSHOT_BYTES:
        return image, "image/png", ""
    # PNG is lossless and can be too large for a busy page; a compressed
    # JPEG retry usually fits without losing the capture entirely.
    try:
        image = page.screenshot(type="jpeg", quality=60, timeout=SCREENSHOT_TIMEOUT_MS)
    except timeout_error:
        return b"", "image/png", "screenshot exceeded the size limit"
    if len(image) <= MAX_SCREENSHOT_BYTES:
        return image, "image/jpeg", ""
    return b"", "image/jpeg", "screenshot exceeded the size limit even after compression"


def _observation_for_page(page, timeout_error, *, include_html: bool = True) -> dict[str, object]:
    html = page.content().encode("utf-8") if include_html else b""
    if len(html) > MAX_HTML_BYTES:
        html = html[:MAX_HTML_BYTES]
    image, media_type, screenshot_error = _capture_screenshot(page, timeout_error)
    elements = _element_inventory(page) if include_html else []
    return {
        "url": page.url,
        "title": str(page.title())[:256],
        "html": html.decode("utf-8", "replace"),
        "screenshot": base64.b64encode(image).decode("ascii"),
        "screenshot_media_type": media_type,
        "screenshot_error": screenshot_error,
        "elements": elements,
    }


def _wait_for_page_ready(page, timeout_error) -> None:
    """Wait for load and a bounded period of network quiet before capturing."""
    try:
        page.wait_for_load_state("load", timeout=PAGE_SETTLE_TIMEOUT_MS)
    except timeout_error:
        pass
    try:
        # Some pages keep analytics or long-polling requests open forever, so
        # network idle is useful but intentionally best-effort.
        page.wait_for_load_state("networkidle", timeout=PAGE_SETTLE_TIMEOUT_MS)
    except timeout_error:
        pass


def _launch_failure_message(error: Exception) -> str:
    """Distinguish "Chromium is not provisioned" from any other launch failure.

    ``playwright_available()`` only checks that the Python package is
    installed; the Chromium binary itself is a separate download
    (``scripts/install-browser.ps1``). Without this, a package-present,
    binary-absent install spawns a process per turn that immediately dies,
    and the caller only ever sees an opaque timeout.
    """
    text = str(error)
    if "Executable doesn't exist" in text or "playwright install" in text.lower():
        return "Chromium is not provisioned for the isolated browser. Run scripts/install-browser.ps1 (or `python -m playwright install chromium`) and try again."
    return f"browser engine failed to start: {type(error).__name__}"


def _policy_for(capability: str) -> NetworkPolicy:
    """The network policy for this session's capability level.

    The conversation browser may also render loopback-only development
    servers. This is deliberately narrower than private-network access: LAN,
    link-local, metadata and reserved addresses remain blocked. ``full``
    additionally widens public destinations to HTTP and non-standard ports.
    """
    if capability == "full":
        return NetworkPolicy(allowed_schemes=("http", "https"), allowed_ports=(), allow_subresources=True, allow_loopback=True)
    return NetworkPolicy(allow_subresources=True, allow_loopback=True)


def _host(connection: Connection, capability: str = "interact") -> None:
    """Process entry point. Importing Playwright here keeps it out of chat."""
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except Exception as error:  # pragma: no cover - only reached on broken installation
        connection.send({"ok": False, "error": f"Playwright is unavailable: {type(error).__name__}"})
        return

    policy = _policy_for(capability)
    try:
        with sync_playwright() as engine:
            try:
                browser = engine.chromium.launch(headless=True)
            except Exception as error:
                connection.send({"ok": False, "error": _launch_failure_message(error)})
                return
            context = browser.new_context(viewport={"width": 1280, "height": 800}, locale="pt-BR")

            def guard(route, request) -> None:
                try:
                    validate_url(request.url, policy)
                    route.continue_()
                except (NetworkPolicyError, ValueError):
                    route.abort("blockedbyclient")

            # Every agent that talks to this host gets its own Page (its own
            # DOM, its own navigate-cache) so a subagent's navigation never
            # yanks the tab out from under a concurrent one. Pages still share
            # one BrowserContext, so cookies and localStorage behave the way
            # real browser tabs in the same profile do.
            states: dict[str, _AgentPageState] = {}

            def state_for(agent_key: str) -> _AgentPageState:
                existing = states.get(agent_key)
                if existing is not None:
                    return existing
                if len(states) >= MAX_AGENT_PAGES:
                    raise ValueError(f"too many concurrent browser tabs for this turn (max {MAX_AGENT_PAGES})")
                new_page = context.new_page()
                new_page.set_default_timeout(DEFAULT_TIMEOUT_MS)
                new_page.route("**/*", guard)
                created = _AgentPageState(new_page)
                states[agent_key] = created
                return created

            def observation(page, *, include_html: bool = True) -> dict[str, object]:
                return _observation_for_page(page, PlaywrightTimeoutError, include_html=include_html)

            def observation_after_action(page) -> dict[str, object]:
                # A click can start a document navigation. Waiting for `load`
                # is immediate for non-navigating JS controls and bounded for
                # a real navigation, so the following screenshot sees the
                # resulting page instead of the old document.
                try:
                    page.wait_for_load_state("load", timeout=PAGE_SETTLE_TIMEOUT_MS)
                except PlaywrightTimeoutError:
                    pass
                return observation(page)

            while True:
                command = connection.recv()
                if not isinstance(command, Mapping):
                    connection.send({"ok": False, "error": "invalid browser command"})
                    continue
                action = str(command.get("action") or "")
                if action == "close":
                    connection.send({"ok": True})
                    break
                try:
                    state = state_for(str(command.get("agent_key") or "main")[:64] or "main")
                    page = state.page
                    if action == "navigate":
                        target = validate_url(str(command.get("url") or ""), policy)
                        if (
                            target == state.last_navigation_target
                            and page.url == target
                            and state.last_navigation_observation is not None
                        ):
                            # Repeated browse_page calls observe the current
                            # tab instead of reloading it or creating another
                            # visual capture.
                            result = dict(state.last_navigation_observation)
                        else:
                            page.goto(target, wait_until="domcontentloaded", timeout=DEFAULT_TIMEOUT_MS)
                            _wait_for_page_ready(page, PlaywrightTimeoutError)
                            result = observation(page)
                            state.last_navigation_target = target
                            state.last_navigation_observation = dict(result)
                    elif action == "observe":
                        result = observation(page)
                        if state.last_navigation_target is not None:
                            state.last_navigation_observation = dict(result)
                    elif action == "click":
                        state.last_navigation_target = None
                        state.last_navigation_observation = None
                        locator = _single(page, command.get("selector"))
                        tag = str(locator.evaluate("el => el.tagName.toLowerCase()"))
                        input_type = str(locator.get_attribute("type") or "").lower()
                        if _is_form_submission_control(locator, tag, input_type) and command.get("confirmed") is not True:
                            result = observation(page, include_html=False)
                            result["submit_preview"] = _describe_form(locator)
                        else:
                            locator.click()
                            result = observation_after_action(page)
                    elif action == "fill":
                        state.last_navigation_target = None
                        state.last_navigation_observation = None
                        text = command.get("text")
                        if not isinstance(text, str) or len(text) > MAX_INPUT_LENGTH:
                            raise ValueError("text must contain at most 4000 characters")
                        locator = _single(page, command.get("selector"))
                        if str(locator.get_attribute("type") or "").lower() == "password":
                            raise ValueError("password fields are not filled by the agent")
                        locator.fill(text)
                        result = observation_after_action(page)
                    elif action == "press":
                        state.last_navigation_target = None
                        state.last_navigation_observation = None
                        key = command.get("key")
                        if key not in _PRESS_KEYS:
                            raise ValueError("key is not allowed")
                        locator = _single(page, command.get("selector"))
                        submits_form = key == "Enter" and bool(locator.evaluate("el => Boolean(el.form && el.tagName.toLowerCase() !== 'textarea')"))
                        if submits_form and command.get("confirmed") is not True:
                            result = observation(page, include_html=False)
                            result["submit_preview"] = _describe_form(locator)
                        else:
                            locator.press(str(key))
                            result = observation_after_action(page)
                    elif action == "select":
                        state.last_navigation_target = None
                        state.last_navigation_observation = None
                        raw_values = command.get("values")
                        if not isinstance(raw_values, list) or not raw_values or len(raw_values) > 10:
                            raise ValueError("values must contain between 1 and 10 options")
                        values = [str(value) for value in raw_values]
                        if any(not value or len(value) > 256 for value in values):
                            raise ValueError("select values are invalid")
                        _single(page, command.get("selector")).select_option(values)
                        result = observation_after_action(page)
                    elif action == "check":
                        state.last_navigation_target = None
                        state.last_navigation_observation = None
                        checked = command.get("checked")
                        if not isinstance(checked, bool):
                            raise ValueError("checked must be boolean")
                        locator = _single(page, command.get("selector"))
                        locator.check() if checked else locator.uncheck()
                        result = observation_after_action(page)
                    elif action == "screenshot":
                        result = observation(page, include_html=False)
                        if state.last_navigation_target is not None and state.last_navigation_observation is not None:
                            refreshed = dict(state.last_navigation_observation)
                            refreshed.update({
                                "url": result.get("url"),
                                "title": result.get("title"),
                                "screenshot": result.get("screenshot"),
                            })
                            state.last_navigation_observation = refreshed
                    elif action == "submit":
                        # The confirmation gate lives here, not only in
                        # agent_tools.py: without confirmed=True nothing is
                        # ever clicked, so a model that skips asking the user
                        # cannot cause a submission by construction.
                        locator = _single(page, command.get("selector"))
                        tag = str(locator.evaluate("el => el.tagName.toLowerCase()"))
                        input_type = str(locator.get_attribute("type") or "").lower()
                        if not _is_form_submission_control(locator, tag, input_type):
                            raise ValueError("selector must identify a form submission control")
                        if command.get("confirmed") is True:
                            state.last_navigation_target = None
                            state.last_navigation_observation = None
                            locator.click()
                            result = observation_after_action(page)
                        else:
                            result = observation(page, include_html=False)
                            result["submit_preview"] = _describe_form(locator)
                    elif action == "back":
                        state.last_navigation_target = None
                        state.last_navigation_observation = None
                        page.go_back(timeout=DEFAULT_TIMEOUT_MS, wait_until="domcontentloaded")
                        _wait_for_page_ready(page, PlaywrightTimeoutError)
                        result = observation(page)
                    elif action == "scroll":
                        direction = command.get("direction")
                        if direction not in {"up", "down"}:
                            raise ValueError("direction must be 'up' or 'down'")
                        delta = SCROLL_PX if direction == "down" else -SCROLL_PX
                        page.mouse.wheel(0, delta)
                        try:
                            page.wait_for_load_state("load", timeout=PAGE_SETTLE_TIMEOUT_MS)
                        except PlaywrightTimeoutError:
                            pass
                        result = observation(page)
                        if state.last_navigation_target is not None:
                            state.last_navigation_observation = dict(result)
                    elif action == "wait_for":
                        wait_state = command.get("state") or "visible"
                        if wait_state not in _WAIT_STATES:
                            raise ValueError("state must be one of visible, hidden, attached, detached")
                        # A wait's whole point is that the element may not
                        # exist yet, so this waits for the first match to
                        # reach the state instead of requiring exactly one
                        # match up front the way click/fill do.
                        css = _selector(command.get("selector"))
                        page.locator(css).first.wait_for(state=wait_state, timeout=WAIT_FOR_TIMEOUT_MS)
                        result = observation(page)
                    else:
                        raise ValueError("unsupported browser action")
                    connection.send({"ok": True, "result": result})
                except PlaywrightTimeoutError:
                    connection.send({"ok": False, "error": "browser operation timed out"})
                except (NetworkPolicyError, ValueError) as error:
                    connection.send({"ok": False, "error": str(error)})
                except Exception as error:  # browser content must not expose stack traces
                    connection.send({"ok": False, "error": f"browser operation failed: {type(error).__name__}"})
            context.close()
            browser.close()
    except Exception as error:  # pragma: no cover - process bootstrap failure
        try:
            connection.send({"ok": False, "error": f"browser host failed: {type(error).__name__}"})
        except Exception:
            pass
    finally:
        connection.close()


class IsolatedConversationBrowser:
    """Synchronous, serialized client for the dedicated browser child process."""

    def __init__(
        self,
        *,
        timeout_seconds: float | None = None,
        process_context: BaseContext | None = None,
        capability: str = "interact",
    ) -> None:
        if not playwright_available():
            raise RuntimeError("Playwright capability is not installed")
        self._context = process_context or multiprocessing.get_context("spawn")
        self._timeout_seconds = timeout_seconds if timeout_seconds is not None else default_parent_timeout_seconds()
        self._capability = capability
        self._lock = Lock()
        self._closed = False
        self._parent: Connection
        self._process: Any
        self._spawn()

    def _spawn(self) -> None:
        self._parent, child = self._context.Pipe()
        self._process = self._context.Process(target=_host, args=(child, self._capability), name="orin-browser", daemon=True)
        self._process.start()
        child.close()

    def _request(self, action: str, **arguments: object) -> dict[str, object]:
        with self._lock:
            if self._closed:
                raise RuntimeError("browser session is closed")
            self._parent.send({"action": action, **arguments})
            if not self._poll_until_ready():
                died = not self._process.is_alive()
                self._recycle()
                if died:
                    raise RuntimeError(
                        "the browser process exited unexpectedly; the tab was reset — "
                        "if this keeps happening, Chromium may not be provisioned "
                        "(run scripts/install-browser.ps1)"
                    )
                raise RuntimeError("the browser page took too long to respond; the tab was reset and the next call starts a fresh page")
            try:
                response = self._parent.recv()
            except (EOFError, OSError):
                self._recycle()
                raise RuntimeError("the browser process stopped unexpectedly; the tab was reset and the next call starts a fresh page")
            if not isinstance(response, Mapping) or not response.get("ok"):
                raise RuntimeError(str(response.get("error") if isinstance(response, Mapping) else "browser host returned an invalid response"))
            result = response.get("result", {})
            return dict(result) if isinstance(result, Mapping) else {}

    def _poll_until_ready(self) -> bool:
        """Wait for a response, but give up early if the child has already died.

        A dead child never answers, so waiting out the full per-call timeout in
        that case (e.g. a broken Chromium install) only delays every browser
        tool call by tens of seconds for no reason. Polling in short slices
        catches that within about a second instead.
        """
        deadline = time.monotonic() + self._timeout_seconds
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            if self._parent.poll(min(1.0, remaining)):
                return True
            if not self._process.is_alive():
                return False

    def render(self, url: str, *, agent_key: str = "main") -> str:
        return str(self.navigate(url, agent_key=agent_key).get("html") or "")

    def navigate(self, url: str, *, agent_key: str = "main") -> dict[str, object]:
        return self._request("navigate", url=url, agent_key=agent_key)

    def observe(self, *, agent_key: str = "main") -> dict[str, object]:
        return self._request("observe", agent_key=agent_key)

    def click(self, selector: str, confirmed: bool = False, *, agent_key: str = "main") -> dict[str, object]:
        return self._request("click", selector=selector, confirmed=confirmed, agent_key=agent_key)

    def fill(self, selector: str, text: str, *, agent_key: str = "main") -> dict[str, object]:
        return self._request("fill", selector=selector, text=text, agent_key=agent_key)

    def press(self, selector: str, key: str, confirmed: bool = False, *, agent_key: str = "main") -> dict[str, object]:
        return self._request("press", selector=selector, key=key, confirmed=confirmed, agent_key=agent_key)

    def select(self, selector: str, values: list[str], *, agent_key: str = "main") -> dict[str, object]:
        return self._request("select", selector=selector, values=values, agent_key=agent_key)

    def check(self, selector: str, checked: bool, *, agent_key: str = "main") -> dict[str, object]:
        return self._request("check", selector=selector, checked=checked, agent_key=agent_key)

    def screenshot(self, *, agent_key: str = "main") -> dict[str, object]:
        return self._request("screenshot", agent_key=agent_key)

    def submit(self, selector: str, confirmed: bool, *, agent_key: str = "main") -> dict[str, object]:
        return self._request("submit", selector=selector, confirmed=confirmed, agent_key=agent_key)

    def back(self, *, agent_key: str = "main") -> dict[str, object]:
        return self._request("back", agent_key=agent_key)

    def scroll(self, direction: str, *, agent_key: str = "main") -> dict[str, object]:
        return self._request("scroll", direction=direction, agent_key=agent_key)

    def wait_for(self, selector: str, state: str = "visible", *, agent_key: str = "main") -> dict[str, object]:
        return self._request("wait_for", selector=selector, state=state, agent_key=agent_key)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._kill_process()

    def _recycle(self) -> None:
        """Replace a stuck or dead child so the next call gets a fresh page.

        A single slow page must not brick browser tools for the rest of the
        turn: only an explicit ``close()`` should end the session permanently.
        The caller already holds ``self._lock``.
        """
        self._kill_process()
        if not self._closed:
            self._spawn()

    def _kill_process(self) -> None:
        """Release the current host while the caller already holds the session lock."""
        try:
            if self._process.is_alive():
                self._parent.send({"action": "close"})
                self._parent.poll(2)
        except (BrokenPipeError, EOFError, OSError):
            pass
        self._parent.close()
        self._process.join(timeout=3)
        if self._process.is_alive():
            self._process.terminate()
            self._process.join(timeout=2)


__all__ = ["IsolatedConversationBrowser", "playwright_available"]
