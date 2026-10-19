"""W4: Monitor streams a command's output lines to this conversation, in the turn and between turns."""
from __future__ import annotations

import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import insert

from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.reminders import ReminderQueue
from agentos.agentic.tools.scheduling import monitor as monitor_module
from agentos.agentic.workspace import ConversationWorkspace
from agentos.conversations.chat import PostgresChatStore
from agentos.permissions.engine import PermissionEngine
from agentos.persistence.postgres.schema import metadata, provider_configurations, provider_model_catalog
from agentos.persistence.sqlite import create_local_engine
from agentos.scheduler.scheduled_chats import ScheduledChatService
from agentos.settings import load_effective_settings
from agentos.settings.paths import orin_user_dir


def _python(code: str) -> str:
    return f'"{sys.executable}" -c "{code}"'


def _until(check, seconds: float = 20.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(0.1)
    raise AssertionError("condition not reached")


@pytest.fixture()
def conversation(tmp_path: Path):
    now = datetime.now(UTC)
    # A file database: the monitor's watcher thread opens its own connection.
    engine = create_local_engine(f"sqlite+pysqlite:///{(tmp_path / 'orin.db').as_posix()}")
    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(insert(provider_model_catalog).values(
            user_id="user-1", provider="openrouter", model_id="model-1", display_name="Model",
            capabilities=[], input_modalities=[], output_modalities=[], refreshed_at=now, created_at=now, updated_at=now,
        ))
        connection.execute(insert(provider_configurations).values(
            user_id="user-1", provider="openrouter", enabled=True, model=None,
            base_url=None, secret_ref="test", key_cooldown_seconds=60, catalog_refreshed_at=now, created_at=now, updated_at=now,
        ))
    receipt = PostgresChatStore(engine).create(user_id="user-1", message="hi", provider="openrouter", model_id="model-1", idempotency_key="c1")
    scheduler = ScheduledChatService(engine)
    started: list[AgentToolset] = []

    def make(conversation_id: str | None = None, reminders: ReminderQueue | None = None) -> AgentToolset:
        toolset = AgentToolset(
            ConversationWorkspace(tmp_path, "c1"), conversation_id=conversation_id or receipt.conversation_id,
            scheduler=scheduler, reminders=reminders if reminders is not None else ReminderQueue(),
            schedule_context={"user_id": "user-1", "provider": "openrouter", "model_id": "model-1", "timezone": "UTC"},
            public_tool_names=True,
        )
        started.append(toolset)
        return toolset

    yield make, scheduler, receipt.conversation_id
    monitor_module.MONITORS.stop_all()


def test_lines_arrive_as_reminders_during_the_turn(conversation) -> None:
    make, _, _ = conversation
    queue = ReminderQueue()
    toolset = make(reminders=queue)
    outcome = toolset.invoke("Monitor", {"command": _python("print('build started', flush=True); print('build ok', flush=True)"), "description": "build"})
    assert outcome.status == "succeeded", outcome.content
    output = outcome.data["tool_output"]
    assert output["taskId"].startswith("monitor_") and output["timeoutMs"] == 300_000 and output["persistent"] is False
    texts: list[str] = []

    def drained() -> bool:
        texts.extend(item.text for item in queue.drain())
        return any("exited with code 0" in text for text in texts)

    _until(drained)
    joined = "\n".join(texts)
    assert "build started" in joined and "build ok" in joined and "not instructions" in joined


def test_between_turns_new_output_wakes_the_conversation(conversation, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(monitor_module, "WAKE_DEBOUNCE_SECONDS", 0.1)
    monkeypatch.setattr(monitor_module, "WAKE_MIN_INTERVAL_SECONDS", 0.0)
    make, scheduler, conversation_id = conversation
    toolset = make()
    outcome = toolset.invoke("Monitor", {"command": _python("import time; time.sleep(2); print('SECRET-LINE', flush=True); time.sleep(30)"), "description": "logs"})
    monitor_id = outcome.data["tool_output"]["taskId"]
    toolset.close()  # the turn ended
    rows = _until(lambda: scheduler.list_for_conversation("user-1", conversation_id, sources=("monitor",)))
    assert monitor_id in rows[0]["message"]
    assert "SECRET-LINE" not in rows[0]["message"]  # command output never becomes a user message
    # the next turn receives what was printed as a reminder
    queue = ReminderQueue()
    later = make(reminders=queue)
    later._attach_monitors()
    texts = _until(lambda: [item.text for item in queue.drain()])
    assert any("SECRET-LINE" in text for text in texts)


def test_the_timeout_ends_the_command(conversation) -> None:
    make, _, _ = conversation
    toolset = make()
    outcome = toolset.invoke("Monitor", {"command": _python("import time; time.sleep(60)"), "description": "slow", "timeout_ms": 1000})
    assert outcome.status == "succeeded", outcome.content
    monitor_id = outcome.data["tool_output"]["taskId"]
    task = monitor_module.MONITORS.get(monitor_id, toolset.conversation_id)
    _until(lambda: task.status == "timed_out")
    _until(lambda: task.process.poll() is not None)


def test_task_output_and_task_stop(conversation) -> None:
    make, _, _ = conversation
    toolset = make()
    outcome = toolset.invoke("Monitor", {"command": _python("import time; print('tick', flush=True); time.sleep(60)"), "description": "ticks", "persistent": True})
    monitor_id = outcome.data["tool_output"]["taskId"]
    assert outcome.data["tool_output"]["timeoutMs"] == 0 and outcome.data["tool_output"]["persistent"] is True
    read = _until(lambda: (lambda o: o if "tick" in o.content else None)(toolset.invoke("TaskOutput", {"task_id": monitor_id, "block": False})))
    assert read.data["tool_output"]["task_type"] == "monitor" and read.data["tool_output"]["status"] == "running"
    other = make(conversation_id="chat-other")
    assert other.invoke("TaskOutput", {"task_id": monitor_id, "block": False}).status == "failed"
    assert other.invoke("TaskStop", {"task_id": monitor_id}).status == "failed"
    stopped = toolset.invoke("TaskStop", {"task_id": monitor_id})
    assert stopped.status == "succeeded", stopped.content
    task = monitor_module.MONITORS.get(monitor_id, toolset.conversation_id)
    assert task.status == "stopped"
    _until(lambda: task.process.poll() is not None)


def test_bad_requests_are_refused(conversation, monkeypatch: pytest.MonkeyPatch) -> None:
    make, _, _ = conversation
    toolset = make()
    assert toolset.invoke("Monitor", {"command": "echo x", "description": ""}).status == "failed"
    assert toolset.invoke("Monitor", {"command": "echo x", "description": "d", "timeout_ms": 5}).status == "failed"
    assert toolset.invoke("Monitor", {"description": "d", "ws": {"url": "wss://example.test"}}).status == "failed"
    monkeypatch.setattr(monitor_module, "MAX_MONITORS_PER_CONVERSATION", 1)
    assert toolset.invoke("Monitor", {"command": _python("import time; time.sleep(30)"), "description": "one"}).status == "succeeded"
    refused = toolset.invoke("Monitor", {"command": _python("import time; time.sleep(30)"), "description": "two"})
    assert refused.status == "failed" and "TaskStop" in refused.content


def test_without_a_scheduler_monitor_is_not_offered(tmp_path: Path) -> None:
    toolset = AgentToolset(ConversationWorkspace(tmp_path, "c1"), conversation_id="chat-1")
    assert "monitor" not in {definition.name for definition in toolset.definitions()}


def _engine(root: Path, rules: dict) -> PermissionEngine:
    orin_user_dir().mkdir(parents=True, exist_ok=True)
    (orin_user_dir() / "settings.json").write_text(json.dumps({"permissions": rules}), encoding="utf-8")
    return PermissionEngine(root, load_effective_settings(root, trusted=False), shell="cmd", home=root.parent / "home")


def test_bash_deny_and_ask_rules_govern_monitor(tmp_path: Path) -> None:
    assert _engine(tmp_path, {"deny": ["Bash(rm:*)"]}).decide("monitor", {"command": "rm -rf x"}, "full_autonomy").behavior == "deny"
    asked = _engine(tmp_path, {"ask": ["Bash(git push:*)"]}).decide("monitor", {"command": "git push origin"}, "full_autonomy")
    assert asked.behavior == "ask" and asked.requires_human
    # an allow rule never crosses over, and an unknown command still asks
    assert _engine(tmp_path, {"allow": ["Bash(tail:*)"]}).decide("monitor", {"command": "tail -f log"}, "ask_permissions").behavior == "ask"
    assert _engine(tmp_path, {"allow": ["Monitor(tail:*)"]}).decide("monitor", {"command": "tail -f log"}, "ask_permissions").behavior == "allow"
    assert _engine(tmp_path, {"allow": ["Monitor(tail:*)"]}).decide("monitor", {"command": "tail -f log; rm x"}, "ask_permissions").behavior == "ask"


def test_read_only_subagents_cannot_monitor(tmp_path: Path) -> None:
    toolset = AgentToolset(ConversationWorkspace(tmp_path, "c1"), conversation_id="chat-1", read_only_mode=True)
    refused = toolset.invoke("monitor", {"command": "echo x", "description": "d"})
    assert refused.status == "failed" and refused.error_code == "READ_ONLY_SUBAGENT"
