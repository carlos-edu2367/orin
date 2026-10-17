from __future__ import annotations

import json
from pathlib import Path
import threading
from typing import Any

import sqlalchemy as sa

from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.events import AgentActivityEventType
from agentos.agentic.provider_stream import NormalizedStreamItem, StreamKind
from agentos.agentic.session import TurnSession
from agentos.agentic.workspace import ConversationWorkspace
from agentos.persistence.postgres.conversation_agents import ConversationAgentStore
from agentos.persistence.postgres.schema import metadata
from agentos.persistence.sqlite import create_local_engine


TURN = {
    "turn_id": "turn-par-1",
    "conversation_id": "conv-par-1",
    "user_id": "user-1",
    "execution_id": "exe-par-1",
    "provider": "litellm",
    "model_id": "~anthropic/claude-opus-4-6",
    "user_message_id": "msg-user",
    "assistant_message_id": "msg-assistant",
    "code_mode": "code",
}


class _RecordingStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.deltas: list[str] = []
        self.finished: list[tuple[bool, str | None]] = []
        self.activity: list[tuple[AgentActivityEventType, str, dict[str, Any], str | None]] = []
        self.tool_records: list[dict[str, object]] = []

    def load(self, _turn_id: str) -> dict[str, Any]:
        return dict(TURN)

    def history_for_turn(self, _turn: object) -> list[dict[str, str]]:
        return [{"role": "user", "content": "Investigue o projeto em paralelo."}]

    def delta(self, _turn: object, text: str) -> None:
        with self._lock:
            self.deltas.append(text)

    def finish(self, _turn: object, *, failed: bool = False, code: str | None = None) -> None:
        with self._lock:
            self.finished.append((failed, code))

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
            self.activity.append((event_type, summary, dict(payload or {}), agent_id))

    def main_agent_id(self, turn: dict[str, Any]) -> str:
        return f"agent:{turn['conversation_id']}:main"

    def record_tool_call(
        self,
        _turn: object,
        *,
        tool_name: str,
        arguments: object,
        status: str,
        summary: str,
    ) -> None:
        with self._lock:
            self.tool_records.append(
                {
                    "tool_name": tool_name,
                    "arguments": arguments,
                    "status": status,
                    "summary": summary,
                }
            )


def _text_batch(value: str) -> list[NormalizedStreamItem]:
    return [
        NormalizedStreamItem(StreamKind.TEXT, 1, text=value),
        NormalizedStreamItem(StreamKind.FINISH, 2, finish_reason="stop"),
    ]


def _parallel_tool_calls_batch(calls: list[tuple[str, str, dict[str, Any]]]) -> list[NormalizedStreamItem]:
    items: list[NormalizedStreamItem] = []
    seq = 1
    for call_id, name, args in calls:
        items.append(
            NormalizedStreamItem(
                StreamKind.TOOL_CALL,
                seq,
                tool_call_id=call_id,
                tool_name=name,
                arguments_delta=json.dumps(args),
            )
        )
        seq += 1
    items.append(NormalizedStreamItem(StreamKind.FINISH, seq, finish_reason="tool_calls"))
    return items


def test_agent_toolset_active_invocation_id_is_thread_local(tmp_path: Path) -> None:
    workspace = ConversationWorkspace(tmp_path, "conv-tls")
    (workspace.root / "sample.txt").write_text("hello\n", encoding="utf-8")

    observed_ids: dict[str, str | None] = {}
    barrier = threading.Barrier(4, timeout=5.0)
    toolset_ref: list[AgentToolset] = []

    def _run_subagent(subagent_type: str, task: str, **_kwargs: Any):
        from agentos.agentic.agent_tools import ToolOutcome

        barrier.wait()
        observed_ids[task] = toolset_ref[0]._active_invocation_id
        barrier.wait()
        return ToolOutcome("succeeded", "ok", f"done {task}", {"subagent_type": subagent_type})

    toolset = AgentToolset(workspace, run_subagent=_run_subagent)
    toolset_ref.append(toolset)

    def _worker(idx: int) -> None:
        call_id = f"call-{idx}"
        task_key = f"task-{idx}"
        toolset.invoke(
            "run_subagent",
            {"subagent_type": "explore", "task": task_key},
            call_id=call_id,
        )

    threads = [threading.Thread(target=_worker, args=(i,)) for i in range(1, 5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5.0)

    assert observed_ids == {
        "task-1": "call-1",
        "task-2": "call-2",
        "task-3": "call-3",
        "task-4": "call-4",
    }


def test_conversation_agent_store_concurrent_create_force_new_and_usage(tmp_path: Path) -> None:
    db_file = tmp_path / "agents_concurrency.db"
    engine = create_local_engine(f"sqlite:///{db_file.as_posix()}")
    metadata.create_all(engine)

    store = ConversationAgentStore(engine, conversation_id="conv-1", user_id="user-1")
    barrier = threading.Barrier(4, timeout=5.0)
    created_records: list[dict[str, object]] = []
    lock = threading.Lock()

    def _create_and_record(idx: int) -> None:
        barrier.wait()
        rec = store.create(
            "explore",
            f"Explorer #{idx}",
            parent_agent_id="agent:conv-1:main",
            provider="litellm",
            model_id="m1",
            force_new=True,
        )
        with lock:
            created_records.append(rec)
        # Also record usage concurrently on a shared agent id
        store.record_usage(
            "agent:conv-1:main",
            "litellm",
            "m1",
            input_tokens=10,
            output_tokens=5,
            total_tokens=15,
        )

    threads = [threading.Thread(target=_create_and_record, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5.0)

    agent_ids = {str(r["agent_id"]) for r in created_records}
    assert len(agent_ids) == 4
    for aid in agent_ids:
        fetched = store.get(aid)
        assert fetched is not None
        assert fetched["agent_id"] == aid

    usage = store.usage_by_agent()["agent:conv-1:main"]
    assert usage["input_tokens"] == 40
    assert usage["output_tokens"] == 20
    assert usage["total_tokens"] == 60
    engine.dispose()


def test_parallel_run_subagent_calls_in_single_batch_execute_concurrently_with_distinct_agent_ids(
    tmp_path: Path,
) -> None:
    db_file = tmp_path / "par_subagents.db"
    engine = create_local_engine(f"sqlite:///{db_file.as_posix()}")
    metadata.create_all(engine)

    store = _RecordingStore()
    agents_store = ConversationAgentStore(engine, conversation_id=TURN["conversation_id"], user_id=TURN["user_id"])
    barrier = threading.Barrier(2, timeout=5.0)

    class _ConcurrentChildProvider:
        def __init__(self, label: str) -> None:
            self.label = label

        def stream(self, _request: dict[str, Any]):
            barrier.wait()
            return iter(_text_batch(f"Relatório paralelo de {self.label}"))

    child_counter = 0
    child_lock = threading.Lock()

    def _child_provider_factory(_model_id: str):
        nonlocal child_counter
        with child_lock:
            child_counter += 1
            idx = child_counter
        return _ConcurrentChildProvider(f"explorer-{idx}")

    main_batches = [
        _parallel_tool_calls_batch(
            [
                ("call-sub-1", "run_subagent", {"subagent_type": "explore", "task": "Inspecione módulo A", "description": "Explore A"}),
                ("call-sub-2", "run_subagent", {"subagent_type": "explore", "task": "Inspecione módulo B", "description": "Explore B"}),
            ]
        ),
        _text_batch("Síntese final dos dois subagentes."),
    ]

    class _MainProvider:
        def stream(self, _request: dict[str, Any]):
            return iter(main_batches.pop(0))

    session = TurnSession(
        turn=dict(TURN),
        store=store,
        agents_store=agents_store,
        memory_store=None,
        provider_factory=_MainProvider,
        workspace_root=tmp_path,
        child_provider_factory=_child_provider_factory,
    )

    result = session.build_runtime().run(TURN["turn_id"])
    assert result.state == "completed"

    created_agents = agents_store.list()
    assert len(created_agents) == 2
    distinct_ids = {row["agent_id"] for row in created_agents}
    assert len(distinct_ids) == 2

    received_events = [
        (payload, agent_id)
        for ev_type, _summary, payload, agent_id in store.activity
        if ev_type == AgentActivityEventType.AGENT_MESSAGE_RECEIVED
    ]
    assert len(received_events) == 2
    assert {agent_id for _, agent_id in received_events} == distinct_ids
    engine.dispose()


def test_run_subagent_resumes_existing_subagent_by_agent_id_preserving_context(tmp_path: Path) -> None:
    db_file = tmp_path / "resume_subagent.db"
    engine = create_local_engine(f"sqlite:///{db_file.as_posix()}")
    metadata.create_all(engine)

    store = _RecordingStore()
    agents_store = ConversationAgentStore(engine, conversation_id=TURN["conversation_id"], user_id=TURN["user_id"])
    captured_child_requests: list[dict[str, Any]] = []

    child_responses = [
        _text_batch("Encontrei auth.py na linha 42."),
        _text_batch("O fluxo de login usa JWT validado em auth.py:42."),
    ]

    class _ChildProvider:
        def stream(self, request: dict[str, Any]):
            captured_child_requests.append(request)
            return iter(child_responses.pop(0))

    session = TurnSession(
        turn=dict(TURN),
        store=store,
        agents_store=agents_store,
        memory_store=None,
        provider_factory=_ChildProvider,
        workspace_root=tmp_path,
        child_provider_factory=lambda _m: _ChildProvider(),
    )

    first = session._run_subagent(
        subagent_type="explore",
        task="Localize o arquivo de autenticação",
        description="Busca inicial",
    )
    assert first.status == "succeeded"
    first_agent_id = str(first.payload["agent_id"])
    assert first_agent_id

    resumed = session._run_subagent(
        subagent_type="explore",
        task="Agora detalhe como o token é validado nesse arquivo",
        description="Continuação",
        agent_id=first_agent_id,
    )
    assert resumed.status == "succeeded"
    assert resumed.payload["agent_id"] == first_agent_id
    assert resumed.payload.get("resumed") is True

    assert len(captured_child_requests) == 2
    second_messages = captured_child_requests[1]["messages"]
    flattened = json.dumps(second_messages, ensure_ascii=False)
    assert "Localize o arquivo de autenticação" in flattened
    assert "Encontrei auth.py na linha 42." in flattened
    assert "Agora detalhe como o token é validado nesse arquivo" in flattened
    engine.dispose()


def test_run_subagent_in_background_and_check_subagent_polls_and_waits(tmp_path: Path) -> None:
    db_file = tmp_path / "bg_subagent.db"
    engine = create_local_engine(f"sqlite:///{db_file.as_posix()}")
    metadata.create_all(engine)

    store = _RecordingStore()
    agents_store = ConversationAgentStore(engine, conversation_id=TURN["conversation_id"], user_id=TURN["user_id"])
    release_child = threading.Event()
    child_started = threading.Event()

    class _SlowChildProvider:
        def stream(self, _request: dict[str, Any]):
            child_started.set()
            release_child.wait(timeout=5.0)
            return iter(_text_batch("Relatório em background finalizado com sucesso."))

    main_agent_id_holder: list[str] = []

    class _MainProvider:
        def __init__(self) -> None:
            self.step = 0

        def stream(self, request: dict[str, Any]):
            self.step += 1
            if self.step == 1:
                return iter(
                    _parallel_tool_calls_batch(
                        [
                            (
                                "call-bg-1",
                                "run_subagent",
                                {
                                    "subagent_type": "explore",
                                    "task": "Auditoria demorada em background",
                                    "description": "Bg Audit",
                                    "run_in_background": True,
                                },
                            )
                        ]
                    )
                )
            if self.step == 2:
                # Extract agent_id from the tool result message
                messages_str = json.dumps(request["messages"])
                import re

                m = re.search(r"(agent:conv-par-1:[a-z0-9_-]+)", messages_str)
                assert m is not None, f"agent_id not found in {messages_str}"
                bg_agent_id = m.group(1)
                main_agent_id_holder.append(bg_agent_id)
                child_started.wait(timeout=2.0)
                return iter(
                    _parallel_tool_calls_batch(
                        [("call-chk-1", "check_subagent", {"agent_id": bg_agent_id, "wait_seconds": 0})]
                    )
                )
            if self.step == 3:
                # Release child and poll again via check_subagent
                release_child.set()
                return iter(
                    _parallel_tool_calls_batch(
                        [("call-chk-2", "check_subagent", {"agent_id": main_agent_id_holder[0], "wait_seconds": 5})]
                    )
                )
            return iter(_text_batch("Concluído após aguardar o subagente em background."))

    main_provider = _MainProvider()
    session = TurnSession(
        turn=dict(TURN),
        store=store,
        agents_store=agents_store,
        memory_store=None,
        provider_factory=lambda: main_provider,
        workspace_root=tmp_path,
        child_provider_factory=lambda _m: _SlowChildProvider(),
    )

    runtime = session.build_runtime()
    try:
        result = runtime.run(TURN["turn_id"])
    finally:
        runtime.close()

    assert result.state == "completed"
    check_calls = [r for r in store.tool_records if r["tool_name"] == "check_subagent"]
    assert len(check_calls) == 2
    assert "ainda em execução" in str(check_calls[0]["summary"]).lower() or "running" in str(check_calls[0]["summary"]).lower()
    assert "recebeu a resposta" in str(check_calls[1]["summary"]).lower()
    engine.dispose()


def test_cannot_resume_background_subagent_while_still_running_and_polling_is_never_cached(tmp_path: Path) -> None:
    """Adversarial test:
    1. Attempting to resume a background subagent while it is still running must fail with SUBAGENT_STILL_RUNNING.
    2. Calling check_subagent with identical arguments (wait_seconds=0) once while running and once after completion must NOT return a stale cached result.
    """
    store = _RecordingStore()
    engine = create_local_engine(f"sqlite:///{tmp_path / 'adv.db'}")
    metadata.create_all(engine)
    agents_store = ConversationAgentStore(engine, conversation_id=TURN["conversation_id"], user_id=TURN["user_id"])

    release_child = threading.Event()
    child_started = threading.Event()
    child_finished = threading.Event()

    class _SlowChildProvider:
        def stream(self, _request: dict[str, Any]):
            child_started.set()
            release_child.wait(timeout=5.0)
            return iter(_text_batch("Resultado final do background."))

    session = TurnSession(
        turn=dict(TURN),
        store=store,
        agents_store=agents_store,
        memory_store=None,
        provider_factory=lambda: _SlowChildProvider(),
        workspace_root=tmp_path,
        child_provider_factory=lambda _m: _SlowChildProvider(),
    )

    bg_outcome = session._run_subagent(
        "explore",
        "Investigar em background",
        description="Background",
        run_in_background=True,
    )
    assert bg_outcome.status == "succeeded"
    bg_agent_id = str(bg_outcome.payload["agent_id"])
    assert child_started.wait(timeout=2.0)

    # 1. Trying to resume while still running must be rejected
    premature_resume = session._run_subagent(
        "explore",
        "Continuar antes da hora",
        agent_id=bg_agent_id,
    )
    assert premature_resume.status == "failed"
    assert premature_resume.error_code == "SUBAGENT_STILL_RUNNING"

    # 2. First poll with wait_seconds=0 returns running
    poll_1 = session._check_subagent(bg_agent_id, wait_seconds=0)
    assert poll_1.status == "succeeded"
    assert poll_1.payload["status"] == "running"

    # Release child and wait via check_subagent(wait_seconds=5), then poll with wait_seconds=0 again
    release_child.set()
    poll_wait = session._check_subagent(bg_agent_id, wait_seconds=5)
    assert poll_wait.status == "succeeded"
    assert poll_wait.payload["status"] == "completed"

    poll_2 = session._check_subagent(bg_agent_id, wait_seconds=0)
    assert poll_2.status == "succeeded"
    assert poll_2.payload["status"] == "completed"
    assert "Resultado final do background." in poll_2.content

    session.close_background_subagents()
    engine.dispose()

