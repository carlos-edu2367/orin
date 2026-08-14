"""Isolated Playwright host used by one conversational turn.

The chat worker can ask this process for a bounded observation or a typed page
interaction, but it never owns a Playwright object.  The child receives only
the command pipe: it has no database, Redis, workspace or provider objects.
"""
from __future__ import annotations

import base64
import importlib.util
from multiprocessing.connection import Connection
from multiprocessing.context import BaseContext
import multiprocessing
from threading import Lock
from typing import Any, Mapping

from .security import NetworkPolicy, NetworkPolicyError, validate_url


DEFAULT_TIMEOUT_MS = 30_000
PAGE_SETTLE_TIMEOUT_MS = 5_000
SCREENSHOT_TIMEOUT_MS = 5_000
MAX_SCREENSHOT_BYTES = 4_000_000
MAX_HTML_BYTES = 2_000_000
MAX_SELECTOR_LENGTH = 256
MAX_INPUT_LENGTH = 4_000
_PRESS_KEYS = frozenset({"Escape", "Tab", "Space", "ArrowDown", "ArrowUp", "ArrowLeft", "ArrowRight", "Backspace", "Delete", "Home", "End", "PageDown", "PageUp"})


def playwright_available() -> bool:
    return importlib.util.find_spec("playwright") is not None


def _selector(value: object) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_SELECTOR_LENGTH:
        raise ValueError("selector must be a non-blank CSS selector up to 256 characters")
    return value.strip()


def _single(page, selector: object):
    locator = page.locator(_selector(selector))
    if locator.count() != 1:
        raise ValueError("selector must match exactly one visible element")
    return locator


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


def _observation_for_page(page, timeout_error, *, include_html: bool = True) -> dict[str, object]:
    html = page.content().encode("utf-8") if include_html else b""
    if len(html) > MAX_HTML_BYTES:
        html = html[:MAX_HTML_BYTES]
    # A visual capture is useful feedback, but it must not make a successfully
    # loaded page unusable. Playwright waits for fonts/animations while taking
    # a screenshot, and complex or third-party pages can exceed the navigation
    # timeout here. Keep this best-effort and bounded; the rendered HTML
    # remains available to the agent when the image cannot be produced.
    image = b""
    try:
        image = page.screenshot(type="png", timeout=SCREENSHOT_TIMEOUT_MS)
    except timeout_error:
        pass
    if len(image) > MAX_SCREENSHOT_BYTES:
        raise ValueError("screenshot exceeds the configured limit")
    return {
        "url": page.url,
        "title": str(page.title())[:256],
        "html": html.decode("utf-8", "replace"),
        "screenshot": base64.b64encode(image).decode("ascii"),
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


def _host(connection: Connection) -> None:
    """Process entry point. Importing Playwright here keeps it out of chat."""
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except Exception as error:  # pragma: no cover - only reached on broken installation
        connection.send({"ok": False, "error": f"Playwright is unavailable: {type(error).__name__}"})
        return

    policy = NetworkPolicy(allow_subresources=True)
    try:
        with sync_playwright() as engine:
            browser = engine.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1280, "height": 800}, locale="pt-BR")
            page = context.new_page()
            page.set_default_timeout(DEFAULT_TIMEOUT_MS)

            def guard(route, request) -> None:
                try:
                    validate_url(request.url, policy)
                    route.continue_()
                except (NetworkPolicyError, ValueError):
                    route.abort("blockedbyclient")

            page.route("**/*", guard)

            last_navigation_target: str | None = None
            last_navigation_observation: dict[str, object] | None = None

            def observation(*, include_html: bool = True) -> dict[str, object]:
                return _observation_for_page(page, PlaywrightTimeoutError, include_html=include_html)

            def observation_after_action() -> dict[str, object]:
                # A click can start a document navigation. Waiting for `load`
                # is immediate for non-navigating JS controls and bounded for
                # a real navigation, so the following screenshot sees the
                # resulting page instead of the old document.
                try:
                    page.wait_for_load_state("load", timeout=PAGE_SETTLE_TIMEOUT_MS)
                except PlaywrightTimeoutError:
                    pass
                return observation()

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
                    if action == "navigate":
                        target = validate_url(str(command.get("url") or ""), policy)
                        if (
                            target == last_navigation_target
                            and page.url == target
                            and last_navigation_observation is not None
                        ):
                            # Repeated browse_page calls observe the current
                            # tab instead of reloading it or creating another
                            # visual capture.
                            result = dict(last_navigation_observation)
                        else:
                            page.goto(target, wait_until="domcontentloaded", timeout=DEFAULT_TIMEOUT_MS)
                            _wait_for_page_ready(page, PlaywrightTimeoutError)
                            result = observation()
                            last_navigation_target = target
                            last_navigation_observation = dict(result)
                    elif action == "observe":
                        result = observation()
                        if last_navigation_target is not None:
                            last_navigation_observation = dict(result)
                    elif action == "click":
                        last_navigation_target = None
                        last_navigation_observation = None
                        locator = _single(page, command.get("selector"))
                        tag = str(locator.evaluate("el => el.tagName.toLowerCase()"))
                        input_type = str(locator.get_attribute("type") or "").lower()
                        if _is_form_submission_control(locator, tag, input_type):
                            raise ValueError("form submission requires an explicit user approval and is not automated")
                        locator.click()
                        result = observation_after_action()
                    elif action == "fill":
                        last_navigation_target = None
                        last_navigation_observation = None
                        text = command.get("text")
                        if not isinstance(text, str) or len(text) > MAX_INPUT_LENGTH:
                            raise ValueError("text must contain at most 4000 characters")
                        locator = _single(page, command.get("selector"))
                        if str(locator.get_attribute("type") or "").lower() == "password":
                            raise ValueError("password fields are not filled by the agent")
                        locator.fill(text)
                        result = observation_after_action()
                    elif action == "press":
                        last_navigation_target = None
                        last_navigation_observation = None
                        key = command.get("key")
                        if key not in _PRESS_KEYS:
                            raise ValueError("key is not allowed")
                        _single(page, command.get("selector")).press(str(key))
                        result = observation_after_action()
                    elif action == "select":
                        last_navigation_target = None
                        last_navigation_observation = None
                        raw_values = command.get("values")
                        if not isinstance(raw_values, list) or not raw_values or len(raw_values) > 10:
                            raise ValueError("values must contain between 1 and 10 options")
                        values = [str(value) for value in raw_values]
                        if any(not value or len(value) > 256 for value in values):
                            raise ValueError("select values are invalid")
                        _single(page, command.get("selector")).select_option(values)
                        result = observation_after_action()
                    elif action == "check":
                        last_navigation_target = None
                        last_navigation_observation = None
                        checked = command.get("checked")
                        if not isinstance(checked, bool):
                            raise ValueError("checked must be boolean")
                        locator = _single(page, command.get("selector"))
                        locator.check() if checked else locator.uncheck()
                        result = observation_after_action()
                    elif action == "screenshot":
                        result = observation(include_html=False)
                        if last_navigation_target is not None and last_navigation_observation is not None:
                            refreshed = dict(last_navigation_observation)
                            refreshed.update({
                                "url": result.get("url"),
                                "title": result.get("title"),
                                "screenshot": result.get("screenshot"),
                            })
                            last_navigation_observation = refreshed
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

    def __init__(self, *, timeout_seconds: int = 35, process_context: BaseContext | None = None) -> None:
        if not playwright_available():
            raise RuntimeError("Playwright capability is not installed")
        self._context = process_context or multiprocessing.get_context("spawn")
        self._parent, child = self._context.Pipe()
        self._process = self._context.Process(target=_host, args=(child,), name="orin-browser", daemon=True)
        self._process.start()
        child.close()
        self._timeout_seconds = timeout_seconds
        self._lock = Lock()
        self._closed = False

    def _request(self, action: str, **arguments: object) -> dict[str, object]:
        with self._lock:
            if self._closed:
                raise RuntimeError("browser session is closed")
            self._parent.send({"action": action, **arguments})
            if not self._parent.poll(self._timeout_seconds):
                self._terminate()
                raise RuntimeError("browser operation timed out")
            response = self._parent.recv()
            if not isinstance(response, Mapping) or not response.get("ok"):
                raise RuntimeError(str(response.get("error") if isinstance(response, Mapping) else "browser host returned an invalid response"))
            result = response.get("result", {})
            return dict(result) if isinstance(result, Mapping) else {}

    def render(self, url: str) -> str:
        return str(self.navigate(url).get("html") or "")

    def navigate(self, url: str) -> dict[str, object]:
        return self._request("navigate", url=url)

    def observe(self) -> dict[str, object]:
        return self._request("observe")

    def click(self, selector: str) -> dict[str, object]:
        return self._request("click", selector=selector)

    def fill(self, selector: str, text: str) -> dict[str, object]:
        return self._request("fill", selector=selector, text=text)

    def press(self, selector: str, key: str) -> dict[str, object]:
        return self._request("press", selector=selector, key=key)

    def select(self, selector: str, values: list[str]) -> dict[str, object]:
        return self._request("select", selector=selector, values=values)

    def check(self, selector: str, checked: bool) -> dict[str, object]:
        return self._request("check", selector=selector, checked=checked)

    def screenshot(self) -> dict[str, object]:
        return self._request("screenshot")

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._terminate()

    def _terminate(self) -> None:
        """Release the host while the caller already holds the session lock."""
        self._closed = True
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
