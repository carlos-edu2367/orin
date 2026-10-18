from __future__ import annotations

import json
from pathlib import Path

from agentos.agentic.agent_tools import AgentToolset, ToolOutcome
from agentos.agentic.lifecycle import HookEvent, LifecycleBus, LifecycleDecision
from agentos.agentic.session import TurnSession
from agentos.agentic.workspace import ConversationWorkspace
from agentos.settings.watcher import reset_snapshots


def _toolset(tmp_path: Path, subscriber) -> AgentToolset:
    bus = LifecycleBus()
    bus.subscribe(subscriber)
    return AgentToolset(ConversationWorkspace(tmp_path, "conv-hooks"), lifecycle=bus)


def test_a_task_completed_block_keeps_the_task_open(tmp_path: Path) -> None:
    toolset = _toolset(tmp_path, lambda event: LifecycleDecision(blocked=True, reason="tests are red") if event.name is HookEvent.TASK_COMPLETED else None)
    created = toolset.invoke("task_create", {"subject": "Ship it"})
    task_id = created.payload["task"]["id"]
    refused = toolset.invoke("task_update", {"taskId": task_id, "status": "completed"})
    assert refused.status == "failed" and refused.error_code == "HOOK_BLOCKED_TASK" and "tests are red" in refused.content
    assert toolset._todos[0]["status"] == "pending"


def test_a_task_created_block_adds_nothing(tmp_path: Path) -> None:
    toolset = _toolset(tmp_path, lambda event: LifecycleDecision(blocked=True, reason="no new tasks") if event.name is HookEvent.TASK_CREATED else None)
    refused = toolset.invoke("task_create", {"subject": "More work"})
    assert refused.error_code == "HOOK_BLOCKED_TASK" and toolset._todos == []


def test_post_tool_use_failure_context_reaches_the_model(tmp_path: Path) -> None:
    def subscriber(event):
        if event.name is HookEvent.POST_TOOL_USE_FAILURE:
            return LifecycleDecision(additional_context=["the file lives under src/"])
        return None

    outcome = _toolset(tmp_path, subscriber).invoke("read_file", {"path": "missing.txt"})
    assert outcome.status == "failed" and "<system-reminder>" in outcome.content and "the file lives under src/" in outcome.content


class Store:
    turn = {"turn_id": "t", "conversation_id": "conv-config", "user_id": "u", "execution_id": "e", "provider": "openrouter",
            "model_id": "m", "user_message_id": "um", "assistant_message_id": "am"}

    def __init__(self) -> None:
        self.deltas: list[str] = []
        self.finished: list[str | None] = []

    def load(self, turn_id): return self.turn
    def history_for_turn(self, turn): return [{"role": "user", "content": "go"}]
    def lifecycle(self, turn, state, **payload): pass
    def delta(self, turn, text): self.deltas.append(text)
    def finish(self, turn, *, failed=False, code=None): self.finished.append(code)
    def record(self, *args, **kwargs): pass
    def main_agent_id(self, turn): return "agent:c:main"


class Agents:
    def list(self): return []


def _runtime(tmp_path: Path, bus: LifecycleBus, store: Store):
    return TurnSession(turn=dict(store.turn), store=store, agents_store=Agents(), memory_store=None,
                       provider_factory=lambda: object(), workspace_root=tmp_path, enable_subagents=False, lifecycle=bus).build_runtime()


def test_a_blocked_config_change_stops_the_turn_until_a_hook_accepts_it(tmp_path: Path) -> None:
    reset_snapshots()
    mode = {"block": True}
    bus = LifecycleBus()
    bus.subscribe(lambda event: LifecycleDecision(blocked=True, reason="settings are frozen") if event.name is HookEvent.CONFIG_CHANGE and mode["block"] else None)
    store = Store()
    first = _runtime(tmp_path, bus, store)
    settings = first.toolset.workspace.root / ".orin" / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(json.dumps({"permissions": {"deny": ["Bash(rm:*)"]}}), encoding="utf-8")
    blocked = _runtime(tmp_path, bus, store)
    assert blocked.config_block_reason and "settings are frozen" in blocked.config_block_reason
    assert blocked.run("t").error_code == "HOOK_BLOCKED_CONFIG" and any("bloqueada" in text for text in store.deltas)
    again = _runtime(tmp_path, bus, store)
    assert again.config_block_reason  # still reported: the change was never accepted
    mode["block"] = False
    assert _runtime(tmp_path, bus, store).config_block_reason is None


def test_subagent_start_context_joins_the_subagent_task(tmp_path: Path) -> None:
    bus = LifecycleBus()
    bus.subscribe(lambda event: LifecycleDecision(additional_context=["use the staging database"]) if event.name is HookEvent.SUBAGENT_START else None)
    store = Store()
    session = TurnSession(turn=dict(store.turn), store=store, agents_store=Agents(), memory_store=None,
                          provider_factory=lambda: object(), workspace_root=tmp_path, enable_subagents=True, lifecycle=bus)
    captured: dict = {}
    session._execute_subagent_run_inner = lambda **kwargs: captured.update(kwargs) or ToolOutcome("succeeded", "ok", "done")
    session._execute_subagent_run(agent_id="a1", builtin_key="explore", clean="explore", task_text="find the bug")
    assert captured["task_text"].startswith("find the bug") and "use the staging database" in captured["task_text"]
