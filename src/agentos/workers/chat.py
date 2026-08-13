"""Durable chat-turn worker implementation.

It is run by the local polling worker process. The queue carries only a turn
identifier; credentials and prompt history are looked up after the worker has
acquired the durable turn.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Callable

from sqlalchemy import select

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
from agentos.persistence.postgres.schema import (
    conversation_dispatches,
    provider_configurations,
    provider_model_catalog,
    provider_model_favorites,
    vision_model_selections,
)
from agentos.persistence.provider_secrets import ProviderSecretCipher
from agentos.persistence.postgres.skills import PostgresSkillLibraryService
from agentos.reading.selection import VisionModel, choose_vision_model
from agentos.reading.vision import VisionReader
from agentos.persistence.sqlite import create_local_engine


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

    def attachments_for_turn(self, turn: dict[str, object]):
        return self._store.attachments_for_turn(turn)

    def delta(self, turn: dict[str, object], text: str) -> None:
        self._store.delta(turn, text)

    def finish(self, turn: dict[str, object], *, failed: bool = False, code: str | None = None) -> None:
        self._store.finish(turn, failed=failed, code=code)

    def lifecycle(self, turn: dict[str, object], state: str, **payload: object) -> None:
        # Public activity is published by the session before this is called; the
        # only job left here is the technical execution projection.
        target = {
            "running": "RUNNING", "waiting_tool": "WAITING_TOOL", "tool_started": "RUNNING",
            "tool_finished": "RUNNING", "retrying": "RUNNING", "waiting_user": "WAITING_USER",
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
        elif result.state == "waiting_user":
            self._project(turn, "WAITING_USER", "agent_requested_user_input")
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

    def _catalog_row_for(self, turn: dict[str, object]) -> dict[str, object] | None:
        """The turn model's provider-catalog row, or None if it is unknown.

        Best-effort only: any failure here (catalog not yet refreshed for
        this model, or -- in unit tests -- a store whose engine is a bare
        stub) reports None rather than blocking turn construction over a
        sizing or capability refinement.
        """
        try:
            with self.store._engine.connect() as c:
                row = c.execute(
                    select(provider_model_catalog).where(
                        provider_model_catalog.c.user_id == turn["user_id"],
                        provider_model_catalog.c.provider == turn["provider"],
                        provider_model_catalog.c.model_id == turn["model_id"],
                    )
                ).mappings().first()
        except Exception:
            return None
        return dict(row) if row else None

    def _context_window_for(self, turn: dict[str, object]) -> int | None:
        """The turn model's real context window, or None if it is unknown."""
        row = self._catalog_row_for(turn) or {}
        context_window = row.get("context_window")
        return int(context_window) if context_window else None

    def _model_sees_images(self, turn: dict[str, object]) -> bool:
        row = self._catalog_row_for(turn) or {}
        modalities = row.get("input_modalities") or ()
        if isinstance(modalities, str):
            modalities = [item.strip() for item in modalities.split(",")]
        return "image" in {str(item).lower() for item in modalities}

    def _model_calls_tools(self, turn: dict[str, object]) -> bool:
        row = self._catalog_row_for(turn) or {}
        capabilities = row.get("capabilities") or ()
        if isinstance(capabilities, str):
            capabilities = [item.strip() for item in capabilities.split(",")]
        names = {str(item).lower() for item in capabilities}
        # An unrefreshed catalog must not silently disable tools: only an
        # explicit capability list that omits tools counts as "cannot".
        return not names or "tools" in names or "tool_use" in names or "function_calling" in names

    def _vision_candidates(self, user_id: str) -> list[VisionModel]:
        """Every catalog model this user has that lists ``image`` as an input modality."""
        try:
            with self.store._engine.connect() as c:
                rows = c.execute(
                    select(provider_model_catalog.c.provider, provider_model_catalog.c.model_id, provider_model_catalog.c.input_modalities)
                    .where(provider_model_catalog.c.user_id == user_id)
                ).mappings().all()
        except Exception:
            return []
        candidates: list[VisionModel] = []
        for row in rows:
            modalities = row.get("input_modalities") or ()
            if isinstance(modalities, str):
                modalities = [item.strip() for item in modalities.split(",")]
            if "image" in {str(item).lower() for item in modalities}:
                candidates.append(VisionModel(str(row["provider"]), str(row["model_id"])))
        return candidates

    def _vision_override(self, user_id: str) -> VisionModel | None:
        """The model the person explicitly picked for visual reading, if any."""
        try:
            with self.store._engine.connect() as c:
                row = c.execute(
                    select(vision_model_selections.c.provider, vision_model_selections.c.model_id)
                    .where(vision_model_selections.c.user_id == user_id)
                ).mappings().first()
        except Exception:
            return None
        return VisionModel(str(row["provider"]), str(row["model_id"])) if row else None

    def _vision_reader_factory(self, turn: dict[str, object]) -> Callable[[], VisionReader | None]:
        """A cheap closure the session calls only once a visual read is actually needed.

        Nothing here touches the database -- or even reads ``turn``'s own
        fields -- until the returned callable runs: most turns carry no visual
        attachment and must not pay for a catalog query and a model-selection
        pass they will never use.
        """

        def build() -> VisionReader | None:
            user_id = str(turn["user_id"])
            turn_provider = str(turn["provider"])
            candidates = self._vision_candidates(user_id)
            if not candidates:
                return None
            chosen = choose_vision_model(candidates, turn_provider=turn_provider, override=self._vision_override(user_id))
            if chosen is None:
                return None

            def transport_factory(model: VisionModel) -> HTTPProviderStreamTransport | None:
                # A model chosen for visual reading may belong to a different
                # provider than the turn's own model, so its transport is built
                # from *its* credential, never the turn's.
                vision_turn = {"user_id": user_id, "provider": model.provider, "model_id": model.model_id}
                num_ctx = self._num_ctx_for(vision_turn) if model.provider == "ollama" else None
                try:
                    return self._transport_for(user_id, model.provider, model.model_id, num_ctx=num_ctx)
                except Exception:
                    return None

            return VisionReader(transport_factory, model=chosen)

        return build

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

    def _transport_for(self, user_id: str, provider: str, model_id: str, num_ctx: int | None = None) -> HTTPProviderStreamTransport:
        """Build a transport for any (user, provider, model), not just the turn's own.

        This is the credential-handling seam both the turn's own provider
        (``_provider_transport``) and a visual-reading model on a *different*
        provider (``_vision_reader_factory``) go through, so a provider's
        credential is only ever looked up under its own name.
        """
        with self.store._engine.connect() as c:
            credential = c.execute(select(provider_configurations.c.api_key, provider_configurations.c.api_key_ciphertext, provider_configurations.c.base_url, provider_configurations.c.enabled).where(provider_configurations.c.user_id == user_id, provider_configurations.c.provider == provider)).mappings().first()
        if credential is None or not credential["enabled"]:
            raise ValueError("provider unavailable")
        api_key = self._credential_value(credential, user_id, provider, allow_empty=provider in PROVIDERS_WITH_BASE_URL)
        base_url = self._base_url_for(provider, credential)
        return HTTPProviderStreamTransport(provider=provider, base_url=base_url, api_key=api_key, model=model_id, num_ctx=num_ctx)

    def _provider_transport(self, turn: dict[str, object]) -> HTTPProviderStreamTransport:
        provider = str(turn["provider"])
        num_ctx = self._num_ctx_for(turn) if provider == "ollama" else None
        return self._transport_for(str(turn["user_id"]), provider, str(turn["model_id"]), num_ctx=num_ctx)

    def _favorite_child_model_ids(self, turn: dict[str, object]) -> tuple[str, ...]:
        """Return this user's favorites, limited to the provider of the active turn."""
        try:
            with self.store._engine.connect() as connection:
                rows = connection.execute(
                    select(provider_model_catalog.c.model_id)
                    .join(
                        provider_model_favorites,
                        (provider_model_favorites.c.user_id == provider_model_catalog.c.user_id)
                        & (provider_model_favorites.c.provider == provider_model_catalog.c.provider)
                        & (provider_model_favorites.c.model_id == provider_model_catalog.c.model_id),
                    )
                    .where(
                        provider_model_catalog.c.user_id == turn["user_id"],
                        provider_model_catalog.c.provider == turn["provider"],
                    )
                    .order_by(provider_model_catalog.c.display_name, provider_model_catalog.c.model_id)
                ).all()
        except Exception:
            return ()
        return tuple(str(row.model_id) for row in rows)

    def _is_favorite_child_model(self, turn: dict[str, object], model_id: str) -> bool:
        """Authorize a model at execution time against the durable user catalog."""
        try:
            with self.store._engine.connect() as connection:
                return connection.execute(
                    select(provider_model_catalog.c.id)
                    .join(
                        provider_model_favorites,
                        (provider_model_favorites.c.user_id == provider_model_catalog.c.user_id)
                        & (provider_model_favorites.c.provider == provider_model_catalog.c.provider)
                        & (provider_model_favorites.c.model_id == provider_model_catalog.c.model_id),
                    )
                    .where(
                        provider_model_catalog.c.user_id == turn["user_id"],
                        provider_model_catalog.c.provider == turn["provider"],
                        provider_model_catalog.c.model_id == model_id,
                    )
                ).scalar_one_or_none() is not None
        except Exception:
            return False

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
                skill_library=skill_library,
                skill_load_recorder=lambda loaded: skill_library.record_load(
                    user_id=str(turn["user_id"]), execution_id=str(turn["execution_id"]),
                    agent_id=str(self.store.main_agent_id(turn)), loaded=loaded,
                ),
                search_client=search_client_from_environment(),
                browser=browser,
                enable_subagents=self._enable_subagents,
                model_sees_images=self._model_sees_images(turn),
                model_calls_tools=self._model_calls_tools(turn),
                vision_reader_factory=self._vision_reader_factory(turn),
                child_model_ids=self._favorite_child_model_ids(turn),
                child_model_authorizer=lambda model_id: self._is_favorite_child_model(turn, model_id),
                child_provider_factory=lambda model_id: self._transport_for(
                    str(turn["user_id"]), str(turn["provider"]), model_id,
                    num_ctx=self._num_ctx_for({**turn, "model_id": model_id}) if str(turn["provider"]) == "ollama" else None,
                ),
            )
            return session.build_runtime()
        except Exception:
            if browser is not None:
                browser.close()
            raise


async def agentos_agent(ctx: dict, turn_id: str) -> None:
    """Compatibility helper for integrations that schedule a worker thread."""
    await asyncio.to_thread(ctx["chat_worker"].run, turn_id)


def create_chat_worker() -> ChatWorker:
    """Build the synchronous worker used by the local durable poller."""
    # ``run`` is synchronous throughout: it streams from the provider over a
    # blocking HTTP client and talks to PostgreSQL between chunks. arq drives
    # every job on one event loop, so calling it inline would hold that loop for
    # the entire turn — ``max_jobs`` would be meaningless, no further turn could
    # be acquired, and ``ChatWorker.watchdog`` would fail each waiting turn as
    # ``worker_unavailable`` after 30s. A slow route (OmniRoute's free providers
    # routinely stream keepalives for tens of seconds) makes that the norm.
    settings = ProductionSettings()
    engine = create_local_engine(settings.DATABASE_URL)
    secret = settings.AGENTOS_ACTIVITY_CURSOR_SECRET.get_secret_value() if settings.AGENTOS_ACTIVITY_CURSOR_SECRET else None
    # The worker and the API must sign activity cursors identically, otherwise a
    # cursor issued by one is rejected by the other and every stream resyncs.
    worker = ChatWorker(PostgresChatStore(engine, PostgresAgenticActivityStore(engine, secret or activity_cursor_fallback(engine))))
    # Report on startup, not only when a turn arrives. A worker that has claimed
    # nothing yet is still a worker that is up, and the launcher needs to be able
    # to tell "ready" from "never started" without waiting for a first message.
    worker.store.heartbeat("chat-worker")
    return worker


__all__ = ["ChatWorker", "PROVIDER_BASE_URLS", "agentos_agent", "create_chat_worker"]
