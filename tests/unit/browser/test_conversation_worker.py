from __future__ import annotations

import importlib.util

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


@pytest.mark.skipif(not playwright_available(), reason="Playwright is optional")
def test_isolated_browser_starts_and_captures_without_exposing_a_native_handle() -> None:
    browser = IsolatedConversationBrowser(timeout_seconds=15)
    try:
        observation = browser.observe()
    finally:
        browser.close()

    assert isinstance(observation["screenshot"], str)
    assert observation["html"]
