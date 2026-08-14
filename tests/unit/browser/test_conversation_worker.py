from __future__ import annotations

import importlib.util
import sys
import types
from threading import Thread

import pytest

from agentos.browser import conversation_worker
from agentos.browser.conversation_worker import IsolatedConversationBrowser, playwright_available


def test_conversation_browser_detects_the_optional_engine_without_importing_it() -> None:
    assert playwright_available() is (importlib.util.find_spec("playwright") is not None)


def test_chat_facing_browser_module_never_imports_playwright_directly() -> None:
    source = __import__("pathlib").Path("src/agentos/agentic/browser_tools.py").read_text(encoding="utf-8")
    assert "from playwright" not in source


def test_observation_keeps_rendered_html_when_screenshot_times_out() -> None:
    class ScreenshotTimeout(Exception):
        pass

    class Page:
        url = "https://example.test/page"

        def content(self):
            return "<html><body>usable page text</body></html>"

        def title(self):
            return "Example"

        def screenshot(self, **kwargs):
            assert kwargs == {"type": "png", "timeout": conversation_worker.SCREENSHOT_TIMEOUT_MS}
            raise ScreenshotTimeout()

    observation = conversation_worker._observation_for_page(Page(), ScreenshotTimeout)

    assert "usable page text" in observation["html"]
    assert observation["screenshot"] == ""


def test_page_ready_waits_for_load_and_network_idle_with_bounded_timeouts() -> None:
    class Page:
        def __init__(self) -> None:
            self.calls = []

        def wait_for_load_state(self, state, **kwargs):
            self.calls.append((state, kwargs))

    page = Page()

    conversation_worker._wait_for_page_ready(page, RuntimeError)

    assert page.calls == [
        ("load", {"timeout": conversation_worker.PAGE_SETTLE_TIMEOUT_MS}),
        ("networkidle", {"timeout": conversation_worker.PAGE_SETTLE_TIMEOUT_MS}),
    ]


def test_browser_click_allows_ui_buttons_but_blocks_form_submission_controls() -> None:
    class Locator:
        def __init__(self, in_form: bool) -> None:
            self.in_form = in_form

        def evaluate(self, expression):
            assert expression == "el => Boolean(el.form)"
            return self.in_form

    assert not conversation_worker._is_form_submission_control(Locator(False), "button", "button")
    assert not conversation_worker._is_form_submission_control(Locator(False), "button", "")
    assert conversation_worker._is_form_submission_control(Locator(True), "button", "")
    assert conversation_worker._is_form_submission_control(Locator(False), "button", "submit")
    assert conversation_worker._is_form_submission_control(Locator(False), "input", "submit")


def test_isolated_host_executes_safe_page_actions_in_one_tab(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exercise the command boundary used by the real Chromium child process."""
    import multiprocessing

    class FakeTimeout(Exception):
        pass

    class Locator:
        def __init__(self, page, selector: str) -> None:
            self.page = page
            self.selector = selector

        def count(self):
            return 1

        def evaluate(self, expression):
            if expression == "el => el.tagName.toLowerCase()":
                return "button" if self.selector in {"#ui", "#submit"} else "input" if self.selector == "#name" else "select"
            if expression == "el => Boolean(el.form)":
                return self.selector == "#submit"
            raise AssertionError(expression)

        def get_attribute(self, name):
            if name != "type":
                return None
            return {"#ui": "button", "#submit": "submit", "#name": "text"}.get(self.selector)

        def click(self):
            self.page.state["clicked"] = self.selector

        def fill(self, value):
            self.page.state["filled"] = value

        def press(self, key):
            self.page.state["pressed"] = key

        def select_option(self, values):
            self.page.state["selected"] = list(values)

        def check(self):
            self.page.state["checked"] = True

        def uncheck(self):
            self.page.state["checked"] = False

    class Page:
        url = "about:blank"

        def __init__(self) -> None:
            self.state: dict[str, object] = {}

        def set_default_timeout(self, _timeout):
            pass

        def route(self, *_args):
            pass

        def goto(self, target, **_kwargs):
            self.url = target

        def wait_for_load_state(self, _state, **_kwargs):
            pass

        def locator(self, selector):
            return Locator(self, selector)

        def content(self):
            return f"<html><head><title>Actions</title></head><body>{self.state}</body></html>"

        def title(self):
            return "Actions"

        def screenshot(self, **_kwargs):
            return b"fake-png"

    class Context:
        def __init__(self) -> None:
            self.page = Page()

        def new_page(self):
            return self.page

        def close(self):
            pass

    class Browser:
        def __init__(self) -> None:
            self.context = Context()

        def new_context(self, **_kwargs):
            return self.context

        def close(self):
            pass

    class Chromium:
        def launch(self, **_kwargs):
            return Browser()

    class Engine:
        chromium = Chromium()

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

    package = types.ModuleType("playwright")
    sync_api = types.ModuleType("playwright.sync_api")
    sync_api.TimeoutError = FakeTimeout
    sync_api.sync_playwright = lambda: Engine()
    monkeypatch.setitem(sys.modules, "playwright", package)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", sync_api)

    parent, child = multiprocessing.Pipe()
    thread = Thread(target=conversation_worker._host, args=(child,), daemon=True)
    thread.start()

    def request(action: str, **arguments):
        parent.send({"action": action, **arguments})
        response = parent.recv()
        assert response["ok"], response
        return response["result"]

    try:
        request("navigate", url="https://example.com/actions")
        request("click", selector="#ui")
        request("fill", selector="#name", text="Orin")
        request("press", selector="#name", key="ArrowRight")
        request("select", selector="#mode", values=["compact"])
        result = request("check", selector="#agree", checked=True)

        assert "'clicked': '#ui'" in result["html"]
        assert "'filled': 'Orin'" in result["html"]
        assert "'pressed': 'ArrowRight'" in result["html"]
        assert "'selected': ['compact']" in result["html"]
        assert "'checked': True" in result["html"]

        parent.send({"action": "click", "selector": "#submit"})
        rejected = parent.recv()
        assert rejected["ok"] is False
        assert "form submission" in rejected["error"]
    finally:
        parent.send({"action": "close"})
        parent.recv()
        thread.join(timeout=2)
        parent.close()


@pytest.mark.skipif(not playwright_available(), reason="Playwright is optional")
def test_isolated_browser_starts_and_captures_without_exposing_a_native_handle() -> None:
    browser = IsolatedConversationBrowser(timeout_seconds=15)
    try:
        observation = browser.observe()
    finally:
        browser.close()

    assert isinstance(observation["screenshot"], str)
    assert observation["html"]
