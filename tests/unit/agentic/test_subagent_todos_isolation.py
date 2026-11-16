from __future__ import annotations

from pathlib import Path
import threading
from typing import Any

from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.events import AgentActivityEventType
from agentos.agentic.provider_stream import NormalizedStreamItem, StreamKind
from agentos.agentic.read_state import copy_read_state
from agentos.agentic.runtime import AgenticTurnRuntime
from agentos.agentic.session import TurnSession
from agentos.agentic.tools._shared import ReadFileRecord
from agentos.agentic.tools.agents.agent import (
    remember_subagent_character_name,
    resolve_subagent_character_name,
)
from agentos.agentic.workspace import ConversationWorkspace
from agentos.persistence.postgres.conversation_agents import ConversationAgentStore
from agentos.persistence.postgres.schema import metadata
from agentos.persistence.sqlite import create_local_engine


TURN = {
    "turn_id": "turn-todos-iso",
    "conversation_id": "conv-todos-iso",
    "user_id": "user-1",
    "execution_id": "exe-todos-iso",
    "provider": "litellm",
    "model_id": "~anthropic/claude-opus-4-6",
    "user_message_id": "msg-user",
    "assistant_message_id": "msg-assistant",
    "code_mode": "code",
}


class _ActivityStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.events: list[dict[str, Any]] = []

    def load(self, _turn_id: str) -> dict[str, Any]:
        return dict(TURN)

    def history_for_turn(self, _turn: object) -> list[dict[str, str]]:
        return [{"role": "user", "content": "Execute a tarefa com subagentes."}]

    def delta(self, _turn: object, text: str) -> None:
        pass

    def finish(self, _turn: object, *, failed: bool = False, code: str | None = None) -> None:
        pass

    def main_agent_id(self, turn: dict[str, Any]) -> str:
        return f"agent:{turn['conversation_id']}:main"

    def record(
        self,
        _turn: object,
        event_type: AgentActivityEventType,
        summary: str,
        payload: dict[str, Any] | None = None,
        *,
        agent_id: str | None = None,
        parent_agent_id: str | None = None,
    ) -> None:
        with self._lock:
            self.events.append(
                {
                    "event_type": event_type,
                    "summary": summary,
                    "payload": dict(payload or {}),
                    "agent_id": agent_id,
                    "parent_agent_id": parent_agent_id,
                }
            )


def _tool_call_and_done(call_id: str, name: str, args: dict[str, Any], final_text: str) -> list[list[NormalizedStreamItem]]:
    import json

    return [
        [
            NormalizedStreamItem(
                StreamKind.TOOL_CALL,
                1,
                tool_call_id=call_id,
                tool_name=name,
                arguments_delta=json.dumps(args),
            ),
            NormalizedStreamItem(StreamKind.FINISH, 2, finish_reason="tool_calls"),
        ],
        [
            NormalizedStreamItem(StreamKind.TEXT, 1, text=final_text),
            NormalizedStreamItem(StreamKind.FINISH, 2, finish_reason="stop"),
        ],
    ]


def test_subagent_todo_write_does_not_overwrite_main_todos_or_emit_stage_changed(tmp_path: Path) -> None:
    db_file = tmp_path / "todos_iso.db"
    engine = create_local_engine(f"sqlite:///{db_file.as_posix()}")
    metadata.create_all(engine)
    store = _ActivityStore()
    agents_store = ConversationAgentStore(engine, conversation_id=TURN["conversation_id"], user_id=TURN["user_id"])

    child_batches = _tool_call_and_done(
        "call-sub-todo",
        "TodoWrite",
        {
            "todos": [
                {"content": "Subagent step 1", "status": "in_progress", "activeForm": "Running subagent step 1"},
            ]
        },
        "Subagent finished its work.",
    )

    class _ChildProvider:
        def stream(self, _request: dict[str, Any]):
            return iter(child_batches.pop(0))

    session = TurnSession(
        turn=dict(TURN),
        store=store,
        agents_store=agents_store,
        memory_store=None,
        provider_factory=_ChildProvider,
        workspace_root=tmp_path,
        child_provider_factory=lambda _m: _ChildProvider(),
    )
    runtime = session.build_runtime()
    main_toolset = session.toolset

    main_todos = [
        {"content": "Main task A", "status": "in_progress", "activeForm": "Working on main task A"},
        {"content": "Main task B", "status": "pending", "activeForm": "Pending main task B"},
    ]
    main_outcome = main_toolset.invoke("TodoWrite", {"todos": main_todos})
    assert main_outcome.status == "succeeded"
    session.emit_lifecycle(
        session.turn,
        "tool_finished",
        agent_id=session.main_agent_id,
        tool_name="todo_write",
        status="succeeded",
        summary=main_outcome.summary,
        tool_payload=main_outcome.payload,
    )

    assert [item["content"] for item in session._todos_state] == ["Main task A", "Main task B"]
    assert [item["content"] for item in main_toolset._todos] == ["Main task A", "Main task B"]

    # Run a general subagent (and also verify fork isolation) that calls TodoWrite
    sub_outcome = session._run_subagent(
        subagent_type="fork",
        task="Run subagent with its own TodoWrite",
        description="Subagent todo test",
    )
    assert sub_outcome.status == "succeeded", sub_outcome.content
    sub_agent_id = str(sub_outcome.payload["agent_id"])

    # Main agent's todos_state and main_toolset._todos must remain untouched
    assert [item["content"] for item in session._todos_state] == ["Main task A", "Main task B"]
    assert [item["content"] for item in main_toolset._todos] == ["Main task A", "Main task B"]

    # Runtime reminder for the main agent must only list main agent's items
    from agentos.agentic.runtime import TODO_REMINDER_EVERY

    runtime._reminder_requests = TODO_REMINDER_EVERY - 1
    runtime._add_periodic_reminders()
    drained = runtime.reminders.drain()
    todo_notes = [item.text for item in drained if item.kind == "todo"]
    assert len(todo_notes) == 1
    assert "Main task A" in todo_notes[0]
    assert "Main task B" in todo_notes[0]
    assert "Subagent step 1" not in todo_notes[0]

    # CODE_MODE_STAGE_CHANGED with todos must only be emitted for the main agent, never for the subagent
    stage_events = [
        ev for ev in store.events if ev["event_type"] == AgentActivityEventType.CODE_MODE_STAGE_CHANGED
    ]
    assert len(stage_events) >= 1
    for ev in stage_events:
        assert ev["agent_id"] == session.main_agent_id
        if "todos" in ev["payload"]:
            assert all(t["content"] != "Subagent step 1" for t in ev["payload"]["todos"])

    # Meanwhile, the subagent's TOOL_FINISHED event for todo_write carries its agent_id and parent_agent_id
    sub_tool_finished = [
        ev
        for ev in store.events
        if ev["event_type"] == AgentActivityEventType.TOOL_FINISHED
        and ev["agent_id"] == sub_agent_id
        and ev["payload"].get("tool_name") == "todo_write"
    ]
    assert len(sub_tool_finished) == 1
    assert sub_tool_finished[0]["parent_agent_id"] == session.main_agent_id
    engine.dispose()


def test_copy_read_state_is_thread_safe_under_concurrent_insertions() -> None:
    state: dict[str, object] = {
        f"initial_{i}.py": ReadFileRecord(mtime_ns=1, sha256="abc", read_seq=i)
        for i in range(20)
    }
    stop = threading.Event()
    errors: list[BaseException] = []

    def _writer() -> None:
        idx = 0
        while not stop.is_set():
            key = f"file_{idx % 200}.py"
            state[key] = ReadFileRecord(mtime_ns=idx, sha256="def", read_seq=idx)
            if idx % 5 == 0:
                state.pop(f"file_{(idx - 3) % 200}.py", None)
            idx += 1

    def _reader() -> None:
        try:
            for _ in range(500):
                copied = copy_read_state(state)
                assert isinstance(copied, dict)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
            stop.set()

    writers = [threading.Thread(target=_writer, daemon=True) for _ in range(2)]
    readers = [threading.Thread(target=_reader, daemon=True) for _ in range(2)]
    for t in writers + readers:
        t.start()
    for t in readers:
        t.join(timeout=5.0)
    stop.set()
    for t in writers:
        t.join(timeout=2.0)

    assert errors == []


def test_delegation_tools_concurrency_safe_and_plan_mode_invariant(tmp_path: Path) -> None:
    from agentos.agentic.agent_tools import ToolOutcome

    ws = ConversationWorkspace(tmp_path, "conv-plan-inv")
    toolset = AgentToolset(
        ws,
        code_mode_active=True,
        permission_mode="plan_mode",
        run_subagent=lambda *a, **kw: ToolOutcome("succeeded", "ok", "ok", {}),
        check_subagent=lambda *a, **kw: ToolOutcome("succeeded", "ok", "ok", {}),
        delegate=lambda *a, **kw: ToolOutcome("succeeded", "ok", "ok", {}),
        delegate_batch=lambda *a, **kw: ToolOutcome("succeeded", "ok", "ok", {}),
        create_agent=lambda *a, **kw: ToolOutcome("succeeded", "ok", "ok", {}),
    )

    for name in ("run_subagent", "Agent", "ask_agent", "ask_agents", "check_subagent", "CheckSubagent"):
        assert toolset.is_concurrency_safe(name) is True, name
        assert toolset.is_parallel_delegation(name) is True, name

    assert toolset.is_concurrency_safe("create_agent") is False
    assert toolset.is_parallel_delegation("create_agent") is False
    assert toolset.is_parallel_delegation("read_file") is False
    assert toolset.is_concurrency_safe("unknown_tool") is False

    # Mutating and read_only invariants must remain unchanged
    for mut_tool in ("run_subagent", "ask_agent", "ask_agents"):
        assert toolset.is_read_only(mut_tool) is False
        assert toolset.is_mutating(mut_tool) is True
    assert toolset.is_read_only("check_subagent") is True

    # Plan mode invariant: general subagent in plan_mode keeps the same permission decision
    decision = toolset._permission_broker.evaluate("run_subagent", {"subagent_type": "general", "task": "edit x"})
    assert (getattr(decision, "behavior", decision)) == "allow"

    # Character name registry thread safety
    barrier = threading.Barrier(4, timeout=5.0)

    def _char_worker(idx: int) -> None:
        barrier.wait()
        for j in range(50):
            rec: dict[str, object] = {}
            aid = f"agent-{idx}-{j}"
            remember_subagent_character_name("conv-char", aid, rec, f"Hero{idx}_{j}")
            resolved = resolve_subagent_character_name("conv-char", "agent:main", {aid: rec}, aid)
            assert resolved == f"Hero{idx}_{j}"

    threads = [threading.Thread(target=_char_worker, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5.0)
