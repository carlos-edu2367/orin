"""W4 review fixes for Monitor output: one pending reminder per monitor within a budget (R9), and a subagent's
monitor reaches the running parent turn instead of waking the conversation (R9)."""
from __future__ import annotations

import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import insert

from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.session import TurnSession
from agentos.agentic.reminders import MAX_MONITOR_REMINDERS, MONITOR_CHAR_BUDGET, ReminderQueue, render_reminders
from agentos.agentic.tools.scheduling import monitor as monitor_module
from agentos.agentic.tools.scheduling.monitor import MAX_EVENT_CHARS, MonitorRegistry, MonitorTask
from agentos.agentic.workspace import ConversationWorkspace
from agentos.conversations.chat import PostgresChatStore
from agentos.persistence.postgres.conversation_agents import ConversationAgentStore
from agentos.persistence.postgres.schema import metadata, provider_configurations, provider_model_catalog
from agentos.persistence.sqlite import create_local_engine
from agentos.scheduler.scheduled_chats import ScheduledChatService
from tests.unit.agentic.test_parallel_and_background_subagents import TURN, _RecordingStore, _text_batch


def _until(check, seconds: float = 20.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(0.1)
    raise AssertionError("condition not reached")


# -- the reminder queue -------------------------------------------------------

def test_monitor_reminders_take_at_most_half_of_the_queue() -> None:
    queue = ReminderQueue()
    accepted = [queue.add("monitor", f"monitor {number} output", key=f"monitor:m{number}") for number in range(MAX_MONITOR_REMINDERS + 4)]
    assert sum(accepted) == MAX_MONITOR_REMINDERS
    assert queue.add("file_changed", "src/app.py changed on disk", key="fc-1") is True


def test_monitor_reminders_share_one_character_budget() -> None:
    queue = ReminderQueue()
    text = "x" * (MONITOR_CHAR_BUDGET // 3)
    assert [queue.add("monitor", text, key=f"monitor:m{number}") for number in range(4)] == [True, True, True, False]
    assert queue.add("file_changed", "src/app.py changed on disk", key="fc-2") is True


def test_replace_rewrites_a_pending_reminder_in_place_only() -> None:
    queue = ReminderQueue()
    assert queue.add("monitor", "first", key="monitor:m1") is True
    assert queue.replace("monitor:m1", "monitor", "first and second") is True
    assert queue.replace("monitor:m2", "monitor", "unknown key") is False
    assert [item.text for item in queue.drain()] == ["first and second"]
    assert queue.replace("monitor:m1", "monitor", "after the drain") is False


# -- the monitor's delivery, on a synthetic task (no process) -----------------------

def _task(tmp_path: Path, sink: ReminderQueue) -> MonitorTask:
    return MonitorTask(
        monitor_id="monitor_burst", conversation_id="conversation-1", command="burst", description="burst test",
        pid=0, log_file=tmp_path / "burst.log", process=None, persistent=False, timeout_ms=1000,
        deadline=time.monotonic() + 100, sink=sink,
    )


def _emit(registry: MonitorRegistry, task: MonitorTask, lines: list[str]) -> None:
    now = time.monotonic()
    with task.lock:
        registry._append_locked(task, lines, now)
        registry._deliver_locked(task, now)


def test_a_burst_of_200_lines_is_one_reminder_within_budget(tmp_path: Path) -> None:
    registry, queue = MonitorRegistry(), ReminderQueue()
    task = _task(tmp_path, queue)
    for batch in range(20):  # twenty polls, ten lines each, while nothing drains the queue
        _emit(registry, task, [f"line {number:03d} " + "y" * 100 for number in range(batch * 10, batch * 10 + 10)])

    items = queue.drain()
    assert len(items) == 1
    assert len(items[0].text) <= MAX_EVENT_CHARS
    assert "line 199" in items[0].text  # the newest line is what the model sees
    assert "earlier line(s) omitted" in items[0].text
    assert len(render_reminders(items)) <= MONITOR_CHAR_BUDGET


def test_a_busy_monitor_leaves_room_for_a_file_changed_reminder(tmp_path: Path) -> None:
    registry, queue = MonitorRegistry(), ReminderQueue()
    task = _task(tmp_path, queue)
    for number in range(40):  # forty polls while nothing drains the queue
        _emit(registry, task, [f"line {number}"])
    assert queue.add("file_changed", "src/app.py changed on disk", key="fc-1") is True
    kinds = [item.kind for item in queue.drain()]
    assert kinds == ["monitor", "file_changed"]


def test_lines_after_a_drain_start_a_new_reminder_without_the_old_ones(tmp_path: Path) -> None:
    registry, queue = MonitorRegistry(), ReminderQueue()
    task = _task(tmp_path, queue)
    _emit(registry, task, ["old line A", "old line B"])
    first = [item.text for item in queue.drain()]
    assert len(first) == 1 and "old line A" in first[0] and "old line B" in first[0]
    _emit(registry, task, ["new line C"])
    second = [item.text for item in queue.drain()]
    assert len(second) == 1 and second[0].endswith("new line C") and "old line" not in second[0]


# -- a subagent's monitor, through a real process -----------------------------------

@pytest.fixture()
def turn(tmp_path: Path):
    now = datetime.now(UTC)
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
    conversation_id = PostgresChatStore(engine).create(user_id="user-1", message="hi", provider="openrouter", model_id="model-1", idempotency_key="c1").conversation_id
    scheduler = ScheduledChatService(engine)

    def make(reminders: ReminderQueue | None) -> AgentToolset:
        return AgentToolset(
            ConversationWorkspace(tmp_path, "c1"), conversation_id=conversation_id, scheduler=scheduler,
            reminders=reminders, schedule_context={"user_id": "user-1", "provider": "openrouter", "model_id": "model-1", "timezone": "UTC"},
            public_tool_names=True,
        )

    yield make, scheduler, conversation_id
    monitor_module.MONITORS.stop_all()


def test_a_monitor_sinks_into_the_queue_it_was_given_and_never_wakes_while_attached(turn, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(monitor_module, "WAKE_DEBOUNCE_SECONDS", 0.1)
    monkeypatch.setattr(monitor_module, "WAKE_MIN_INTERVAL_SECONDS", 0.0)
    make, scheduler, conversation_id = turn
    parent_queue = ReminderQueue()
    parent = make(parent_queue)
    parent._attach_monitors()  # the parent turn is running
    subagent = make(parent_queue)
    command = f'"{sys.executable}" -c "import time; print(\'BUILD FAILED\', flush=True); time.sleep(60)"'
    outcome = subagent.invoke("Monitor", {"command": command, "description": "build"})
    assert outcome.status == "succeeded", outcome.content

    texts: list[str] = []

    def received() -> bool:
        texts.extend(item.text for item in parent_queue.drain())
        return any("BUILD FAILED" in text for text in texts)

    _until(received)
    time.sleep(1.0)  # longer than the wake debounce: a wake would have been scheduled by now
    assert scheduler.list_for_conversation("user-1", conversation_id, sources=("monitor",)) == []


def test_a_subagent_is_built_with_the_turn_reminder_queue(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _RecordingStore()
    engine = create_local_engine(f"sqlite:///{(tmp_path / 'subagent.db').as_posix()}")
    metadata.create_all(engine)
    agents_store = ConversationAgentStore(engine, conversation_id=TURN["conversation_id"], user_id=TURN["user_id"])

    class _ChildProvider:
        def stream(self, _request):
            return iter(_text_batch("Build looked at."))

    session = TurnSession(
        turn=dict(TURN), store=store, agents_store=agents_store, memory_store=None,
        provider_factory=_ChildProvider, workspace_root=tmp_path, child_provider_factory=lambda _model: _ChildProvider(),
    )
    built: list[dict] = []
    original_toolset = TurnSession._toolset

    def recording_toolset(self, **kwargs):
        toolset = original_toolset(self, **kwargs)
        built.append({**kwargs, "toolset": toolset})
        return toolset

    monkeypatch.setattr(TurnSession, "_toolset", recording_toolset)
    outcome = session._run_subagent(subagent_type="explore", task="Look at the build", description="build")
    engine.dispose()

    assert outcome.status == "succeeded", outcome.content
    subagent_builds = [kwargs for kwargs in built if kwargs.get("subagents") is False]
    assert subagent_builds, "the subagent toolset was never built"
    # A monitor the subagent starts sinks into the running turn's queue, not into a queue nobody drains (W4-R9).
    # The subagent's other reminders go to its own queue, which its runtime drains (W5, W3-R26).
    assert all(kwargs["toolset"]._monitor_sink is session._reminders for kwargs in subagent_builds)
    assert all(kwargs.get("reminders") is not session._reminders for kwargs in subagent_builds)
