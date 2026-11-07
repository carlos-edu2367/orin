"""Regressions for automatic context compaction (the meter said full, autocompact never fired)."""

from __future__ import annotations

from agentos.agentic.compaction import COMPACTION_HEADING, COMPACTION_SECTIONS
from agentos.agentic.provider_stream import NormalizedStreamItem, StreamKind
from agentos.agentic.runtime import AgenticLimits, AgenticTurnRuntime
from agentos.conversations.chat import _repair_context_usage


class Summarizer:
    def __init__(self) -> None:
        self.requests: list[dict] = []

    def stream(self, request):
        self.requests.append(request)
        return iter([NormalizedStreamItem(StreamKind.TEXT, 1, text="\n".join(f"### {s}\n- d" for s in COMPACTION_SECTIONS))])


class Store:
    def __init__(self, history: list[dict] | None = None) -> None:
        self.turn = {
            "turn_id": "t", "conversation_id": "c", "user_id": "u", "execution_id": "e", "provider": "openrouter",
            "model_id": "m", "user_message_id": "um", "assistant_message_id": "am",
        }
        self.history = list(history or [])
        self.states: list[tuple[str, dict]] = []
        self.boundaries: list[dict] = []
        self.deltas: list[str] = []

    def load(self, turn_id):
        return self.turn

    def history_for_turn(self, turn):
        return list(self.history)

    def lifecycle(self, turn, state, **payload):
        self.states.append((state, dict(payload)))

    def delta(self, turn, text):
        self.deltas.append(text)

    def finish(self, turn, *, failed=False, code=None):
        pass

    def record_compact_boundary(self, turn, **values):
        self.boundaries.append(dict(values))


def _runtime(*, system_prompt: str = "prompt", **limits) -> AgenticTurnRuntime:
    return AgenticTurnRuntime(store=Store(), provider=Summarizer(), system_prompt=system_prompt, limits=AgenticLimits(**limits))


def test_autocompact_counts_the_system_prompt_the_request_carries() -> None:
    # Messages alone (~2.6k tokens) stay under the 16.4k threshold; with the 15k-token system prompt
    # the request is over it, which is what the context meter shows.
    runtime = _runtime(system_prompt="s" * 60_000, max_context_tokens=20_000)
    messages = [{"role": "user", "content": f"old {index} " + "x" * 1_000, "_sequence": index} for index in range(10)]
    messages.append({"role": "user", "content": "current request"})
    runtime._pinned_index = len(messages) - 1

    runtime._maybe_compact(messages, {"turn_id": "t"}, [])

    assert runtime._compaction_count == 1
    assert any(COMPACTION_HEADING in str(item.get("content")) for item in messages)
    assert messages[runtime._pinned_index]["content"] == "current request"


def test_autocompact_summarizes_a_window_filled_by_a_few_large_units() -> None:
    runtime = _runtime(max_context_tokens=2_000)
    messages = [{"role": "user", "content": f"old {index} " + "x" * 4_000, "_sequence": index} for index in range(4)]
    messages.append({"role": "user", "content": "current request"})
    runtime._pinned_index = len(messages) - 1

    runtime._maybe_compact(messages, {"turn_id": "t"}, [])

    assert runtime._compaction_count == 1
    assert any(COMPACTION_HEADING in str(item.get("content")) for item in messages)
    assert messages[runtime._pinned_index]["content"] == "current request"
    # The two most recent earlier units are kept verbatim.
    assert sum(1 for item in messages if str(item.get("content", "")).startswith("old ")) == 2
    assert runtime.store.boundaries and runtime.store.boundaries[-1]["through_sequence"] == 1


def test_a_hook_blocked_autocompact_is_not_retried_with_fewer_units() -> None:
    from agentos.agentic.lifecycle import HookEvent, LifecycleBus, LifecycleDecision

    bus = LifecycleBus()
    pre_compacts: list[object] = []

    def block(event):
        if event.name is HookEvent.PRE_COMPACT:
            pre_compacts.append(event)
            return LifecycleDecision(blocked=True, reason="no")
        return None

    bus.subscribe(block)
    runtime = AgenticTurnRuntime(store=Store(), provider=Summarizer(), system_prompt="p", lifecycle=bus,
                                 limits=AgenticLimits(max_context_tokens=2_000))
    messages = [{"role": "user", "content": "x" * 4_000} for _ in range(10)]
    messages.append({"role": "user", "content": "current request"})
    runtime._pinned_index = len(messages) - 1

    runtime._maybe_compact(messages, {"turn_id": "t"}, [])

    assert runtime._compaction_count == 0 and len(pre_compacts) == 1


def test_manual_compaction_reports_the_new_context_size() -> None:
    history = [
        {"role": "user", "content": "build the API", "_sequence": 1}, {"role": "assistant", "content": "done " + "x" * 8_000, "_sequence": 2},
        {"role": "user", "content": "add tests", "_sequence": 3}, {"role": "assistant", "content": "added " + "y" * 8_000, "_sequence": 4},
    ]
    store = Store(history)
    runtime = AgenticTurnRuntime(store=store, provider=Summarizer(), compact_request="", context_reporting=True,
                                 limits=AgenticLimits())
    assert runtime.run("t").state == "completed"

    compacted_at = next(index for index, (state, _) in enumerate(store.states) if state == "context_compacted")
    updates = [payload for state, payload in store.states[compacted_at:] if state == "context_updated"]
    assert updates, "the meter must be refreshed after /compact"
    pre_tokens = store.states[compacted_at][1]["pre_tokens"]
    assert 0 < updates[-1]["used_tokens"] < pre_tokens
    assert updates[-1]["compaction_count"] == 1


def test_repaired_prompt_count_does_not_include_memory_twice() -> None:
    repaired = _repair_context_usage({
        "used_tokens": 1_000, "history_tokens": 300, "input_tokens": 100, "tools_tokens": 200,
        "skills_tokens": 0, "mcps_tokens": 0, "memory_tokens": 150,
    })
    assert repaired["system_prompt_tokens"] == 250
