from __future__ import annotations

import importlib.util
import sys
import time
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
    assert "timed out" in observation["screenshot_error"]


def test_oversized_png_is_retried_as_a_smaller_jpeg_instead_of_dropping_the_observation() -> None:
    class Timeout(Exception):
        pass

    class Page:
        url = "https://example.test/page"

        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def content(self):
            return "<html><body>usable page text</body></html>"

        def title(self):
            return "Example"

        def screenshot(self, **kwargs):
            self.calls.append(kwargs)
            if kwargs.get("type") == "png":
                return b"x" * (conversation_worker.MAX_SCREENSHOT_BYTES + 1)
            return b"small-jpeg"

    page = Page()
    observation = conversation_worker._observation_for_page(page, Timeout)

    assert observation["screenshot_error"] == ""
    assert observation["screenshot_media_type"] == "image/jpeg"
    assert page.calls == [
        {"type": "png", "timeout": conversation_worker.SCREENSHOT_TIMEOUT_MS},
        {"type": "jpeg", "quality": 60, "timeout": conversation_worker.SCREENSHOT_TIMEOUT_MS},
    ]
    assert "usable page text" in observation["html"]


def test_still_oversized_after_jpeg_retry_drops_the_image_but_keeps_the_html() -> None:
    class Timeout(Exception):
        pass

    class Page:
        url = "https://example.test/page"

        def content(self):
            return "<html><body>usable page text</body></html>"

        def title(self):
            return "Example"

        def screenshot(self, **kwargs):
            return b"x" * (conversation_worker.MAX_SCREENSHOT_BYTES + 1)

    observation = conversation_worker._observation_for_page(Page(), Timeout)

    assert observation["screenshot"] == ""
    assert "size limit" in observation["screenshot_error"]
    assert "usable page text" in observation["html"]


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


def test_enter_is_an_allowed_press_key_now_that_submit_has_a_confirmation_gate() -> None:
    assert "Enter" in conversation_worker._PRESS_KEYS


def test_policy_for_interact_keeps_the_https_only_default_but_allows_loopback_development() -> None:
    policy = conversation_worker._policy_for("interact")
    assert policy.allowed_schemes == ("https",)
    assert policy.allowed_ports == (443,)
    assert policy.allow_loopback is True


def test_policy_for_full_widens_scheme_and_port_but_stays_otherwise_default() -> None:
    policy = conversation_worker._policy_for("full")
    assert set(policy.allowed_schemes) == {"http", "https"}
    assert policy.allowed_ports == ()  # empty means "no port restriction" per security.validate_url


def test_browser_capabilities_allow_loopback_but_still_reject_a_private_destination() -> None:
    from agentos.browser.security import NetworkPolicyError, validate_url

    policy = conversation_worker._policy_for("full")
    assert validate_url("http://127.0.0.1:8080/", policy) == "http://127.0.0.1:8080/"
    with pytest.raises(NetworkPolicyError):
        validate_url("http://192.168.1.10:8080/admin", policy)


def test_describe_form_reads_visible_field_values_and_masks_the_password() -> None:
    class Locator:
        def evaluate(self, _script):
            return {"action": "/login", "method": "POST", "fields": [
                {"name": "email", "type": "email", "value": "a@b.com"},
                {"name": "password", "type": "password", "value": "[hidden]"},
            ]}

    preview = conversation_worker._describe_form(Locator())

    assert preview["action"] == "/login"
    assert preview["fields"][1]["value"] == "[hidden]"


def test_describe_form_is_best_effort_and_never_raises() -> None:
    class Locator:
        def evaluate(self, _script):
            raise RuntimeError("no form")

    assert conversation_worker._describe_form(Locator()) == {"action": None, "method": None, "fields": []}


def test_parent_timeout_budget_exceeds_the_worst_case_child_navigate_time() -> None:
    worst_case_seconds = conversation_worker.WORST_CASE_OPERATION_MS / 1000
    assert conversation_worker.default_parent_timeout_seconds() > worst_case_seconds


def test_ref_selector_resolves_to_the_tagged_data_attribute() -> None:
    assert conversation_worker._selector("ref:e12") == '[data-orin-ref="e12"]'


@pytest.mark.parametrize("bad_ref", ["ref:", "ref:abc", "ref:e", "ref:1", "ref:e12; DROP", "ref:e" + "1" * 10])
def test_ref_selector_rejects_anything_that_is_not_a_plain_index(bad_ref: str) -> None:
    with pytest.raises(ValueError, match="ref:e12"):
        conversation_worker._selector(bad_ref)


def test_single_distinguishes_no_match_from_ambiguous_match() -> None:
    class Locator:
        def __init__(self, count: int) -> None:
            self._count = count

        def count(self):
            return self._count

    class Page:
        def __init__(self, count: int) -> None:
            self._count = count

        def locator(self, _selector):
            return Locator(self._count)

    with pytest.raises(ValueError, match="matched no visible element"):
        conversation_worker._single(Page(0), "#missing")
    with pytest.raises(ValueError, match="matched 3 elements"):
        conversation_worker._single(Page(3), "button")


def test_element_inventory_is_best_effort_and_never_raises() -> None:
    class Page:
        def evaluate(self, _script):
            raise RuntimeError("no JS context in this fake")

    assert conversation_worker._element_inventory(Page()) == []


def test_element_inventory_returns_the_tagged_lines_from_the_page() -> None:
    class Page:
        def evaluate(self, _script):
            return ['[e1] button "Criar minha loja"', '[e2] a[href] "Planos" href=/planos']

    assert conversation_worker._element_inventory(Page()) == ['[e1] button "Criar minha loja"', '[e2] a[href] "Planos" href=/planos']


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

        preview = request("click", selector="#submit")
        assert "submitted" not in preview["html"]
        assert "submit_preview" in preview
        confirmed = request("click", selector="#submit", confirmed=True)
        assert "'clicked': '#submit'" in confirmed["html"]
    finally:
        parent.send({"action": "close"})
        parent.recv()
        thread.join(timeout=2)
        parent.close()


def test_submit_previews_without_clicking_then_clicks_only_when_confirmed(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeTimeout(Exception):
        pass

    class Mouse:
        def __init__(self) -> None:
            self.wheeled: list[tuple[int, int]] = []

        def wheel(self, x, y):
            self.wheeled.append((x, y))

    class Locator:
        def __init__(self, page, selector: str) -> None:
            self.page = page
            self.selector = selector

        def count(self):
            return 1

        def evaluate(self, expression):
            if expression == "el => el.tagName.toLowerCase()":
                return "button"
            if expression == "el => Boolean(el.form)":
                return True
            return {"action": "/login", "method": "POST", "fields": [{"name": "q", "type": "text", "value": "hello"}]}

        def get_attribute(self, name):
            return "submit" if name == "type" else None

        def click(self):
            self.page.state["submitted"] = True

        @property
        def first(self):
            return self

        def wait_for(self, **_kwargs):
            self.page.state["waited"] = self.selector

    class Page:
        url = "about:blank"

        def __init__(self) -> None:
            self.state: dict[str, object] = {}
            self.mouse = Mouse()

        def set_default_timeout(self, _timeout):
            pass

        def route(self, *_args):
            pass

        def goto(self, target, **_kwargs):
            self.url = target

        def go_back(self, **_kwargs):
            self.state["went_back"] = True

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

    pages: list[Page] = []

    class Context:
        def new_page(self):
            page = Page()
            pages.append(page)
            return page

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

    import multiprocessing

    parent, child = multiprocessing.Pipe()
    thread = Thread(target=conversation_worker._host, args=(child, "full"), daemon=True)
    thread.start()

    def request(action: str, **arguments):
        parent.send({"action": action, **arguments})
        response = parent.recv()
        assert response["ok"], response
        return response["result"]

    try:
        request("navigate", url="https://example.com/form")

        preview = request("submit", selector="#go", confirmed=False)
        assert "submitted" not in preview["html"]
        assert preview["submit_preview"]["action"] == "/login"
        assert preview["submit_preview"]["fields"][0]["value"] == "hello"

        confirmed = request("submit", selector="#go", confirmed=True)
        assert "'submitted': True" in confirmed["html"]

        back = request("back")
        assert "'went_back': True" in back["html"]

        request("scroll", direction="down")
        assert pages[0].mouse.wheeled == [(0, conversation_worker.SCROLL_PX)]

        waited = request("wait_for", selector="#late", state="visible")
        assert pages[0].state["waited"] == "#late"
        assert waited["url"] == pages[0].url
    finally:
        parent.send({"action": "close"})
        parent.recv()
        thread.join(timeout=2)
        parent.close()


def test_host_reports_missing_chromium_instead_of_an_opaque_bootstrap_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    class Chromium:
        def launch(self, **_kwargs):
            raise RuntimeError("Executable doesn't exist at C:\\fake\\chromium.exe\nRun playwright install")

    class Engine:
        chromium = Chromium()

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

    package = types.ModuleType("playwright")
    sync_api = types.ModuleType("playwright.sync_api")
    sync_api.TimeoutError = TimeoutError
    sync_api.sync_playwright = lambda: Engine()
    monkeypatch.setitem(sys.modules, "playwright", package)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", sync_api)

    import multiprocessing

    parent, child = multiprocessing.Pipe()
    thread = Thread(target=conversation_worker._host, args=(child,), daemon=True)
    thread.start()
    try:
        # The launch fails before _host ever reaches its command loop, so it
        # sends this failure unprompted; recv() blocks until that happens.
        response = parent.recv()
        assert response["ok"] is False
        assert "scripts/install-browser.ps1" in response["error"]
    finally:
        thread.join(timeout=2)
        parent.close()


def test_host_gives_each_agent_key_its_own_isolated_page(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two agents talking to the same host must not see each other's navigation."""

    class FakeTimeout(Exception):
        pass

    contexts: list[object] = []

    class Page:
        def __init__(self, name: str) -> None:
            self.name = name
            self.url = "about:blank"

        def set_default_timeout(self, _timeout):
            pass

        def route(self, *_args):
            pass

        def goto(self, target, **_kwargs):
            self.url = target

        def wait_for_load_state(self, _state, **_kwargs):
            pass

        def content(self):
            return f"<html><body>{self.name} sees {self.url}</body></html>"

        def title(self):
            return self.name

        def screenshot(self, **_kwargs):
            return b"fake-png"

    class Context:
        def __init__(self) -> None:
            self.pages: list[Page] = []
            contexts.append(self)

        def new_page(self):
            page = Page(f"page{len(self.pages)}")
            self.pages.append(page)
            return page

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

    import multiprocessing

    parent, child = multiprocessing.Pipe()
    thread = Thread(target=conversation_worker._host, args=(child,), daemon=True)
    thread.start()

    def request(action: str, agent_key: str, **arguments):
        parent.send({"action": action, "agent_key": agent_key, **arguments})
        response = parent.recv()
        assert response["ok"], response
        return response["result"]

    try:
        request("navigate", "agent-a", url="https://example.com/a")
        request("navigate", "agent-b", url="https://example.com/b")

        observed_a = request("observe", "agent-a")
        observed_b = request("observe", "agent-b")

        assert observed_a["url"] == "https://example.com/a"
        assert observed_b["url"] == "https://example.com/b"
        assert len(contexts[0].pages) == 2
    finally:
        parent.send({"action": "close"})
        parent.recv()
        thread.join(timeout=2)
        parent.close()


def test_host_caps_the_number_of_concurrent_agent_tabs(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeTimeout(Exception):
        pass

    class Page:
        url = "about:blank"

        def set_default_timeout(self, _timeout):
            pass

        def route(self, *_args):
            pass

        def goto(self, target, **_kwargs):
            self.url = target

        def wait_for_load_state(self, _state, **_kwargs):
            pass

        def content(self):
            return "<html></html>"

        def title(self):
            return "t"

        def screenshot(self, **_kwargs):
            return b"png"

    class Context:
        def new_page(self):
            return Page()

        def close(self):
            pass

    class Browser:
        def new_context(self, **_kwargs):
            return Context()

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

    import multiprocessing

    parent, child = multiprocessing.Pipe()
    thread = Thread(target=conversation_worker._host, args=(child,), daemon=True)
    thread.start()

    def request(action: str, agent_key: str, **arguments):
        parent.send({"action": action, "agent_key": agent_key, **arguments})
        return parent.recv()

    try:
        for i in range(conversation_worker.MAX_AGENT_PAGES):
            response = request("observe", f"agent-{i}")
            assert response["ok"], response

        overflow = request("observe", "agent-overflow")
        assert overflow["ok"] is False
        assert "too many concurrent browser tabs" in overflow["error"]
    finally:
        parent.send({"action": "close"})
        parent.recv()
        thread.join(timeout=2)
        parent.close()


class _StubConnection:
    def __init__(self, *, hang: bool = False, result: dict[str, object] | None = None) -> None:
        self.hang = hang
        self.result = result or {"url": "https://example.test", "title": "t", "html": "<html></html>", "screenshot": ""}
        self.sent: list[object] = []
        self.closed = False

    def send(self, obj: object) -> None:
        self.sent.append(obj)

    def poll(self, _timeout: float | None = None) -> bool:
        return not self.hang

    def recv(self) -> dict[str, object]:
        return {"ok": True, "result": self.result}

    def close(self) -> None:
        self.closed = True


class _StubProcess:
    def __init__(self, target, args, name, daemon, *, alive: bool = True) -> None:
        self._alive = alive

    def start(self) -> None:
        pass

    def is_alive(self) -> bool:
        return self._alive

    def terminate(self) -> None:
        self._alive = False

    def join(self, timeout: float | None = None) -> None:
        self._alive = False


class _StubContext:
    """A fake ``multiprocessing`` context so recycle-on-timeout can be tested without a real subprocess."""

    def __init__(self, hang_on_spawn: list[bool], *, dead_on_spawn: list[bool] | None = None) -> None:
        self._hang_on_spawn = list(hang_on_spawn)
        self._dead_on_spawn = list(dead_on_spawn or [])
        self.spawn_count = 0
        self._processes: list[_StubProcess] = []

    def Pipe(self):
        hang = self._hang_on_spawn[self.spawn_count] if self.spawn_count < len(self._hang_on_spawn) else False
        self.spawn_count += 1
        return _StubConnection(hang=hang), _StubConnection()

    def Process(self, *, target, args, name, daemon):
        # Pipe() already advanced spawn_count for this spawn, so the matching
        # liveness flag is one behind it.
        index = self.spawn_count - 1
        alive = not (index < len(self._dead_on_spawn) and self._dead_on_spawn[index])
        process = _StubProcess(target, args, name, daemon, alive=alive)
        self._processes.append(process)
        return process


def test_a_timed_out_call_recycles_the_child_instead_of_closing_the_session(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(conversation_worker, "playwright_available", lambda: True)
    context = _StubContext(hang_on_spawn=[True, False])

    browser = IsolatedConversationBrowser(timeout_seconds=0.01, process_context=context)
    try:
        with pytest.raises(RuntimeError, match="took too long"):
            browser.navigate("https://example.test")

        # The session recovered: the next call reaches a freshly spawned child.
        result = browser.observe()
        assert result["url"] == "https://example.test"
    finally:
        browser.close()

    assert context.spawn_count == 2


def test_close_after_a_timeout_does_not_respawn(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(conversation_worker, "playwright_available", lambda: True)
    context = _StubContext(hang_on_spawn=[True])

    browser = IsolatedConversationBrowser(timeout_seconds=0.01, process_context=context)
    with pytest.raises(RuntimeError, match="took too long"):
        browser.navigate("https://example.test")

    browser.close()

    assert context.spawn_count == 2  # the recycle spawned a second child
    with pytest.raises(RuntimeError, match="session is closed"):
        browser.observe()


def test_a_dead_child_is_detected_quickly_instead_of_waiting_the_full_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(conversation_worker, "playwright_available", lambda: True)
    context = _StubContext(hang_on_spawn=[True], dead_on_spawn=[True])

    browser = IsolatedConversationBrowser(timeout_seconds=30, process_context=context)
    started = time.monotonic()
    try:
        with pytest.raises(RuntimeError, match="exited unexpectedly"):
            browser.navigate("https://example.test")
        # A dead child must be caught by the liveness check, not by exhausting
        # the whole 30-second per-call budget.
        assert time.monotonic() - started < 5
    finally:
        browser.close()


def test_launch_failure_message_distinguishes_missing_chromium_from_other_errors() -> None:
    missing = conversation_worker._launch_failure_message(Exception("Executable doesn't exist at /path/to/chromium\nRun playwright install"))
    assert "scripts/install-browser.ps1" in missing

    other = conversation_worker._launch_failure_message(RuntimeError("some other engine failure"))
    assert "scripts/install-browser.ps1" not in other
    assert "RuntimeError" in other


@pytest.mark.skipif(not playwright_available(), reason="Playwright is optional")
def test_isolated_browser_starts_and_captures_without_exposing_a_native_handle() -> None:
    browser = IsolatedConversationBrowser(timeout_seconds=15)
    try:
        observation = browser.observe()
    finally:
        browser.close()

    assert isinstance(observation["screenshot"], str)
    assert observation["html"]
