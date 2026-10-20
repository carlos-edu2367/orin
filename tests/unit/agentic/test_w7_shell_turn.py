"""W7: a `!command` turn runs the person's command as one tool call, answers with its output and never calls the model."""
from __future__ import annotations

import sys
from pathlib import Path

from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.runtime import AgenticTurnRuntime
from agentos.agentic.workspace import ConversationWorkspace

from tests.unit.agentic.test_manual_compact_runtime import Store


class NoModel:
    def stream(self, request):
        raise AssertionError("a shell turn must not call the model")


def _runtime(tmp_path: Path, command: str):
    store = Store([{"role": "user", "content": f"!{command}"}])
    store.events = []
    store.lifecycle = lambda turn, state, **payload: store.events.append((state, payload.get("tool_name"), payload.get("invocation_id")))
    toolset = AgentToolset(ConversationWorkspace(tmp_path, "c"))
    return store, AgenticTurnRuntime(store=store, provider=NoModel(), toolset=toolset, shell_command=command)


def test_the_command_runs_and_its_output_is_the_answer(tmp_path: Path) -> None:
    store, runtime = _runtime(tmp_path, f'"{sys.executable}" -c "print(40 + 2)"')
    result = runtime.run("t")
    assert result.state == "completed" and result.error_code is None
    answer = "".join(store.deltas)
    assert answer.startswith("```") and "42" in answer
    assert store.finished == [None]
    started = [event for event in store.events if event[0] == "tool_started"]
    assert started and started[0][1] == "run_command"  # an ordinary tool card for the interface


def test_a_failing_command_says_so(tmp_path: Path) -> None:
    store, runtime = _runtime(tmp_path, f'"{sys.executable}" -c "import sys; print(\'oops\'); sys.exit(3)"')
    result = runtime.run("t")
    assert result.state == "completed"
    assert "oops" in "".join(store.deltas)


def test_an_empty_command_does_nothing(tmp_path: Path) -> None:
    store, runtime = _runtime(tmp_path, "   ")
    result = runtime.run("t")
    assert result.error_code == "EMPTY_SHELL_COMMAND" and "Nenhum comando" in "".join(store.deltas)
