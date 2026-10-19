"""W4: plan mode and open todos are restated to the model every few requests."""
from __future__ import annotations

from agentos.agentic.reminders import ReminderQueue
from agentos.agentic.runtime import AgenticLimits, AgenticTurnRuntime


class _Toolset:
    def __init__(self, plan_mode: bool, todos: list[dict]) -> None:
        self._plan_mode_active = plan_mode
        self._todos = todos

    def schemas(self, allowed=None, kinds=None): return []


def _runtime(toolset: _Toolset) -> AgenticTurnRuntime:
    return AgenticTurnRuntime(
        store=object(), provider=object(), toolset=toolset, system_prompt="p",
        limits=AgenticLimits(), context_reporting=False, reminders=ReminderQueue(),
    )


def _collect(runtime: AgenticTurnRuntime, requests: int) -> list[list[str]]:
    seen = []
    for _ in range(requests):
        messages: list[dict] = []
        runtime._inject_reminders(messages)
        seen.append([str(item.get("content")) for item in messages])
    return seen


def test_plan_mode_is_restated_every_fifth_request() -> None:
    seen = _collect(_runtime(_Toolset(True, [])), 6)
    with_reminder = [index for index, batch in enumerate(seen) if any("plan mode" in text.lower() for text in batch)]
    assert with_reminder == [0, 5]


def test_open_todos_are_restated_every_eighth_request() -> None:
    todos = [{"id": "todo_1", "content": "Write the parser", "status": "in_progress"}, {"id": "todo_2", "content": "Ship", "status": "completed"}]
    seen = _collect(_runtime(_Toolset(False, todos)), 16)
    hits = [index for index, batch in enumerate(seen) if any("Write the parser" in text for text in batch)]
    assert hits == [7, 15]
    assert all("Ship" not in text for batch in seen for text in batch)


def test_nothing_is_added_without_plan_mode_or_open_todos() -> None:
    assert all(batch == [] for batch in _collect(_runtime(_Toolset(False, [{"content": "x", "status": "completed"}])), 10))
