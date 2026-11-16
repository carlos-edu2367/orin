from __future__ import annotations

from concurrent.futures import Future
from pathlib import Path
import threading
import time
from typing import Any

import pytest

from agentos.agentic.agent_tools import AgentToolError, AgentToolset, ToolOutcome
from agentos.agentic.session import SubagentCaller, TurnSession
from agentos.agentic.workspace import ConversationWorkspace
from agentos.persistence.postgres.conversation_agents import ConversationAgentStore
from agentos.persistence.postgres.schema import metadata
from agentos.persistence.sqlite import create_local_engine


TURN = {
    "turn_id": "turn-chk-many",
    "conversation_id": "conv-chk-many",
    "user_id": "user-1",
    "execution_id": "exe-chk-many",
    "provider": "litellm",
    "model_id": "~anthropic/claude-opus-4-6",
    "user_message_id": "msg-user",
    "assistant_message_id": "msg-assistant",
    "code_mode": "code",
}


class _DummyStore:
    def load(self, _turn_id: str) -> dict[str, Any]:
        return dict(TURN)

    def history_for_turn(self, _turn: object) -> list[dict[str, str]]:
        return [{"role": "user", "content": "Check subagents"}]

    def delta(self, _turn: object, text: str) -> None:
        pass

    def finish(self, _turn: object, *, failed: bool = False, code: str | None = None) -> None:
        pass

    def record(self, *args: Any, **kwargs: Any) -> None:
        pass

    def main_agent_id(self, turn: dict[str, Any]) -> str:
        return f"agent:{turn['conversation_id']}:main"


def _make_session(tmp_path: Path) -> tuple[TurnSession, Any]:
    db_file = tmp_path / "chk_many.db"
    engine = create_local_engine(f"sqlite:///{db_file.as_posix()}")
    metadata.create_all(engine)
    agents_store = ConversationAgentStore(engine, conversation_id=TURN["conversation_id"], user_id=TURN["user_id"])
    session = TurnSession(
        turn=dict(TURN),
        store=_DummyStore(),
        agents_store=agents_store,
        memory_store=None,
        provider_factory=lambda: object(),
        workspace_root=tmp_path,
    )
    return session, engine


def _register_fake_bg_agent(
    session: TurnSession,
    agent_id: str,
    agent_name: str,
    future: Future[ToolOutcome],
    *,
    parent_agent_id: str | None = None,
) -> None:
    parent = parent_agent_id or session.main_agent_id
    session._agent_tree()[agent_id] = parent
    with session._subagent_lock:
        session._background_subagents[agent_id] = {
            "future": future,
            "agent_id": agent_id,
            "agent_name": agent_name,
            "subagent_type": "explore",
            "cancel": threading.Event(),
            "output_file": session._agent_output_file(agent_id),
        }


def test_check_subagent_many_waits_once_and_returns_running_when_one_still_runs(tmp_path: Path) -> None:
    session, engine = _make_session(tmp_path)
    try:
        fut1: Future[ToolOutcome] = Future()
        fut2: Future[ToolOutcome] = Future()
        fut3: Future[ToolOutcome] = Future()  # stays running

        _register_fake_bg_agent(session, "agent:1", "Explorer-1", fut1)
        _register_fake_bg_agent(session, "agent:2", "Explorer-2", fut2)
        _register_fake_bg_agent(session, "agent:3", "Explorer-3", fut3)

        def _complete_first_two() -> None:
            time.sleep(0.15)
            fut1.set_result(ToolOutcome("succeeded", "ok 1", "Relatório 1 pronto.", {"agent_id": "agent:1"}))
            fut2.set_result(ToolOutcome("succeeded", "ok 2", "Relatório 2 pronto.", {"agent_id": "agent:2"}))

        finisher = threading.Thread(target=_complete_first_two, daemon=True)
        finisher.start()

        toolset = session._toolset(subagents=True, code_mode_active=True)
        t0 = time.monotonic()
        outcome = toolset.invoke(
            "CheckSubagent",
            {"agent_ids": ["agent:1", "agent:2", "agent:3"], "wait_seconds": 1},
        )
        elapsed = time.monotonic() - t0
        finisher.join(timeout=2.0)

        # Waited once (~1s for fut3), not 3x1s
        assert 0.8 <= elapsed < 1.8, f"Expected single ~1s wait, got {elapsed:.2f}s"
        assert outcome.status == "succeeded"
        assert outcome.payload["status"] == "running"
        assert outcome.payload["background"] is True
        assert outcome.payload["tool_kind"] == "agent"

        agents_info = outcome.payload["agents"]
        assert len(agents_info) == 3
        by_id = {item["agent_id"]: item for item in agents_info}
        assert by_id["agent:1"]["status"] == "completed"
        assert by_id["agent:2"]["status"] == "completed"
        assert by_id["agent:3"]["status"] == "running"

        assert '## Explorer-1 (agent_id="agent:1") — completed' in outcome.content
        assert "Relatório 1 pronto." in outcome.content
        assert '## Explorer-2 (agent_id="agent:2") — completed' in outcome.content
        assert "Relatório 2 pronto." in outcome.content
        assert '## Explorer-3 (agent_id="agent:3") — running' in outcome.content

        # Now complete fut3 and verify status becomes completed rapidly
        fut3.set_result(ToolOutcome("succeeded", "ok 3", "Relatório 3 pronto.", {"agent_id": "agent:3"}))
        t1 = time.monotonic()
        done_outcome = toolset.invoke(
            "check_subagent",
            {"agent_ids": ["agent:1", "agent:2", "agent:3"], "wait_seconds": 5},
        )
        assert (time.monotonic() - t1) < 0.5
        assert done_outcome.status == "succeeded"
        assert done_outcome.payload["status"] == "completed"
        assert "Relatório 3 pronto." in done_outcome.content
    finally:
        engine.dispose()


def test_check_subagent_many_ownership_refusal_per_id_and_all_failed(tmp_path: Path) -> None:
    session, engine = _make_session(tmp_path)
    try:
        fut_own: Future[ToolOutcome] = Future()
        fut_other: Future[ToolOutcome] = Future()
        fut_own.set_result(ToolOutcome("succeeded", "ok own", "Meu relatório.", {"agent_id": "agent:own"}))
        fut_other.set_result(ToolOutcome("succeeded", "ok other", "Relatório alheio.", {"agent_id": "agent:other"}))

        caller = SubagentCaller(
            agent_id="agent:sub-caller",
            name="SubCaller",
            depth=1,
            permission_mode="accept_edits",
            cancelled=lambda _t: False,
        )
        session._agent_tree()["agent:sub-caller"] = session.main_agent_id
        _register_fake_bg_agent(session, "agent:own", "OwnChild", fut_own, parent_agent_id="agent:sub-caller")
        _register_fake_bg_agent(session, "agent:other", "OtherChild", fut_other, parent_agent_id=session.main_agent_id)

        # Mixed: 1 owned (succeeded) + 1 unowned (refused) -> overall succeeded, per-id refusal present
        mixed = session._check_subagent(
            agent_ids=["agent:own", "agent:other"],
            wait_seconds=0,
            caller=caller,
        )
        assert mixed.status == "succeeded"
        by_id = {item["agent_id"]: item for item in mixed.payload["agents"]}
        assert by_id["agent:own"]["status"] == "completed"
        assert by_id["agent:other"]["status"] == "failed"
        assert by_id["agent:other"]["error_code"] == "AGENT_NOT_OWNED"
        assert "Meu relatório." in mixed.content
        assert "Relatório alheio." not in mixed.content
        assert "was not started by you" in mixed.content

        # All unowned -> overall status == "failed"
        all_unowned = session._check_subagent(
            agent_ids=["agent:other"],
            wait_seconds=0,
            caller=caller,
        )
        assert all_unowned.status == "failed"
        assert all_unowned.error_code == "AGENT_NOT_OWNED"
    finally:
        engine.dispose()


def test_check_subagent_validation_dedupe_truncation_and_legacy_fallback(tmp_path: Path) -> None:
    ws = ConversationWorkspace(tmp_path, "conv-val")
    seen_calls: list[tuple[str, int]] = []

    # Legacy callable that does NOT accept agent_ids kwarg (raises TypeError if agent_ids is passed)
    def _legacy_check(agent_id: str, *, wait_seconds: int = 0) -> ToolOutcome:
        seen_calls.append((agent_id, wait_seconds))
        return ToolOutcome(
            "succeeded",
            f"Done {agent_id}",
            f"Content for {agent_id}",
            {"agent_id": agent_id, "agent_name": f"Name-{agent_id}", "status": "completed"},
        )

    toolset = AgentToolset(ws, check_subagent=_legacy_check, public_tool_names=True)

    # Schema checks: required is empty, agent_ids has maxItems 16
    defn = toolset.resolve("CheckSubagent")
    assert list(defn.parameters.get("required") or []) == []
    props = defn.parameters["properties"]
    assert "agent_id" in props
    assert "agent_ids" in props
    assert props["agent_ids"]["maxItems"] == 16

    # Missing both agent_id and agent_ids -> AgentToolError / INVALID_ARGUMENTS
    with pytest.raises(AgentToolError, match="Pass agent_id or agent_ids"):
        toolset.check_subagent()
    with pytest.raises(AgentToolError, match="Pass agent_id or agent_ids"):
        toolset.check_subagent(agent_id="   ", agent_ids=["", "  "])

    inv_empty = toolset.invoke("CheckSubagent", {})
    assert inv_empty.status == "failed"
    assert inv_empty.error_code in {"INVALID_ARGUMENTS", "TOOL_REFUSED"}
    assert "Pass agent_id or agent_ids" in inv_empty.content

    # Single ID via agent_ids uses multi-agent format while preserving wait_seconds
    seen_calls.clear()
    single = toolset.check_subagent(agent_ids=["a1", "a1", "  "], wait_seconds=5)
    assert single.status == "succeeded"
    assert seen_calls == [("a1", 5)]
    assert single.payload.get("agent_ids") == ["a1"]
    assert isinstance(single.payload.get("agents"), list)
    assert len(single.payload["agents"]) == 1
    assert single.payload["agents"][0]["agent_id"] == "a1"
    assert '## Name-a1 (agent_id="a1") — completed' in single.content

    # >16 IDs + agent_id combined: deduplicated in order and truncated to 16, works via legacy fallback
    seen_calls.clear()
    many_ids = [f"id-{i}" for i in range(2, 25)]
    multi = toolset.check_subagent(agent_id="id-1", agent_ids=["id-1", *many_ids], wait_seconds=1)
    assert multi.status == "succeeded"
    assert len(multi.payload["agents"]) == 16
    assert [item["agent_id"] for item in multi.payload["agents"]] == [f"id-{i}" for i in range(1, 17)]
    assert '## Name-id-1 (agent_id="id-1") — completed' in multi.content
    assert '## Name-id-16 (agent_id="id-16") — completed' in multi.content
    assert "id-17" not in multi.content


def test_check_subagent_single_item_agent_ids_symmetry_cancellable_wait_and_corrupt_report(
    tmp_path: Path,
) -> None:
    """Regression test for Red Team #10:
    1) CheckSubagent(agent_ids=['agent:1']) via TurnSession returns multi-agent payload ('agents', '## ...').
    2) CheckSubagent(wait_seconds=10) aborts promptly in <= 0.25s slices when cancelled or call_abandoned.
    3) _resolve_subagent_entry handles non-UTF8 bytes in report_path with errors='replace'.
    """
    session, engine = _make_session(tmp_path)
    try:
        fut_done: Future[ToolOutcome] = Future()
        fut_done.set_result(ToolOutcome("succeeded", "ok 1", "Relatório único.", {"agent_id": "agent:1"}))
        _register_fake_bg_agent(session, "agent:1", "Explorer-1", fut_done)

        toolset = session._toolset(subagents=True, code_mode_active=True)
        out_multi_one = toolset.invoke("CheckSubagent", {"agent_ids": ["agent:1"], "wait_seconds": 0})
        assert out_multi_one.status == "succeeded"
        assert out_multi_one.payload.get("agent_ids") == ["agent:1"]
        assert isinstance(out_multi_one.payload.get("agents"), list)
        assert len(out_multi_one.payload["agents"]) == 1
        assert '## Explorer-1 (agent_id="agent:1") — completed' in out_multi_one.content

        # Cancellable wait on running future: cancel after 0.1s while wait_seconds=10
        fut_running: Future[ToolOutcome] = Future()
        _register_fake_bg_agent(session, "agent:run", "SlowRunner", fut_running)
        abandoned = threading.Event()
        toolset.set_call_abandoned_check(abandoned.is_set)
        session.toolset = toolset

        def _trigger_abandon() -> None:
            time.sleep(0.12)
            abandoned.set()

        threading.Thread(target=_trigger_abandon, daemon=True).start()
        t0 = time.monotonic()
        out_aborted = toolset.invoke(
            "CheckSubagent",
            {"agent_ids": ["agent:run"], "wait_seconds": 10},
        )
        elapsed = time.monotonic() - t0
        assert elapsed < 0.8, f"Expected prompt abort (< 0.8s), took {elapsed:.2f}s"
        assert out_aborted.payload["status"] == "running"

        # Non-UTF8 bytes in report_path on disk
        rec = session.agents_store.create(
            "corrupt-agent",
            "Explorer",
            parent_agent_id=session.main_agent_id,
            provider="litellm",
            model_id="~anthropic/claude-opus-4-6",
            force_new=True,
        )
        corrupt_id = str(rec["agent_id"])
        session._agent_tree()[corrupt_id] = session.main_agent_id
        rel_out = session._agent_output_file(corrupt_id)
        report_file = session.workspace.root / rel_out
        report_file.parent.mkdir(parents=True, exist_ok=True)
        report_file.write_bytes(b"Relatorio com byte invalido: \xff\xfe fim.")

        abandoned.clear()
        out_corrupt = toolset.invoke("CheckSubagent", {"agent_id": corrupt_id, "wait_seconds": 0})
        assert out_corrupt.status == "succeeded"
        assert "Relatorio com byte invalido:" in out_corrupt.content
        assert "\ufffd" in out_corrupt.content
    finally:
        engine.dispose()

