"""Durable chat-turn worker implementation.

It is run by the local polling worker process. The queue carries only a turn
identifier; credentials and prompt history are looked up after the worker has
acquired the durable turn.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from uuid import uuid4
from typing import Callable

from sqlalchemy import select, update

from agentos.api.contracts import ApplicationConflictError
from agentos.bootstrap.production import ProductionSettings, activity_cursor_fallback
from agentos.agentic.provider_stream import HTTPProviderStreamTransport
from agentos.agentic.provider_key_fallback import MultiKeyProviderStreamTransport
from agentos.persistence.postgres.provider_api_keys import PostgresProviderApiKeyAdapter
from agentos.provider_catalog.models import PROVIDERS_WITH_BASE_URL
from agentos.provider_catalog.ollama import DEFAULT_OLLAMA_BASE_URL, normalize_ollama_base_url
from agentos.provider_catalog.omniroute import DEFAULT_OMNIROUTE_BASE_URL, normalize_omniroute_base_url
from agentos.agentic.runtime import AgenticLimits, AgenticRunResult, AgenticTurnRuntime
from agentos.agentic.settings import AgentRuntimeSettingsStore
from agentos.agentic.events import AgentActivityEventType
from agentos.code_mode.models import (
    CodePermissionMode,
    CodeStage,
    explicitly_authorizes_git_push,
    normalize_code_autonomy,
    normalize_permission_mode,
)
from agentos.agentic.transcript import REHYDRATION_BUDGET_FRACTION
from agentos.agentic.session import (
    TurnSession,
    build_retrieval_for_turn,
    resolve_child_model_id,
    resolve_effective_workspace_id,
)
from agentos.agentic.web_search import search_client_from_environment
from agentos.retrieval.registry import RetrievalRegistry
from agentos.accounts.store import UserStore
from agentos.configuration.capabilities import InstanceCapabilities
from agentos.configuration.mode import current_mode
from agentos.conversations.chat import PostgresChatStore
from agentos.installation import orin_paths
from agentos.local_workspace import PostgresLocalWorkspaceStore
from agentos.mcp.auth import McpOAuth
from agentos.projects.store import PostgresProjectStore
from agentos.mcp.service import McpServerService
from agentos.mcp.toolset import McpToolProvider
from agentos.plugins.hook_engine import HookEngine
from agentos.plugins.rehydrate import rehydrate_hooks
from agentos.plugins.fetcher import PluginFetcher
from agentos.plugins.service import PluginService
from agentos.profile_files.binding import relative_display
from agentos.persistence.postgres.agent_memory import PostgresAgentMemoryStore
from agentos.persistence.postgres.agentic_activity import PostgresAgenticActivityStore
from agentos.persistence.postgres.conversation_agents import ConversationAgentStore
from agentos.persistence.postgres.execution_adapters import ExecutionApplicationAdapter, ExecutionQueryAdapter
from agentos.persistence.postgres.execution_journal import PostgresExecutionJournal
from agentos.execution.journal import (
    ExecutionCheckpoint,
    ExecutionEffect,
    ExecutionEffectKind,
    ExecutionEffectRetryability,
    ExecutionEffectState,
    ExecutionJournalScope,
)
from agentos.execution.models import CancellationReason, CancellationReasonCode, ExecutionState
from agentos.runtime.models import (
    ContextSnapshot,
    ModelSelection,
    ProviderCancelled,
    ProviderFailed,
    ProviderFinal,
    ProviderIndeterminate,
    ProviderUserInputRequest,
    CompletedOutcome,
    CancelledOutcome,
    FailedOutcome,
    WaitingOutcome,
    RuntimeErrorCategory,
    RuntimeErrorInfo,
    RuntimeRequest,
)
from agentos.runtime.service import RuntimeService
from agentos.persistence.postgres.schema import (
    code_mode_runs,
    conversation_dispatches,
    conversation_turns,
    provider_configurations,
    provider_model_catalog,
    provider_model_favorites,
    runtime_heartbeats,
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

# Upper bound used only when the turn's model has no known context window.
# It is a safety value for an unrefreshed catalog, not a ceiling on a model
# whose real window we do know: capping a 200k model at 60k is what forced
# compaction at ~49k and made the agent forget mid-task.
DEFAULT_MAX_CONTEXT_TOKENS = 60_000
# AgenticLimits rejects anything below 1_000; this keeps a real margin above
# that floor so a tiny-context model still has room for the pinned request.
MIN_MAX_CONTEXT_TOKENS = 4_000
# Headroom subtracted from a model's real context window before it becomes
# the trim budget: the system prompt and tool schemas ride outside the
# trimmed message window, and the reply itself (uncapped here, so it can use
# the provider's own max) still has to fit in what's left. This is the floor;
# a large window reserves a proportion instead, because 12k is not enough
# room for thirty tool schemas plus a long reply.
CONTEXT_WINDOW_RESERVE_TOKENS = 12_000
CONTEXT_WINDOW_RESERVE_FRACTION = 0.10
# A million-token window does not need to reserve a hundred thousand tokens.
CONTEXT_WINDOW_RESERVE_CEILING = 64_000

# num_ctx for an Ollama model the catalog has no window for. Deliberately not
# DEFAULT_MAX_CONTEXT_TOKENS: asking an unknown local model for a 60k KV cache
# is what spills into system RAM and drops inference by 20-50x.
OLLAMA_FALLBACK_NUM_CTX = 16_384

# A turn is allowed to continue until the worker's job timeout.  The old
# five-minute deadline cut off ordinary multi-step file work mid-task.
TURN_DEADLINE = timedelta(seconds=3600)
HEARTBEAT_REFRESH_SECONDS = 10.0


def _retry_limits(turn: Mapping[str, object]) -> dict[str, object]:
    """Code turns retry a failing provider 8 times with exponential backoff; Chat keeps one immediate retry."""
    from agentos.agentic.session import CODE_MAX_PROVIDER_RETRIES, CODE_RETRY_BASE_DELAY_SECONDS

    if turn.get("code_mode") == "code":
        return {"max_provider_retries": CODE_MAX_PROVIDER_RETRIES, "retry_base_delay_seconds": CODE_RETRY_BASE_DELAY_SECONDS}
    return {"max_provider_retries": 1, "retry_base_delay_seconds": 0.0}


class _RuntimeStore:
    """Worker-scoped view that keeps the chat store outside the runtime API.

    It is also the seam where a runtime lifecycle callback becomes the
    canonical execution transition alongside the public activity event the UI
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
        # The trajectory of earlier turns is replayed into the history, bounded
        # by a fraction of this turn's context budget so a long conversation
        # cannot spend the whole window on what already happened.
        budget = int(self._worker._max_context_tokens_for(turn) * REHYDRATION_BUDGET_FRACTION)
        return self._store.history_for_turn(turn, rehydration_budget_tokens=budget)

    def attachments_for_turn(self, turn: dict[str, object]):
        return self._store.attachments_for_turn(turn)

    def delta(self, turn: dict[str, object], text: str) -> None:
        self._worker._refresh_heartbeat_if_due()
        self._store.delta(turn, text)

    def reset_assistant_message(self, turn: dict[str, object]) -> None:
        resetter = getattr(self._store, "reset_assistant_message", None)
        if callable(resetter):
            resetter(turn)

    def finish(self, turn: dict[str, object], *, failed: bool = False, code: str | None = None) -> None:
        if not self._worker._kernel_manages(turn):
            self._store.finish(turn, failed=failed, code=code)

    def exclude_from_context(self, turn: dict[str, object]) -> None:
        # Not gated on kernel management: the erasure must happen on every path that blocked the prompt.
        excluder = getattr(self._store, "exclude_from_context", None)
        if callable(excluder):
            excluder(turn)

    def lifecycle(self, turn: dict[str, object], state: str, **payload: object) -> None:
        self._worker._refresh_heartbeat_if_due()
        # Public activity is published by the session before this is called;
        # this is the authoritative lifecycle commit for the current turn.
        if self._worker._kernel_manages(turn):
            return
        target = {
            "running": "RUNNING", "waiting_tool": "WAITING_TOOL", "retrying": "RUNNING",
            "waiting_user": "WAITING_USER",
        }.get(state)
        if target is not None:
            self._worker._transition(
                turn, target, f"agentic_{state}",
                result_ref=str(payload["result_ref"]) if payload.get("result_ref") else None,
            )

    def effect_started(self, turn: dict[str, object], *, kind: str, invocation_ref: str, request_ref: str) -> str:
        return self._worker._effect_started(turn, kind=kind, invocation_ref=invocation_ref, request_ref=request_ref)

    def effect_finished(self, turn: dict[str, object], *, effect_id: str, state: str, result_ref: str | None = None, error_code: str | None = None, private_result=None) -> None:
        self._worker._effect_finished(
            turn, effect_id=effect_id, state=state, result_ref=result_ref,
            error_code=error_code, private_result=private_result,
        )

    def record_quality(self, turn: dict[str, object], **values: object) -> None:
        self._store.record_quality(turn, **values)

    def record_step(self, turn: dict[str, object], **values: object) -> None:
        self._store.record_step(turn, **values)

    def latest_contract(self, conversation_id: str):
        return self._store.latest_contract(conversation_id)

    def claim_queued_inputs(self, turn: dict[str, object]):
        return self._store.claim_queued_inputs(turn)

    def mark_queued_input_blocked(self, queue_id: str) -> None:
        self._store.mark_queued_input_blocked(queue_id)

    def prompt_snapshot(self, conversation_id: str):
        return self._store.prompt_snapshot(conversation_id)

    def save_prompt_snapshot(self, conversation_id: str, **values) -> None:
        self._store.save_prompt_snapshot(conversation_id, **values)

    def clear_prompt_snapshot(self, conversation_id: str) -> None:
        self._store.clear_prompt_snapshot(conversation_id)

    def record_compact_boundary(self, turn: dict[str, object], **values: object):
        return self._store.record_compact_boundary(turn, **values)

    def mark_command_turn(self, turn: dict[str, object]) -> None:
        self._store.mark_command_turn(turn)

    # -- session seams ---------------------------------------------------

    def session_state(self, conversation_id: str):
        return self._store.session_state(conversation_id)

    def record_session_start(self, conversation_id: str, **values) -> None:
        self._store.record_session_start(conversation_id, **values)

    def record(self, *args, **kwargs) -> None:
        self._store.record(*args, **kwargs)

    def main_agent_id(self, turn) -> str:
        return self._store.main_agent_id(turn)


class _ConversationContextManager:
    """Reference-only context adapter for the conversational Kernel boundary."""

    def __init__(self, turn: dict[str, object]) -> None:
        self._turn = turn

    def assemble(self, request):
        ref = f"conversation-turn:{self._turn['turn_id']}:transcript"
        return ContextSnapshot(ref, ref)

    def apply_turn(self, request):
        return ContextSnapshot(request.context_ref, request.manifest_ref or request.context_ref)

    def finalize(self, execution_id, disposition) -> None:
        return None


class _ConversationModelResolver:
    def __init__(self, turn: dict[str, object]) -> None:
        self._turn = turn

    def resolve(self, request):
        ref = f"conversation-model:{self._turn['provider']}:{self._turn['model_id']}"
        return ModelSelection(ref, ref)


class _ConversationCheckpointPort:
    """Adapts the durable journal to the generic Kernel checkpoint port."""

    def __init__(self, worker: "ChatWorker", turn: dict[str, object]) -> None:
        self._worker, self._turn = worker, turn

    def load(self, checkpoint_ref, context):
        scope = self._worker._journal_scope(self._turn)
        checkpoint = self._worker._journal.latest_safe(scope)
        if checkpoint is None or checkpoint.checkpoint_id != checkpoint_ref:
            raise LookupError("checkpoint not found or not safe")
        from agentos.runtime.models import CheckpointSnapshot

        return CheckpointSnapshot(
            checkpoint.checkpoint_id, checkpoint.scope.execution_id,
            checkpoint.execution_state_version, checkpoint.sequence,
            checkpoint.context_manifest_ref,
            checkpoint.pending_effect_id,
        )

    def latest_safe(self, execution_id, context):
        scope = self._worker._journal_scope(self._turn)
        checkpoint = self._worker._journal.latest_safe(scope)
        if checkpoint is None:
            return None
        from agentos.runtime.models import CheckpointSnapshot

        return CheckpointSnapshot(
            checkpoint.checkpoint_id, checkpoint.scope.execution_id,
            checkpoint.execution_state_version, checkpoint.sequence,
            checkpoint.context_manifest_ref, checkpoint.pending_effect_id,
        )


class _ConversationBudgetPolicy:
    def evaluate(self, request):
        from agentos.runtime.models import BudgetDecision, BudgetEvaluation

        return BudgetEvaluation(BudgetDecision.CONTINUE)


class _ConversationClock:
    def now(self):
        return datetime.now(UTC)

    def monotonic(self):
        import time

        return time.monotonic()


class _UnusedActionPort:
    def invoke(self, request):
        raise AssertionError("the conversational provider owns tool dispatch")


class _ConversationProviderPort:
    """Compatibility adapter: stream/tool formatting stays in AgenticTurnRuntime.

    It deliberately has no lifecycle authority. RuntimeService owns acquisition,
    running/waiting/terminal states and outbox commits around this adapter.
    """

    def __init__(self, runtime: AgenticTurnRuntime, turn: dict[str, object]) -> None:
        self._runtime, self._turn = runtime, turn
        self.reconciliation_required = False
        self.completed_with_caveats = False
        self.error_code: str | None = None
        self.queued_turn_count = 0

    def generate(self, request):
        result = self._runtime.run(str(self._turn["turn_id"]))
        self.error_code = result.error_code
        self.queued_turn_count = int(getattr(result, "queued_turn_count", 0) or 0)
        if result.state == "completed":
            return ProviderFinal(f"conversation-message:{self._turn['assistant_message_id']}")
        if result.state == "completed_with_caveats":
            self.completed_with_caveats = True
            return ProviderFinal(f"conversation-message:{self._turn['assistant_message_id']}")
        if result.state == "waiting_user":
            return ProviderUserInputRequest(f"conversation-turn:{self._turn['turn_id']}:input")
        if result.state == "cancelled":
            return ProviderCancelled(CancellationReason(CancellationReasonCode.USER_REQUESTED))
        if result.state == "reconciliation_required":
            self.reconciliation_required = True
            # The generic Kernel has a first-class WAITING_USER transition but
            # not a dedicated reconciliation state. It commits that durable
            # stop first; the chat adapter immediately projects it to PAUSED,
            # preserving the no-retry invariant without changing the generic
            # ProviderIndeterminate contract used by other runtimes.
            return ProviderUserInputRequest(f"conversation-turn:{self._turn['turn_id']}:reconciliation")
        return ProviderFailed(RuntimeErrorInfo(
            RuntimeErrorCategory.PROVIDER, result.error_code or "CONVERSATION_RUNTIME_FAILED"
        ))


class ChatWorker:
    def __init__(
        self,
        store: PostgresChatStore,
        runtime_factory: Callable[[dict[str, object]], AgenticTurnRuntime] | None = None,
        *,
        workspace_root: str | None = None,
        enable_subagents: bool = True,
        runtime_settings: AgentRuntimeSettingsStore | None = None,
        retrieval_registry: RetrievalRegistry | None = None,
        capabilities: InstanceCapabilities | None = None,
    ) -> None:
        self.store, self._executions, self._queries = store, ExecutionApplicationAdapter(store._engine), ExecutionQueryAdapter(store._engine)
        self._journal = PostgresExecutionJournal(store._engine)
        self._reconciliation_turns: set[str] = set()
        self._kernel_turns: set[str] = set()
        self._runtime_factory = runtime_factory
        self._workspace_root = workspace_root
        self._enable_subagents = enable_subagents
        self._runtime_settings = runtime_settings or AgentRuntimeSettingsStore()
        # A project's retrieval index survives across turns (and
        # across every conversation about that project): building it fresh per
        # turn would open a new sqlite connection, a new embedder HTTP client
        # and a new background indexing thread every single time with nothing
        # ever closing the previous one.
        self._retrieval_registry = retrieval_registry or RetrievalRegistry(
            factory=lambda workspace_id, local_root: build_retrieval_for_turn(workspace_id=workspace_id, local_root=local_root)
        )
        # Kept for the whole worker process (unlike skill_library/plugin_service,
        # which are cheap DB-backed rebuilds per turn): registrations here are an
        # in-process index, and are refreshed per-user before each turn below.
        self._capabilities = capabilities or InstanceCapabilities.for_mode(current_mode())
        self._hook_engine = HookEngine(enabled=self._capabilities.plugin_hooks)
        self._state_lock = threading.Lock()
        self._last_heartbeat_mono: float = 0.0

    def _refresh_heartbeat_if_due(self) -> None:
        now_mono = time.monotonic()
        with self._state_lock:
            due = now_mono - self._last_heartbeat_mono >= HEARTBEAT_REFRESH_SECONDS
            if due:
                self._last_heartbeat_mono = now_mono
        if due:
            try:
                self.store.heartbeat("chat-worker")
            except Exception:
                pass

    def _is_turn_cancelled(self, current: Mapping[str, object]) -> bool:
        self._refresh_heartbeat_if_due()
        return bool(self.store.cancel_requested(str(current["turn_id"])))

    def run(self, turn_id: str) -> None:
        self.store.heartbeat("chat-worker")
        with self._state_lock:
            self._last_heartbeat_mono = time.monotonic()
        turn = self.store.claim(turn_id)
        if turn is None:
            return
        refusal = self._preflight_refusal(turn)
        if refusal is not None:
            self._refuse(turn, refusal)
            return
        kernel_managed = self._runtime_factory is None
        if not kernel_managed:
            try:
                self._project(turn, "STARTING", "worker_acquired")
                self._project(turn, "RUNNING", "provider_started")
            except Exception:
                # Compatibility factories exist for tests and integrations
                # still exercising the old seam. Production never enters it.
                _LOGGER.exception("execution lifecycle could not start for chat turn %s", turn_id)
                self.store.finish(turn, failed=True, code="execution_lifecycle_unavailable")
                return
        if kernel_managed:
            with self._state_lock:
                self._kernel_turns.add(str(turn_id))
        runtime = None
        try:
            runtime = self._runtime_for(turn)
            if kernel_managed:
                control, context = self._executions.trusted_control_context(
                    execution_id=str(turn["execution_id"]), user_id=str(turn["user_id"]),
                )
                provider_port = _ConversationProviderPort(runtime, turn)
                outcome = RuntimeService(
                    control=control,
                    context_manager=_ConversationContextManager(turn),
                    model_resolver=_ConversationModelResolver(turn),
                    provider=provider_port,
                    action_port=_UnusedActionPort(),
                    checkpoint_port=_ConversationCheckpointPort(self, turn),
                    clock=_ConversationClock(), budget_policy=_ConversationBudgetPolicy(),
                ).execute(RuntimeRequest(
                    execution_id=context.execution_id, user_id=context.user_id,
                    workspace_id=context.workspace_id, agent_id=context.agent_id,
                    actor_ref="chat-worker", worker_ref="chat-worker",
                    correlation_id=context.correlation_id, purpose=context.purpose,
                    model_requirements_ref=f"conversation-model:{turn['provider']}:{turn['model_id']}",
                ))
                if isinstance(outcome, CompletedOutcome):
                    result = AgenticRunResult(
                        "completed_with_caveats" if provider_port.completed_with_caveats else "completed",
                        error_code=provider_port.error_code,
                        queued_turn_count=provider_port.queued_turn_count,
                    )
                elif isinstance(outcome, WaitingOutcome):
                    result = AgenticRunResult(
                        "reconciliation_required" if provider_port.reconciliation_required or outcome.state is ExecutionState.PAUSED else "waiting_user",
                        error_code="EFFECT_RECONCILIATION_REQUIRED" if provider_port.reconciliation_required or outcome.state is ExecutionState.PAUSED else None,
                        queued_turn_count=provider_port.queued_turn_count,
                    )
                elif isinstance(outcome, CancelledOutcome):
                    result = AgenticRunResult("cancelled", queued_turn_count=provider_port.queued_turn_count)
                elif isinstance(outcome, FailedOutcome):
                    result = AgenticRunResult("failed", error_code=outcome.error.code, queued_turn_count=provider_port.queued_turn_count)
                else:
                    raise AssertionError("unknown conversational Kernel outcome")
            else:
                result = runtime.run(str(turn_id))
        except Exception:
            # A newly created turn always has its Execution now.  Do not run
            # A turn that dies silently is the single worst failure mode for a
            # local install: the UI shows "failed" with no way to learn why.
            _LOGGER.exception("chat turn %s failed", turn_id)
            try:
                self._project(turn, "FAILED", "provider_failed")
            except Exception:
                _LOGGER.exception("execution failure transition could not be committed for chat turn %s", turn_id)
            self.store.finish(turn, failed=True, code="provider_unavailable")
            return
        finally:
            with self._state_lock:
                self._kernel_turns.discard(str(turn_id))
                self._reconciliation_turns.discard(str(turn_id))
            closer = getattr(runtime, "close", None)
            if callable(closer):
                try:
                    closer()
                except Exception:
                    _LOGGER.exception("chat runtime cleanup for turn %s failed", turn_id)
        result_state = result.state
        error_code = result.error_code
        if result_state not in {"completed", "completed_with_caveats", "waiting_user", "cancelled", "reconciliation_required"}:
            cancel_req = getattr(self.store, "cancel_requested", None)
            if callable(cancel_req) and cancel_req(turn["turn_id"]):
                result_state = "cancelled"
                error_code = "TURN_CANCELLED"
        try:
            if result_state in {"completed", "completed_with_caveats"}:
                self._project(turn, "COMPLETED", "provider_completed", result_ref=f"conversation-message:{turn['assistant_message_id']}")
                if kernel_managed:
                    self.store.finish(turn)
            elif result_state == "waiting_user":
                self._project(turn, "WAITING_USER", "agent_requested_user_input")
                if kernel_managed:
                    self.store.finish(turn, code="WAITING_USER")
            elif result_state == "cancelled":
                self._project(turn, "CANCELLED", "user_cancelled")
                if kernel_managed:
                    self.store.finish(turn, failed=True, code="TURN_CANCELLED")
            elif result_state == "reconciliation_required":
                self._project(turn, "PAUSED", "effect_reconciliation_required")
                self.store.pause_for_reconciliation(turn, code=error_code or "EFFECT_RECONCILIATION_REQUIRED")
            else:
                self._project(turn, "FAILED", error_code or "agentic_runtime_failed")
                if kernel_managed:
                    self.store.finish(turn, failed=True, code=error_code or "agentic_runtime_failed")
        except ApplicationConflictError:
            _LOGGER.info("execution %s already transitioned concurrently for turn %s", turn.get("execution_id"), turn_id)
            if kernel_managed:
                if result_state == "cancelled":
                    self.store.finish(turn, failed=True, code="TURN_CANCELLED")
                elif result_state in {"completed", "completed_with_caveats"}:
                    self.store.finish(turn)
                elif result_state == "waiting_user":
                    self.store.finish(turn, code="WAITING_USER")
                elif result_state != "reconciliation_required":
                    self.store.finish(turn, failed=True, code=error_code or "agentic_runtime_failed")
        finally:
            self._settle_code_mode(turn, result_state, error_code=error_code)
        self._promote_queued_inputs(turn, result_state)

    def _settle_code_mode(self, turn: dict[str, object], result_state: str, *, error_code: str | None) -> None:
        if turn.get("code_mode") != "code":
            return
        target = {
            "completed": CodeStage.COMPLETED,
            "completed_with_caveats": CodeStage.COMPLETED_WITH_CAVEATS,
            "waiting_user": CodeStage.WAITING_DECISION,
            "reconciliation_required": CodeStage.BLOCKED,
            "cancelled": CodeStage.BLOCKED,
        }.get(result_state, CodeStage.BLOCKED)
        try:
            plan = turn.get("code_mode_plan_path") or turn.get("plan_path") or turn.get("plan")
            begin = getattr(getattr(self.store, "_engine", None), "begin", None)
            if callable(begin):
                with begin() as connection:
                    run = connection.execute(select(code_mode_runs).where(
                        code_mode_runs.c.execution_id == turn["execution_id"],
                    )).mappings().first()
                    if run is not None and not plan:
                        plan = run.get("plan_path")
                    if run is not None:
                        connection.execute(update(code_mode_runs).where(
                            code_mode_runs.c.execution_id == turn["execution_id"],
                        ).values(
                            stage=target.value,
                            completion_kind=("verified" if target is CodeStage.COMPLETED else "with_caveats" if target is CodeStage.COMPLETED_WITH_CAVEATS else None),
                            caveats=error_code if target in {CodeStage.BLOCKED, CodeStage.COMPLETED_WITH_CAVEATS} and error_code else None,
                            updated_at=datetime.now(UTC),
                        ))
            if not plan:
                return
            event = (
                AgentActivityEventType.CODE_MODE_COMPLETED if target is CodeStage.COMPLETED else
                AgentActivityEventType.CODE_MODE_COMPLETED_WITH_CAVEATS if target is CodeStage.COMPLETED_WITH_CAVEATS else
                AgentActivityEventType.CODE_MODE_DECISION_REQUIRED if target is CodeStage.WAITING_DECISION else
                AgentActivityEventType.CODE_MODE_BLOCKED
            )
            summary = "Entrega de código concluída" if target is CodeStage.COMPLETED else (
                "Entrega de código concluída com ressalvas" if target is CodeStage.COMPLETED_WITH_CAVEATS else
                "Modo Code aguarda uma decisão" if target is CodeStage.WAITING_DECISION else "Modo Code bloqueado"
            )
            self.store._activity(
                turn,
                event,
                summary,
                {"stage": target.value, "plan_path": plan, "error_code": error_code}
                if error_code
                else {"stage": target.value, "plan_path": plan},
            )
        except Exception:
            _LOGGER.exception("could not settle Code mode for turn %s", turn.get("turn_id"))

    def _promote_queued_inputs(self, turn: dict[str, object], result_state: str) -> None:
        """Queued text the turn never read becomes the next turn, but only after a turn that finished.

        A turn that ends ``waiting_user`` (a question, or a safety pause) leaves the queue to the person: queued
        text written before the question existed would otherwise answer it. A cancelled or failed turn leaves it too.
        """
        if result_state not in {"completed", "completed_with_caveats"}:
            return
        if not callable(getattr(self.store, "take_queued_inputs", None)):
            return
        from agentos.conversations.chat import ChatApplication

        try:
            ChatApplication(self.store).promote_queued_inputs(
                str(turn["conversation_id"]), str(turn["user_id"]), source_turn_id=str(turn["turn_id"]),
            )
        except Exception:
            _LOGGER.exception("could not promote the queued input of turn %s", turn.get("turn_id"))

    def _kernel_manages(self, turn: dict[str, object]) -> bool:
        with self._state_lock:
            return str(turn["turn_id"]) in self._kernel_turns

    def _plugin_service(self, engine, *, skill_library, mcp_service, hook_engine: HookEngine | None = None) -> PluginService:
        # The agent can install plugins from a turn, so the worker's service
        # must refuse host paths on a server exactly like the API's does.
        plugin_root = orin_paths().data / "plugins"
        return PluginService(
            engine, plugin_root=plugin_root, skill_library=skill_library, mcp_service=mcp_service,
            fetcher=PluginFetcher(plugin_root, remote_only=not self._capabilities.host_folders),
            hook_engine=hook_engine or self._hook_engine,
        )

    def _preflight_refusal(self, turn: dict[str, object]) -> str | None:
        """Why this turn must not run at all on this instance, if anything."""
        user_id = str(turn.get("user_id") or "")
        if self._capabilities.user_admin and not UserStore(self.store._engine).is_active(user_id):
            return "owner_inactive"
        local_root = turn.get("workspace_root_path")
        if self._capabilities.profile_files and isinstance(local_root, str) and local_root.strip():
            try:
                outside = relative_display(orin_paths().user_files(user_id), local_root) is None
            except ValueError:
                outside = True
            if outside:
                return "workspace_unavailable"
        return None

    def _refuse(self, turn: dict[str, object], code: str) -> None:
        # Close the canonical execution too, so a refused turn never stays
        # QUEUED for a sweeper to pick up again. QUEUED only reaches FAILED
        # through STARTING. Best-effort: the chat read model is what the user
        # sees, and it is finished regardless.
        try:
            self._project(turn, "STARTING", "worker_acquired")
            self._project(turn, "FAILED", code)
        except Exception:
            _LOGGER.exception("execution for refused chat turn %s could not be closed", turn.get("turn_id"))
        self.store.finish(turn, failed=True, code=code)

    def _project(self, turn: dict[str, object], target: str, reason: str, result_ref: str | None = None) -> None:
        """Commit the canonical execution lifecycle before the provider runs.

        The method name is retained temporarily for test and call-site
        compatibility.  It is no longer a lossy best-effort projection: the
        initial transitions gate provider execution and terminal transition
        failures surface to the worker for recovery/alerting.
        """
        self._transition(turn, target, reason, result_ref=result_ref)

    def _transition(self, turn: dict[str, object], target: str, reason: str, result_ref: str | None = None) -> None:
        view = self._queries.get({"resource_id": turn["execution_id"], "user_id": turn["user_id"], "purpose": "execution.read"})
        if view["state"] == target:
            return
        self._executions.transition({"execution_id": turn["execution_id"], "user_id": turn["user_id"], "target_state": target, "reason_code": reason, "result_ref": result_ref, "expected_state_version": view["state_version"], "idempotency_key": f"chat-worker:{turn['turn_id']}:{target}", "requested_at": datetime.now(UTC)})

    def _journal_scope(self, turn: dict[str, object]) -> ExecutionJournalScope:
        return ExecutionJournalScope(
            execution_id=str(turn["execution_id"]), user_id=str(turn["user_id"]),
            workspace_id=(str(turn["project_workspace_id"]) if turn.get("project_workspace_id") else None),
            agent_id=str(self.store.main_agent_id(turn)), correlation_id=f"corr_{turn['execution_id']}",
        )

    def _effect_started(self, turn: dict[str, object], *, kind: str, invocation_ref: str, request_ref: str) -> str:
        scope = self._journal_scope(turn)
        now = datetime.now(UTC)
        effect_id = f"eff_{uuid4().hex}"
        effect = self._journal.prepare(ExecutionEffect(
            effect_id=effect_id, scope=scope, kind=ExecutionEffectKind(kind.upper()),
            invocation_ref=invocation_ref, request_ref=request_ref,
            idempotency_key=f"{turn['execution_id']}:{invocation_ref}",
            state=ExecutionEffectState.PREPARED, retryability=ExecutionEffectRetryability.NEVER,
            attempt=1, prepared_at=now,
        ))
        self._checkpoint(turn, scope, next_decision=kind, is_safe=False, pending_effect_id=effect.effect_id)
        # This write is deliberately immediately before handing control to the
        # gateway. A crash after it is not retried automatically; recovery
        # classifies it as UNKNOWN unless a reconciler supplies a receipt.
        self._journal.mark_in_flight(effect.effect_id, scope, now=now)
        return effect.effect_id

    def _effect_finished(self, turn: dict[str, object], *, effect_id: str, state: str, result_ref: str | None, error_code: str | None, private_result) -> None:
        scope = self._journal_scope(turn)
        resolved = self._journal.resolve(
            effect_id, scope, state=ExecutionEffectState(state), now=datetime.now(UTC),
            result_ref=result_ref, error_code=error_code,
            private_result=private_result if isinstance(private_result, dict) else None,
        )
        if resolved.state is ExecutionEffectState.UNKNOWN:
            with self._state_lock:
                self._reconciliation_turns.add(str(turn["turn_id"]))
            return
        self._checkpoint(turn, scope, next_decision="continue", is_safe=True, pending_effect_id=None)

    def _checkpoint(self, turn: dict[str, object], scope: ExecutionJournalScope, *, next_decision: str, is_safe: bool, pending_effect_id: str | None) -> None:
        # Querying the canonical version makes a checkpoint auditable against
        # the exact lifecycle decision it can resume from. The journal itself
        # only stores references, never prompt, credentials or raw tool args.
        view = self._queries.get({"resource_id": turn["execution_id"], "user_id": turn["user_id"], "purpose": "execution.read"})
        sequence = self._journal.next_checkpoint_sequence(scope)
        self._journal.save_checkpoint(ExecutionCheckpoint(
            checkpoint_id=f"chk_{uuid4().hex}", scope=scope, sequence=sequence,
            execution_state_version=int(view["state_version"]),
            context_manifest_ref=f"conversation-turn:{turn['turn_id']}:transcript",
            next_decision=next_decision, is_safe=is_safe, pending_effect_id=pending_effect_id,
            snapshot={"turn_id": str(turn["turn_id"]), "assistant_message_id": str(turn["assistant_message_id"])},
            created_at=datetime.now(UTC),
        ))

    def _reconciliation_required(self, turn: dict[str, object]) -> bool:
        with self._state_lock:
            return str(turn["turn_id"]) in self._reconciliation_turns

    def watchdog(self, *, maximum_age: timedelta = timedelta(seconds=30)) -> tuple[str, ...]:
        """Mark unacquired jobs terminal so the UI never waits indefinitely."""
        now = datetime.now(UTC)
        expired: list[str] = []
        with self.store._engine.connect() as c:
            active_convs = {
                str(cid)
                for cid in c.execute(
                    select(conversation_turns.c.conversation_id)
                    .outerjoin(
                        conversation_dispatches,
                        conversation_dispatches.c.turn_id == conversation_turns.c.turn_id,
                    )
                    .where(
                        conversation_turns.c.state.in_(("running", "starting", "waiting_user"))
                        | (
                            (conversation_dispatches.c.state == "active")
                            & conversation_turns.c.state.in_(("queued", "starting", "running"))
                        )
                    )
                ).scalars()
            }
            hb_updated = c.execute(
                select(runtime_heartbeats.c.updated_at).where(runtime_heartbeats.c.component == "chat-worker")
            ).scalar_one_or_none()
            if isinstance(hb_updated, datetime) and hb_updated.tzinfo is None:
                hb_updated = hb_updated.replace(tzinfo=UTC)
            worker_alive_and_busy = bool(
                active_convs
                and isinstance(hb_updated, datetime)
                and (now - hb_updated) < maximum_age
            )
            rows = c.execute(
                select(
                    conversation_dispatches.c.turn_id,
                    conversation_dispatches.c.queued_at,
                    conversation_turns.c.conversation_id,
                )
                .join(
                    conversation_turns,
                    conversation_turns.c.turn_id == conversation_dispatches.c.turn_id,
                )
                .where(conversation_dispatches.c.state.in_(("pending", "enqueued")))
            ).mappings().all()
        for row in rows:
            if str(row["conversation_id"]) in active_convs or worker_alive_and_busy:
                continue
            queued_at = row["queued_at"]
            if isinstance(queued_at, datetime) and queued_at.tzinfo is None:
                queued_at = queued_at.replace(tzinfo=UTC)
            if now - queued_at >= maximum_age:
                turn = self.store.claim(row["turn_id"])
                if turn is not None:
                    self.store.finish(turn, failed=True, code="worker_unavailable")
                    expired.append(str(row["turn_id"]))
        return tuple(expired)

    def recover_stale(self, *, maximum_age: timedelta) -> tuple[str, ...]:
        """Recover only executions whose journal proves no effect is in doubt."""
        recovered: list[str] = []
        for turn in self.store.stale_turns(maximum_age=maximum_age):
            scope = self._journal_scope(turn)
            effects = self._journal.mark_unresolved_unknown(scope, now=datetime.now(UTC))
            if any(effect.state is ExecutionEffectState.UNKNOWN for effect in effects):
                self._transition(turn, "PAUSED", "effect_reconciliation_required")
                self.store.pause_for_reconciliation(turn, code="EFFECT_RECONCILIATION_REQUIRED")
                continue
            # With no recorded external boundary, restarting from the durable
            # conversation input is safe. Once an effect has a terminal record,
            # a future resume adapter must consume its result reference; it is
            # never sent through the old fresh-turn loop.
            if self._journal.next_checkpoint_sequence(scope) == 1:
                self._transition(turn, "QUEUED", "worker_recovered_before_effect")
                if self.store.requeue_recovered(turn, reason="worker_recovered_before_effect"):
                    recovered.append(str(turn["turn_id"]))
            else:
                self._transition(turn, "PAUSED", "checkpoint_resume_required")
                self.store.pause_for_reconciliation(turn, code="CHECKPOINT_RESUME_REQUIRED")
        return tuple(recovered)

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

    def _catalog_pricing_for(self, turn: dict[str, object]) -> dict[str, object] | None:
        """The turn model's catalog prices, used to estimate cost when the provider reports none."""
        row = self._catalog_row_for(turn) or {}
        pricing = {key: row.get(key) for key in ("input_per_million", "output_per_million") if row.get(key) is not None}
        return pricing or None

    def _catalog_capabilities_for(self, turn: dict[str, object]) -> tuple[str, ...]:
        """The turn model's catalog capability names (reasoning, tools, thinking, ...); empty when unknown."""
        row = self._catalog_row_for(turn) or {}
        capabilities = row.get("capabilities") or ()
        if isinstance(capabilities, str):
            capabilities = [item.strip() for item in capabilities.split(",")]
        return tuple(str(item) for item in capabilities if str(item).strip())

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
        names = {str(item).lower() for item in modalities}
        # An unrefreshed catalog must not silently disable image reading (a
        # browser screenshot, an attached photo): only an explicit modality
        # list that omits "image" counts as "cannot see images". Mirrors
        # _model_calls_tools below for the same reason.
        return not names or "image" in names

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

    @staticmethod
    def _context_reserve_for(context_window: int) -> int:
        """Tokens held outside the trimmed message window.

        The system prompt, the tool schemas and the model's own reply all
        live there. A flat 12k is right for a small model and far too little
        for a large one carrying thirty schemas, so a big window reserves a
        proportion instead -- bounded, because ten percent of a million is
        a hundred thousand tokens spent on nothing.
        """
        proportional = -(-context_window * 10 // 100)  # ceil without importing math
        return min(CONTEXT_WINDOW_RESERVE_CEILING, max(CONTEXT_WINDOW_RESERVE_TOKENS, proportional))

    def _max_context_tokens_for(self, turn: dict[str, object]) -> int:
        """Derive the context-trim budget from the turn's actual model window.

        The provider catalog knows each model's real context window, and this
        budget now follows it. It used to be capped at a flat 60k for every
        model: a 200k model was trimmed to 60k and started compacting at
        ~49k, which is what made the agent lose what it had already read and
        re-run searches it had already run. A model with no catalogued window
        still falls back to the safe flat default rather than risking an
        oversized request.
        """
        context_window = self._context_window_for(turn)
        if context_window is None:
            return DEFAULT_MAX_CONTEXT_TOKENS
        return max(MIN_MAX_CONTEXT_TOKENS, context_window - self._context_reserve_for(context_window))

    def _num_ctx_for(self, turn: dict[str, object]) -> int:
        """The KV cache Ollama should allocate for this turn.

        Deliberately *not* derived from ``_max_context_tokens_for``. That
        budget is a bound on tokens sent to a remote provider; this one is
        real VRAM on the user's own machine, so it keeps the conservative
        flat ceiling even for a model whose window is far larger. Asking a
        262k local model for a 262k KV cache is what spills into system RAM
        and drops inference by 20-50x.
        """
        context_window = self._context_window_for(turn)
        if context_window is None:
            return OLLAMA_FALLBACK_NUM_CTX
        capped = max(MIN_MAX_CONTEXT_TOKENS, min(DEFAULT_MAX_CONTEXT_TOKENS, context_window - CONTEXT_WINDOW_RESERVE_TOKENS))
        return min(context_window, capped + CONTEXT_WINDOW_RESERVE_TOKENS)

    def _base_url_for(self, provider: str, credential) -> str:
        configured = str(credential.get("base_url") or "")
        if provider == "omniroute":
            return normalize_omniroute_base_url(configured or DEFAULT_OMNIROUTE_BASE_URL)
        if provider == "ollama":
            return normalize_ollama_base_url(configured or DEFAULT_OLLAMA_BASE_URL)
        return PROVIDER_BASE_URLS.get(provider, PROVIDER_BASE_URLS["openrouter"])

    def _transport_for(self, user_id: str, provider: str, model_id: str, num_ctx: int | None = None) -> HTTPProviderStreamTransport:
        """Build a transport for any (user, provider, model), not just the turn's own.

        This is the credential-handling seam a visual-reading model on a
        *different* provider (``_vision_reader_factory``) and a subagent's
        favorite-model override (``child_provider_factory``) go through, so a
        provider's credential is only ever looked up under its own name. It
        always uses the principal key (no fallback rotation): unlike the
        turn's own provider (``_provider_transport``), these calls are
        secondary reads a turn can already recover from without a key-swap.
        """
        with self.store._engine.connect() as c:
            credential = c.execute(select(provider_configurations.c.base_url, provider_configurations.c.enabled).where(provider_configurations.c.user_id == user_id, provider_configurations.c.provider == provider)).mappings().first()
        if credential is None or not credential["enabled"]:
            raise ValueError("provider unavailable")
        chosen = PostgresProviderApiKeyAdapter(self.store._engine, cipher=ProviderSecretCipher.from_environment(required=True)).next_available_key(user_id, provider)
        if chosen is None:
            if provider not in PROVIDERS_WITH_BASE_URL:
                raise ValueError("provider credential is missing")
            api_key = ""
        else:
            api_key = chosen.plaintext
        base_url = self._base_url_for(provider, credential)
        return HTTPProviderStreamTransport(provider=provider, base_url=base_url, api_key=api_key, model=model_id, num_ctx=num_ctx)

    def _provider_transport(self, turn: dict[str, object]) -> object:
        provider = str(turn["provider"])
        user_id = str(turn["user_id"])
        num_ctx = self._num_ctx_for(turn) if provider == "ollama" else None
        model_id = str(turn["model_id"])
        with self.store._engine.connect() as c:
            config = c.execute(
                select(provider_configurations.c.base_url, provider_configurations.c.enabled, provider_configurations.c.key_cooldown_seconds)
                .where(provider_configurations.c.user_id == user_id, provider_configurations.c.provider == provider)
            ).mappings().first()
        if config is None or not config["enabled"]:
            raise ValueError("provider unavailable")
        base_url = self._base_url_for(provider, {"base_url": config["base_url"]})
        keys = PostgresProviderApiKeyAdapter(self.store._engine, cipher=ProviderSecretCipher.from_environment(required=True))

        def build(api_key: str) -> HTTPProviderStreamTransport:
            return HTTPProviderStreamTransport(provider=provider, base_url=base_url, api_key=api_key, model=model_id, num_ctx=num_ctx)

        if provider not in PROVIDERS_WITH_BASE_URL:
            # configure()'s validation never lets a required-key provider stay
            # enabled with zero keys, so there is no need to spend a query
            # confirming one exists before wrapping -- MultiKeyProviderStreamTransport
            # already raises its own clear error if that invariant is ever violated.
            return MultiKeyProviderStreamTransport(
                key_pool=keys, user_id=user_id, provider=provider,
                cooldown_seconds=int(config["key_cooldown_seconds"]), transport_factory=build,
            )
        if keys.next_available_key(user_id, provider) is None:
            return build("")
        return MultiKeyProviderStreamTransport(
            key_pool=keys, user_id=user_id, provider=provider,
            cooldown_seconds=int(config["key_cooldown_seconds"]), transport_factory=build,
        )

    def _allowed_child_providers(self, connection, turn: dict[str, object]) -> set[str]:
        allowed = {str(turn.get("provider") or "")}
        try:
            enabled_rows = connection.execute(
                select(provider_configurations.c.provider).where(
                    provider_configurations.c.user_id == turn["user_id"],
                    provider_configurations.c.enabled.is_(True),
                )
            ).all()
            for row in enabled_rows:
                prov = str(getattr(row, "provider", "") or "")
                if prov:
                    allowed.add(prov)
        except Exception:
            pass
        allowed.discard("")
        return allowed

    def _favorite_child_model_ids(self, turn: dict[str, object]) -> tuple[str, ...]:
        """Return this user's non-batch favorites across the active turn provider and enabled providers."""
        try:
            with self.store._engine.connect() as connection:
                allowed_providers = self._allowed_child_providers(connection, turn)
                if not allowed_providers:
                    return ()
                rows = connection.execute(
                    select(
                        provider_model_catalog.c.model_id,
                        provider_model_catalog.c.provider,
                    )
                    .join(
                        provider_model_favorites,
                        (provider_model_favorites.c.user_id == provider_model_catalog.c.user_id)
                        & (provider_model_favorites.c.provider == provider_model_catalog.c.provider)
                        & (provider_model_favorites.c.model_id == provider_model_catalog.c.model_id),
                    )
                    .where(
                        provider_model_catalog.c.user_id == turn["user_id"],
                        provider_model_catalog.c.provider.in_(sorted(allowed_providers)),
                    )
                    .order_by(provider_model_catalog.c.display_name, provider_model_catalog.c.model_id)
                ).all()
        except Exception:
            return ()
        active_provider = str(turn.get("provider") or "")
        ordered_rows = sorted(
            rows,
            key=lambda r: (0 if str(r.provider) == active_provider else 1),
        )
        seen: list[str] = []
        for row in ordered_rows:
            mid = str(row.model_id or "").strip()
            if not mid or mid.lower().endswith(":batch"):
                continue
            if mid not in seen:
                seen.append(mid)
        return tuple(seen)

    def _is_favorite_child_model(self, turn: dict[str, object], model_id: str) -> bool:
        """Authorize a model at execution time against the durable user catalog."""
        raw = str(model_id or "").strip()
        if not raw or raw.lower().endswith(":batch"):
            return False
        favorites = self._favorite_child_model_ids(turn)
        resolved = resolve_child_model_id(raw, str(turn.get("model_id") or ""), favorites) or raw
        if resolved.lower().endswith(":batch"):
            return False
        if resolved in favorites:
            return True
        try:
            with self.store._engine.connect() as connection:
                allowed_providers = self._allowed_child_providers(connection, turn)
                if not allowed_providers:
                    return False
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
                        provider_model_catalog.c.provider.in_(sorted(allowed_providers)),
                        provider_model_catalog.c.model_id == resolved,
                    )
                ).scalar_one_or_none() is not None
        except Exception:
            return False

    def _child_provider_for_turn(self, turn: dict[str, object], model_id: str) -> HTTPProviderStreamTransport:
        favorites = self._favorite_child_model_ids(turn)
        raw_model_id = str(model_id or "").strip()
        resolved_model_id = (
            resolve_child_model_id(
                raw_model_id,
                str(turn.get("model_id") or ""),
                favorites,
            )
            or raw_model_id
        )
        target_provider = str(turn["provider"])
        try:
            with self.store._engine.connect() as connection:
                allowed_providers = self._allowed_child_providers(connection, turn)
                rows = connection.execute(
                    select(provider_model_favorites.c.provider)
                    .where(
                        provider_model_favorites.c.user_id == turn["user_id"],
                        provider_model_favorites.c.model_id == resolved_model_id,
                        provider_model_favorites.c.provider.in_(sorted(allowed_providers)),
                    )
                ).all()
                matching_providers = [str(r.provider) for r in rows if r.provider]
                if matching_providers and target_provider not in matching_providers:
                    target_provider = matching_providers[0]
        except Exception:
            pass
        return self._transport_for(
            str(turn["user_id"]),
            target_provider,
            resolved_model_id,
            num_ctx=self._num_ctx_for({**turn, "provider": target_provider, "model_id": resolved_model_id})
            if target_provider == "ollama"
            else None,
        )

    def _media_policy_for_turn(self, turn: dict[str, object]):
        from agentos.provider_catalog.modalities import SameProviderMediaModelPolicy

        active_provider = str(turn.get("provider") or "").strip().lower()
        if not active_provider:
            return SameProviderMediaModelPolicy.from_catalog_models(turn_provider="", favorite_models=())
        try:
            with self.store._engine.connect() as connection:
                rows = connection.execute(
                    select(
                        provider_model_catalog.c.provider,
                        provider_model_catalog.c.model_id,
                        provider_model_catalog.c.output_modalities,
                    )
                    .join(
                        provider_model_favorites,
                        (provider_model_favorites.c.user_id == provider_model_catalog.c.user_id)
                        & (provider_model_favorites.c.provider == provider_model_catalog.c.provider)
                        & (provider_model_favorites.c.model_id == provider_model_catalog.c.model_id),
                    )
                    .where(
                        provider_model_catalog.c.user_id == turn["user_id"],
                        provider_model_catalog.c.provider == active_provider,
                    )
                    .order_by(provider_model_catalog.c.display_name, provider_model_catalog.c.model_id)
                ).all()
        except Exception:
            rows = []
        favorites: list[tuple[str, str, tuple[str, ...]]] = []
        for row in rows:
            mid = str(row.model_id or "").strip()
            if not mid or mid.lower().endswith(":batch"):
                continue
            raw_mods = row.output_modalities or ()
            if isinstance(raw_mods, str):
                mods = tuple(part.strip() for part in raw_mods.split(",") if part.strip())
            else:
                mods = tuple(str(part).strip() for part in raw_mods if str(part).strip())
            favorites.append((str(row.provider or active_provider), mid, mods))
        return SameProviderMediaModelPolicy.from_catalog_models(
            turn_provider=active_provider,
            favorite_models=favorites,
        )

    def _media_client_for_turn(self, turn: dict[str, object]):
        from agentos.agentic.media_client import MediaGenerationClient

        def build() -> MediaGenerationClient | None:
            provider = str(turn.get("provider") or "").strip().lower()
            user_id = str(turn.get("user_id") or "").strip()
            if not provider or not user_id:
                return None
            with self.store._engine.connect() as c:
                credential = c.execute(
                    select(provider_configurations.c.base_url, provider_configurations.c.enabled)
                    .where(
                        provider_configurations.c.user_id == user_id,
                        provider_configurations.c.provider == provider,
                    )
                ).mappings().first()
            if credential is None or not credential["enabled"]:
                return None
            chosen = PostgresProviderApiKeyAdapter(
                self.store._engine,
                cipher=ProviderSecretCipher.from_environment(required=True),
            ).next_available_key(user_id, provider)
            if chosen is None:
                if provider not in PROVIDERS_WITH_BASE_URL:
                    return None
                api_key = ""
            else:
                api_key = chosen.plaintext
            base_url = self._base_url_for(provider, credential)
            return MediaGenerationClient(provider=provider, base_url=base_url, api_key=api_key)

        return build

    def _runtime_for(self, turn: dict[str, object]) -> AgenticTurnRuntime:
        if self._runtime_factory is not None:
            return self._runtime_factory(turn)
        engine = self.store._engine
        skill_library = PostgresSkillLibraryService(engine)
        configured_limits = self._runtime_settings.get(str(turn["user_id"]))
        turn = self._hydrate_code_mode(turn)
        local_root = turn.get("workspace_root_path")
        try:
            retrieval_bundle = self._retrieval_registry.acquire(
                workspace_id=resolve_effective_workspace_id(turn),
                local_root=local_root if isinstance(local_root, str) else None,
            )
        except Exception:
            # A malformed turn record must not cost the turn its code search
            # any more loudly than it already costs everything else below.
            _LOGGER.exception("could not acquire the retrieval index for %s", turn.get("conversation_id"))
            retrieval_bundle = None
        mcp_service = McpServerService(engine, allow_stdio=self._capabilities.mcp_stdio)
        turn_hook_engine = HookEngine(enabled=self._capabilities.plugin_hooks)
        plugin_service = self._plugin_service(
            engine,
            skill_library=skill_library,
            mcp_service=mcp_service,
            hook_engine=turn_hook_engine,
        )
        # Refresh this user's active, consented hooks into a turn-local
        # HookEngine so concurrent turns never overwrite each other's index.
        try:
            rehydrate_hooks(plugin_service, turn_hook_engine, user_id=str(turn["user_id"]))
        except Exception:
            _LOGGER.exception("could not rehydrate plugin hooks for %s", turn["user_id"])
        # A broken MCP configuration must never stop a turn from running.
        try:
            mcp_bundles = mcp_service.active_servers(str(turn["user_id"]))
        except Exception:
            _LOGGER.exception("could not load the MCP servers for %s", turn["user_id"])
            mcp_bundles = []
        mcp_provider = McpToolProvider(mcp_bundles, oauth=McpOAuth(engine)) if mcp_bundles else None
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
            enable_terminal=self._capabilities.shell,
            cancelled=self._is_turn_cancelled,
            file_history_root=orin_paths().data / "file-history",
            reconciliation_required=self._reconciliation_required,
            limits=AgenticLimits(
                deadline=TURN_DEADLINE,
                max_iterations=configured_limits["max_iterations"],
                max_actions=None if configured_limits["max_iterations"] is None else max(24, configured_limits["max_iterations"] * 2),
                max_context_tokens=self._max_context_tokens_for(turn),
                context_window_tokens=self._context_window_for(turn),
                subagent_max_iterations=configured_limits.get("subagent_max_iterations", configured_limits["max_iterations"]),
                subagent_max_actions=(
                    None
                    if configured_limits.get("subagent_max_iterations", configured_limits["max_iterations"]) is None
                    else max(24, int(configured_limits.get("subagent_max_iterations", configured_limits["max_iterations"]) or 12) * 2)
                ),
                **_retry_limits(turn),
            ),
            skills=skill_library.registry_for(str(turn["user_id"]), agent_id=str(self.store.main_agent_id(turn))),
            skill_library=skill_library,
            skill_load_recorder=lambda loaded: skill_library.record_load(
                user_id=str(turn["user_id"]), execution_id=str(turn["execution_id"]),
                agent_id=str(self.store.main_agent_id(turn)), loaded=loaded,
            ),
            search_client=search_client_from_environment(),
            retrieval_bundle=retrieval_bundle,
            browser=None,
            enable_subagents=self._enable_subagents,
            model_sees_images=self._model_sees_images(turn),
            model_calls_tools=self._model_calls_tools(turn),
            vision_reader_factory=self._vision_reader_factory(turn),
            child_model_ids=self._favorite_child_model_ids(turn),
            child_model_authorizer=lambda model_id: self._is_favorite_child_model(turn, model_id),
            child_provider_factory=lambda model_id: self._child_provider_for_turn(turn, model_id),
            mcp_provider=mcp_provider,
            mcp_service=mcp_service,
            plugin_service=plugin_service,
            hook_engine=turn_hook_engine,
            project_store=PostgresProjectStore(engine),
            chat_store=self.store,
            local_workspaces=PostgresLocalWorkspaceStore(engine),
            media_policy=self._media_policy_for_turn(turn),
            media_client=self._media_client_for_turn(turn),
            catalog_pricing=self._catalog_pricing_for(turn),
            model_capabilities=self._catalog_capabilities_for(turn),
            fallback_provider_factory=lambda model_id: self._provider_transport({**turn, "model_id": model_id}),
        )
        return session.build_runtime()

    def _hydrate_code_mode(self, turn: dict[str, object]) -> dict[str, object]:
        """Attach durable Code run policy to the otherwise generic chat turn."""
        if turn.get("code_mode") != "code":
            return turn
        try:
            with self.store._engine.begin() as connection:
                run = connection.execute(select(code_mode_runs).where(
                    code_mode_runs.c.execution_id == turn["execution_id"],
                    code_mode_runs.c.user_id == turn["user_id"],
                )).mappings().first()
                if run is None:
                    return {**turn, "interactive_permissions": bool(turn.get("interactive_permissions", True))}
                raw_perm = (
                    turn.get("permission_mode")
                    or turn.get("code_mode_permission_mode")
                    or run.get("autonomy")
                    or "accept_edits"
                )
                resolved_perm = normalize_permission_mode(str(raw_perm))
                resolved_autonomy = normalize_code_autonomy(resolved_perm)
                history = self.store.history_for_turn(turn)
                request_text = next((str(item.get("content") or "") for item in reversed(history) if item.get("role") == "user"), "")
                push_authorized = explicitly_authorizes_git_push(request_text)
                existing_plan = str(turn.get("code_mode_plan_path") or run.get("plan_path") or "").strip() or None
                if existing_plan is None and turn.get("conversation_id"):
                    prev_plan = connection.execute(
                        select(code_mode_runs.c.plan_path)
                        .where(
                            code_mode_runs.c.conversation_id == turn["conversation_id"],
                            code_mode_runs.c.user_id == turn["user_id"],
                            code_mode_runs.c.plan_path.is_not(None),
                        )
                        .order_by(code_mode_runs.c.created_at.desc())
                    ).scalar_one_or_none()
                    if isinstance(prev_plan, str) and prev_plan.strip():
                        existing_plan = prev_plan.strip()
                plan_path: str | None = (
                    existing_plan or f".orin/plans/{turn['turn_id']}-plan.md"
                    if resolved_perm is CodePermissionMode.PLAN_MODE
                    else existing_plan
                )
                stage = (
                    CodeStage.PLANNING
                    if resolved_perm is CodePermissionMode.PLAN_MODE or plan_path is not None
                    else CodeStage.IMPLEMENTING
                )
                connection.execute(update(code_mode_runs).where(code_mode_runs.c.run_id == run["run_id"]).values(
                    autonomy=resolved_autonomy.value,
                    stage=stage.value,
                    plan_path=plan_path,
                    updated_at=datetime.now(UTC),
                ))
            enriched = dict(turn)
            enriched.update({
                "permission_mode": resolved_perm.value,
                "code_mode_permission_mode": resolved_perm.value,
                "code_mode_work_kind": str(run["work_kind"]),
                "code_mode_autonomy": resolved_autonomy.value,
                "code_mode_push_authorized": push_authorized,
                "code_mode_plan_path": plan_path,
                "interactive_permissions": bool(turn.get("interactive_permissions", True)),
            })
            if stage is CodeStage.PLANNING:
                self.store._activity(enriched, AgentActivityEventType.CODE_MODE_STAGE_CHANGED, "Planejando entrega de código", {
                    "stage": CodeStage.PLANNING.value, "work_kind": str(run["work_kind"]),
                    "autonomy": resolved_autonomy.value, "push_authorized": push_authorized,
                })
            return enriched
        except Exception:
            _LOGGER.exception("could not hydrate Code mode for turn %s", turn.get("turn_id"))
            return turn


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
