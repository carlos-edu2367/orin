"""ARQ worker for durable chat turns.

Run separately from HTTP with ``arq agentos.workers.chat.WorkerSettings``.
The only queue payload is a turn identifier; credentials and prompt history are
looked up inside this trusted worker after it has acquired the durable turn.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import UTC, datetime, timedelta
from typing import Callable

from arq.connections import RedisSettings
from sqlalchemy import create_engine, select

from agentos.bootstrap.production import ProductionSettings, activity_cursor_fallback
from agentos.agentic.provider_stream import HTTPProviderStreamTransport
from agentos.provider_catalog.models import PROVIDERS_WITH_BASE_URL
from agentos.provider_catalog.ollama import DEFAULT_OLLAMA_BASE_URL, normalize_ollama_base_url
from agentos.provider_catalog.omniroute import DEFAULT_OMNIROUTE_BASE_URL, normalize_omniroute_base_url
from agentos.agentic.runtime import AgenticLimits, AgenticTurnRuntime
from agentos.agentic.settings import AgentRuntimeSettingsStore
from agentos.agentic.session import TurnSession
from agentos.agentic.browser_tools import conversation_browser_for
from agentos.agentic.web_search import search_client_from_environment
from agentos.conversations.chat import PostgresChatStore
from agentos.installation import orin_paths
from agentos.persistence.postgres.agent_memory import PostgresAgentMemoryStore
from agentos.persistence.postgres.agentic_activity import PostgresAgenticActivityStore
from agentos.persistence.postgres.conversation_agents import ConversationAgentStore
from agentos.persistence.postgres.execution_adapters import ExecutionApplicationAdapter, ExecutionQueryAdapter
from agentos.persistence.postgres.schema import conversation_dispatches, provider_configurations, provider_model_catalog
from agentos.persistence.provider_secrets import ProviderSecretCipher
from agentos.persistence.postgres.skills import PostgresSkillLibraryService


_LOGGER = logging.getLogger("agentos.workers.chat")

PROVIDER_BASE_URLS = {
    "anthropic": "https://api.anthropic.com/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "openai": "https://api.openai.com/v1",
    "omniroute": DEFAULT_OMNIROUTE_BASE_URL,
    "ollama": DEFAULT_OLLAMA_BASE_URL,
}

# Upper bound used whenever the turn's model has no known (or no smaller)
# context window -- unchanged from the previous flat behavior.
DEFAULT_MAX_CONTEXT_TOKENS = 60_000
# AgenticLimits rejects anything below 1_000; this keeps a real margin above
# that floor so a tiny-context model still has room for the pinned request.
MIN_MAX_CONTEXT_TOKENS = 4_000
# Headroom subtracted from a model's real context window before it becomes
# the trim budget: the system prompt and tool schemas ride outside the
# trimmed message window, and the reply itself (uncapped here, so it can use
# the provider's own max) still has to fit in what's left.
CONTEXT_WINDOW_RESERVE_TOKENS = 12_000

# num_ctx for an Ollama model the catalog has no window for. Deliberately not
# DEFAULT_MAX_CONTEXT_TOKENS: asking an unknown local model for a 60k KV cache
# is what spills into system RAM and drops inference by 20-50x.
OLLAMA_FALLBACK_NUM_CTX = 16_384

# A turn is allowed to continue until the worker's job timeout.  The old
# five-minute deadline cut off ordinary multi-step file work mid-task.
TURN_DEADLINE = timedelta(seconds=3600)


class _RuntimeStore:
    """Worker-scoped view that keeps the chat store outside the runtime API.

    It is also the seam where a runtime lifecycle callback becomes two things:
    a technical execution transition, and a public activity event that the UI
    renders.
    """

    def __init__(self, worker: "ChatWorker", turn: dict[str, object]) -> None:
        self._worker, self._store, self._turn = worker, worker.store, turn

    # -- runtime contract ------------------------------------------------

    def load(self, turn_id: str) -> dict[str, object]:
        if turn_id != self._turn["turn_id"]:
            raise KeyError("turn not found")
        return self._turn

    def history_for_turn(self, turn: dict[str, object]):
        return self._store.history_for_turn(turn)

    def delta(self, turn: dict[str, object], text: str) -> None:
        self._store.delta(turn, text)

    def finish(self, turn: dict[str, object], *, failed: bool = False, code: str | None = None) -> None:
        self._store.finish(turn, failed=failed, code=code)

    def lifecycle(self, turn: dict[str, object], state: str, **payload: object) -> None:
        # Public activity is published by the session before this is called; the
        # only job left here is the technical execution projection.
        target = {
            "running": "RUNNING", "waiting_tool": "WAITING_TOOL", "tool_started": "RUNNING",
            "tool_finished": "RUNNING", "retrying": "RUNNING",
        }.get(state)
        if target is not None:
            try:
                self._worker._transition(turn, target, f"agentic_{state}", result_ref=str(payload["result_ref"]) if payload.get("result_ref") else None)
            except Exception:
                # The durable chat finish remains authoritative if a duplicate
                # execution projection races with another worker attempt.
                pass

    # -- session seams ---------------------------------------------------

    def record(self, *args, **kwargs) -> None:
        self._store.record(*args, **kwargs)

    def main_agent_id(self, turn) -> str:
        return self._store.main_agent_id(turn)


class ChatWorker:
    def __init__(
        self,
        store: PostgresChatStore,
        runtime_factory: Callable[[dict[str, object]], AgenticTurnRuntime] | None = None,
        *,
        workspace_root: str | None = None,
        enable_subagents: bool = True,
        runtime_settings: AgentRuntimeSettingsStore | None = None,
    ) -> None:
        self.store, self._executions, self._queries = store, ExecutionApplicationAdapter(store._engine), ExecutionQueryAdapter(store._engine)
        self._runtime_factory = runtime_factory
        self._workspace_root = workspace_root if workspace_root is not None else str(orin_paths().workspaces)
        self._enable_subagents = enable_subagents
        self._runtime_settings = runtime_settings or AgentRuntimeSettingsStore()

    def run(self, turn_id: str) -> None:
        self.store.heartbeat("chat-worker")
        turn = self.store.claim(turn_id)
        if turn is None:
            return
        self._project(turn, "STARTING", "worker_acquired")
        self._project(turn, "RUNNING", "provider_started")
        runtime = None
        try:
            runtime = self._runtime_for(turn)
            result = runtime.run(str(turn_id))
        except Exception:
            # A turn that dies silently is the single worst failure mode for a
            # local install: the UI shows "failed" with no way to learn why.
            _LOGGER.exception("chat turn %s failed", turn_id)
            self._project(turn, "FAILED", "provider_failed")
            self.store.finish(turn, failed=True, code="provider_unavailable")
            return
        finally:
            closer = getattr(runtime, "close", None)
            if callable(closer):
                try:
                    closer()
                except Exception:
                    _LOGGER.exception("chat runtime cleanup for turn %s failed", turn_id)
        if result.state == "completed":
            self._project(turn, "COMPLETED", "provider_completed", result_ref=f"conversation-message:{turn['assistant_message_id']}")
        elif result.state == "cancelled":
            self._project(turn, "CANCELLED", "user_cancelled")
        else:
            self._project(turn, "FAILED", result.error_code or "agentic_runtime_failed")

    def _project(self, turn: dict[str, object], target: str, reason: str, result_ref: str | None = None) -> None:
        """Best-effort execution projection.

        The conversation record is the authority for what the user sees. A stale
        or conflicting execution row must never turn a finished answer into a
        failed one, so a projection error is logged and dropped.
        """
        try:
            self._transition(turn, target, reason, result_ref=result_ref)
        except Exception as error:
            _LOGGER.warning("execution projection %s for turn %s was skipped: %s", target, turn["turn_id"], error)

    def _transition(self, turn: dict[str, object], target: str, reason: str, result_ref: str | None = None) -> None:
        view = self._queries.get({"resource_id": turn["execution_id"], "user_id": turn["user_id"], "purpose": "execution.read"})
        self._executions.transition({"execution_id": turn["execution_id"], "user_id": turn["user_id"], "target_state": target, "reason_code": reason, "result_ref": result_ref, "expected_state_version": view["state_version"], "idempotency_key": f"chat-worker:{turn['turn_id']}:{target}", "requested_at": datetime.now(UTC)})

    def watchdog(self, *, maximum_age: timedelta = timedelta(seconds=30)) -> tuple[str, ...]:
        """Mark unacquired jobs terminal so the UI never waits indefinitely."""
        now = datetime.now(UTC)
        expired: list[str] = []
        with self.store._engine.connect() as c:
            rows = c.execute(select(conversation_dispatches.c.turn_id, conversation_dispatches.c.queued_at).where(conversation_dispatches.c.state.in_(("pending", "enqueued")))).mappings().all()
        for row in rows:
            if now - row["queued_at"] >= maximum_age:
                turn = self.store.claim(row["turn_id"])
                if turn is not None:
                    self.store.finish(turn, failed=True, code="worker_unavailable")
                    expired.append(str(row["turn_id"]))
        return tuple(expired)

    def _context_window_for(self, turn: dict[str, object]) -> int | None:
        """The turn model's real context window, or None if it is unknown.

        Best-effort only: any failure here (catalog not yet refreshed for
        this model, or -- in unit tests -- a store whose engine is a bare
        stub) reports None rather than blocking turn construction over a
        sizing refinement.
        """
        try:
            with self.store._engine.connect() as c:
                context_window = c.execute(
                    select(provider_model_catalog.c.context_window).where(
                        provider_model_catalog.c.user_id == turn["user_id"],
                        provider_model_catalog.c.provider == turn["provider"],
                        provider_model_catalog.c.model_id == turn["model_id"],
                    )
                ).scalar()
        except Exception:
            return None
        return int(context_window) if context_window else None

    def _max_context_tokens_for(self, turn: dict[str, object]) -> int:
        """Derive the context-trim budget from the turn's actual model window.

        The provider catalog already knows each model's real context window
        (used today only to filter model *selection*); this is the first
        place that spends it on the agentic loop's own request-sizing budget,
        instead of the flat 60k every model used to get regardless of what it
        can actually accept. A small or unrefreshed-catalog model falls back
        to a safe smaller budget rather than risking an oversized request.
        """
        context_window = self._context_window_for(turn)
        if context_window is None:
            return DEFAULT_MAX_CONTEXT_TOKENS
        return max(MIN_MAX_CONTEXT_TOKENS, min(DEFAULT_MAX_CONTEXT_TOKENS, context_window - CONTEXT_WINDOW_RESERVE_TOKENS))

    def _num_ctx_for(self, turn: dict[str, object]) -> int:
        """The KV cache Ollama should allocate for this turn.

        Never more than the window this turn will actually fill, and never
        more than the model can hold: both ceilings matter, because the cache
        is real VRAM on the user's own machine.
        """
        context_window = self._context_window_for(turn)
        if context_window is None:
            return OLLAMA_FALLBACK_NUM_CTX
        return min(context_window, self._max_context_tokens_for(turn) + CONTEXT_WINDOW_RESERVE_TOKENS)

    def _base_url_for(self, provider: str, credential) -> str:
        configured = str(credential.get("base_url") or "")
        if provider == "omniroute":
            return normalize_omniroute_base_url(configured or DEFAULT_OMNIROUTE_BASE_URL)
        if provider == "ollama":
            return normalize_ollama_base_url(configured or DEFAULT_OLLAMA_BASE_URL)
        return PROVIDER_BASE_URLS.get(provider, PROVIDER_BASE_URLS["openrouter"])

    def _provider_transport(self, turn: dict[str, object]) -> HTTPProviderStreamTransport:
        with self.store._engine.connect() as c:
            credential = c.execute(select(provider_configurations.c.api_key, provider_configurations.c.api_key_ciphertext, provider_configurations.c.base_url, provider_configurations.c.enabled).where(provider_configurations.c.user_id == turn["user_id"], provider_configurations.c.provider == turn["provider"])).mappings().first()
        if credential is None or not credential["enabled"]:
            raise ValueError("provider unavailable")
        provider, model = str(turn["provider"]), str(turn["model_id"])
        api_key = self._credential_value(credential, str(turn["user_id"]), provider, allow_empty=provider in PROVIDERS_WITH_BASE_URL)
        base_url = self._base_url_for(provider, credential)
        num_ctx = self._num_ctx_for(turn) if provider == "ollama" else None
        return HTTPProviderStreamTransport(provider=provider, base_url=base_url, api_key=api_key, model=model, num_ctx=num_ctx)

    def _credential_value(self, credential, user_id: str, provider: str, *, allow_empty: bool = False) -> str:
        """Read the provider key, upgrading a pre-encryption row in place.

        Installations that saved a key before credential encryption existed hold
        it as cleartext. Refusing to run would strand a working install behind a
        key the UI never shows again, so the value is accepted once and
        immediately rewritten as ciphertext.
        """
        cipher = ProviderSecretCipher.from_environment(required=True)
        ciphertext = credential["api_key_ciphertext"]
        if ciphertext:
            return cipher.decrypt(str(ciphertext))
        legacy = str(credential["api_key"] or "")
        if not legacy and not allow_empty:
            raise ValueError("provider credential is missing")
        with self.store._engine.begin() as connection:
            connection.execute(
                provider_configurations.update()
                .where(provider_configurations.c.user_id == user_id, provider_configurations.c.provider == provider)
                .values(api_key=None, api_key_ciphertext=cipher.encrypt(legacy), updated_at=datetime.now(UTC))
            )
        _LOGGER.info("migrated the %s credential for %s to encrypted storage", provider, user_id)
        return legacy

    def _runtime_for(self, turn: dict[str, object]) -> AgenticTurnRuntime:
        if self._runtime_factory is not None:
            return self._runtime_factory(turn)
        engine = self.store._engine
        skill_library = PostgresSkillLibraryService(engine)
        configured_limits = self._runtime_settings.get(str(turn["user_id"]))
        browser = conversation_browser_for(turn)
        try:
            session = TurnSession(
                turn=turn,
                store=_RuntimeStore(self, turn),
                agents_store=ConversationAgentStore(engine, conversation_id=str(turn["conversation_id"]), user_id=str(turn["user_id"])),
                memory_store=PostgresAgentMemoryStore(
                    engine, str(turn["user_id"]), conversation_id=str(turn["conversation_id"]),
                    project_id=str(turn["project_id"]) if turn.get("project_id") else None,
                    execution_id=str(turn["execution_id"]),
                ),
                provider_factory=lambda: self._provider_transport(turn),
                workspace_root=self._workspace_root,
                cancelled=lambda current: self.store.cancel_requested(str(current["turn_id"])),
                limits=AgenticLimits(deadline=TURN_DEADLINE, max_iterations=configured_limits["max_iterations"], max_actions=None if configured_limits["max_iterations"] is None else 24, max_context_tokens=self._max_context_tokens_for(turn)),
                skills=skill_library.registry_for(str(turn["user_id"]), agent_id=str(self.store.main_agent_id(turn))),
                skill_load_recorder=lambda loaded: skill_library.record_load(
                    user_id=str(turn["user_id"]), execution_id=str(turn["execution_id"]),
                    agent_id=str(self.store.main_agent_id(turn)), loaded=loaded,
                ),
                search_client=search_client_from_environment(),
                browser=browser,
                enable_subagents=self._enable_subagents,
            )
            return session.build_runtime()
        except Exception:
            if browser is not None:
                browser.close()
            raise


async def agentos_agent(ctx: dict, turn_id: str) -> None:
    # ``run`` is synchronous throughout: it streams from the provider over a
    # blocking HTTP client and talks to PostgreSQL between chunks. arq drives
    # every job on one event loop, so calling it inline would hold that loop for
    # the entire turn — ``max_jobs`` would be meaningless, no further turn could
    # be acquired, and ``ChatWorker.watchdog`` would fail each waiting turn as
    # ``worker_unavailable`` after 30s. A slow route (OmniRoute's free providers
    # routinely stream keepalives for tens of seconds) makes that the norm.
    await asyncio.to_thread(ctx["chat_worker"].run, turn_id)


async def startup(ctx: dict) -> None:
    settings = ProductionSettings()
    engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True)
    secret = settings.AGENTOS_ACTIVITY_CURSOR_SECRET.get_secret_value() if settings.AGENTOS_ACTIVITY_CURSOR_SECRET else None
    # The worker and the API must sign activity cursors identically, otherwise a
    # cursor issued by one is rejected by the other and every stream resyncs.
    worker = ChatWorker(PostgresChatStore(engine, PostgresAgenticActivityStore(engine, secret or activity_cursor_fallback(engine))))
    # Report on startup, not only when a turn arrives. A worker that has claimed
    # nothing yet is still a worker that is up, and the launcher needs to be able
    # to tell "ready" from "never started" without waiting for a first message.
    worker.store.heartbeat("chat-worker")
    ctx["chat_worker"] = worker


class WorkerSettings:
    """arq entry point.

    ``redis_settings`` must be a plain class attribute: arq reads this class
    through ``__dict__``, so a property or a metaclass descriptor is invisible to
    it and the worker silently falls back to arq's default Redis instead of the
    configured one. It is bound conditionally only so that importing this module
    for its ``ChatWorker`` does not require a full deployment environment.
    """

    functions = [agentos_agent]
    on_startup = startup
    max_jobs = 8
    job_timeout = int(os.getenv("AGENTOS_WORKER_JOB_TIMEOUT", "3900"))
    if os.getenv("REDIS_URL"):
        redis_settings = RedisSettings.from_dsn(os.environ["REDIS_URL"])


__all__ = ["ChatWorker", "PROVIDER_BASE_URLS", "WorkerSettings", "agentos_agent"]
