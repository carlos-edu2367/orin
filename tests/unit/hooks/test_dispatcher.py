from __future__ import annotations

import threading
import time

from agentos.agentic.lifecycle import HookEvent as E, LifecycleBus
from agentos.hooks.config import HookSpec
from agentos.hooks.dispatcher import HookDispatcher
from agentos.hooks.protocol import HookRun


def _spec(command: str, **values) -> HookSpec:
    base = {"event": E.PRE_TOOL_USE, "matcher": "", "type": "command", "source": "user", "timeout": 5.0, "command": command}
    base.update(values)
    return HookSpec(**base)


class FakeCommands:
    def __init__(self, results: dict[str, HookRun], delay: float = 0.0) -> None:
        self.results = results
        self.delay = delay
        self.calls: list[str] = []
        self.lock = threading.Lock()

    def run(self, spec, payload):
        with self.lock:
            self.calls.append(spec.command)
        if self.delay:
            time.sleep(self.delay)
        result = self.results.get(spec.command)
        if isinstance(result, Exception):
            raise result
        return result or HookRun(0, "", "")


def test_matching_dedupe_merge_and_recording() -> None:
    commands = FakeCommands({
        "deny": HookRun(2, "", "no"),
        "first": HookRun(0, '{"hookSpecificOutput": {"permissionDecision": "allow", "updatedInput": {"command": "a"}}}', ""),
        "second": HookRun(0, '{"hookSpecificOutput": {"updatedInput": {"command": "b"}}}', ""),
    })
    records = []
    specs = (
        _spec("first", source="managed"), _spec("second"), _spec("first", source="user"),
        _spec("deny", matcher="Edit"), _spec("other", event=E.STOP),
    )
    bus = LifecycleBus()
    bus.subscribe(HookDispatcher(specs, command_runner=commands, recorder=records.append))
    decision = bus.emit(E.PRE_TOOL_USE, tool_name="Bash", tool_input={})
    assert sorted(commands.calls) == ["first", "second"]  # deduped; deny's matcher does not apply; Stop not run
    assert decision.permission_decision == "allow" and decision.updated_input == {"command": "a"}
    assert {record["status"] for record in records} == {"ok"} and len(records) == 2
    assert all("stdout" not in record for record in records)
    commands.calls.clear()
    blocked = bus.emit(E.PRE_TOOL_USE, tool_name="Edit")
    assert blocked.permission_decision == "deny" and blocked.reason == "no"
    assert any(record["status"] == "blocked" for record in records)


def test_runs_concurrently() -> None:
    commands = FakeCommands({}, delay=0.5)
    dispatcher = HookDispatcher(tuple(_spec(f"c{i}") for i in range(4)), command_runner=commands)
    started = time.monotonic()
    bus = LifecycleBus()
    bus.subscribe(dispatcher)
    bus.emit(E.PRE_TOOL_USE, tool_name="Bash")
    assert time.monotonic() - started < 1.5


def test_async_hooks_do_not_block_or_decide() -> None:
    commands = FakeCommands({"slow": HookRun(2, "", "would block")}, delay=1.0)
    bus = LifecycleBus()
    bus.subscribe(HookDispatcher((_spec("slow", run_async=True),), command_runner=commands))
    started = time.monotonic()
    decision = bus.emit(E.PRE_TOOL_USE, tool_name="Bash")
    assert time.monotonic() - started < 0.5 and decision.permission_decision is None


def test_failures_are_contained() -> None:
    commands = FakeCommands({"boom": RuntimeError("x"), "strict": RuntimeError("y")})
    records = []

    def broken_recorder(record):
        records.append(record)
        raise RuntimeError("recorder down")

    bus = LifecycleBus()
    bus.subscribe(HookDispatcher((_spec("boom"), _spec("strict", on_failure="block"), _spec("http", type="http", url="https://x")), command_runner=commands, recorder=broken_recorder))
    decision = bus.emit(E.PRE_TOOL_USE, tool_name="Bash")
    assert decision.permission_decision == "deny"  # only the onFailure=block hook blocks
    assert {record["status"] for record in records} == {"failed"}


def test_no_match_returns_none() -> None:
    dispatcher = HookDispatcher((_spec("x", matcher="Edit"),), command_runner=FakeCommands({}))
    from agentos.agentic.lifecycle import LifecycleEvent

    assert dispatcher(LifecycleEvent(E.PRE_TOOL_USE, {"tool_name": "Bash"})) is None
