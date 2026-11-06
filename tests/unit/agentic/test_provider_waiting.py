from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.events import AgentActivityEventType
from agentos.agentic.provider_stream import NormalizedStreamItem, StreamKind
from agentos.agentic.runtime import AgenticLimits, AgenticTurnRuntime
from agentos.agentic.session import TurnSession
from agentos.agentic.workspace import ConversationWorkspace
from agentos.providers.models import FinishReason


class _WaitingMemoryStore:
    def __init__(self) -> None:
        self.turn: dict[str, object] = {
            "turn_id": "turn-1",
            "conversation_id": "conversation-1",
            "user_id": "user-1",
            "execution_id": "exec-1",
            "provider": "anthropic",
            "model_id": "claude-sonnet-4-6",
            "user_message_id": "u-1",
            "assistant_message_id": "a-1",
        }
        self.events: list[tuple[str, dict[str, Any]]] = []
        self.text: list[str] = []

    def load(self, turn_id: str) -> dict[str, object]:
        return self.turn

    def history_for_turn(self, turn: Any) -> list[dict[str, str]]:
        return [{"role": "user", "content": "oi"}]

    def lifecycle(self, turn: Any, state: str, **payload: object) -> None:
        self.events.append((state, dict(payload)))

    def delta(self, turn: Any, text: str) -> None:
        self.text.append(text)

    def finish(self, turn: Any, *, failed: bool = False, code: str | None = None) -> None:
        self.turn["failed"] = failed
        self.turn["error_code"] = code


class _WaitingProvider:
    def __init__(self, advance_clock) -> None:
        self.requests: list[dict[str, object]] = []
        self._advance = advance_clock

    def stream(self, request: dict[str, object]):
        self.requests.append(request)
        for idx, pos in enumerate((2, 2, 1, 1, 1), start=1):
            self._advance(60)
            yield NormalizedStreamItem(
                StreamKind.WAITING,
                sequence=idx,
                queue_position=pos,
                queue_limit=3,
            )
        self._advance(1)
        yield NormalizedStreamItem(StreamKind.TEXT, sequence=6, text="pronto")
        yield NormalizedStreamItem(
            StreamKind.FINISH, sequence=7, finish_reason=FinishReason.STOP
        )


def _build_waiting_runtime(tmp_path: Path):
    now = {"t": datetime(2026, 11, 6, 12, 0, 0, tzinfo=UTC)}

    def _clock() -> datetime:
        return now["t"]

    def _advance(seconds: int) -> None:
        now["t"] = now["t"] + timedelta(seconds=seconds)

    store = _WaitingMemoryStore()
    provider = _WaitingProvider(_advance)
    runtime = AgenticTurnRuntime(
        store=store,
        provider=provider,
        toolset=AgentToolset(ConversationWorkspace(tmp_path, "w1")),
        system_prompt="prompt",
        limits=AgenticLimits(
            max_iterations=4,
            max_actions=4,
            deadline=timedelta(minutes=30),
        ),
        clock=_clock,
        phase_controller=None,
    )
    return store, provider, runtime


def test_waiting_for_a_slot_longer_than_the_stall_limit_does_not_time_out(
    tmp_path: Path,
) -> None:
    _store, _provider, runtime = _build_waiting_runtime(tmp_path)
    assert runtime.run("turn-1").state == "completed"  # 300 s de espera > 180 s do stall


def test_waiting_is_reported_once_per_position_and_request_carries_the_conversation(
    tmp_path: Path,
) -> None:
    store, provider, runtime = _build_waiting_runtime(tmp_path)
    assert runtime.run("turn-1").state == "completed"
    assert [p["position"] for s, p in store.events if s == "provider_waiting"] == [2, 1]
    assert provider.requests[0]["conversation_id"] == "conversation-1"


def test_turn_session_records_provider_waiting_for_limit_and_memory(
    tmp_path: Path,
) -> None:
    recorded: list[tuple[AgentActivityEventType, str, dict[str, object], dict[str, object]]] = []

    class _DummyStore:
        def record(self, turn, event_type, summary, payload, **kw):
            recorded.append((event_type, summary, dict(payload or {}), dict(kw)))

    class _DummyAgents:
        def list(self):
            return []

    turn = {
        "turn_id": "turn-w",
        "conversation_id": "conv-w",
        "user_id": "user-1",
        "execution_id": "exec-w",
        "provider": "anthropic",
        "model_id": "claude-sonnet-4-6",
    }
    session = TurnSession(
        turn=turn,
        store=_DummyStore(),
        agents_store=_DummyAgents(),
        memory_store=None,
        provider_factory=lambda: None,
        workspace_root=str(tmp_path),
    )
    session.emit_lifecycle(turn, "provider_waiting", position=2, limit=3, reason="limit")
    session.emit_lifecycle(turn, "provider_waiting", position=1, limit=None, reason="memory")

    assert len(recorded) == 2
    ev1, sum1, pay1, _ = recorded[0]
    assert ev1 == AgentActivityEventType.TURN_STARTED
    assert sum1 == "Aguardando vaga na CLI (2º na fila, limite de 3)"
    assert pay1["status"] == "queued"
    assert pay1["waiting_for_slot"] is True
    assert pay1["queue_position"] == 2

    ev2, sum2, pay2, _ = recorded[1]
    assert ev2 == AgentActivityEventType.TURN_STARTED
    assert sum2 == "Aguardando memória livre para abrir outro processo (1º na fila)"
    assert pay2["status"] == "queued"
    assert pay2["waiting_for_slot"] is True
    assert pay2["queue_position"] == 1
