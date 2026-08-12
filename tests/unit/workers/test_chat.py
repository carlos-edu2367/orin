from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, insert

from agentos.agentic.runtime import AgenticRunResult
from agentos.persistence.postgres.schema import metadata, provider_model_catalog
from agentos.workers import chat as chat_module
from agentos.workers.chat import ChatWorker


TURN = {
    "turn_id": "turn-1",
    "conversation_id": "conversation-1",
    "user_id": "user-1",
    "execution_id": "execution-1",
    "assistant_message_id": "message-1",
}


@pytest.fixture(autouse=True)
def stub_execution_adapters(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(chat_module, "ExecutionApplicationAdapter", lambda engine: object())
    monkeypatch.setattr(chat_module, "ExecutionQueryAdapter", lambda engine: object())


class Store:
    _engine = object()

    def heartbeat(self, worker):
        return None

    def claim(self, turn_id):
        return dict(TURN) if turn_id == TURN["turn_id"] else None

    def finish(self, turn, *, failed=False, code=None):
        return None

    def cancel_requested(self, turn_id):
        return False

    def main_agent_id(self, turn):
        return "agent-main"


class ClosableRuntime:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.closed = 0

    def run(self, turn_id):
        if self.error:
            raise self.error
        return self.result

    def close(self):
        self.closed += 1


@pytest.mark.parametrize("state", ("completed", "failed", "cancelled"))
def test_chat_worker_closes_runtime_for_every_terminal_result(state: str) -> None:
    runtime = ClosableRuntime(AgenticRunResult(state, error_code="FAILED" if state == "failed" else None))
    worker = ChatWorker(Store(), runtime_factory=lambda turn: runtime)
    worker._project = lambda *args, **kwargs: None

    worker.run("turn-1")

    assert runtime.closed == 1


def test_chat_worker_closes_runtime_when_execution_raises() -> None:
    runtime = ClosableRuntime(error=RuntimeError("provider failed"))
    worker = ChatWorker(Store(), runtime_factory=lambda turn: runtime)
    worker._project = lambda *args, **kwargs: None

    worker.run("turn-1")

    assert runtime.closed == 1


def test_runtime_construction_failure_closes_the_optional_browser(monkeypatch: pytest.MonkeyPatch) -> None:
    class Browser:
        def __init__(self):
            self.closed = 0

        def close(self):
            self.closed += 1

    class Settings:
        def get(self, user_id):
            return {"max_iterations": 8}

    class SkillLibrary:
        def registry_for(self, user_id, *, agent_id):
            return None

    browser = Browser()
    monkeypatch.setattr(chat_module, "conversation_browser_for", lambda turn: browser)
    monkeypatch.setattr(chat_module, "PostgresSkillLibraryService", lambda engine: SkillLibrary())
    monkeypatch.setattr(chat_module, "ConversationAgentStore", lambda *args, **kwargs: object())
    monkeypatch.setattr(chat_module, "PostgresAgentMemoryStore", lambda *args, **kwargs: None)
    monkeypatch.setattr(chat_module, "search_client_from_environment", lambda: None)
    monkeypatch.setattr(chat_module, "TurnSession", lambda **kwargs: (_ for _ in ()).throw(RuntimeError("construction failed")))
    worker = ChatWorker(Store(), runtime_settings=Settings())

    with pytest.raises(RuntimeError, match="construction failed"):
        worker._runtime_for(dict(TURN))

    assert browser.closed == 1


def test_unlimited_runtime_setting_removes_the_action_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    class Settings:
        def get(self, user_id):
            return {"max_iterations": None}

    class SkillLibrary:
        def registry_for(self, user_id, *, agent_id):
            return None

    captured = {}
    monkeypatch.setattr(chat_module, "conversation_browser_for", lambda turn: None)
    monkeypatch.setattr(chat_module, "PostgresSkillLibraryService", lambda engine: SkillLibrary())
    monkeypatch.setattr(chat_module, "ConversationAgentStore", lambda *args, **kwargs: object())
    monkeypatch.setattr(chat_module, "PostgresAgentMemoryStore", lambda *args, **kwargs: None)
    monkeypatch.setattr(chat_module, "search_client_from_environment", lambda: None)

    def capture_session(**kwargs):
        captured["limits"] = kwargs["limits"]
        return type("Session", (), {"build_runtime": lambda self: object()})()

    monkeypatch.setattr(chat_module, "TurnSession", capture_session)
    worker = ChatWorker(Store(), runtime_settings=Settings())

    worker._runtime_for(dict(TURN))

    assert captured["limits"].max_iterations is None
    assert captured["limits"].max_actions is None


def _catalog_engine(*rows: dict[str, object]):
    engine = create_engine("sqlite://", future=True)
    metadata.create_all(engine)
    if rows:
        now = datetime.now(UTC)
        with engine.begin() as c:
            c.execute(insert(provider_model_catalog), [
                {
                    "capabilities": [], "input_modalities": ["text"], "output_modalities": ["text"],
                    "input_per_million": None, "output_per_million": None, "route_kind": "model",
                    "refreshed_at": now, "created_at": now, "updated_at": now,
                    **row,
                }
                for row in rows
            ])
    return engine


def test_max_context_tokens_derives_from_the_models_real_context_window() -> None:
    engine = _catalog_engine({
        "user_id": "user-1", "provider": "openrouter", "model_id": "some/small-model",
        "display_name": "Small Model", "context_window": 16_000,
    })

    class SmallModelStore(Store):
        _engine = engine

    worker = ChatWorker(SmallModelStore())
    turn = {**TURN, "provider": "openrouter", "model_id": "some/small-model"}

    assert worker._max_context_tokens_for(turn) == 16_000 - chat_module.CONTEXT_WINDOW_RESERVE_TOKENS


def test_max_context_tokens_falls_back_to_the_default_when_the_catalog_has_no_row() -> None:
    engine = _catalog_engine()

    class EmptyStore(Store):
        _engine = engine

    worker = ChatWorker(EmptyStore())
    turn = {**TURN, "provider": "anthropic", "model_id": "claude-opus-5"}

    assert worker._max_context_tokens_for(turn) == chat_module.DEFAULT_MAX_CONTEXT_TOKENS


def test_max_context_tokens_never_drops_below_the_floor_for_a_tiny_context_model() -> None:
    engine = _catalog_engine({
        "user_id": "user-1", "provider": "openrouter", "model_id": "tiny/model",
        "display_name": "Tiny Model", "context_window": 4_000,
    })

    class TinyModelStore(Store):
        _engine = engine

    worker = ChatWorker(TinyModelStore())
    turn = {**TURN, "provider": "openrouter", "model_id": "tiny/model"}

    assert worker._max_context_tokens_for(turn) == chat_module.MIN_MAX_CONTEXT_TOKENS


def test_max_context_tokens_falls_back_when_the_store_engine_cannot_be_queried() -> None:
    """The stub ``Store`` used throughout this file has a bare ``object()``
    engine; the lookup must degrade to the flat default instead of raising
    and blocking turn construction over a sizing refinement."""
    worker = ChatWorker(Store())
    turn = {**TURN, "provider": "anthropic", "model_id": "claude-opus-5"}

    assert worker._max_context_tokens_for(turn) == chat_module.DEFAULT_MAX_CONTEXT_TOKENS


def test_agentos_agent_does_not_block_the_event_loop_during_a_turn() -> None:
    """A chat turn is fully synchronous (blocking HTTP streaming plus database
    calls). arq runs every job coroutine on a single event loop, so awaiting
    ``run`` inline freezes that loop for the whole turn: no other turn can be
    acquired, and the 30s watchdog then fails each queued turn as
    ``worker_unavailable``. OmniRoute's free routes are slow enough to make this
    the normal case, so the blocking call belongs on a worker thread.
    """

    class BlockingWorker:
        def run(self, turn_id: str) -> None:
            # Stands in for a long provider stream holding the calling thread.
            time.sleep(0.4)

    async def scenario() -> list[str]:
        order: list[str] = []

        async def concurrent_job() -> None:
            await asyncio.sleep(0.1)
            order.append("other-turn-acquired")

        task = asyncio.create_task(concurrent_job())
        await chat_module.agentos_agent({"chat_worker": BlockingWorker()}, "turn-1")
        order.append("turn-finished")
        await task
        return order

    assert asyncio.run(scenario()) == ["other-turn-acquired", "turn-finished"]
