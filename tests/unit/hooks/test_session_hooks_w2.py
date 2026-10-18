from __future__ import annotations

import json
from pathlib import Path

from agentos.agentic.events import AgentActivityEventType
from agentos.agentic.lifecycle import HookEvent, LifecycleBus
from agentos.agentic.session import TurnSession
from agentos.settings.paths import orin_user_dir
from agentos.settings.watcher import reset_snapshots

TURN = {
    "turn_id": "turn-1", "conversation_id": "chat_session", "user_id": "local-user", "execution_id": "exe-1",
    "provider": "openrouter", "model_id": "test-model", "user_message_id": "msg-user", "assistant_message_id": "msg-assistant",
}


class Store:
    def __init__(self, history: list[dict[str, object]]) -> None:
        self.history = history
        self.activity: list[tuple[AgentActivityEventType, str]] = []

    def load(self, turn_id: str) -> dict:
        return TURN

    def history_for_turn(self, turn) -> list[dict[str, object]]:
        return list(self.history)

    def delta(self, turn, text: str) -> None:
        pass

    def finish(self, turn, *, failed: bool = False, code: str | None = None) -> None:
        pass

    def record(self, turn, event_type, summary, payload=None, *, agent_id=None, parent_agent_id=None) -> None:
        self.activity.append((event_type, summary))

    def main_agent_id(self, turn) -> str:
        return f"agent:{turn['conversation_id']}:main"


class Agents:
    def list(self) -> list[dict]:
        return []


def _user(data: dict) -> None:
    orin_user_dir().mkdir(parents=True, exist_ok=True)
    (orin_user_dir() / "settings.json").write_text(json.dumps(data), encoding="utf-8")


def _session(tmp_path: Path, history: list[dict[str, object]], **kwargs) -> tuple[TurnSession, Store]:
    store = Store(history)
    session = TurnSession(
        turn=dict(TURN), store=store, agents_store=Agents(), memory_store=None,
        provider_factory=lambda: object(), workspace_root=tmp_path, enable_subagents=False, **kwargs,
    )
    return session, store


def test_user_hook_runs_and_blocks_prompt(tmp_path):
    import sys
    script = tmp_path / "block.py"
    script.write_text("import sys, json; d=json.load(sys.stdin); print('no secrets', file=sys.stderr); sys.exit(2 if 'password' in d['prompt'] else 0)", encoding="utf-8")
    _user({"hooks": {"UserPromptSubmit": [{"hooks": [{"command": f'"{sys.executable}" "{script}"'}]}]}})
    session, store = _session(tmp_path, [{"role": "user", "content": "my password is x"}])
    runtime = session.build_runtime()
    assert runtime.prompt_block_reason == "no secrets"
    assert any(item[0].value == "plugin.hook_executed" for item in store.activity)


def test_session_start_context_reaches_the_prompt_and_env_file(tmp_path):
    import sys
    script = tmp_path / "start.py"
    script.write_text("import os; open(os.environ['CLAUDE_ENV_FILE'],'a').write('export FROM_HOOK=1\\n'); print('branch: main')", encoding="utf-8")
    _user({"hooks": {"SessionStart": [{"hooks": [{"command": f'"{sys.executable}" "{script}"'}]}]}})
    session, store = _session(tmp_path, [{"role": "user", "content": "hi"}])
    runtime = session.build_runtime()
    assert "branch: main" in runtime.system_prompt + runtime.volatile_prompt
    assert runtime.toolset._extra_env.get("FROM_HOOK") == "1"


def test_untrusted_repo_hooks_do_not_run(tmp_path):
    marker = tmp_path / "ran.txt"
    session, store = _session(tmp_path, [{"role": "user", "content": "hi"}])
    (session.workspace.root / ".claude").mkdir()
    (session.workspace.root / ".claude" / "settings.json").write_text(json.dumps({"hooks": {"UserPromptSubmit": [{"hooks": [{"command": f"echo x > \"{marker}\""}]}]}}), encoding="utf-8")
    session.build_runtime()
    assert not marker.exists()


def test_config_change_has_claude_code_shape(tmp_path):
    reset_snapshots()
    bus = LifecycleBus()
    events = []
    bus.subscribe(events.append)
    first, _ = _session(tmp_path, [{"role": "user", "content": "x"}], lifecycle=bus)
    first.build_runtime()
    (first.workspace.root / ".orin").mkdir(exist_ok=True)
    (first.workspace.root / ".orin" / "settings.json").write_text("{}", encoding="utf-8")
    events.clear()
    second, _ = _session(tmp_path, [{"role": "user", "content": "y"}], lifecycle=bus)
    second.build_runtime()
    changes = [dict(e.payload) for e in events if e.name is HookEvent.CONFIG_CHANGE]
    assert changes and changes[0]["source"] == "project_settings" and changes[0]["file_path"].endswith("settings.json")


def test_agent_verifier_cannot_start_another_verifier(tmp_path):
    session, _ = _session(tmp_path, [{"role": "user", "content": "x"}])
    calls = []

    def fake_subagent(subagent_type, task, *, description="", **_kwargs):
        calls.append(task)
        nested = session._hook_agent("nested check")  # the verifier's own tool call firing the hook
        return type("Outcome", (), {"content": f"nested={nested!r}"})()

    session._run_subagent = fake_subagent
    assert session._hook_agent("outer check") == "nested=''"
    assert calls == ["outer check"]
    assert session._hook_agent_running is False


def test_a_prompt_blocked_by_a_hook_is_not_expanded_or_written_to_a_plan(tmp_path):
    # W2-R6: a blocked prompt triggers no @-mention read and no plan scaffold.
    import sys
    script = tmp_path / "block.py"
    script.write_text("import sys, json; d=json.load(sys.stdin); sys.exit(2 if 'hunter2' in d['prompt'] else 0)", encoding="utf-8")
    _user({"hooks": {"UserPromptSubmit": [{"hooks": [{"command": f'"{sys.executable}" "{script}"'}]}]}})
    store = Store([{"role": "user", "content": "my password is hunter2, plan this @notes.txt"}])
    (tmp_path / "notes.txt").write_text("notes\n", encoding="utf-8")
    turn = {**TURN, "code_mode": "code", "permission_mode": "plan_mode", "code_mode_plan_path": ".orin/plans/turn-1-plan.md"}
    session = TurnSession(
        turn=turn, store=store, agents_store=Agents(), memory_store=None,
        provider_factory=lambda: object(), workspace_root=tmp_path, enable_subagents=False,
    )
    runtime = session.build_runtime()
    assert runtime.prompt_block_reason == "blocked by hook"
    written = [path for path in session.workspace.root.rglob("*") if path.is_file() and ".orin" in path.parts]
    assert all("hunter2" not in path.read_text(encoding="utf-8", errors="replace") for path in written)
    assert not any(path.name.endswith("-plan.md") for path in written)
    assert session._read_file_state == {}


def test_concurrent_agent_hooks_run_exactly_one_verifier(tmp_path):
    # W2-R8: the guard is atomic; a second verifier started while one runs is refused, not queued.
    import threading
    import time

    session, _ = _session(tmp_path, [{"role": "user", "content": "x"}])
    calls = []

    def slow_subagent(subagent_type, task, *, description="", **_kwargs):
        calls.append(task)
        time.sleep(0.4)
        return type("Outcome", (), {"content": '{"ok": true}'})()

    session._run_subagent = slow_subagent
    results = []
    threads = [threading.Thread(target=lambda: results.append(session._hook_agent("check"))) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(calls) == 1
    assert sorted(results) == ["", '{"ok": true}']
    assert session._hook_agent_running is False


def test_a_cancelled_verifier_is_told_to_stop(tmp_path):
    # W2-R8: the cancel event of a timed-out verifier reaches the verifier's own subagent predicate.
    import threading

    session, _ = _session(tmp_path, [{"role": "user", "content": "x"}])
    cancel = threading.Event()
    seen = []

    def fake_subagent(subagent_type, task, *, description="", hook_cancel=None, **_kwargs):
        seen.append(session._child_cancelled(hook_cancel)({"turn_id": "child"}))
        cancel.set()
        seen.append(session._child_cancelled(hook_cancel)({"turn_id": "child"}))
        return type("Outcome", (), {"content": ""})()

    session._run_subagent = fake_subagent
    session._hook_agent("check", cancel)
    assert seen == [False, True]
