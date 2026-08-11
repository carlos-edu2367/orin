from __future__ import annotations

import importlib.util


def test_playwright_boundary_is_optional_and_never_required_by_domain() -> None:
    module = __import__("agentos.browser.playwright_adapter", fromlist=["PlaywrightBrowserAdapter"])
    if importlib.util.find_spec("playwright") is None:
        assert module.PlaywrightBrowserAdapter.is_available() is False
    else:
        assert module.PlaywrightBrowserAdapter.is_available() is True


def test_playwright_navigation_uses_the_redirect_policy_helper() -> None:
    from agentos.browser import playwright_adapter
    from agentos.browser.security import validate_redirect

    assert playwright_adapter.validate_redirect is validate_redirect
