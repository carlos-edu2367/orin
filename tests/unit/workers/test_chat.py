from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, insert

from agentos.agentic.runtime import AgenticRunResult
from agentos.persistence.postgres.schema import metadata, provider_model_catalog, provider_model_favorites
from agentos.workers import chat as chat_module
from agentos.workers.chat import ChatWorker
from agentos.conversations.chat import PostgresChatStore
from agentos.persistence.postgres.execution_adapters import ExecutionQueryAdapter


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


def test_runtime_for_reuses_the_same_browser_across_two_turns_of_one_conversation(monkeypatch: pytest.MonkeyPatch) -> None:
    class Browser:
        def close(self):
            pass

    class Settings:
        def get(self, user_id):
            return {"max_iterations": 8}

    class SkillLibrary:
        def registry_for(self, user_id, *, agent_id):
            return None

    class FakeSession:
        def __init__(self, **kwargs):
            pass

        def build_runtime(self):
            return object()

    built = []
    monkeypatch.setattr(chat_module, "conversation_browser_for", lambda turn: built.append(Browser()) or built[-1])
    monkeypatch.setattr(chat_module, "PostgresSkillLibraryService", lambda engine: SkillLibrary())
    monkeypatch.setattr(chat_module, "ConversationAgentStore", lambda *args, **kwargs: object())
    monkeypatch.setattr(chat_module, "PostgresAgentMemoryStore", lambda *args, **kwargs: None)
    monkeypatch.setattr(chat_module, "search_client_from_environment", lambda: None)
    monkeypatch.setattr(chat_module, "TurnSession", FakeSession)
    worker = ChatWorker(Store(), runtime_settings=Settings())

    worker._runtime_for(dict(TURN))
    worker._runtime_for(dict(TURN))

    assert len(built) == 1  # the second turn of the same conversation reused it


def test_runtime_for_reuses_the_same_retrieval_bundle_across_two_turns_of_one_project(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression test: a bundle built fresh every turn leaked a thread and a
    sqlite connection per turn, since nothing ever tore the previous one down."""
    from agentos.retrieval.registry import RetrievalRegistry

    class Settings:
        def get(self, user_id):
            return {"max_iterations": 8}

    class SkillLibrary:
        def registry_for(self, user_id, *, agent_id):
            return None

    captured = []

    class FakeSession:
        def __init__(self, **kwargs):
            captured.append(kwargs.get("retrieval_bundle"))

        def build_runtime(self):
            return object()

    built = []

    def factory(workspace_id, local_root):
        bundle = object()
        built.append(bundle)
        return bundle

    registry = RetrievalRegistry(factory=factory)
    monkeypatch.setattr(chat_module, "conversation_browser_for", lambda turn: None)
    monkeypatch.setattr(chat_module, "PostgresSkillLibraryService", lambda engine: SkillLibrary())
    monkeypatch.setattr(chat_module, "ConversationAgentStore", lambda *args, **kwargs: object())
    monkeypatch.setattr(chat_module, "PostgresAgentMemoryStore", lambda *args, **kwargs: None)
    monkeypatch.setattr(chat_module, "search_client_from_environment", lambda: None)
    monkeypatch.setattr(chat_module, "TurnSession", FakeSession)
    worker = ChatWorker(Store(), runtime_settings=Settings(), retrieval_registry=registry)

    worker._runtime_for(dict(TURN))
    worker._runtime_for(dict(TURN))

    assert len(built) == 1  # the second turn of the same project reused it
    assert captured[0] is captured[1] is built[0]


def test_run_releases_the_browser_registry_so_a_long_turn_is_not_evicted_as_idle() -> None:
    class Registry:
        def __init__(self) -> None:
            self.released: list[dict[str, object]] = []

        def release(self, turn):
            self.released.append(turn)

    runtime = ClosableRuntime(AgenticRunResult("completed"))
    registry = Registry()
    worker = ChatWorker(Store(), runtime_factory=lambda turn: runtime, browser_registry=registry)
    worker._project = lambda *args, **kwargs: None

    worker.run("turn-1")

    assert len(registry.released) == 1
    assert registry.released[0]["turn_id"] == "turn-1"


def test_default_worker_runs_the_chat_through_the_canonical_kernel() -> None:
    """The production path has no runtime factory and must not use _project
    to acquire/complete the execution around a second lifecycle loop."""
    engine = create_engine("sqlite://", future=True)
    metadata.create_all(engine)
    store = PostgresChatStore(engine)
    receipt = store.create(
        user_id="user-1", message="hello", provider="openrouter", model_id="model-1", idempotency_key="kernel-turn",
    )

    class CompletedRuntime:
        def run(self, turn_id):
            return AgenticRunResult("completed")

        def close(self):
            return None

    worker = ChatWorker(store)
    # The module-level fixture replaces the production adapters for the small
    # unit doubles above; this integration-shaped assertion restores them.
    from agentos.persistence.postgres.execution_adapters import ExecutionApplicationAdapter

    worker._executions = ExecutionApplicationAdapter(engine)
    worker._queries = ExecutionQueryAdapter(engine)
    worker._runtime_for = lambda turn: CompletedRuntime()
    worker.run(receipt.turn_id)

    execution = ExecutionQueryAdapter(engine).get({"resource_id": store.execution_id_for(receipt.turn_id), "user_id": "user-1"})
    assert execution["state"] == "COMPLETED"
    assert store.get(receipt.conversation_id, "user-1")["messages"][-1]["status"] == "completed"


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


def test_child_model_authorization_is_limited_to_the_turn_users_provider_favorites() -> None:
    engine = _catalog_engine(
        {"user_id": "user-1", "provider": "openrouter", "model_id": "favorite-model", "display_name": "Favorite", "context_window": 16_000},
        {"user_id": "user-1", "provider": "openrouter", "model_id": "other-model", "display_name": "Other", "context_window": 16_000},
        {"user_id": "user-1", "provider": "ollama", "model_id": "other-provider", "display_name": "Other provider", "context_window": 16_000},
        {"user_id": "user-2", "provider": "openrouter", "model_id": "other-user", "display_name": "Other user", "context_window": 16_000},
    )
    now = datetime.now(UTC)
    with engine.begin() as connection:
        connection.execute(insert(provider_model_favorites), [
            {"user_id": "user-1", "provider": "openrouter", "model_id": "favorite-model", "created_at": now},
            {"user_id": "user-1", "provider": "ollama", "model_id": "other-provider", "created_at": now},
            {"user_id": "user-2", "provider": "openrouter", "model_id": "other-user", "created_at": now},
        ])

    class CatalogStore(Store):
        _engine = engine

    worker = ChatWorker(CatalogStore())
    turn = {**TURN, "provider": "openrouter", "model_id": "current-model"}

    assert worker._favorite_child_model_ids(turn) == ("favorite-model",)
    assert worker._is_favorite_child_model(turn, "favorite-model") is True
    assert worker._is_favorite_child_model(turn, "other-model") is False
    assert worker._is_favorite_child_model(turn, "other-provider") is False
    assert worker._is_favorite_child_model(turn, "other-user") is False


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


def _ollama_worker(*rows: dict[str, object]) -> ChatWorker:
    engine = _catalog_engine(*rows)

    class OllamaStore(Store):
        _engine = engine

    return ChatWorker(OllamaStore())


def test_num_ctx_never_exceeds_the_models_own_window() -> None:
    """A 262k model must not be asked to allocate a 262k KV cache.

    num_ctx deliberately no longer tracks ``_max_context_tokens_for``: that
    budget follows the model's real window (someone else's tokens), while
    this one is VRAM on the user's machine and keeps the conservative flat
    ceiling.
    """
    worker = _ollama_worker({
        "user_id": "user-1", "provider": "ollama", "model_id": "qwen3:8b",
        "display_name": "qwen3:8b", "context_window": 262_144,
    })
    turn = {**TURN, "provider": "ollama", "model_id": "qwen3:8b"}

    assert worker._num_ctx_for(turn) == chat_module.DEFAULT_MAX_CONTEXT_TOKENS + chat_module.CONTEXT_WINDOW_RESERVE_TOKENS
    assert worker._num_ctx_for(turn) < 262_144


def test_num_ctx_is_capped_by_a_small_models_window() -> None:
    worker = _ollama_worker({
        "user_id": "user-1", "provider": "ollama", "model_id": "tiny:1b",
        "display_name": "tiny:1b", "context_window": 8_192,
    })
    turn = {**TURN, "provider": "ollama", "model_id": "tiny:1b"}

    assert worker._num_ctx_for(turn) == 8_192


def test_num_ctx_falls_back_conservatively_for_an_uncatalogued_model() -> None:
    """The worker's 60k default would be a VRAM trap for an unknown model."""
    worker = _ollama_worker()
    turn = {**TURN, "provider": "ollama", "model_id": "ghost:1b"}

    assert worker._num_ctx_for(turn) == chat_module.OLLAMA_FALLBACK_NUM_CTX
    assert chat_module.OLLAMA_FALLBACK_NUM_CTX < chat_module.DEFAULT_MAX_CONTEXT_TOKENS


def test_base_url_resolution_covers_local_and_cloud_ollama() -> None:
    worker = _ollama_worker()

    assert worker._base_url_for("ollama", {"base_url": None}) == "http://localhost:11434"
    assert worker._base_url_for("ollama", {"base_url": "https://ollama.com/v1"}) == "https://ollama.com"
    assert worker._base_url_for("openai", {"base_url": None}) == chat_module.PROVIDER_BASE_URLS["openai"]


def test_a_large_window_model_is_no_longer_capped_at_the_flat_default() -> None:
    """A 200k model used to be trimmed to 60k, forcing compaction at ~49k.

    That is what made the agent forget mid-task and re-run searches it had
    already run. The budget now follows the model's real window.
    """
    engine = _catalog_engine({
        "user_id": "user-1", "provider": "anthropic", "model_id": "claude-sonnet-4",
        "display_name": "Claude Sonnet 4", "context_window": 200_000,
    })

    class LargeModelStore(Store):
        _engine = engine

    worker = ChatWorker(LargeModelStore())
    turn = {**TURN, "provider": "anthropic", "model_id": "claude-sonnet-4"}

    assert worker._max_context_tokens_for(turn) == 200_000 - 20_000
    assert worker._max_context_tokens_for(turn) > chat_module.DEFAULT_MAX_CONTEXT_TOKENS


def test_a_very_large_window_reserves_a_bounded_amount_of_headroom() -> None:
    """Ten percent of a 1M window would reserve 100k for nothing."""
    engine = _catalog_engine({
        "user_id": "user-1", "provider": "openrouter", "model_id": "google/gemini-pro",
        "display_name": "Gemini Pro", "context_window": 1_000_000,
    })

    class HugeModelStore(Store):
        _engine = engine

    worker = ChatWorker(HugeModelStore())
    turn = {**TURN, "provider": "openrouter", "model_id": "google/gemini-pro"}

    assert worker._max_context_tokens_for(turn) == 1_000_000 - chat_module.CONTEXT_WINDOW_RESERVE_CEILING


def test_a_small_window_keeps_the_flat_minimum_reserve() -> None:
    """Ten percent of 16k is 1.6k, far too little for the tool schemas."""
    engine = _catalog_engine({
        "user_id": "user-1", "provider": "openrouter", "model_id": "some/small-model",
        "display_name": "Small Model", "context_window": 16_000,
    })

    class SmallModelStore(Store):
        _engine = engine

    worker = ChatWorker(SmallModelStore())
    turn = {**TURN, "provider": "openrouter", "model_id": "some/small-model"}

    assert worker._max_context_tokens_for(turn) == 16_000 - chat_module.CONTEXT_WINDOW_RESERVE_TOKENS


def test_num_ctx_does_not_follow_the_liberated_context_budget() -> None:
    """The trim budget is someone else's tokens; num_ctx is the user's VRAM.

    Liberating the first must never enlarge the second, or a large-window
    local model spills its KV cache into system RAM.
    """
    worker = _ollama_worker({
        "user_id": "user-1", "provider": "ollama", "model_id": "qwen3:8b",
        "display_name": "qwen3:8b", "context_window": 262_144,
    })
    turn = {**TURN, "provider": "ollama", "model_id": "qwen3:8b"}

    assert worker._num_ctx_for(turn) < worker._max_context_tokens_for(turn)
    assert worker._num_ctx_for(turn) <= chat_module.DEFAULT_MAX_CONTEXT_TOKENS + chat_module.CONTEXT_WINDOW_RESERVE_TOKENS
