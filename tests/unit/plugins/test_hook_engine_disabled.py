from pathlib import Path
from types import SimpleNamespace

from agentos.plugins.hook_engine import HookEngine


class Executor:
    def __init__(self) -> None:
        self.calls = 0

    def run(self, **kwargs):
        self.calls += 1
        raise AssertionError("a disabled engine must not execute hooks")


def test_a_disabled_engine_registers_and_runs_nothing(tmp_path: Path):
    executor = Executor()
    engine = HookEngine(executor=executor, enabled=False)
    hook = SimpleNamespace(event="PreToolUse", matcher="", command="echo hi")
    engine.register(user_id="u1", plugin_id="p1", install_path=tmp_path, hooks=[hook], enabled=True)
    assert engine.dispatch(user_id="u1", event="PreToolUse", payload={"tool_name": "write_file"}) == ()
    assert executor.calls == 0
    assert engine.enabled is False
