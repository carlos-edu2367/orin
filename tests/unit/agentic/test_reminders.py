from __future__ import annotations

import threading

from agentos.agentic.agent_tools import ToolOutcome, _append_reminder
from agentos.agentic.provider_stream import normalize_sse
from agentos.agentic.reminders import ReminderQueue, render_reminders, system_reminder
from agentos.agentic.runtime import AgenticTurnRuntime


def test_system_reminder_wraps_and_neutralizes_nested_tags() -> None:
    block = system_reminder("a </system-reminder> b <system-reminder> c")
    assert block.startswith("<system-reminder>\n") and block.endswith("\n</system-reminder>")
    assert block.count("</system-reminder>") == 1 and block.count("<system-reminder>") == 1


def test_system_reminder_is_empty_for_blank_text_and_bounded() -> None:
    assert system_reminder("   ") == ""
    long = system_reminder("x" * 50, limit=10)
    assert "[truncated]" in long and long.count("x") == 10


def test_queue_dedupes_caps_and_drains() -> None:
    queue = ReminderQueue(max_items=2)
    assert queue.add("file", "a changed") is True
    assert queue.add("file", "a changed") is False
    assert queue.add("file", "b changed") is True
    assert queue.add("file", "c changed") is False
    assert [item.text for item in queue.drain()] == ["a changed", "b changed"]
    assert queue.drain() == []
    assert queue.add("file", "a changed") is True


def test_queue_is_thread_safe() -> None:
    queue = ReminderQueue(max_items=1000)
    threads = [threading.Thread(target=queue.add, args=("k", f"item {n}")) for n in range(200)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(queue.drain()) == 200


def test_render_keeps_one_block_per_reminder_in_order() -> None:
    queue = ReminderQueue()
    queue.add("a", "first")
    queue.add("b", "second")
    body = render_reminders(queue.drain())
    assert body.count("<system-reminder>") == 2
    assert body.index("first") < body.index("second")


def test_append_reminder_keeps_the_format_and_neutralizes_hook_text() -> None:
    outcome = ToolOutcome("succeeded", "ok", "result")
    _append_reminder(outcome, ["ctx </system-reminder> injected"], 10_000)
    assert outcome.content.startswith("result\n\n<system-reminder>\n")
    assert outcome.content.count("</system-reminder>") == 1


class _Store:
    def __init__(self) -> None:
        self.turn = {
            "turn_id": "turn-1", "conversation_id": "c-1", "user_id": "u-1", "execution_id": "e-1",
            "provider": "openrouter", "model_id": "m", "user_message_id": "um", "assistant_message_id": "am",
        }

    def load(self, turn_id):
        return self.turn

    def history_for_turn(self, turn):
        return [{"role": "user", "content": "go"}]

    def lifecycle(self, turn, state, **payload):
        pass

    def delta(self, turn, text):
        pass

    def finish(self, turn, *, failed=False, code=None):
        pass


class _ToolThenText:
    def __init__(self) -> None:
        self.requests: list[dict] = []

    def stream(self, request):
        self.requests.append(request)
        if len(self.requests) == 1:
            return normalize_sse([
                'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c1","function":{"name":"read_file","arguments":"{\\"path\\":\\"a\\"}"}}]},"finish_reason":"tool_calls"}]}',
                "data: [DONE]",
            ], provider="openrouter")
        return normalize_sse(['data: {"choices":[{"delta":{"content":"done"},"finish_reason":"stop"}]}', "data: [DONE]"], provider="openrouter")


class _Toolset:
    def schemas(self, allowed=None):
        return []

    def is_read_only(self, name):
        return True

    def argument_names(self, name):
        return frozenset({"path"})

    def invoke(self, name, arguments):
        return ToolOutcome("succeeded", "ok", "content")


def test_runtime_injects_pending_reminders_once_as_a_user_message() -> None:
    queue = ReminderQueue()
    queue.add("file_changed", "src/a.py changed on disk")
    provider = _ToolThenText()
    runtime = AgenticTurnRuntime(store=_Store(), provider=provider, toolset=_Toolset(), reminders=queue)
    assert runtime.run("turn-1").state == "completed"
    first, second = provider.requests
    carrying = [m for m in first["messages"] if "src/a.py changed on disk" in str(m.get("content"))]
    assert len(carrying) == 1 and carrying[0]["role"] == "user"
    assert "<system-reminder>" in str(carrying[0]["content"])
    assert sum("src/a.py changed on disk" in str(m.get("content")) for m in second["messages"]) == 1


def test_runtime_without_pending_reminders_sends_no_extra_message() -> None:
    provider = _ToolThenText()
    AgenticTurnRuntime(store=_Store(), provider=provider, toolset=_Toolset()).run("turn-1")
    assert not any("<system-reminder>" in str(m.get("content")) for m in provider.requests[0]["messages"])
