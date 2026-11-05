from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from agentos.agentic.provider_stream import NormalizedStreamItem, StreamKind
from agentos.agentic.runtime import AgenticLimits, AgenticTurnRuntime
from agentos.providers.models import FinishReason


def test_runtime_records_thinking_progress_for_empty_deltas(monkeypatch: Any) -> None:
    mono_now = [1000.0]
    monkeypatch.setattr("agentos.agentic.runtime.time.monotonic", lambda: mono_now[0])

    class _EmptyThinkingProvider:
        def stream(self, request: dict[str, object]) -> Any:
            yield NormalizedStreamItem(StreamKind.THINKING, 1, thinking="")
            mono_now[0] += 0.5  # throttled (< 2.0s)
            yield NormalizedStreamItem(StreamKind.THINKING, 2, thinking="")
            mono_now[0] += 2.1  # emitted (>= 2.0s)
            yield NormalizedStreamItem(StreamKind.THINKING, 3, thinking="")
            yield NormalizedStreamItem(StreamKind.TEXT, 4, text="Resposta final.")
            yield NormalizedStreamItem(StreamKind.FINISH, 5, finish_reason=FinishReason.STOP)

    class _FakeStore:
        def __init__(self) -> None:
            self.turn = {
                "turn_id": "t-think",
                "conversation_id": "c-think",
                "user_id": "u-1",
                "execution_id": "e-1",
                "provider": "anthropic",
                "model_id": "claude-opus-5-5",
                "user_message_id": "um-1",
                "assistant_message_id": "am-1",
            }
            self.lifecycle_events: list[tuple[str, dict[str, object]]] = []
            self.deltas: list[str] = []

        def load(self, turn_id: str) -> dict[str, object]:
            return self.turn

        def history_for_turn(self, turn: dict[str, object]) -> list[dict[str, object]]:
            return [{"role": "user", "content": "Pense em silêncio"}]

        def lifecycle(self, turn: dict[str, object], state: str, **payload: object) -> None:
            self.lifecycle_events.append((state, payload))

        def delta(self, turn: dict[str, object], text: str) -> None:
            self.deltas.append(text)

        def finish(self, turn: dict[str, object], *, failed: bool = False, code: str | None = None) -> None:
            pass

    store = _FakeStore()
    runtime = AgenticTurnRuntime(
        store=store,
        provider=_EmptyThinkingProvider(),
        clock=lambda: datetime(2026, 11, 5, 12, 0, 0, tzinfo=UTC),
        limits=AgenticLimits(deadline=timedelta(seconds=90)),
    )
    res = runtime.run("t-think")
    assert res.state == "completed"
    empty_thinking_lifecycles = [
        payload
        for state, payload in store.lifecycle_events
        if state == "thinking_delta" and payload.get("delta") == ""
    ]
    assert len(empty_thinking_lifecycles) == 2
