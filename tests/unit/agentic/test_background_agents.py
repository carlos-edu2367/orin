"""W5: a background agent writes its report to an output file, and TaskStop can stop it."""
from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.session import TurnSession
from agentos.agentic.workspace import ConversationWorkspace
from agentos.persistence.postgres.conversation_agents import ConversationAgentStore
from agentos.persistence.postgres.schema import metadata
from agentos.persistence.sqlite import create_local_engine
from tests.unit.agentic.test_parallel_and_background_subagents import (
    TURN, _RecordingStore, _parallel_tool_calls_batch, _text_batch,
)


class _QuickChild:
    def stream(self, _request):
        return iter(_text_batch("Background report: all good."))


class _EndlessChild:
    """Keeps asking for another listing until the run is cancelled."""

    def __init__(self) -> None:
        self.calls = 0
        self.started = threading.Event()

    def stream(self, _request):
        self.calls += 1
        self.started.set()
        time.sleep(0.05)
        return iter(_parallel_tool_calls_batch([(f"call-{self.calls}", "list_files", {"path": "."})]))


def _session(tmp_path: Path, child) -> tuple[TurnSession, object]:
    engine = create_local_engine(f"sqlite:///{(tmp_path / 'bg.db').as_posix()}")
    metadata.create_all(engine)
    session = TurnSession(
        turn=dict(TURN), store=_RecordingStore(),
        agents_store=ConversationAgentStore(engine, conversation_id=TURN["conversation_id"], user_id=TURN["user_id"]),
        memory_store=None, provider_factory=lambda: child, workspace_root=tmp_path, child_provider_factory=lambda _model: child,
    )
    return session, engine


def test_a_background_agent_writes_its_report_to_an_output_file(tmp_path: Path) -> None:
    session, engine = _session(tmp_path, _QuickChild())
    try:
        started = session._run_subagent(subagent_type="general-purpose", task="Summarise", run_in_background=True)
        assert started.status == "succeeded", started.content
        agent_id, output_file = started.payload["agent_id"], started.payload["output_file"]
        assert output_file.startswith(".orin/agent-outputs/") and output_file in started.content
        done = session._check_subagent(agent_id, wait_seconds=20)
        assert done.status == "succeeded", done.content
        assert "Background report: all good." in (session.workspace.root / output_file).read_text(encoding="utf-8")
    finally:
        session.close_background_subagents()
        engine.dispose()


def test_taskstop_stops_a_running_background_agent(tmp_path: Path) -> None:
    child = _EndlessChild()
    session, engine = _session(tmp_path, child)
    try:
        started = session._run_subagent(subagent_type="general-purpose", task="Loop", run_in_background=True)
        agent_id = started.payload["agent_id"]
        assert child.started.wait(10)
        toolset = AgentToolset(ConversationWorkspace(tmp_path / "other", "c1"), public_tool_names=True)
        toolset._stop_subagent = session._stop_background_subagent
        stopped = toolset.invoke("TaskStop", {"task_id": agent_id})
        assert stopped.status == "succeeded", stopped.content
        assert stopped.data["tool_output"]["task_type"] == "local_agent"
        result = session._check_subagent(agent_id, wait_seconds=20)
        assert result.payload["status"] != "running"
        calls = child.calls
        time.sleep(0.5)
        assert child.calls == calls  # nothing runs after the stop
    finally:
        session.close_background_subagents()
        engine.dispose()


def test_stopping_an_unknown_or_finished_agent_says_so(tmp_path: Path) -> None:
    session, engine = _session(tmp_path, _QuickChild())
    try:
        assert session._stop_background_subagent("agent:nope").status == "failed"
        started = session._run_subagent(subagent_type="general-purpose", task="Quick", run_in_background=True)
        session._check_subagent(started.payload["agent_id"], wait_seconds=20)
        finished = session._stop_background_subagent(started.payload["agent_id"])
        assert finished.status == "succeeded" and "already finished" in finished.content
    finally:
        session.close_background_subagents()
        engine.dispose()


def test_without_agent_support_a_name_is_still_refused(tmp_path: Path) -> None:
    toolset = AgentToolset(ConversationWorkspace(tmp_path / "w", "c1"), public_tool_names=True)
    assert toolset.invoke("TaskStop", {"task_id": "agent:x"}).status == "failed"


def test_registry_finish_wakes_wait_any() -> None:
    from agentos.agentic.background_agents import BackgroundAgentEntry, BackgroundAgentRegistry

    reg = BackgroundAgentRegistry()
    entry = BackgroundAgentEntry(
        agent_id="agent:c1:1",
        conversation_id="c1",
        name="codex",
        output_file=".orin/agent-outputs/agent_c1_1.md",
        status="running",
        started_at=time.monotonic(),
        finished_at=None,
        summary="",
        cancel=threading.Event(),
        future=None,
    )
    reg.register(entry)
    assert len(reg.running("c1")) == 1

    def _finish_soon() -> None:
        time.sleep(0.05)
        reg.finish("agent:c1:1", "completed", "Done line 1\nDone line 2")

    t = threading.Thread(target=_finish_soon, daemon=True)
    t0 = time.monotonic()
    t.start()
    woke = reg.wait_any("c1", timeout=2.0, cancelled=lambda: False)
    elapsed = time.monotonic() - t0
    t.join(timeout=1.0)
    assert woke is True
    assert elapsed < 1.0
    assert reg.running("c1") == []
    got = reg.get("c1", "agent:c1:1")
    assert got is not None
    assert got.status == "completed"
    assert got.summary == "Done line 1"


def test_registry_take_unnotified_delivers_once() -> None:
    from agentos.agentic.background_agents import BackgroundAgentEntry, BackgroundAgentRegistry

    reg = BackgroundAgentRegistry()
    entry = BackgroundAgentEntry(
        agent_id="agent:c1:2",
        conversation_id="c1",
        name="antigravity",
        output_file=".orin/agent-outputs/agent_c1_2.md",
        status="running",
        started_at=time.monotonic(),
        finished_at=None,
        summary="",
        cancel=threading.Event(),
        future=None,
    )
    reg.register(entry)
    assert reg.has_unnotified("c1") is False

    reg.finish("agent:c1:2", "completed", "Summary text")
    assert reg.has_unnotified("c1") is True
    first = reg.take_unnotified("c1")
    assert [e.agent_id for e in first] == ["agent:c1:2"]
    assert first[0].notified is True
    assert reg.has_unnotified("c1") is False
    assert reg.take_unnotified("c1") == []


def test_registry_wait_any_returns_on_cancelled_or_extra_wake() -> None:
    from agentos.agentic.background_agents import BackgroundAgentEntry, BackgroundAgentRegistry

    reg = BackgroundAgentRegistry()
    entry = BackgroundAgentEntry(
        agent_id="agent:c1:3",
        conversation_id="c1",
        name="codex",
        output_file=".orin/agent-outputs/agent_c1_3.md",
        status="running",
        started_at=time.monotonic(),
        finished_at=None,
        summary="",
        cancel=threading.Event(),
        future=None,
    )
    reg.register(entry)

    cancel_flag = threading.Event()
    threading.Timer(0.05, cancel_flag.set).start()
    t0 = time.monotonic()
    reg.wait_any("c1", timeout=5.0, cancelled=cancel_flag.is_set)
    assert (time.monotonic() - t0) <= 0.6

    wake_flag = threading.Event()
    threading.Timer(0.05, wake_flag.set).start()
    t1 = time.monotonic()
    reg.wait_any("c1", timeout=5.0, cancelled=lambda: False, extra_wake=wake_flag.is_set)
    assert (time.monotonic() - t1) <= 0.6


def test_registry_isolates_different_conversations() -> None:
    from agentos.agentic.background_agents import BackgroundAgentEntry, BackgroundAgentRegistry

    reg = BackgroundAgentRegistry()
    e1 = BackgroundAgentEntry(
        agent_id="agent:c1:1",
        conversation_id="c1",
        name="codex",
        output_file=".orin/agent-outputs/1.md",
        status="running",
        started_at=time.monotonic(),
        finished_at=None,
        summary="",
        cancel=threading.Event(),
        future=None,
    )
    e2 = BackgroundAgentEntry(
        agent_id="agent:c2:1",
        conversation_id="c2",
        name="antigravity",
        output_file=".orin/agent-outputs/2.md",
        status="running",
        started_at=time.monotonic(),
        finished_at=None,
        summary="",
        cancel=threading.Event(),
        future=None,
    )
    reg.register(e1)
    reg.register(e2)
    reg.finish("agent:c2:1", "completed", "c2 done")

    assert len(reg.running("c1")) == 1
    assert len(reg.running("c2")) == 0
    assert reg.get("c1", "agent:c2:1") is None
    assert reg.has_unnotified("c1") is False
    assert reg.has_unnotified("c2") is True


def test_session_b_check_subagent_reads_completed_agent_from_session_a(tmp_path: Path) -> None:
    from agentos.agentic.background_agents import BACKGROUND_AGENTS, fail_orphaned_running_agents

    gate = threading.Event()

    class _DelayedChild:
        def stream(self, _request):
            gate.wait(timeout=5.0)
            return iter(_text_batch("Cross-turn report ready."))

    engine = create_local_engine(f"sqlite:///{(tmp_path / 'cross.db').as_posix()}")
    metadata.create_all(engine)
    try:
        session_a = TurnSession(
            turn=dict(TURN),
            store=_RecordingStore(),
            agents_store=ConversationAgentStore(engine, conversation_id=TURN["conversation_id"], user_id=TURN["user_id"]),
            memory_store=None,
            provider_factory=lambda: _DelayedChild(),
            workspace_root=tmp_path,
            child_provider_factory=lambda _model: _DelayedChild(),
        )
        started = session_a._run_subagent(subagent_type="general-purpose", task="Work across turns", run_in_background=True)
        assert started.status == "succeeded"
        agent_id = started.payload["agent_id"]

        # Session A closes while child is still running; child must survive and finish.
        session_a.close_background_subagents()
        gate.set()

        session_b = TurnSession(
            turn={**TURN, "turn_id": "turn-2"},
            store=_RecordingStore(),
            agents_store=ConversationAgentStore(engine, conversation_id=TURN["conversation_id"], user_id=TURN["user_id"]),
            memory_store=None,
            provider_factory=lambda: _DelayedChild(),
            workspace_root=tmp_path,
            child_provider_factory=lambda _model: _DelayedChild(),
        )
        done = session_b._check_subagent(agent_id, wait_seconds=10)
        assert done.status == "succeeded", done.content
        assert done.payload["status"] == "completed"
        assert "Cross-turn report ready." in done.content

        # Also test fallback to DB + output file when in-memory registry does not have the entry (process restart)
        BACKGROUND_AGENTS.clear_for_tests()
        done_from_disk = session_b._check_subagent(agent_id, wait_seconds=0)
        assert done_from_disk.status == "succeeded", done_from_disk.content
        assert done_from_disk.payload["status"] == "completed"
        assert "Cross-turn report ready." in done_from_disk.content

        # And test startup sweep of orphaned running/working agents in DB
        session_b.agents_store.set_state(agent_id, "working")
        swept = fail_orphaned_running_agents(engine, workspace_root_resolver=lambda _cid: session_b.workspace.root)
        assert agent_id in swept
        row = session_b.agents_store.get(agent_id)
        assert row is not None and row["state"] == "failed"
    finally:
        gate.set()
        session_a.close_background_subagents()
        engine.dispose()


def test_subagent_finishing_after_turn_closes_schedules_continuation_once(tmp_path: Path) -> None:
    from datetime import UTC, datetime
    from sqlalchemy import insert, select
    from agentos.agentic.background_agents import BACKGROUND_AGENTS, BackgroundAgentEntry
    from agentos.persistence.postgres.schema import (
        conversations,
        provider_configurations,
        provider_model_catalog,
        scheduled_chat_tasks,
        schedules,
    )
    from agentos.scheduler.scheduled_chats import ScheduledChatService, TOOL_SCHEDULE_SOURCES

    assert "subagent" in TOOL_SCHEDULE_SOURCES

    BACKGROUND_AGENTS.clear_for_tests()
    now = datetime.now(UTC)
    engine = create_local_engine(f"sqlite:///{(tmp_path / 't5.db').as_posix()}")
    metadata.create_all(engine)
    try:
        with engine.begin() as conn:
            conn.execute(
                insert(conversations).values(
                    conversation_id=TURN["conversation_id"],
                    user_id=TURN["user_id"],
                    provider=TURN["provider"],
                    model_id=TURN["model_id"],
                    state="active",
                    title="T5 test",
                    created_at=now,
                    updated_at=now,
                )
            )
            conn.execute(
                insert(provider_model_catalog).values(
                    user_id=TURN["user_id"],
                    provider=TURN["provider"],
                    model_id=TURN["model_id"],
                    display_name="Model",
                    capabilities=[],
                    input_modalities=[],
                    output_modalities=[],
                    refreshed_at=now,
                    created_at=now,
                    updated_at=now,
                )
            )
            conn.execute(
                insert(provider_configurations).values(
                    user_id=TURN["user_id"],
                    provider=TURN["provider"],
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

        session = TurnSession(
            turn=dict(TURN),
            store=_RecordingStore(),
            agents_store=ConversationAgentStore(engine, conversation_id=TURN["conversation_id"], user_id=TURN["user_id"]),
            memory_store=None,
            provider_factory=lambda: _QuickChild(),
            workspace_root=tmp_path,
            child_provider_factory=lambda _model: _QuickChild(),
        )
        session._scheduler_service = ScheduledChatService(engine)

        bg_entry = BackgroundAgentEntry(
            agent_id="agent:conv-par-1:antigravity",
            conversation_id=TURN["conversation_id"],
            name="antigravity",
            output_file=".orin/agent-outputs/antigravity.md",
            status="running",
            started_at=time.monotonic(),
            finished_at=None,
            summary="",
            cancel=threading.Event(),
            future=None,
        )
        BACKGROUND_AGENTS.register(bg_entry)

        # Turn closes while subagent is still running
        session.close_background_subagents()

        # Subagent finishes after the turn closed
        BACKGROUND_AGENTS.finish(
            "agent:conv-par-1:antigravity",
            "completed",
            "Implementação concluída com sucesso.",
        )
        # Second finish for the same agent must not create another schedule
        BACKGROUND_AGENTS.finish(
            "agent:conv-par-1:antigravity",
            "completed",
            "Implementação concluída com sucesso.",
        )

        with engine.connect() as conn:
            rows = conn.execute(
                select(schedules, scheduled_chat_tasks.c.message)
                .join(scheduled_chat_tasks, scheduled_chat_tasks.c.schedule_id == schedules.c.schedule_id)
                .where(schedules.c.user_id == TURN["user_id"])
            ).mappings().all()

        subagent_schedules = [
            r for r in rows if str(dict(r["rule"] or {}).get("source") or "") == "subagent"
        ]
        assert len(subagent_schedules) == 1
        msg = str(subagent_schedules[0]["message"])
        assert '<subagent-finished agent_id="agent:conv-par-1:antigravity" name="antigravity" status="completed">' in msg
        assert "Implementação concluída com sucesso." in msg
        assert ".orin/agent-outputs/antigravity.md" in msg
    finally:
        BACKGROUND_AGENTS.clear_for_tests()
        engine.dispose()



def test_cancel_api_with_keep_subagents_leaves_background_entry_running_while_default_stops_it(
    tmp_path: Path,
) -> None:
    from fastapi.testclient import TestClient

    from agentos.agentic.background_agents import BACKGROUND_AGENTS, BackgroundAgentEntry
    from agentos.agentic.settings import AgentRuntimeSettingsStore
    from agentos.api.gateway import ApiServices, create_app
    from agentos.api.security import AuthenticatedPrincipal, InMemorySecurityService
    from agentos.conversations.chat import ChatApplication, PostgresChatStore

    BACKGROUND_AGENTS.clear_for_tests()
    engine = create_local_engine(f"sqlite:///{(tmp_path / 't7.db').as_posix()}")
    metadata.create_all(engine)
    try:
        store = PostgresChatStore(engine)
        chat_app = ChatApplication(store)
        security = InMemorySecurityService()
        security.add_pat(
            "pat",
            AuthenticatedPrincipal(
                user_id=TURN["user_id"],
                credential_ref="pat-1",
                scopes=frozenset({"api"}),
            ),
        )
        services = ApiServices(
            security=security,
            conversation_application=chat_app,
            agentic_runtime=AgentRuntimeSettingsStore(tmp_path / "runtime.json"),
            workspace_root=tmp_path / "workspaces",
        )
        client = TestClient(create_app(services))
        headers = {"Authorization": "Bearer pat"}

        receipt1 = chat_app.create(
            user_id=TURN["user_id"],
            message="Tarefa 1",
            provider=TURN["provider"],
            model_id=TURN["model_id"],
            idempotency_key="idem-t7-1",
        )
        claimed1 = store.claim(receipt1.turn_id)
        assert claimed1 is not None

        session1 = TurnSession(
            turn=dict(claimed1),
            store=store,
            agents_store=ConversationAgentStore(
                engine,
                conversation_id=receipt1.conversation_id,
                user_id=TURN["user_id"],
            ),
            memory_store=None,
            provider_factory=lambda: _QuickChild(),
            workspace_root=tmp_path,
            child_provider_factory=lambda _model: _QuickChild(),
        )
        cancel1 = threading.Event()
        entry1 = BackgroundAgentEntry(
            agent_id=f"agent:{receipt1.conversation_id}:bg1",
            conversation_id=receipt1.conversation_id,
            name="antigravity",
            output_file=".orin/agent-outputs/bg1.md",
            status="running",
            started_at=time.monotonic(),
            finished_at=None,
            summary="",
            cancel=cancel1,
            future=None,
        )
        BACKGROUND_AGENTS.register(entry1)
        session1._background_subagents[entry1.agent_id] = {
            "future": None,
            "agent_id": entry1.agent_id,
            "agent_name": "antigravity",
            "subagent_type": "antigravity",
            "cancel": cancel1,
            "output_file": entry1.output_file,
        }

        resp1 = client.post(
            f"/v1/conversations/{receipt1.conversation_id}/cancel",
            json={"keep_subagents": True},
            headers={**headers, "Idempotency-Key": "cancel-t7-1"},
        )
        assert resp1.status_code == 202
        session1.close_background_subagents()

        assert cancel1.is_set() is False
        assert BACKGROUND_AGENTS.get(receipt1.conversation_id, entry1.agent_id).status == "running"

        receipt2 = chat_app.create(
            user_id=TURN["user_id"],
            message="Tarefa 2",
            provider=TURN["provider"],
            model_id=TURN["model_id"],
            idempotency_key="idem-t7-2",
        )
        claimed2 = store.claim(receipt2.turn_id)
        assert claimed2 is not None

        session2 = TurnSession(
            turn=dict(claimed2),
            store=store,
            agents_store=ConversationAgentStore(
                engine,
                conversation_id=receipt2.conversation_id,
                user_id=TURN["user_id"],
            ),
            memory_store=None,
            provider_factory=lambda: _QuickChild(),
            workspace_root=tmp_path,
            child_provider_factory=lambda _model: _QuickChild(),
        )
        cancel2 = threading.Event()
        entry2 = BackgroundAgentEntry(
            agent_id=f"agent:{receipt2.conversation_id}:bg2",
            conversation_id=receipt2.conversation_id,
            name="antigravity",
            output_file=".orin/agent-outputs/bg2.md",
            status="running",
            started_at=time.monotonic(),
            finished_at=None,
            summary="",
            cancel=cancel2,
            future=None,
        )
        BACKGROUND_AGENTS.register(entry2)
        session2._background_subagents[entry2.agent_id] = {
            "future": None,
            "agent_id": entry2.agent_id,
            "agent_name": "antigravity",
            "subagent_type": "antigravity",
            "cancel": cancel2,
            "output_file": entry2.output_file,
        }

        resp2 = client.post(
            f"/v1/conversations/{receipt2.conversation_id}/cancel",
            headers={**headers, "Idempotency-Key": "cancel-t7-2"},
        )
        assert resp2.status_code == 202
        session2.close_background_subagents()

        assert cancel2.is_set() is True
        assert BACKGROUND_AGENTS.get(receipt2.conversation_id, entry2.agent_id).status == "stopped"
    finally:
        BACKGROUND_AGENTS.clear_for_tests()
        engine.dispose()


def test_cancelled_subagent_returns_partial_progress_with_recent_tool_calls_and_agent_id(
    tmp_path: Path,
) -> None:
    from agentos.agentic.workspace import ConversationWorkspace

    ws = ConversationWorkspace(tmp_path, TURN["conversation_id"])
    for fname in ("alpha.py", "beta.py", "gamma.py"):
        (ws.root / fname).write_text(f"# {fname}\n", encoding="utf-8")

    cancelled_flag = [False]

    class _ThreeReadsThenCancelledChild:
        def __init__(self) -> None:
            self.calls = 0

        def stream(self, _request: dict[str, object]):
            self.calls += 1
            if self.calls == 1:
                return iter(
                    _parallel_tool_calls_batch(
                        [
                            ("c1", "read_file", {"path": "alpha.py"}),
                            ("c2", "read_file", {"path": "beta.py"}),
                            ("c3", "read_file", {"path": "gamma.py"}),
                        ]
                    )
                )
            cancelled_flag[0] = True
            return iter(_text_batch("unreachable"))

    engine = create_local_engine(f"sqlite:///{(tmp_path / 't14.db').as_posix()}")
    metadata.create_all(engine)
    try:
        child = _ThreeReadsThenCancelledChild()
        session = TurnSession(
            turn=dict(TURN),
            store=_RecordingStore(),
            agents_store=ConversationAgentStore(
                engine,
                conversation_id=TURN["conversation_id"],
                user_id=TURN["user_id"],
            ),
            memory_store=None,
            provider_factory=lambda: child,
            workspace_root=tmp_path,
            child_provider_factory=lambda _model: child,
            cancelled=lambda _turn: cancelled_flag[0],
        )

        outcome = session._run_subagent(
            subagent_type="general-purpose",
            task="Leia alpha.py, beta.py e gamma.py",
            run_in_background=False,
        )
        assert outcome.status == "failed"
        assert "did not finish (TURN_CANCELLED)" in outcome.content
        assert "alpha.py" in outcome.content
        assert "beta.py" in outcome.content
        assert "gamma.py" in outcome.content
        agent_id = str(outcome.payload.get("agent_id") or "")
        assert agent_id
        assert agent_id in outcome.content
    finally:
        engine.dispose()

