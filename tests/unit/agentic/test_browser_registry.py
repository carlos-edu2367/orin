from __future__ import annotations

from agentos.agentic.browser_tools import AgentBrowserView, ConversationBrowserRegistry


class FakeBrowser:
    def __init__(self, name: str) -> None:
        self.name = name
        self.closed = False
        self.calls: list[tuple[str, dict[str, object]]] = []

    def navigate(self, url: str, *, agent_key: str) -> dict[str, object]:
        self.calls.append(("navigate", {"url": url, "agent_key": agent_key}))
        return {"url": url, "agent_key": agent_key}

    def observe(self, *, agent_key: str) -> dict[str, object]:
        self.calls.append(("observe", {"agent_key": agent_key}))
        return {"agent_key": agent_key}

    def click(self, selector: str, *, agent_key: str) -> dict[str, object]:
        self.calls.append(("click", {"selector": selector, "agent_key": agent_key}))
        return {}

    def submit(self, selector: str, confirmed: bool, *, agent_key: str) -> dict[str, object]:
        self.calls.append(("submit", {"selector": selector, "confirmed": confirmed, "agent_key": agent_key}))
        return {}

    def back(self, *, agent_key: str) -> dict[str, object]:
        self.calls.append(("back", {"agent_key": agent_key}))
        return {}

    def scroll(self, direction: str, *, agent_key: str) -> dict[str, object]:
        self.calls.append(("scroll", {"direction": direction, "agent_key": agent_key}))
        return {}

    def wait_for(self, selector: str, state: str, *, agent_key: str) -> dict[str, object]:
        self.calls.append(("wait_for", {"selector": selector, "state": state, "agent_key": agent_key}))
        return {}

    def close(self) -> None:
        self.closed = True


class _FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _turn(conversation_id: str) -> dict[str, object]:
    return {"conversation_id": conversation_id}


# -- AgentBrowserView ----------------------------------------------------


def test_agent_browser_view_forwards_the_agent_key_to_every_call() -> None:
    browser = FakeBrowser("shared")
    view = AgentBrowserView(browser, "agent-42")

    view.navigate("https://example.test")
    view.observe()
    view.click("ref:e1")

    assert browser.calls == [
        ("navigate", {"url": "https://example.test", "agent_key": "agent-42"}),
        ("observe", {"agent_key": "agent-42"}),
        ("click", {"selector": "ref:e1", "agent_key": "agent-42"}),
    ]


def test_agent_browser_view_forwards_the_fase3_verbs_with_the_agent_key() -> None:
    browser = FakeBrowser("shared")
    view = AgentBrowserView(browser, "agent-42")

    view.submit("ref:e5", True)
    view.back()
    view.scroll("down")
    view.wait_for("ref:e9", "visible")

    assert browser.calls == [
        ("submit", {"selector": "ref:e5", "confirmed": True, "agent_key": "agent-42"}),
        ("back", {"agent_key": "agent-42"}),
        ("scroll", {"direction": "down", "agent_key": "agent-42"}),
        ("wait_for", {"selector": "ref:e9", "state": "visible", "agent_key": "agent-42"}),
    ]


def test_agent_browser_view_has_no_close_method() -> None:
    """A per-agent view must never be able to tear down the shared session."""
    view = AgentBrowserView(FakeBrowser("shared"), "agent-42")

    assert getattr(view, "close", None) is None


# -- ConversationBrowserRegistry ------------------------------------------


def test_acquire_reuses_the_same_browser_for_repeated_turns_of_one_conversation() -> None:
    built: list[FakeBrowser] = []

    def factory(turn):
        browser = FakeBrowser(f"browser-{len(built)}")
        built.append(browser)
        return browser

    registry = ConversationBrowserRegistry(factory=factory, clock=_FakeClock())

    first = registry.acquire(_turn("conversation-1"))
    second = registry.acquire(_turn("conversation-1"))

    assert first is second
    assert len(built) == 1


def test_acquire_gives_different_conversations_different_browsers() -> None:
    built: list[FakeBrowser] = []

    def factory(turn):
        browser = FakeBrowser(f"browser-{len(built)}")
        built.append(browser)
        return browser

    registry = ConversationBrowserRegistry(factory=factory, clock=_FakeClock())

    a = registry.acquire(_turn("conversation-a"))
    b = registry.acquire(_turn("conversation-b"))

    assert a is not b
    assert len(built) == 2


def test_acquire_without_a_conversation_id_is_unmanaged() -> None:
    calls = 0

    def factory(turn):
        nonlocal calls
        calls += 1
        return FakeBrowser(f"browser-{calls}")

    registry = ConversationBrowserRegistry(factory=factory, clock=_FakeClock())

    first = registry.acquire({})
    second = registry.acquire({})

    assert first is not second
    assert calls == 2


def test_idle_browser_is_closed_and_the_next_acquire_builds_a_fresh_one() -> None:
    clock = _FakeClock()
    built: list[FakeBrowser] = []

    def factory(turn):
        browser = FakeBrowser(f"browser-{len(built)}")
        built.append(browser)
        return browser

    registry = ConversationBrowserRegistry(factory=factory, idle_seconds=100, clock=clock)

    first = registry.acquire(_turn("conversation-1"))
    clock.advance(101)
    # Any acquire() call sweeps idle entries, including one for another conversation.
    registry.acquire(_turn("conversation-other"))

    assert first.closed is True

    second = registry.acquire(_turn("conversation-1"))
    assert second is not first
    assert len(built) == 3


def test_a_conversation_still_in_use_is_not_evicted_as_idle() -> None:
    clock = _FakeClock()
    registry = ConversationBrowserRegistry(factory=lambda turn: FakeBrowser("b"), idle_seconds=100, clock=clock)

    browser = registry.acquire(_turn("conversation-1"))
    clock.advance(50)
    registry.acquire(_turn("conversation-other"))  # sweeps; 50 < 100, must survive

    assert browser.closed is False
    assert registry.acquire(_turn("conversation-1")) is browser


def test_release_extends_the_idle_window_past_a_long_running_turn() -> None:
    clock = _FakeClock()
    built: list[FakeBrowser] = []

    def factory(turn):
        browser = FakeBrowser(f"browser-{len(built)}")
        built.append(browser)
        return browser

    registry = ConversationBrowserRegistry(factory=factory, idle_seconds=100, clock=clock)
    turn = _turn("conversation-1")

    browser = registry.acquire(turn)
    clock.advance(90)  # most of a long turn elapses while the browser is in active use
    registry.release(turn)  # end-of-turn touch, as ChatWorker.run does
    clock.advance(90)  # would have been stale relative to acquire(), not relative to release()
    registry.acquire(_turn("conversation-other"))  # sweeps

    assert browser.closed is False
    assert len(built) == 2  # only the "conversation-other" browser, not a second one for conversation-1


def test_sessions_beyond_the_cap_evict_the_least_recently_used_first() -> None:
    clock = _FakeClock()
    built: dict[str, FakeBrowser] = {}

    def factory(turn):
        browser = FakeBrowser(str(turn["conversation_id"]))
        built[browser.name] = browser
        return browser

    registry = ConversationBrowserRegistry(factory=factory, max_sessions=2, clock=clock)

    registry.acquire(_turn("conversation-1"))
    clock.advance(1)
    registry.acquire(_turn("conversation-2"))
    clock.advance(1)
    registry.acquire(_turn("conversation-3"))  # evicts conversation-1, the least recently used

    assert built["conversation-1"].closed is True
    assert built["conversation-2"].closed is False
    assert built["conversation-3"].closed is False


def test_discard_closes_and_forgets_the_conversation() -> None:
    built: list[FakeBrowser] = []

    def factory(turn):
        browser = FakeBrowser(f"browser-{len(built)}")
        built.append(browser)
        return browser

    registry = ConversationBrowserRegistry(factory=factory, clock=_FakeClock())
    browser = registry.acquire(_turn("conversation-1"))

    registry.discard("conversation-1")

    assert browser.closed is True
    second = registry.acquire(_turn("conversation-1"))
    assert second is not browser


def test_discard_on_an_unknown_conversation_is_a_no_op() -> None:
    registry = ConversationBrowserRegistry(factory=lambda turn: FakeBrowser("b"), clock=_FakeClock())
    registry.discard("never-acquired")  # must not raise


def test_close_all_closes_every_live_session() -> None:
    registry = ConversationBrowserRegistry(factory=lambda turn: FakeBrowser(str(turn["conversation_id"])), clock=_FakeClock())
    a = registry.acquire(_turn("conversation-a"))
    b = registry.acquire(_turn("conversation-b"))

    registry.close_all()

    assert a.closed is True
    assert b.closed is True


def test_a_close_failure_does_not_stop_the_rest_of_the_sweep() -> None:
    class BrokenBrowser(FakeBrowser):
        def close(self) -> None:
            raise RuntimeError("already gone")

    clock = _FakeClock()
    registry = ConversationBrowserRegistry(factory=lambda turn: BrokenBrowser(str(turn["conversation_id"])), idle_seconds=10, clock=clock)
    registry.acquire(_turn("conversation-a"))
    clock.advance(11)

    # Sweeping conversation-a's stale, close()-raising entry must not stop
    # this call from still creating conversation-b's browser.
    result = registry.acquire(_turn("conversation-b"))
    assert result is not None
