from pathlib import Path

from agentos.plugins.hook_engine import HookEngine
from agentos.plugins.models import HookContribution


class RecordingExecutor:
    def __init__(self):
        self.calls = []

    def run(self, *, command, install_path, payload, timeout_seconds, hook_id=""):
        self.calls.append({"command": command, "payload": payload, "hook_id": hook_id})
        from agentos.plugins.hook_executor import HookOutcome
        return HookOutcome(hook_id, "ok", f"out:{hook_id}", "", 0)


def _engine(executor, *, enabled=True):
    engine = HookEngine(executor=executor)
    engine.register(
        user_id="u1", plugin_id="demo", install_path=Path("/pkg"), enabled=enabled,
        hooks=(
            HookContribution("demo:SessionStart:0", "SessionStart", "", "cmd-start", 10),
            HookContribution("demo:PostToolUse:0", "PostToolUse", "Write|Edit", "cmd-tool", 10),
        ),
    )
    return engine


def test_a_matcher_filters_by_tool_name():
    executor = RecordingExecutor()
    engine = _engine(executor)

    engine.dispatch(user_id="u1", event="PostToolUse", payload={"tool_name": "Read"})
    assert executor.calls == []

    engine.dispatch(user_id="u1", event="PostToolUse", payload={"tool_name": "Write"})
    assert [call["hook_id"] for call in executor.calls] == ["demo:PostToolUse:0"]


def test_an_empty_matcher_runs_for_every_event_of_its_kind():
    executor = RecordingExecutor()

    outcomes = _engine(executor).dispatch(user_id="u1", event="SessionStart", payload={})

    assert [outcome.stdout for outcome in outcomes] == ["out:demo:SessionStart:0"]


def test_hooks_without_consent_never_run():
    executor = RecordingExecutor()

    outcomes = _engine(executor, enabled=False).dispatch(user_id="u1", event="SessionStart", payload={})

    assert executor.calls == [] and outcomes == ()


def test_unregistering_a_plugin_stops_its_hooks():
    executor = RecordingExecutor()
    engine = _engine(executor)

    engine.unregister(user_id="u1", plugin_id="demo")

    assert engine.dispatch(user_id="u1", event="SessionStart", payload={}) == ()


def test_an_executor_that_raises_never_escapes_the_engine():
    class Exploding:
        def run(self, **_kwargs):
            raise RuntimeError("boom")

    outcomes = _engine(Exploding()).dispatch(user_id="u1", event="SessionStart", payload={})

    assert [outcome.status for outcome in outcomes] == ["failed"]
