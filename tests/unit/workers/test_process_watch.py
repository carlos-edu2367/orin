"""Unit tests for the worker background-process observer (Fase 4)."""
from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import insert, update

from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.events import AgentActivityEventType
from agentos.agentic.workspace import ConversationWorkspace
from agentos.conversations.chat import PostgresChatStore
from agentos.persistence.postgres.agentic_activity import PostgresAgenticActivityStore
from agentos.persistence.postgres.schema import (
    conversation_turns,
    conversations,
    metadata,
    provider_configurations,
    provider_model_catalog,
)
from agentos.persistence.sqlite import create_local_engine
from agentos.scheduler.scheduled_chats import ScheduledChatService
from agentos.workers.process_watch import sweep_exited_processes


def _make_env(tmp_path: Path):
    now = datetime.now(UTC)
    engine = create_local_engine("sqlite+pysqlite://")
    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            insert(provider_model_catalog).values(
                user_id="user-1",
                provider="openrouter",
                model_id="model-1",
                display_name="Model",
                capabilities=[],
                input_modalities=[],
                output_modalities=[],
                refreshed_at=now,
                created_at=now,
                updated_at=now,
            )
        )
        connection.execute(
            insert(provider_configurations).values(
                user_id="user-1",
                provider="openrouter",
                enabled=True,
                model=None,
                base_url=None,
                secret_ref="test",
                key_cooldown_seconds=60,
                catalog_refreshed_at=now,
                created_at=now,
                updated_at=now,
            )
        )
    activity_store = PostgresAgenticActivityStore(engine, "test-cursor-secret")
    store = PostgresChatStore(engine, activity_store)
    scheduler = ScheduledChatService(engine)
    workspace_root = tmp_path / "workspaces"
    workspace_root.mkdir(parents=True, exist_ok=True)
    return engine, store, activity_store, scheduler, workspace_root


def _create_settled_conversation(store: PostgresChatStore, engine, *, state: str = "completed", key: str = "c1"):
    receipt = store.create(
        user_id="user-1",
        message="run background job",
        provider="openrouter",
        model_id="model-1",
        idempotency_key=key,
    )
    now = datetime.now(UTC)
    with engine.begin() as connection:
        connection.execute(
            update(conversation_turns)
            .where(conversation_turns.c.turn_id == receipt.turn_id)
            .values(state=state, updated_at=now)
        )
        connection.execute(
            update(conversations)
            .where(conversations.c.conversation_id == receipt.conversation_id)
            .values(state=state, updated_at=now)
        )
    return receipt


def _write_manifest(workspace_root: Path, conversation_id: str, data: object, *, tag: bool = True) -> Path:
    """Write a manifest; ``tag`` fills ``conversation_id``/``started_at`` the way the current run_command does."""
    ws = ConversationWorkspace(workspace_root, conversation_id)
    manifest_path = ws.root / ".orin" / "logs" / "processes.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    if tag and isinstance(data, dict):
        started = datetime.now(UTC).isoformat()
        data = {
            key: ({"conversation_id": conversation_id, "started_at": started, **value} if isinstance(value, dict) else value)
            for key, value in data.items()
        }
    if isinstance(data, str):
        manifest_path.write_text(data, encoding="utf-8")
    else:
        manifest_path.write_text(json.dumps(data), encoding="utf-8")
    return manifest_path


def test_dead_process_with_and_without_exit_code_records_event_and_keeps_notified_exit_false(tmp_path: Path) -> None:
    engine, store, activity_store, scheduler, workspace_root = _make_env(tmp_path)
    receipt = _create_settled_conversation(store, engine, state="paused", key="conv-dead")
    ws = ConversationWorkspace(workspace_root, receipt.conversation_id)
    log_dir = ws.root / ".orin" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    lines = [f"step {i}" for i in range(1, 31)] + ["ValueError: Soundfile malformed"]
    (log_dir / "tts.log").write_text("\n".join(lines), encoding="utf-8")
    (log_dir / "render.log").write_text("render done\n", encoding="utf-8")

    manifest_path = _write_manifest(
        workspace_root,
        receipt.conversation_id,
        {
            "999901": {
                "command": "python tts_batch.py",
                "description": "TTS",
                "log_path": ".orin/logs/tts.log",
                "exit_code": 1,
                "notified_exit": False,
            },
            "999902": {
                "command": "python render.py",
                "description": "Render",
                "log_path": ".orin/logs/render.log",
                "notified_exit": False,
            },
        },
    )

    recorded = sweep_exited_processes(store, workspace_root=workspace_root, scheduler=scheduler)
    assert len(recorded) == 2

    saved = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert saved["999901"]["exit_event_recorded"] is True
    assert saved["999901"]["exit_code"] == 1
    assert isinstance(saved["999901"]["exited_at"], str) and saved["999901"]["exited_at"]
    assert saved["999901"]["notified_exit"] is False

    assert saved["999902"]["exit_event_recorded"] is True
    assert saved["999902"]["exit_code"] is None
    assert isinstance(saved["999902"]["exited_at"], str) and saved["999902"]["exited_at"]
    assert saved["999902"]["notified_exit"] is False

    page = activity_store.list_events(conversation_id=receipt.conversation_id, user_id="user-1")
    exited_events = [ev for ev in page.events if ev.event_type is AgentActivityEventType.PROCESS_EXITED]
    assert len(exited_events) == 2

    by_pid = {int(ev.payload["pid"]): ev for ev in exited_events}
    tts_ev = by_pid[999901]
    assert "Processo 999901 (TTS) terminou com erro (exit 1)" in tts_ev.summary
    assert tts_ev.payload["exit_code"] == 1
    assert tts_ev.payload["log_path"] == ".orin/logs/tts.log"
    tail = str(tts_ev.payload["output_tail"])
    assert "ValueError: Soundfile malformed" in tail
    assert len(tail.splitlines()) <= 20
    assert "step 1\n" not in tail

    render_ev = by_pid[999902]
    assert "Processo 999902 (Render) terminou" in render_ev.summary
    assert render_ev.payload.get("exit_code") is None
    assert "render done" in str(render_ev.payload["output_tail"])

    # A second sweep never duplicates the events.
    assert sweep_exited_processes(store, workspace_root=workspace_root, scheduler=scheduler) == ()
    page_after = activity_store.list_events(conversation_id=receipt.conversation_id, user_id="user-1")
    assert len([ev for ev in page_after.events if ev.event_type is AgentActivityEventType.PROCESS_EXITED]) == 2

    # And the next turn still receives the model reminder because notified_exit stayed False.
    toolset = AgentToolset(ws, conversation_id=receipt.conversation_id)
    reminder = toolset._check_exited_background_processes()
    assert "999901" in reminder and "999902" in reminder
    saved_after_turn = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert saved_after_turn["999901"]["notified_exit"] is True
    assert saved_after_turn["999901"]["exit_event_recorded"] is True


def test_already_notified_entry_is_skipped(tmp_path: Path) -> None:
    engine, store, activity_store, scheduler, workspace_root = _make_env(tmp_path)
    receipt = _create_settled_conversation(store, engine, state="completed", key="conv-notified")
    _write_manifest(
        workspace_root,
        receipt.conversation_id,
        {
            "999903": {
                "command": "python done.py",
                "description": "already notified by active turn",
                "log_path": ".orin/logs/done.log",
                "notified_exit": True,
            }
        },
    )

    assert sweep_exited_processes(store, workspace_root=workspace_root, scheduler=scheduler) == ()
    page = activity_store.list_events(conversation_id=receipt.conversation_id, user_id="user-1")
    assert [ev for ev in page.events if ev.event_type is AgentActivityEventType.PROCESS_EXITED] == []


def test_conversation_with_active_turn_is_ignored(tmp_path: Path) -> None:
    engine, store, activity_store, scheduler, workspace_root = _make_env(tmp_path)
    for idx, active_state in enumerate(("running", "queued", "waiting_user", "waiting_subagents")):
        receipt = _create_settled_conversation(store, engine, state=active_state, key=f"conv-active-{idx}")
        _write_manifest(
            workspace_root,
            receipt.conversation_id,
            {
                "999904": {
                    "command": "python bg.py",
                    "description": "BG",
                    "log_path": ".orin/logs/bg.log",
                    "notified_exit": False,
                }
            },
        )
        assert sweep_exited_processes(store, workspace_root=workspace_root, scheduler=scheduler) == ()
        page = activity_store.list_events(conversation_id=receipt.conversation_id, user_id="user-1")
        assert [ev for ev in page.events if ev.event_type is AgentActivityEventType.PROCESS_EXITED] == []


def test_untagged_or_stale_entries_are_not_announced(tmp_path: Path) -> None:
    """Entries from older builds (no conversation tag) or started over a week ago never flood the chat."""
    engine, store, activity_store, scheduler, workspace_root = _make_env(tmp_path)
    receipt = _create_settled_conversation(store, engine, state="completed", key="conv-legacy")
    stale = (datetime.now(UTC) - timedelta(days=8)).isoformat()
    _write_manifest(
        workspace_root,
        receipt.conversation_id,
        {
            "999906": {"command": "python old.py", "log_path": ".orin/logs/old.log", "notified_exit": False},
            "999907": {
                "command": "python other.py",
                "conversation_id": "chat_someone_else",
                "started_at": datetime.now(UTC).isoformat(),
                "notified_exit": False,
            },
            "999908": {
                "command": "python stale.py",
                "conversation_id": receipt.conversation_id,
                "started_at": stale,
                "notified_exit": False,
            },
        },
        tag=False,
    )

    assert sweep_exited_processes(store, workspace_root=workspace_root, scheduler=scheduler) == ()
    page = activity_store.list_events(conversation_id=receipt.conversation_id, user_id="user-1")
    assert [ev for ev in page.events if ev.event_type is AgentActivityEventType.PROCESS_EXITED] == []


def test_corrupted_manifest_does_not_break_sweep(tmp_path: Path) -> None:
    engine, store, _, scheduler, workspace_root = _make_env(tmp_path)
    r1 = _create_settled_conversation(store, engine, state="completed", key="conv-bad-json")
    r2 = _create_settled_conversation(store, engine, state="completed", key="conv-good")
    _write_manifest(workspace_root, r1.conversation_id, "{not valid json")
    _write_manifest(
        workspace_root,
        r2.conversation_id,
        {
            "not-a-pid": "junk",
            "999905": {
                "command": "python ok.py",
                "description": "OK",
                "log_path": ".orin/logs/ok.log",
                "exit_code": 0,
                "notified_exit": False,
            },
        },
    )

    recorded = sweep_exited_processes(store, workspace_root=workspace_root, scheduler=scheduler)
    assert len(recorded) == 1
    assert recorded[0]["pid"] == 999905


def test_wake_on_pid_fires_immediately_when_process_exits(tmp_path: Path) -> None:
    engine, store, _, scheduler, workspace_root = _make_env(tmp_path)
    receipt = _create_settled_conversation(store, engine, state="paused", key="conv-wake")
    ws = ConversationWorkspace(workspace_root, receipt.conversation_id)
    log_dir = ws.root / ".orin" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "tts.log").write_text("batch 15/15\nQC failed: ValueError\n", encoding="utf-8")

    # Schedule a wakeup for 1800s bound to pid 999910.
    toolset = AgentToolset(
        ws,
        conversation_id=receipt.conversation_id,
        scheduler=scheduler,
        schedule_context={"user_id": "user-1", "provider": "openrouter", "model_id": "model-1", "timezone": "UTC"},
        public_tool_names=True,
    )
    wake_res = toolset.invoke(
        "ScheduleWakeup",
        {
            "delaySeconds": 1800,
            "reason": "aguardar fim do TTS",
            "prompt": "Verifique o resultado do TTS e continue.",
            "wake_on_pid": 999910,
        },
    )
    assert wake_res.status == "succeeded", wake_res.content

    # While process is alive (use current pid to simulate a live process), wakeup stays pending.
    live_pid = os.getpid()
    _write_manifest(
        workspace_root,
        receipt.conversation_id,
        {
            str(live_pid): {
                "command": "python live.py",
                "description": "Live",
                "log_path": ".orin/logs/live.log",
                "notified_exit": False,
            },
            "999910": {
                "command": "python tts.py",
                "description": "TTS",
                "log_path": ".orin/logs/tts.log",
                "exit_code": 1,
                "notified_exit": False,
            },
        },
    )

    recorded = sweep_exited_processes(store, workspace_root=workspace_root, scheduler=scheduler)
    assert len(recorded) == 1
    assert recorded[0]["pid"] == 999910
    assert recorded[0]["woke_schedule_id"] is not None

    # The wakeup schedule has fired and created a new turn in the conversation with exit notice appended.
    assert scheduler.list_for_conversation("user-1", receipt.conversation_id, sources=("wakeup",)) == []
    view = store.get(receipt.conversation_id, "user-1")
    user_messages = [m for m in view["messages"] if m["role"] == "user"]
    assert len(user_messages) == 2
    woken_prompt = str(user_messages[-1]["content"])
    assert "Verifique o resultado do TTS e continue." in woken_prompt
    assert "999910" in woken_prompt
    assert "exit 1" in woken_prompt
    assert "QC failed: ValueError" in woken_prompt
