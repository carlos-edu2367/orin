"""WS-B7 Criterion 12: running teammate receives SendMessage(to=<name>) mid-task; idle teammate still uses mailbox."""
from __future__ import annotations

from pathlib import Path
import threading
import time
from typing import Any

from agentos.agentic.session import TurnSession
from agentos.persistence.postgres.conversation_agents import ConversationAgentStore
from agentos.persistence.postgres.schema import metadata
from agentos.persistence.sqlite import create_local_engine
from tests.unit.agentic.test_parallel_and_background_subagents import (
    TURN,
    _RecordingStore,
    _parallel_tool_calls_batch,
    _text_batch,
)


def _wait(session: TurnSession, agent_id: str) -> None:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        entry = session._background_subagents.get(agent_id)
        if entry is not None and entry["future"].done():
            return
        time.sleep(0.05)
    raise AssertionError("teammate did not finish in time")


def test_running_teammate_receives_message_midtask_before_finishing(tmp_path: Path) -> None:
    """Criterion 12: SendMessage(to=<name>) reaches a running teammate in its next tool round (not only after finishing)."""
    engine = create_local_engine(f"sqlite:///{(tmp_path / 'team_midtask.db').as_posix()}")
    metadata.create_all(engine)

    tool_started = threading.Event()
    allow_tool_finish = threading.Event()

    class _MidtaskTeammateProvider:
        def __init__(self) -> None:
            self.requests: list[dict[str, Any]] = []

        def stream(self, request: dict[str, Any]):
            self.requests.append(request)
            if len(self.requests) == 1:
                return iter(_parallel_tool_calls_batch([("c1", "list_files", {"path": "."})]))
            return iter(_text_batch("Recebi a orientacao durante a tarefa."))

    provider = _MidtaskTeammateProvider()
    session = TurnSession(
        turn=dict(TURN),
        store=_RecordingStore(),
        agents_store=ConversationAgentStore(
            engine,
            conversation_id=TURN["conversation_id"],
            user_id=TURN["user_id"],
        ),
        memory_store=None,
        provider_factory=lambda: provider,
        workspace_root=tmp_path,
        child_provider_factory=lambda _m: provider,
    )
    now = [100.0]
    session._agent_control_clock = lambda: now[0]

    orig_toolset = session._toolset

    def _patched_toolset(*args, **kwargs):
        ts = orig_toolset(*args, **kwargs)
        if kwargs.get("caller") is not None or not kwargs.get("subagents", True):
            orig_list = ts.list_files

            def _slow_list(path=".", depth=1, pattern=None):
                tool_started.set()
                allow_tool_finish.wait(10)
                return orig_list(path=path, depth=depth, pattern=pattern)

            ts.list_files = _slow_list
        return ts

    session._toolset = _patched_toolset  # type: ignore[assignment]

    try:
        started = session._run_subagent(
            subagent_type="general-purpose",
            task="Investigue o parser",
            teammate_name="alice",
            team_name="core",
        )
        assert started.status == "succeeded"
        alice_id = str(started.payload["agent_id"])

        assert tool_started.wait(10), "teammate never entered tool call"

        sent = session._send_team_message(
            sender="lead",
            to="alice",
            message="Mude o foco para o lexer agora",
            summary="foco lexer",
        )
        assert sent.status == "succeeded"
        assert sent.payload["status"] == "queued"
        assert sent.payload.get("control_id")

        # Advance control clock so watcher polls before next model request.
        now[0] += 2.0
        session._active_watchers[alice_id].poll()
        allow_tool_finish.set()
        _wait(session, alice_id)

        # Delivered inside the SAME run (2 requests total, no extra mailbox rerun!).
        assert len(provider.requests) == 2
        assert "Mude o foco para o lexer agora" in repr(provider.requests[1])
        assert session._team_mailbox().pending_count(
            user_id=TURN["user_id"], conversation_id=TURN["conversation_id"], recipient="alice"
        ) == 0

        listed = session._agent_controls().list_for_agent(
            TURN["user_id"], TURN["conversation_id"], alice_id
        )
        assert len(listed) == 1
        assert listed[0]["state"] == "delivered"
        assert listed[0]["delivered_at"] is not None
    finally:
        allow_tool_finish.set()
        session.close_background_subagents()
        engine.dispose()


def test_idle_teammate_still_uses_mailbox_and_starts_new_run(tmp_path: Path) -> None:
    """Stopped/idle teammate keeps existing mailbox delivery and wakes up on SendMessage(to=<name>)."""
    engine = create_local_engine(f"sqlite:///{(tmp_path / 'team_idle.db').as_posix()}")
    metadata.create_all(engine)

    class _SimpleProvider:
        def __init__(self) -> None:
            self.requests: list[str] = []

        def stream(self, request: dict[str, Any]):
            self.requests.append(repr(request))
            return iter(_text_batch(f"Done {len(self.requests)}"))

    provider = _SimpleProvider()
    session = TurnSession(
        turn=dict(TURN),
        store=_RecordingStore(),
        agents_store=ConversationAgentStore(
            engine,
            conversation_id=TURN["conversation_id"],
            user_id=TURN["user_id"],
        ),
        memory_store=None,
        provider_factory=lambda: provider,
        workspace_root=tmp_path,
        child_provider_factory=lambda _m: provider,
    )
    try:
        started = session._run_subagent(
            subagent_type="general-purpose",
            task="Primeira tarefa",
            teammate_name="bob",
            team_name="core",
        )
        bob_id = str(started.payload["agent_id"])
        _wait(session, bob_id)

        sent = session._send_team_message(sender="lead", to="bob", message="Segunda tarefa via mailbox")
        assert sent.status == "succeeded"
        assert sent.payload["status"] == "running"
        _wait(session, bob_id)

        assert len(provider.requests) == 2
        assert "Segunda tarefa via mailbox" in provider.requests[-1]
    finally:
        session.close_background_subagents()
        engine.dispose()
