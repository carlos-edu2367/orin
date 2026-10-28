from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from agentos.agentic.agent_tools import ToolOutcome
from agentos.agentic.events import AgentActivityEvent, AgentActivityEventType
from agentos.agentic.prompts import FRIEND_SYSTEM_PROMPT_SECTION, LIVE_MODE_SPEAK_GUIDANCE
from agentos.agentic.provider_stream import NormalizedStreamItem, StreamKind
from agentos.agentic.runtime import AgenticLimits, AgenticTurnRuntime
from agentos.agentic.session import TurnSession, build_system_prompt
from agentos.memory.friend_persona import FriendPersonaStore
from agentos.persistence.postgres.agent_memory import PostgresAgentMemoryStore
from agentos.persistence.postgres.schema import metadata


def _base_turn(**overrides: object) -> dict[str, object]:
    turn: dict[str, object] = {
        "turn_id": "turn-friend-1",
        "conversation_id": "conv-friend-1",
        "user_id": "user-1",
        "workspace_id": "workspace-1",
        "agent_id": "agent:conv-friend-1:main",
        "execution_id": "exec-friend-1",
        "provider": "openrouter",
        "model_id": "test-model",
        "user_message_id": "msg-user-1",
        "assistant_message_id": "msg-assistant-1",
        "conversation_mode": "friend",
        "live_mode": True,
    }
    turn.update(overrides)
    return turn


class TimelineStore:
    def __init__(
        self,
        turn: dict[str, object] | None = None,
        history: list[dict[str, object]] | None = None,
    ) -> None:
        self.turn = turn or _base_turn()
        self.history = list(history or [{"role": "user", "content": "Como está minha agenda hoje?"}])
        self.timeline: list[tuple[str, object]] = []
        self.deltas: list[str] = []
        self.activity: list[tuple[AgentActivityEventType, str, dict[str, object]]] = []
        self.finished: list[tuple[bool, str | None]] = []

    def load(self, turn_id: str) -> dict[str, object]:
        assert turn_id == self.turn["turn_id"]
        return self.turn

    def history_for_turn(self, turn: dict[str, object]) -> list[dict[str, object]]:
        return [dict(item) for item in self.history]

    def main_agent_id(self, turn: dict[str, object]) -> str:
        return f"agent:{turn['conversation_id']}:main"

    def lifecycle(self, turn: dict[str, object], state: str, **payload: object) -> None:
        self.timeline.append((f"lifecycle:{state}", dict(payload)))

    def delta(self, turn: dict[str, object], text: str) -> None:
        self.deltas.append(text)
        self.timeline.append(("delta", text))

    def record(
        self,
        turn: dict[str, object],
        event_type: AgentActivityEventType,
        summary: str,
        payload: dict[str, object] | None = None,
        *,
        agent_id: str | None = None,
        parent_agent_id: str | None = None,
    ) -> None:
        clean_payload = dict(payload or {})
        self.activity.append((event_type, summary, clean_payload))
        self.timeline.append((f"event:{event_type.value}", clean_payload))

    def record_tool_call(
        self,
        turn: dict[str, object],
        *,
        tool_name: str,
        arguments: dict[str, object],
        status: str,
        summary: str,
    ) -> None:
        self.timeline.append((f"tool_record:{tool_name}:{status}", summary))

    def finish(self, turn: dict[str, object], *, failed: bool = False, code: str | None = None) -> None:
        self.finished.append((failed, code))
        self.timeline.append(("finish", {"failed": failed, "code": code}))


def test_speak_and_code_session_activity_event_types_exist_and_serialize() -> None:
    assert AgentActivityEventType.SPEAK_BLOCK_STARTED == "speak.block_started"
    assert AgentActivityEventType.SPEAK_DELTA == "speak.delta"
    assert AgentActivityEventType.SPEAK_CHUNK_READY == "speak.chunk_ready"
    assert AgentActivityEventType.SPEAK_FILLER_TRIGGERED == "speak.filler_triggered"
    assert AgentActivityEventType.SPEAK_INTERRUPTED == "speak.interrupted"
    assert AgentActivityEventType.SPEAK_FINISHED == "speak.finished"
    assert AgentActivityEventType.CODE_SESSION_PROGRESS == "code_session.progress"

    now = datetime.now(UTC)
    evt = AgentActivityEvent(
        event_id="evt-speak-1",
        conversation_id="conv-1",
        turn_id="turn-1",
        execution_id="exec-1",
        user_id="user-1",
        agent_id="agent-1",
        sequence=1,
        event_type=AgentActivityEventType.SPEAK_CHUNK_READY,
        summary="Chunk de fala pronta",
        payload={
            "chunk_index": 0,
            "block_index": 0,
            "clean_text": "Olá, meu nome é Orin",
            "tts_text": "Olá, meu nome é Orin",
            "boundary_kind": "clause",
            "is_first_in_block": True,
            "is_last_in_block": False,
        },
        created_at=now,
    )
    assert evt.payload["clean_text"] == "Olá, meu nome é Orin"
    assert evt.payload["tts_text"] == "Olá, meu nome é Orin"
    assert evt.payload["chunk_index"] == 0


def test_runtime_emits_speak_chunks_before_tool_execution_and_strips_tags_from_stored_message() -> None:
    store = TimelineStore()

    class SpeakAndToolProvider:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def stream(self, request: dict[str, object]):
            self.calls.append(request)
            if len(self.calls) == 1:
                return iter(
                    [
                        NormalizedStreamItem(
                            StreamKind.TEXT,
                            1,
                            text="<speak>Olá, meu nome é Orin, ",
                        ),
                        NormalizedStreamItem(
                            StreamKind.TEXT,
                            2,
                            text="vou checar sua agenda!</speak>",
                        ),
                        NormalizedStreamItem(
                            StreamKind.TOOL_CALL,
                            3,
                            tool_call_id="call-agenda-1",
                            tool_name="check_calendar",
                            arguments_delta='{"date":"today"}',
                        ),
                        NormalizedStreamItem(
                            StreamKind.FINISH,
                            4,
                            finish_reason="tool_calls",
                        ),
                    ]
                )
            return iter(
                [
                    NormalizedStreamItem(
                        StreamKind.TEXT,
                        1,
                        text="<speak>Você tem reunião às 15h.</speak> Aqui estão os detalhes em markdown: **Reunião 15h**.",
                    ),
                    NormalizedStreamItem(
                        StreamKind.FINISH,
                        2,
                        finish_reason="stop",
                    ),
                ]
            )

    class CalendarToolset:
        def schemas(self):
            return [
                {
                    "type": "function",
                    "function": {
                        "name": "check_calendar",
                        "description": "Checa a agenda",
                        "parameters": {
                            "type": "object",
                            "properties": {"date": {"type": "string"}},
                        },
                    },
                }
            ]

        def is_read_only(self, name: str) -> bool:
            return True

        def argument_names(self, name: str) -> frozenset[str]:
            return frozenset({"date"})

        def invoke(self, name: str, arguments: dict[str, object]) -> ToolOutcome:
            store.timeline.append((f"tool_invoke:{name}", dict(arguments)))
            return ToolOutcome("succeeded", "Reunião às 15h", "1 evento encontrado", {"events": ["15:00 Reunião"]})

    provider = SpeakAndToolProvider()
    runtime = AgenticTurnRuntime(
        store=store,
        provider=provider,
        toolset=CalendarToolset(),
        limits=AgenticLimits(max_actions=4, max_iterations=4, deadline=timedelta(seconds=10)),
        clock=lambda: datetime.now(UTC),
    )

    result = runtime.run("turn-friend-1")
    assert result.state == "completed"

    # Find the index where check_calendar was invoked
    tool_invoke_idx = next(
        idx for idx, (kind, _) in enumerate(store.timeline) if kind == "tool_invoke:check_calendar"
    )
    events_before_tool = store.timeline[:tool_invoke_idx]
    chunk_events_before_tool = [
        payload for kind, payload in events_before_tool if kind == f"event:{AgentActivityEventType.SPEAK_CHUNK_READY.value}"
    ]
    assert any(kind == f"event:{AgentActivityEventType.SPEAK_BLOCK_STARTED.value}" for kind, _ in events_before_tool)
    assert len(chunk_events_before_tool) == 1
    assert [c["raw_text"] for c in chunk_events_before_tool] == [
        "Olá, meu nome é Orin, vou checar sua agenda!",
    ]
    assert [c["clean_text"] for c in chunk_events_before_tool] == [
        "Olá, meu nome é Orin, vou checar sua agenda",
    ]
    assert any(kind == f"event:{AgentActivityEventType.SPEAK_FINISHED.value}" for kind, _ in events_before_tool)

    # Stored visual deltas must NOT contain <speak> or </speak>
    full_visual_text = "".join(store.deltas)
    assert "<speak>" not in full_visual_text
    assert "</speak>" not in full_visual_text
    assert "Olá, meu nome é Orin, vou checar sua agenda!" in full_visual_text
    assert "Você tem reunião às 15h. Aqui estão os detalhes em markdown: **Reunião 15h**." in full_visual_text


def test_runtime_rehydrates_speak_tags_in_request_messages_to_prevent_format_drift() -> None:
    history = [
        {"role": "user", "content": "Oi Orin!"},
        {
            "role": "assistant",
            "content": "Fala Carlos! Tudo ótimo por aqui.\n\n- Tópico 1\n- Tópico 2",
        },
        {"role": "user", "content": "Qual é o próximo passo?"},
    ]
    store = TimelineStore(turn=_base_turn(live_mode=True, conversation_mode="friend"), history=history)

    class InspectProvider:
        def __init__(self) -> None:
            self.requests: list[dict[str, object]] = []

        def stream(self, request: dict[str, object]):
            self.requests.append(request)
            return iter(
                [
                    NormalizedStreamItem(StreamKind.TEXT, 1, text="<speak>Vamos revisar o código.</speak>"),
                    NormalizedStreamItem(StreamKind.FINISH, 2, finish_reason="stop"),
                ]
            )

    provider = InspectProvider()
    runtime = AgenticTurnRuntime(store=store, provider=provider)
    runtime.run("turn-friend-1")

    assert len(provider.requests) == 1
    sent_messages = provider.requests[0]["messages"]
    assistant_msgs = [m for m in sent_messages if isinstance(m, dict) and m.get("role") == "assistant"]
    assert len(assistant_msgs) == 1
    assert assistant_msgs[0]["content"] == "<speak>Fala Carlos! Tudo ótimo por aqui.</speak>\n\n- Tópico 1\n- Tópico 2"
    # Original history dict must remain untouched
    assert "<speak>" not in str(history[1]["content"])

    # In standard chat mode with live_mode=False, no <speak> re-hydration occurs
    chat_store = TimelineStore(
        turn=_base_turn(live_mode=False, conversation_mode="chat"),
        history=history,
    )
    chat_provider = InspectProvider()
    AgenticTurnRuntime(store=chat_store, provider=chat_provider).run("turn-friend-1")
    chat_assistant_msgs = [
        m for m in chat_provider.requests[0]["messages"] if isinstance(m, dict) and m.get("role") == "assistant"
    ]
    assert chat_assistant_msgs[0]["content"] == "Fala Carlos! Tudo ótimo por aqui.\n\n- Tópico 1\n- Tópico 2"


def test_runtime_optimistic_speech_lie_watchdog_emits_retraction_when_mutating_tool_fails_without_explanation() -> None:
    store = TimelineStore()

    class OptimisticLieProvider:
        def __init__(self) -> None:
            self.calls = 0

        def stream(self, request: dict[str, object]):
            self.calls += 1
            if self.calls == 1:
                return iter(
                    [
                        NormalizedStreamItem(
                            StreamKind.TEXT,
                            1,
                            text="<speak>Pronto, já salvei a alteração no arquivo!</speak>",
                        ),
                        NormalizedStreamItem(
                            StreamKind.TOOL_CALL,
                            2,
                            tool_call_id="call-write-1",
                            tool_name="write_file",
                            arguments_delta='{"path":"app.py","content":"x=1"}',
                        ),
                        NormalizedStreamItem(StreamKind.FINISH, 3, finish_reason="tool_calls"),
                    ]
                )
            # Iteration 2: model outputs plain text error without a new <speak> block
            return iter(
                [
                    NormalizedStreamItem(StreamKind.TEXT, 1, text="Erro ao salvar: permissão negada."),
                    NormalizedStreamItem(StreamKind.FINISH, 2, finish_reason="stop"),
                ]
            )

    class FailingWriteToolset:
        def schemas(self):
            return [
                {
                    "type": "function",
                    "function": {
                        "name": "write_file",
                        "description": "Write file",
                        "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}},
                    },
                }
            ]

        def is_read_only(self, name: str) -> bool:
            return False

        def argument_names(self, name: str) -> frozenset[str]:
            return frozenset({"path", "content"})

        def invoke(self, name: str, arguments: dict[str, object]) -> ToolOutcome:
            return ToolOutcome("failed", "Permissão negada ao gravar app.py", "permission_denied", {"error": "EACCES"})

    runtime = AgenticTurnRuntime(
        store=store,
        provider=OptimisticLieProvider(),
        toolset=FailingWriteToolset(),
        limits=AgenticLimits(max_actions=4, max_iterations=4, deadline=timedelta(seconds=10)),
        clock=lambda: datetime.now(UTC),
    )

    result = runtime.run("turn-friend-1")
    assert result.state == "completed"

    speak_chunks = [
        payload["clean_text"]
        for evt_type, _, payload in store.activity
        if evt_type is AgentActivityEventType.SPEAK_CHUNK_READY
    ]
    assert "Atenção: houve uma falha ao executar a ação. Veja o detalhe na tela." in speak_chunks


def test_runtime_optimistic_speech_lie_watchdog_does_not_fire_when_model_explains_failure_in_new_speak() -> None:
    store = TimelineStore()

    class HonestRecoveryProvider:
        def __init__(self) -> None:
            self.calls = 0

        def stream(self, request: dict[str, object]):
            self.calls += 1
            if self.calls == 1:
                return iter(
                    [
                        NormalizedStreamItem(
                            StreamKind.TEXT,
                            1,
                            text="<speak>Vou salvar o arquivo agora.</speak>",
                        ),
                        NormalizedStreamItem(
                            StreamKind.TOOL_CALL,
                            2,
                            tool_call_id="call-write-1",
                            tool_name="write_file",
                            arguments_delta='{"path":"app.py","content":"x=1"}',
                        ),
                        NormalizedStreamItem(StreamKind.FINISH, 3, finish_reason="tool_calls"),
                    ]
                )
            return iter(
                [
                    NormalizedStreamItem(
                        StreamKind.TEXT,
                        1,
                        text="<speak>Ops, deu erro de permissão ao tentar salvar o arquivo.</speak>",
                    ),
                    NormalizedStreamItem(StreamKind.FINISH, 2, finish_reason="stop"),
                ]
            )

    class FailingWriteToolset:
        def schemas(self):
            return []

        def is_read_only(self, name: str) -> bool:
            return False

        def argument_names(self, name: str) -> frozenset[str]:
            return frozenset({"path", "content"})

        def invoke(self, name: str, arguments: dict[str, object]) -> ToolOutcome:
            return ToolOutcome("failed", "Permissão negada", "permission_denied")

    runtime = AgenticTurnRuntime(
        store=store,
        provider=HonestRecoveryProvider(),
        toolset=FailingWriteToolset(),
        limits=AgenticLimits(max_actions=4, max_iterations=4, deadline=timedelta(seconds=10)),
        clock=lambda: datetime.now(UTC),
    )
    runtime.run("turn-friend-1")

    speak_chunks = [
        payload["clean_text"]
        for evt_type, _, payload in store.activity
        if evt_type is AgentActivityEventType.SPEAK_CHUNK_READY
    ]
    assert "Atenção: houve uma falha ao executar a ação. Veja o detalhe na tela." not in speak_chunks
    assert any("Ops" in chunk or "erro de permissão" in chunk for chunk in speak_chunks)


def test_build_system_prompt_injects_friend_persona_rubric_in_stable_and_affective_memories_in_volatile() -> None:
    rubric = "## Identidade e Personalidade (Orin Friend)\n- Candor: 0.70\n- Warmth: 0.85"
    affective_memories = [
        {"category": "inside_joke", "fact": "Chama deploy de sexta-feira de roleta russa"},
        {"category": "life_context", "fact": "Está construindo o projeto Orin"},
    ]
    stable, volatile = build_system_prompt(
        agent_name="Orin",
        workspace_entries=[],
        subagents=[],
        memories=[{"category": "preference", "fact": "Usa Python 3.12"}],
        skills=[],
        environment={"os": "Windows", "shell": "powershell", "python": "3.12", "git": True},
        conversation_mode="friend",
        live_mode=True,
        friend_rubric=rubric,
        friend_mood="animado e focado",
        friend_memories=affective_memories,
    )

    assert FRIEND_SYSTEM_PROMPT_SECTION.strip() in stable
    assert LIVE_MODE_SPEAK_GUIDANCE.strip() in stable
    assert rubric in stable
    # Mood and affective memories must live in volatile, NOT in stable (preserving cache prefix)
    assert "animado e focado" not in stable
    assert "roleta russa" not in stable
    assert "animado e focado" in volatile
    assert "roleta russa" in volatile
    assert "Está construindo o projeto Orin" in volatile


def test_turn_session_in_friend_mode_loads_persona_and_dual_layer_memories(tmp_path: Path) -> None:
    engine = create_engine("sqlite://", future=True, connect_args={"check_same_thread": False}, poolclass=StaticPool)
    metadata.create_all(engine)

    soul_dir = tmp_path / "friend_soul"
    persona_store = FriendPersonaStore(engine, soul_dir=soul_dir)
    persona_store.update_persona(
        "user-1",
        current_mood="curioso e atento",
        style_notes=["Use um tom caloroso e direto."],
    )
    memory_store = PostgresAgentMemoryStore(engine, user_id="user-1")
    memory_store.save(
        "Chama bug intermitente de fantasma da ópera",
        kind="inside_joke",
        scope_type="friend",
        salience=0.9,
    )
    memory_store.save(
        "Prefere testes com pytest",
        kind="preference",
        scope_type="user",
        salience=0.8,
    )

    store = TimelineStore(
        turn=_base_turn(conversation_mode="friend", live_mode=False),
        history=[{"role": "user", "content": "Como resolvemos aquele bug fantasma com pytest?"}],
    )

    class AgentsDouble:
        def create(self, name: str, role: str, **kwargs: object) -> dict[str, object]:
            return {"agent_id": "agent-1", "name": name, "role": role, "created": True}

        def find(self, name: str) -> dict[str, object] | None:
            return None

        def list(self) -> list[dict[str, object]]:
            return []

        def set_state(self, agent_id: str, state: str) -> None:
            pass

        def record_usage(self, *args: object, **kwargs: object) -> None:
            pass

    class SimpleProvider:
        def __init__(self) -> None:
            self.requests: list[dict[str, object]] = []

        def stream(self, request: dict[str, object]):
            self.requests.append(request)
            return iter(
                [
                    NormalizedStreamItem(StreamKind.TEXT, 1, text="<speak>Vamos caçar esse fantasma!</speak>"),
                    NormalizedStreamItem(StreamKind.FINISH, 2, finish_reason="stop"),
                ]
            )

    provider = SimpleProvider()
    session = TurnSession(
        turn=store.turn,
        store=store,
        agents_store=AgentsDouble(),
        memory_store=memory_store,
        friend_persona_store=persona_store,
        provider_factory=lambda: provider,
        workspace_root=tmp_path,
        conversation_mode="friend",
    )

    assert "Use um tom caloroso e direto." in session._system_prompt
    assert "curioso e atento" in session._volatile_prompt
    assert "fantasma da ópera" in session._volatile_prompt

    result = session.run()
    assert result.state == "completed"
    assert "".join(store.deltas) == "Vamos caçar esse fantasma!"



def test_live_mode_propagates_through_chat_store_and_gateway_and_preserves_in_chat_session(tmp_path: Path) -> None:
    from sqlalchemy import create_engine
    from agentos.api.gateway import CreateConversationRequest, SendConversationMessageRequest
    from agentos.conversations.chat import ChatApplication, PostgresChatStore
    from agentos.persistence.postgres.schema import metadata
    from agentos.agentic.prompts import LIVE_MODE_SPEAK_GUIDANCE

    create_req = CreateConversationRequest(
        message="Alô?",
        selection={"provider": "openai", "model_id": "gpt-5.4"},
        code_mode="chat",
        live_mode=True,
    )
    send_req = SendConversationMessageRequest(
        message="Me ouve?",
        selection={"provider": "openai", "model_id": "gpt-5.4"},
        live_mode=True,
    )
    assert create_req.live_mode is True
    assert send_req.live_mode is True

    engine = create_engine(f"sqlite:///{tmp_path / 'chat_live.db'}")
    metadata.create_all(engine)
    chat_store = PostgresChatStore(engine)
    app = ChatApplication(chat_store)

    receipt1 = app.create(
        user_id="user-1",
        message="Alô?",
        provider="openai",
        model_id="gpt-5.4",
        idempotency_key="idem-live-1",
        code_mode="chat",
        live_mode=True,
    )
    claimed1 = chat_store.claim(receipt1.turn_id)
    assert claimed1 is not None
    assert claimed1.get("live_mode") is True

    chat_store.finish(claimed1)
    receipt2 = app.send(
        user_id="user-1",
        conversation_id=receipt1.conversation_id,
        message="O que você faz?",
        provider="openai",
        model_id="gpt-5.4",
        idempotency_key="idem-live-2",
        live_mode=True,
    )
    claimed2 = chat_store.claim(receipt2.turn_id)
    assert claimed2 is not None
    assert claimed2.get("live_mode") is True

    class DummyAgents:
        def create(self, name: str, role: str, **kwargs: object) -> dict[str, object]:
            return {"agent_id": "agent-1", "name": name, "role": role, "created": True}

        def find(self, name: str) -> dict[str, object] | None:
            return None

        def list(self) -> list[dict[str, object]]:
            return []

        def set_state(self, agent_id: str, state: str) -> None:
            pass

        def record_usage(self, *args: object, **kwargs: object) -> None:
            pass

    class DummyMemory:
        def recall(self, *args: object, **kwargs: object) -> list[dict[str, object]]:
            return []

        def prompt_section(self, *args: object, **kwargs: object) -> str:
            return ""

    store = TimelineStore(
        turn=_base_turn(conversation_mode="chat", live_mode=True),
        history=[{"role": "user", "content": "Alô?"}],
    )
    session = TurnSession(
        turn=store.turn,
        store=store,
        agents_store=DummyAgents(),
        memory_store=DummyMemory(),
        provider_factory=lambda: None,
        workspace_root=tmp_path,
        conversation_mode="chat",
    )
    assert session.turn["live_mode"] is True
    assert LIVE_MODE_SPEAK_GUIDANCE in session._system_prompt


def test_live_mode_and_friend_mode_fast_path_skips_file_mcp_disables_phase_controller_and_forces_no_effort(
    tmp_path: Path, monkeypatch
) -> None:
    import agentos.mcp.file_servers as file_servers

    assert file_servers.DEFAULT_TIMEOUT_SECONDS == 3.0
    assert file_servers.FAILURE_TTL_SECONDS == 300.0

    mcp_load_calls: list[bool] = []
    monkeypatch.setattr(
        TurnSession,
        "_load_file_mcp_servers",
        lambda self, trusted: mcp_load_calls.append(trusted),
    )

    turn = _base_turn(conversation_mode="friend", live_mode=True)
    turn["effort"] = "high"
    store = TimelineStore(turn=turn, history=[{"role": "user", "content": "Alô?"}])

    class DummyAgents:
        def create(self, name: str, role: str, **kwargs: object) -> dict[str, object]:
            return {"agent_id": "agent-1", "name": name, "role": role, "created": True}

        def find(self, name: str) -> dict[str, object] | None:
            return None

        def list(self) -> list[dict[str, object]]:
            return []

        def set_state(self, agent_id: str, state: str) -> None:
            pass

        def record_usage(self, *args: object, **kwargs: object) -> None:
            pass

    class DummyMemory:
        def recall(self, *args: object, **kwargs: object) -> list[dict[str, object]]:
            return []

        def prompt_section(self, *args: object, **kwargs: object) -> str:
            return ""

    class InspectProvider:
        def __init__(self) -> None:
            self.requests: list[dict[str, object]] = []

        def stream(self, request: dict[str, object]):
            self.requests.append(request)
            return iter(
                [
                    NormalizedStreamItem(StreamKind.TEXT, 1, text="<speak>Oi Carlos! Tudo certo por aqui.</speak>"),
                    NormalizedStreamItem(StreamKind.FINISH, 2, finish_reason="stop"),
                ]
            )

    provider = InspectProvider()
    session = TurnSession(
        turn=store.turn,
        store=store,
        agents_store=DummyAgents(),
        memory_store=DummyMemory(),
        provider_factory=lambda: provider,
        workspace_root=tmp_path,
        conversation_mode="friend",
        model_capabilities=("reasoning", "thinking"),
    )
    runtime = session.build_runtime()
    assert mcp_load_calls == []
    assert runtime.phases is None
    assert runtime.effort is None
    assert "write_contract" not in session._system_prompt

    session.run()
    assert len(provider.requests) == 1
    req = provider.requests[0]
    assert "effort" not in req
    tool_names = [str((t.get("function") or {}).get("name") or "") for t in (req.get("tools") or [])]
    assert "write_contract" not in tool_names


def test_live_mode_speak_guidance_forbids_silent_lists_unnecessary_tools_and_bracket_emphasis() -> None:
    from agentos.agentic.prompts import LIVE_MODE_SPEAK_GUIDANCE

    assert "1 a 3 frases" in LIVE_MODE_SPEAK_GUIDANCE or "1 a 3" in LIVE_MODE_SPEAK_GUIDANCE
    assert "[E]i" in LIVE_MODE_SPEAK_GUIDANCE
    assert "[alô]" in LIVE_MODE_SPEAK_GUIDANCE
    assert "recall" in LIVE_MODE_SPEAK_GUIDANCE
    assert "Alô?" in LIVE_MODE_SPEAK_GUIDANCE


def test_runtime_rehydrates_multi_paragraph_conversational_reply_and_excludes_fenced_code() -> None:
    history = [
        {"role": "user", "content": "Oi Orin!"},
        {
            "role": "assistant",
            "content": "Fala Carlos! Tudo ótimo por aqui.\n\nEstou pronto para te ajudar no projeto.\n\n```python\nprint('oi')\n```",
        },
        {"role": "user", "content": "Qual é o próximo passo?"},
    ]
    store = TimelineStore(turn=_base_turn(live_mode=True, conversation_mode="friend"), history=history)

    class InspectProvider:
        def __init__(self) -> None:
            self.requests: list[dict[str, object]] = []

        def stream(self, request: dict[str, object]):
            self.requests.append(request)
            return iter(
                [
                    NormalizedStreamItem(StreamKind.TEXT, 1, text="<speak>Vamos revisar o código.</speak>"),
                    NormalizedStreamItem(StreamKind.FINISH, 2, finish_reason="stop"),
                ]
            )

    provider = InspectProvider()
    runtime = AgenticTurnRuntime(store=store, provider=provider)
    runtime.run("turn-friend-1")

    sent_messages = provider.requests[0]["messages"]
    assistant_msgs = [m for m in sent_messages if isinstance(m, dict) and m.get("role") == "assistant"]
    assert assistant_msgs[0]["content"] == (
        "<speak>Fala Carlos! Tudo ótimo por aqui.\n\nEstou pronto para te ajudar no projeto.</speak>"
        "\n\n```python\nprint('oi')\n```"
    )


def test_friend_mode_zero_speak_fallback_works_even_if_turn_live_mode_not_preset() -> None:
    turn = {
        "turn_id": "turn-friend-no-flag",
        "conversation_id": "conv-friend-1",
        "execution_id": "exec-friend-1",
        "user_id": "user-1",
        "assistant_message_id": "msg-asst-1",
        "provider": "openai",
        "model_id": "gpt-5.4",
        "conversation_mode": "friend",
    }
    store = TimelineStore(turn=turn)

    class ForgotSpeakProvider:
        def stream(self, _request: dict[str, object]):
            return iter(
                [
                    NormalizedStreamItem(
                        StreamKind.TEXT,
                        1,
                        text="Oi Carlos! Estou te ouvindo alto e claro por aqui, pode falar comigo.",
                    ),
                    NormalizedStreamItem(StreamKind.FINISH, 2, finish_reason="stop"),
                ]
            )

    runtime = AgenticTurnRuntime(store=store, provider=ForgotSpeakProvider())
    result = runtime.run("turn-friend-no-flag")
    assert result.state == "completed"
    chunk_events = [
        payload for evt_type, _summary, payload in store.activity if evt_type is AgentActivityEventType.SPEAK_CHUNK_READY
    ]
    assert len(chunk_events) >= 1
    assert "Oi Carlos" in str(chunk_events[0].get("clean_text") or "")
