"""Durable provider/action turn loop."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from itertools import count
import inspect
import json
import os
import re
import threading
import time
from typing import Callable, Mapping

from agentos.providers.models import ProviderError, ProviderErrorCategory, Retryability

from .action_loop import ActionLoop, MalformedToolCall
from .provider_stream import ANTHROPIC_REQUIRED_MAX_TOKENS, NormalizedStreamItem, StreamKind
from .contract import ContractError, TaskContract, parse as parse_contract, synthesize as synthesize_contract
from .quality import TurnQualityCounters
from .learning import LearnedMemory, TurnLearningLedger
from . import phases, transcript
from .phases import DEFAULT_PHASE_BUDGETS, Phase, PhaseBudget, PhaseController
from .lifecycle import HookEvent, LifecycleBus
from .loop_settings import BudgetMeter
from .tools.public_names import public_name as _public_tool_name
from .reminders import ReminderQueue, render_reminders, system_reminder
from .retry import backoff_delay, is_retryable, retry_after_of, status_code_of
from .compaction import (
    COMPACTION_HEADER,
    COMPACTION_HEADING,
    COMPACTION_INSTRUCTION,
    COMPACTION_SECTIONS,
    MICROCOMPACT_MARKER,
    compaction_message,
)

# Kept for callers written when Code mode had its own headings: there is one set now.
CODE_COMPACTION_SECTIONS = COMPACTION_SECTIONS
CODE_COMPACTION_INSTRUCTION = COMPACTION_INSTRUCTION

MAX_PARALLEL_TOOLS = 8
MAX_PARALLEL_SUBAGENT_TOOLS = max(8, int(os.environ.get("ORIN_MAX_CONCURRENT_SUBAGENTS", "16") or "16"))
AGED_TOOL_RESULT_CHARS = 1600
RECENT_UNAGED_TOOL_RESULTS = 8
MAX_STREAM_KEEPALIVE_STALL_SECONDS = 180.0
MICROCOMPACT_TOOL_RESULT_CHARS = 3_000
MICROCOMPACT_THRESHOLD = 0.65
# Results shorter than this cost less than the marker that would replace them.
MICROCOMPACT_MIN_CHARS = 200
# Autocompact fires at the window minus room for one full reply and a fixed margin.
AUTOCOMPACT_OUTPUT_RESERVE_TOKENS = 20_000
AUTOCOMPACT_MARGIN_TOKENS = 13_000

# Providers whose prompt cache this runtime can actually address. For these the
# message list is append-only, because prompt caching keys on an exact prefix:
# editing a message already sent invalidates every entry written at or after
# it. A cached token costs about a tenth of an ordinary input token, so keeping
# an old tool result whole and cached is cheaper than shrinking it and paying
# full price for the prefix the edit invalidated.
#
# For every other provider there is no cache to protect, shrinking is pure
# gain, and the previous per-iteration behaviour is kept.
PREFIX_CACHING_PROVIDERS = frozenset({"anthropic"})
CONTEXT_COMPACTION_THRESHOLD = 0.82
CONTEXT_COMPACTION_KEEP_UNITS = 6
BROWSER_TOOL_NAMES: frozenset[str] = frozenset()
CODE_MODE_CORE_TOOLS = frozenset({
    "read_file", "view_file", "transcribe_pdf", "write_file", "edit_file", "notebook_edit",
    "glob_files", "list_files", "search_files", "search_code", "project_map",
    "run_command", "powershell", "read_process_output", "stop_process", "verify_project",
    "enter_worktree", "exit_worktree", "preview_start", "preview_stop", "preview_logs",
    "enter_plan_mode", "exit_plan_mode",
    "todo_write", "task_create", "task_update", "task_get", "task_list",
    "ask_user", "run_subagent", "check_subagent", "fetch_url", "web_search",
    "remember", "recall",
    "search_skills", "list_skills", "read_skill_resource", "use_skill", "create_skill", "edit_skill",
    "list_mcp_catalog", "list_mcp_servers", "configure_mcp", "test_mcp_server",
    "search_mcp_tools", "inspect_mcp_server", "call_mcp_tool",
    "search_plugin", "inspect_plugin", "install_plugin", "list_plugins", "uninstall_plugin",
    "list_projects", "create_project_from_session", "move_session_to_project", "read_project_conversation",
    "generate_image", "generate_audio", "generate_video",
})

# Sentinel distinguishing "no pin resolved yet" from "resolved, and there is
# no user message to pin." Used only as the default for the ``_pinned_index``
# instance attribute, which ``run`` sets once per turn.
_PIN_UNSET = object()

# A compacted summary has one job: let the model keep working without going
# back to the tools. Free prose loses exactly the parts that make that
# possible -- the paths, the numbers, the decisions already taken -- so the
# summary is requested and rendered under fixed headings instead. The definitions live in compaction.py.


def strip_analysis_block(text: str) -> str:
    """Strip internal <analysis>...</analysis> scratchpad blocks from a compaction summary."""
    if not text:
        return ""
    cleaned = re.sub(r"<analysis>[\s\S]*?</analysis>", "", text, flags=re.IGNORECASE)
    return cleaned.strip()

CLOSING_INSTRUCTION = (
    "You have reached this turn's action budget. Do not request any more tools. "
    "Answer now with what you already accomplished, state plainly what is still missing, "
    "and say what the next step would be."
)

# How many times a failed ``report_verification`` sends the agent back to
# EXECUTE before the turn gives up and answers with caveats instead. Without
# a ceiling, a task whose acceptance criteria the model keeps failing to
# satisfy would loop until the turn's outer deadline, which is a worse
# failure mode than an honest "still broken" answer.
MAX_VERIFY_REPAIR_ROUNDS = 3
# How many times a Stop hook may send the model back to work in one turn.
MAX_STOP_CONTINUATIONS = 5
# A reply cut by the output limit is continued (text) or retried with a larger cap (a cut tool call),
# at most this many times per turn.
MAX_OUTPUT_RECOVERIES = 3
MAX_ESCALATED_OUTPUT_TOKENS = 32_000
CONTINUE_AFTER_OUTPUT_LIMIT = (
    "Your previous reply was cut off by the output length limit. Continue exactly where it stopped: "
    "do not repeat what you already wrote, do not apologize and do not summarize."
)
INTERRUPTED_TOOL_RESULT = "Interrupted by user: this tool call was not run."

VERIFY_REQUIRED_MESSAGE = (
    "[Sistema] Você alterou o workspace nesta tarefa e ainda não verificou o resultado. Antes de responder, "
    "confira o que mudou com as ferramentas desta etapa e conclua com `report_verification`."
)


def _repair_message(findings: str) -> str:
    detail = findings.strip() or "nenhum detalhe foi informado."
    return (
        f"[Sistema] A verificação não passou: {detail}\n"
        "Corrija o problema descrito e, quando terminar, a verificação roda de novo."
    )


def _elastic_execute_budget(contract: TaskContract, base: PhaseBudget) -> PhaseBudget:
    """Scale the EXECUTE budget with how much the contract actually asks for.

    ``base`` must be the fixed default, not a previously-adjusted budget: the
    result is recomputed from scratch whenever the contract changes (a
    rewrite can raise or lower its deliverable count), so basing it on
    anything but the true floor would compound on repeated calls.

    The default budget assumes an ordinary task; a contract with several
    declared deliverables is not one, and a fixed 20-iteration ceiling would
    force it into VERIFY (or past it) before the work is even done. The
    budget still exists to catch flailing, not to promise unlimited runway,
    hence the caps.
    """
    deliverables = len(contract.deliverables) or 1
    iterations = min(base.iterations + 3 * deliverables, 60)
    actions = min(base.actions + 8 * deliverables, 160)
    return PhaseBudget(iterations=iterations, actions=actions)


@dataclass(frozen=True, slots=True)
class AgenticLimits:
    max_actions: int | None = 8
    max_iterations: int | None = 8
    deadline: timedelta = timedelta(seconds=120)
    max_provider_retries: int = 1
    max_provider_tokens: int | None = None
    max_cost: Decimal | None = None
    # None means the turn asks for no output cap at all, so the model may use
    # whatever its own maximum is. Any cap here is a hard ceiling on a single
    # reply: a tool call that does not fit under it is cut off mid-call, which
    # the model cannot recover from beyond splitting the payload.
    max_output_tokens: int | None = None
    max_context_tokens: int = 60_000
    # The actual provider window, when the catalog knows it. The trim budget
    # above deliberately leaves room for system/tool overhead; this value is
    # what the UI should present as the model's full context window.
    context_window_tokens: int | None = None
    subagent_max_iterations: int | None = None
    subagent_max_actions: int | None = None
    # W3 (Code mode). None means no ceiling. ``retry_base_delay_seconds`` 0.0 keeps the old immediate retry.
    max_turns: int | None = None
    max_budget_usd: Decimal | None = None
    safety_max_tool_rounds: int | None = None
    retry_base_delay_seconds: float = 0.0
    retry_max_delay_seconds: float = 32.0
    max_retry_after_seconds: float = 60.0

    def __post_init__(self) -> None:
        if (self.max_actions is not None and self.max_actions < 0) or (self.max_iterations is not None and self.max_iterations < 1) or self.max_provider_retries < 0 or self.deadline <= timedelta(0):
            raise ValueError("agentic limits are invalid")
        if (self.subagent_max_actions is not None and self.subagent_max_actions < 0) or (self.subagent_max_iterations is not None and self.subagent_max_iterations < 1):
            raise ValueError("agentic limits are invalid")
        if (self.max_output_tokens is not None and self.max_output_tokens < 1) or self.max_context_tokens < 1_000 or (self.context_window_tokens is not None and self.context_window_tokens < 1_000):
            raise ValueError("agentic limits are invalid")
        if (self.max_turns is not None and self.max_turns < 1) or (self.safety_max_tool_rounds is not None and self.safety_max_tool_rounds < 1):
            raise ValueError("agentic limits are invalid")
        if self.max_budget_usd is not None and self.max_budget_usd < 0:
            raise ValueError("agentic limits are invalid")
        if self.retry_base_delay_seconds < 0 or self.retry_max_delay_seconds < 0 or self.max_retry_after_seconds < 0:
            raise ValueError("agentic limits are invalid")


@dataclass(frozen=True, slots=True)
class AgenticRunResult:
    state: str
    iterations: int = 0
    actions: int = 0
    error_code: str | None = None
    # True only when the run completed because it hit its final allowed
    # iteration (the tool_choice="none" closing turn), never on an ordinary
    # completion that finished with budget to spare.
    budget_exhausted: bool = False
    # How many queued messages from the person reached the model during this turn.
    queued_turn_count: int = 0


def _accepts_call_id(invoke: Callable[..., object]) -> bool:
    """Whether a toolset's invoke takes ``call_id``; older adapters do not."""
    try:
        parameters = inspect.signature(invoke).parameters
    except (TypeError, ValueError):
        return False
    return "call_id" in parameters or any(p.kind is inspect.Parameter.VAR_KEYWORD for p in parameters.values())


class AgenticTurnRuntime:
    def __init__(
        self,
        *,
        store: object,
        provider: object,
        actions: ActionLoop | None = None,
        limits: AgenticLimits | None = None,
        clock: Callable[[], datetime] | None = None,
        cancelled: Callable[[Mapping[str, object]], bool] | None = None,
        reconciliation_required: Callable[[Mapping[str, object]], bool] | None = None,
        toolset: object | None = None,
        system_prompt: str | None = None,
        volatile_prompt: str = "",
        tool_kinds: Mapping[str, str] | None = None,
        skill_prompt_tokens: int = 0,
        memory_prompt_tokens: int = 0,
        context_reporting: bool = False,
        hook_engine=None,
        phase_controller: PhaseController | None = None,
        learning_sink: Callable[[tuple[LearnedMemory, ...]], None] | None = None,
        lifecycle: LifecycleBus | None = None,
        prompt_block_reason: str | None = None,
        on_close: Callable[[], None] | None = None,
        reminders: ReminderQueue | None = None,
        cost_estimator: Callable[[int | None, int | None], object] | None = None,
        sleep: Callable[[float], None] | None = None,
        fallback_models: tuple[str, ...] | list[str] = (),
        fallback_provider_factory: Callable[[str], object] | None = None,
        first_user_context: str = "",
        autocompact: object = "auto",
        prompt_renderer: Callable[[], str] | None = None,
        compact_request: str | None = None,
        effort: str | None = None,
        effort_supported: Callable[[str], bool] | None = None,
        model_switched: Callable[[str], None] | None = None,
        budget_meter: BudgetMeter | None = None,
        on_completed: Callable[[], None] | None = None,
        config_block_reason: str | None = None,
    ) -> None:
        self.store, self.provider = store, provider
        # Set for a /compact turn: the turn only summarizes the conversation (instructions may be empty).
        self.compact_request = compact_request
        # Reasoning effort for this turn (already checked against the model); None sends nothing.
        self.effort = effort
        # Whether a model accepts the reasoning effort. A fallback that does not loses the effort (it would be rejected).
        self._effort_supported = effort_supported
        # Told which model a fallback switch moved to, so the cost of later calls is priced at that model.
        self._model_switched = model_switched
        # Called once after a turn that completed (not failed or cancelled); its errors never change the turn.
        self._on_completed = on_completed
        self.lifecycle = lifecycle
        # Set when a UserPromptSubmit hook refused the prompt: the turn ends before any model call.
        self.prompt_block_reason = prompt_block_reason
        # Set when a ConfigChange hook refused a settings change: the turn does not run until a hook accepts it.
        self.config_block_reason = config_block_reason
        # Releases what the turn owns outside the loop (its hook dispatcher); called once from close().
        self._on_close = on_close
        # Notes that reach the model as one <system-reminder> user message before the next request.
        self.reminders = reminders if reminders is not None else ReminderQueue()
        # Prices a model call when the provider reports no cost (most do not); feeds max_budget_usd.
        self._cost_estimator = cost_estimator
        # The turn's money, shared with its subagents: max_budget_usd is checked against this total when it is set.
        self._budget_meter = budget_meter
        # What the summarizer's calls cost when there is no meter (the summary is billed like any other call).
        self._summary_spent = Decimal("0")
        self._sleep = sleep or time.sleep
        # Models tried, in order, when the current one keeps failing; consumed as they are tried.
        self._fallback_models: list[str] = [str(model).strip() for model in fallback_models if str(model or "").strip()]
        self._fallback_provider_factory = fallback_provider_factory
        # Per-machine context kept out of the system prompt (excludeDynamicSystemPromptSections); see _request_messages.
        self.first_user_context = str(first_user_context or "")
        self.autocompact = autocompact
        # Renders the stable system prompt again after a compaction (Code mode); None keeps the current one.
        self._prompt_renderer = prompt_renderer
        self._stop_hook_active = False
        self._stop_continuations = 0
        self._max_tokens_recoveries = 0
        # Raised for the rest of the turn after a tool call was cut by the output limit.
        self._output_cap: int | None = None
        self._queued_turn_count = 0
        self.hook_engine = hook_engine
        # ``toolset`` is the agent-facing path: its results are returned to the
        # model verbatim. ``actions`` remains the policy-projected path used by
        # the tool-runtime contract tests, where results are summaries only.
        self.toolset = toolset
        # Present when the toolset speaks Claude Code tool names to the model.
        self._names = getattr(toolset, "name_translator", None)
        self.actions = actions or ActionLoop(None, None)
        self.limits = limits or AgenticLimits()
        self.clock = clock or (lambda: datetime.now(UTC))
        self.cancelled = cancelled or (lambda _turn: False)
        self.reconciliation_required = reconciliation_required or (lambda _turn: False)
        self.system_prompt = system_prompt
        # The half of the prompt that changes as the conversation moves. It
        # rides as its own system block so it stays out of the cached prefix;
        # a caller that passes only ``system_prompt`` gets the previous
        # behaviour, with the whole thing treated as stable.
        self.volatile_prompt = volatile_prompt or ""
        self.tool_kinds = dict(tool_kinds or {})
        self.skill_prompt_tokens = max(0, int(skill_prompt_tokens))
        self.memory_prompt_tokens = max(0, int(memory_prompt_tokens))
        self.context_reporting = bool(context_reporting)
        self._compaction_count = 0
        self._last_compaction: dict[str, int] | None = None
        self._last_compaction_block: str | None = None
        self._closed = False
        self.counters = TurnQualityCounters()
        self.ledger = TurnLearningLedger()
        self._learning_sink = learning_sink
        self._learning_committed = False
        self._started_at: datetime | None = None
        self._quality_recorded = False
        # The task the agent committed to. Held here rather than in the
        # message list so trimming and compaction cannot reach it: the one
        # thing that must never vanish mid-task is the definition of the task.
        self.contract: TaskContract | None = None
        self._rejected_contracts = 0
        # None keeps the previous single-stage behaviour, which every caller
        # that has not opted in (and every contract test) relies on.
        self.phases = phase_controller
        self.phase_controller = phase_controller
        self._first_request_text = ""
        # Per-turn tool bookkeeping. ``run`` resets these; they are also
        # initialised here so ``_run_toolset`` can be exercised on its own.
        self._failed_signatures: dict[str, str] = {}
        self._consecutive_tool_failures: dict[str, int] = {}
        self._succeeded_reads: dict[str, tuple[str, int]] = {}
        self._writes = 0

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        closer = getattr(self.toolset, "close", None)
        if callable(closer):
            try:
                closer()
            except Exception:
                pass
        if callable(self._on_close):
            try:
                self._on_close()
            except Exception:
                pass

    def _inject_reminders(self, messages: list[dict[str, object]]) -> None:
        """Pending reminders become one user message at the end of the history (append-only, cache-safe)."""
        try:
            pending = self.reminders.drain()
        except Exception:  # noqa: BLE001 - a reminder never breaks a turn
            return
        body = render_reminders(pending)
        if body:
            messages.append({"role": "user", "content": body, "_reminder": True})

    def _label(self, internal: str) -> str:
        """A tool as the model knows it (Claude Code name when the translator is on)."""
        return _public_tool_name(internal) if self._names is not None else internal

    def _labels(self, *internals: str) -> str:
        return ", ".join(self._label(name) for name in internals)

    def _inject_queued_inputs(self, turn: dict[str, object], messages: list[dict[str, object]]) -> None:
        """Text the person queued while tools ran joins the conversation now, through UserPromptSubmit."""
        claim = getattr(self.store, "claim_queued_inputs", None)
        if not callable(claim):
            return
        try:
            items = list(claim(turn) or ())
        except Exception:  # noqa: BLE001 - the queue never breaks a turn
            return
        for item in items:
            text = str((item or {}).get("content") or "").strip()
            queue_id = str((item or {}).get("queue_id") or "")
            if not text:
                continue
            decision = self._emit(HookEvent.USER_PROMPT_SUBMIT, prompt=text)
            if decision is not None and (decision.blocked or decision.stop):
                marker = getattr(self.store, "mark_queued_input_blocked", None)
                if callable(marker):
                    try:
                        marker(queue_id)
                    except Exception:  # noqa: BLE001
                        pass
                reason = decision.reason or decision.stop_reason or "blocked by hook"
                self.store.delta(turn, f"\n\n⚠️ Mensagem da fila bloqueada por um hook: {reason}\n\n")
                continue
            extra = [entry for entry in (decision.additional_context if decision is not None else []) if entry]
            content = f"{text}\n\n{system_reminder(chr(10).join(extra))}" if extra else text
            messages.append({"role": "user", "content": content})
            self._queued_turn_count += 1
            self._record_step(turn, kind=transcript.STEP_QUEUED_INPUT, payload={"content": text})
            self._life(turn, "queued_input", queued_text=text[:2000], queue_id=queue_id)

    def _check_external_changes(self, turn: dict[str, object]) -> None:
        """FileChanged for files the turn read that changed outside it; the model hears about it in a reminder."""
        detector = getattr(self.toolset, "detect_external_changes", None)
        if not callable(detector):
            return
        try:
            changes = list(detector())[:20]
        except Exception:  # noqa: BLE001
            return
        for relative, absolute, event in changes:
            self._emit(HookEvent.FILE_CHANGED, file_path=absolute, event=event)
            if event == "unlink":
                note = f"{relative} was deleted since you read it."
            else:
                note = f"{relative} changed on disk since you last read it (not through your file tools). Read it again before you edit it."
            self.reminders.add("file_changed", note, key=f"file_changed:{relative}:{event}")

    def _wait(self, turn: dict[str, object], seconds: float) -> bool:
        """Sleep in short slices so a cancel is noticed; False when the turn was cancelled meanwhile."""
        remaining = max(0.0, float(seconds))
        while remaining > 1e-9:
            if self.cancelled(turn):
                return False
            step = min(0.25, remaining)
            self._sleep(step)
            remaining -= step
        return not self.cancelled(turn)

    def _retry_wait(self, turn: dict[str, object], attempt: int, error: object | None) -> str:
        """"retry" after waiting, "cancelled", or "exhausted" when the server asks for longer than allowed."""
        base = self.limits.retry_base_delay_seconds
        if base <= 0:
            return "retry"
        retry_after = retry_after_of(error) if isinstance(error, BaseException) else None
        if retry_after is not None and retry_after > self.limits.max_retry_after_seconds:
            return "exhausted"
        delay = backoff_delay(attempt, base=base, maximum=self.limits.retry_max_delay_seconds, retry_after=retry_after)
        return "retry" if self._wait(turn, delay) else "cancelled"

    def _recover_provider_failure(self, turn: dict[str, object], error: object | None, *, retries_used: int, deadline: datetime, code: str) -> str:
        """Retry with backoff, then the fallback chain, then give up: "retry", "fallback", "cancel" or "fail"."""
        retryable = not isinstance(error, BaseException) or is_retryable(error)
        if retryable and retries_used < self.limits.max_provider_retries and self.clock() < deadline:
            self._life(turn, "retrying", attempt=retries_used + 1)
            waited = self._retry_wait(turn, retries_used + 1, error)
            if waited == "cancelled":
                return "cancel"
            if waited == "retry":
                return "retry"
        model_missing = isinstance(error, BaseException) and status_code_of(error) == 404
        if (retryable or model_missing) and self._switch_to_fallback(turn, reason=code):
            return "fallback"
        return "fail"

    def _add_spending(self, amount: object) -> None:
        if self._budget_meter is not None:
            self._budget_meter.add(amount)

    def _spent_usd(self, local_total: Decimal) -> Decimal:
        """The turn's spending so far: the shared meter when there is one, else this runtime's own calls and summaries."""
        return self._budget_meter.total if self._budget_meter is not None else local_total + self._summary_spent

    def _charge_summary(self, reported: Decimal | None, input_tokens: int | None, output_tokens: int | None) -> None:
        """Bill one summarizer call: the provider's reported cost, else the estimate, as the main loop does."""
        cost = reported
        if cost is None and self._cost_estimator is not None and (input_tokens is not None or output_tokens is not None):
            try:
                cost = self._cost_estimator(input_tokens, output_tokens)
            except Exception:  # noqa: BLE001 - pricing never breaks a compaction
                cost = None
        if cost is None:
            return
        try:
            amount = Decimal(str(cost))
        except (ArithmeticError, ValueError):
            return
        if not amount.is_finite() or amount <= 0:
            return
        if self._budget_meter is not None:
            self._budget_meter.add(amount)
        else:
            self._summary_spent += amount

    def _model_accepts_effort(self, model: str) -> bool:
        if self._effort_supported is None:
            return True
        try:
            return bool(self._effort_supported(model))
        except Exception:  # noqa: BLE001 - a model that cannot be checked gets no reasoning control
            return False

    def _switch_to_fallback(self, turn: dict[str, object], *, reason: str) -> bool:
        """Move to the next fallback model of the same provider. A PreModelSwitch block keeps the current one."""
        factory = self._fallback_provider_factory
        if factory is None:
            return False
        while self._fallback_models:
            candidate = self._fallback_models.pop(0)
            current = str(turn.get("model_id") or "")
            if not candidate or candidate == current:
                continue
            try:
                provider = factory(candidate)
            except Exception:  # noqa: BLE001 - an unbuildable fallback is skipped, the next one is tried
                provider = None
            if provider is None:
                continue
            decision = self._emit(HookEvent.PRE_MODEL_SWITCH, from_model=current, to_model=candidate, reason="fallback", error_code=reason)
            if decision is not None and (decision.blocked or decision.stop or decision.permission_decision == "deny"):
                closer = getattr(provider, "close", None)
                if callable(closer):
                    try:
                        closer()
                    except Exception:  # noqa: BLE001
                        pass
                return False
            previous, self.provider = self.provider, provider
            closer = getattr(previous, "close", None)
            if callable(closer):
                try:
                    closer()
                except Exception:  # noqa: BLE001
                    pass
            turn["model_id"] = candidate
            if self.effort and not self._model_accepts_effort(candidate):
                self.effort = None
            if self._model_switched is not None:
                try:
                    self._model_switched(candidate)
                except Exception:  # noqa: BLE001 - pricing never breaks a turn
                    pass
            self._life(turn, "model_switched", from_model=current, to_model=candidate, reason=reason)
            self._emit(HookEvent.POST_MODEL_SWITCH, from_model=current, to_model=candidate, reason="fallback", error_code=reason)
            return True
        return False

    def _recover_from_output_limit(
        self,
        turn: dict[str, object],
        effect_id: str | None,
        finish: object,
        text_parts: list[str],
        calls: Mapping[str, Mapping[str, str]],
        messages: list[dict[str, object]],
        request: Mapping[str, object],
        final_iteration: bool,
    ) -> bool:
        """A reply cut by max_tokens: a cut tool call is never run, a cut answer is continued. At most three times."""
        if final_iteration or finish is None or str(getattr(finish, "value", finish)).upper() != "LENGTH":
            return False
        if self._max_tokens_recoveries >= MAX_OUTPUT_RECOVERIES:
            return False
        text = "".join(text_parts)
        truncated_call = bool(calls) and any(not self._arguments_complete(call) for call in calls.values())
        if not truncated_call and (calls or not text.strip()):
            return False
        self._max_tokens_recoveries += 1
        self._effect_finished(turn, effect_id, state="APPLIED", private_result={"outcome": "max_tokens"})
        if text.strip():
            messages.append({"role": "assistant", "content": text})
        if truncated_call:
            raised = self._raise_output_cap(turn, request)
            cap_note = f" The output limit for this turn is now {raised} tokens." if raised else ""
            messages.append({"role": "user", "content": system_reminder(
                "Your last tool call was cut off by the output length limit before its arguments were complete, "
                f"so it was not run.{cap_note} Make the call again; if it carries a lot of content, split it into "
                "several smaller calls (write a long file in parts, or edit it in pieces)."
            )})
        else:
            messages.append({"role": "user", "content": system_reminder(CONTINUE_AFTER_OUTPUT_LIMIT)})
        return True

    @staticmethod
    def _arguments_complete(call: Mapping[str, str]) -> bool:
        try:
            return isinstance(json.loads(call.get("arguments") or "{}"), dict)
        except (TypeError, ValueError):
            return False

    def _raise_output_cap(self, turn: Mapping[str, object], request: Mapping[str, object]) -> int | None:
        """Double the reply cap (up to 32k) when the provider had one and the person set none."""
        if self.limits.max_output_tokens is not None:
            return None
        current = request.get("max_output_tokens")
        if current is None and str(turn.get("provider") or "").lower() == "anthropic":
            current = ANTHROPIC_REQUIRED_MAX_TOKENS
        if not isinstance(current, int) or isinstance(current, bool) or current <= 0:
            return None
        raised = min(MAX_ESCALATED_OUTPUT_TOKENS, current * 2)
        if raised <= current:
            return None
        self._output_cap = raised
        return raised

    def run(self, turn_id: str, *, turn: dict[str, object] | None = None) -> AgenticRunResult:
        try:
            result = self._run(turn_id, turn=turn)
        except BaseException:
            # An exception that escapes the loop is still a failed turn: exactly one StopFailure.
            self._emit(HookEvent.STOP_FAILURE, error_code="RUNTIME_EXCEPTION")
            raise
        if self._queued_turn_count:
            result = replace(result, queued_turn_count=self._queued_turn_count)
        return result

    def _run(self, turn_id: str, *, turn: dict[str, object] | None = None) -> AgenticRunResult:
        turn = turn or self._load(turn_id)
        # A successful read, and how many writes had happened when it ran. A
        # later identical read is only safe to serve from here if that count
        # has not moved: re-reading a file after editing it is necessary, not
        # redundant.
        self._succeeded_reads: dict[str, tuple[str, int]] = {}
        self._writes = 0
        # How many of the toolset's recorded change events have already been
        # covered by a completed verification pass, and how many repair
        # rounds a failed one has already spent. Both reset per turn: a new
        # turn's own changes are what it must account for, not a previous
        # turn's already-settled work.
        self._verified_at_change_events = 0
        self._verify_repair_rounds = 0
        self._pending_verification: Mapping[str, object] | None = None
        self._phase_transition_note: str | None = None
        self._started_at = self.clock()
        deadline = self._started_at + self.limits.deadline
        self._life(turn, "running")
        self._stop_hook_active = False
        self._stop_continuations = 0
        self._max_tokens_recoveries = 0
        self._output_cap = None
        self._queued_turn_count = 0
        if self.prompt_block_reason:
            self.store.delta(turn, f"⚠️ Mensagem bloqueada por um hook: {self.prompt_block_reason}")
            # W2-R6: erase the refused prompt before the turn finishes; if this fails the turn fails loudly
            # rather than letting the prompt reach the model on the next turn.
            excluder = getattr(self.store, "exclude_from_context", None)
            if callable(excluder):
                excluder(turn)
            self._life(turn, "completed")
            self.store.finish(turn, code="HOOK_BLOCKED_PROMPT")
            self._settle_quality(turn, "completed")
            return AgenticRunResult("completed", 0, 0, "HOOK_BLOCKED_PROMPT")
        if self.config_block_reason:
            self.store.delta(
                turn,
                "⚠️ Uma mudança de configuração foi bloqueada por um hook, então esta mensagem não foi executada: "
                f"{self.config_block_reason}. Desfaça a mudança ou ajuste o hook e envie a mensagem de novo.",
            )
            self._life(turn, "completed")
            self.store.finish(turn, code="HOOK_BLOCKED_CONFIG")
            self._settle_quality(turn, "completed")
            return AgenticRunResult("completed", 0, 0, "HOOK_BLOCKED_CONFIG")
        if self.compact_request is not None:
            return self._run_manual_compaction(turn)
        messages = list(self.store.history_for_turn(turn))
        action_count = 0
        self._failed_signatures: dict[str, str] = {}
        self._consecutive_tool_failures: dict[str, int] = {}
        # ``history_for_turn`` returns the whole conversation up to and
        # including this turn's request, so the current request is always the
        # last user message in this pre-loop snapshot. Resolving it once here
        # -- instead of re-deriving "the last user message" from the growing
        # ``messages`` list on every iteration -- keeps the pin from drifting
        # onto a later tool-result message (Anthropic emits those with
        # ``role: "user"`` too) as the loop appends to ``messages`` below.
        self._pinned_index = self._last_user_index(messages)
        # Kept for contract synthesis: if the model never manages to write a
        # contract, the person's own request becomes the objective.
        pinned_message = messages[self._pinned_index] if self._pinned_index is not None else None
        self._first_request_text = str((pinned_message or {}).get("content") or "")[:2_000]
        provider_retries = 0
        reactive_compacted = False
        code_gate_rejections = 0
        empty_response_retries = 0
        total_tokens = 0
        total_cost = Decimal("0")
        model_turns = 0
        tool_rounds = 0
        emitted_text_in_turn = False
        last_emitted_text_ended_with_newline = True
        continuing_text = False
        iterations = range(1, self.limits.max_iterations + 1) if self.limits.max_iterations is not None else count(1)
        for iteration in iterations:
            self.counters.note_iteration()
            if self.cancelled(turn):
                return self._cancel(turn, iteration, action_count)
            if self.clock() >= deadline:
                return self._fail(turn, "TURN_DEADLINE_EXCEEDED", iteration, action_count)
            remaining_tokens = None if self.limits.max_provider_tokens is None else self.limits.max_provider_tokens - total_tokens
            if remaining_tokens is not None and remaining_tokens <= 0:
                return self._fail(turn, "PROVIDER_TOKEN_LIMIT", iteration, action_count)
            if self.limits.max_turns is not None and model_turns >= self.limits.max_turns:
                return self._fail(turn, "MAX_TURNS", iteration, action_count)
            if self.limits.max_budget_usd is not None and self._spent_usd(total_cost) >= self.limits.max_budget_usd:
                return self._fail(turn, "MAX_BUDGET_USD", iteration, action_count)
            final_iteration = (
                (self.limits.max_iterations is not None and iteration == self.limits.max_iterations)
                or (self.phases is not None and self.phases.is_final)
            )
            self._check_external_changes(turn)
            self._inject_reminders(messages)
            tool_schemas = self._tool_schemas(turn)
            if self._names is not None:
                tool_schemas = self._names.schemas_out(tool_schemas)
            self._maybe_compact(messages, turn, tool_schemas)
            if self.cancelled(turn):
                return self._cancel(turn, iteration, action_count)
            window = self._request_messages(messages)
            if self._names is not None:
                window = self._names.messages_out(window)
            if self.phases is not None:
                # Last, so the stage the agent is in is the nearest thing to
                # the conversation rather than something it read paragraphs ago.
                # The tool list is named here, from the schemas actually being
                # sent this call, rather than as a static line in the cached
                # system prompt: a cached line cannot track a set that changes
                # every time the phase does within the same turn, and a wrong
                # one is worse than none -- it tells the model a tool is
                # callable when the request does not offer it.
                published = ", ".join(sorted(str(item.get("function", {}).get("name", "")) for item in tool_schemas)) or "nenhuma"
                phase_note = f"{phases.PHASE_INSTRUCTIONS[self.phases.current]}\nFerramentas disponíveis agora: {published}."
                window = [*window, {"role": "system", "content": phase_note}]
            if final_iteration:
                window = [*window, {"role": "system", "content": CLOSING_INSTRUCTION}]
            context = self._context_usage(messages, window, tool_schemas)
            if self.context_reporting:
                self._life(turn, "context_updated", **context)
            request = {
                "turn_id": turn_id, "provider": str(turn.get("provider", "")), "model": str(turn.get("model_id", "")),
                "messages": window, "tools": tool_schemas,
                "max_output_tokens": min([value for value in (self._output_cap or self.limits.max_output_tokens, remaining_tokens) if value is not None], default=None),
            }
            if self.effort:
                request["effort"] = self.effort
            if final_iteration:
                request["tool_choice"] = "none"
            provider_effect_id = self._effect_started(
                turn,
                kind="provider",
                invocation_ref=f"provider:{iteration}:{provider_retries + 1}",
                request_ref=f"conversation-turn:{turn_id}:provider:{iteration}",
            )
            # A failure while opening a stream is safe to retry because no
            # provider event has reached the runtime yet.  Keep this flag in
            # scope before calling ``stream``: some transports can raise while
            # constructing their iterator, before the loop below exists.
            provider_yielded = False
            try:
                events = self.provider.stream(request)
            except Exception as exc:
                self._effect_finished(
                    turn, provider_effect_id, state="NOT_APPLIED", error_code="PROVIDER_STREAM_NOT_STARTED"
                )
                if not reactive_compacted and self._is_context_length_error(exc) and self.clock() < deadline:
                    reactive_compacted = True
                    if self._force_compact(messages, turn, tool_schemas):
                        continue
                recovery = self._recover_provider_failure(turn, exc, retries_used=provider_retries, deadline=deadline, code="PROVIDER_STREAM_FAILED")
                if recovery == "retry":
                    provider_retries += 1
                    continue
                if recovery == "fallback":
                    provider_retries = 0
                    continue
                if recovery == "cancel":
                    return self._cancel(turn, iteration, action_count)
                return self._fail(turn, "PROVIDER_STREAM_FAILED", iteration, action_count)
            text_parts: list[str] = []
            thinking_parts: list[str] = []
            thinking_signature: str | None = None
            redacted_blocks: list[str] = []
            thinking_buffer: list[str] = []
            saw_thinking_kind = False
            last_thinking_flush = time.monotonic()
            tool_gen_last_emit: dict[str, float] = {}
            calls: dict[str, dict[str, str]] = {}
            input_tokens: int | None = None
            output_tokens: int | None = None
            reported_total_tokens: int | None = None
            finish = None
            retryable_error = False
            rate_limited = False
            context_overflow_error = False
            call_cost_reported = False

            def flush_thinking_buffer() -> None:
                nonlocal last_thinking_flush
                if not thinking_buffer:
                    return
                delta_text = "".join(thinking_buffer)
                thinking_buffer.clear()
                last_thinking_flush = time.monotonic()
                self._life(turn, "thinking_delta", delta=delta_text, iteration=iteration)

            last_progress_at = self.clock()
            try:
                for raw_event in events:
                    if self.cancelled(turn):
                        self._effect_finished(turn, provider_effect_id, state="UNKNOWN", error_code="PROVIDER_CANCELLED_DURING_EFFECT")
                        return self._cancel(turn, iteration, action_count)
                    now_clock = self.clock()
                    if now_clock >= deadline:
                        self._effect_finished(turn, provider_effect_id, state="UNKNOWN", error_code="PROVIDER_DEADLINE_DURING_EFFECT")
                        return self._fail(turn, "TURN_DEADLINE_EXCEEDED", iteration, action_count)
                    event = self._coerce(raw_event)
                    if event.kind is StreamKind.KEEPALIVE:
                        diff = now_clock - last_progress_at
                        elapsed_s = float(diff.total_seconds()) if hasattr(diff, "total_seconds") else float(diff)
                        if elapsed_s >= MAX_STREAM_KEEPALIVE_STALL_SECONDS:
                            raise ProviderError(
                                ProviderErrorCategory.TIMEOUT,
                                "PROVIDER_TIMEOUT",
                                "Provider stream stalled without tokens for over 180s",
                                Retryability.SAFE,
                            )
                        continue
                    provider_yielded = True
                    if event.kind in {StreamKind.TEXT, StreamKind.THINKING, StreamKind.TOOL_CALL, StreamKind.FINISH}:
                        last_progress_at = now_clock
                    if event.kind is StreamKind.THINKING:
                        saw_thinking_kind = True
                        if getattr(event, "signature", None):
                            thinking_signature = str(event.signature)
                        if getattr(event, "redacted_thinking", None):
                            redacted_blocks.append(str(event.redacted_thinking))
                        if event.thinking:
                            thinking_parts.append(event.thinking)
                            thinking_buffer.append(event.thinking)
                            buffered_chars = sum(len(part) for part in thinking_buffer)
                            if buffered_chars >= 60 or (time.monotonic() - last_thinking_flush) >= 0.12:
                                flush_thinking_buffer()
                        continue
                    if event.thinking and not saw_thinking_kind:
                        thinking_parts.append(event.thinking)
                        if self.toolset is not None:
                            thinking_buffer.append(event.thinking)
                            buffered_chars = sum(len(part) for part in thinking_buffer)
                            if buffered_chars >= 60 or (time.monotonic() - last_thinking_flush) >= 0.12:
                                flush_thinking_buffer()
                    if event.kind is StreamKind.TEXT and event.text:
                        flush_thinking_buffer()
                        chunk_text = event.text
                        if (
                            not text_parts
                            and emitted_text_in_turn
                            and not last_emitted_text_ended_with_newline
                            and not chunk_text.startswith("\n")
                            and not continuing_text
                        ):
                            chunk_text = "\n\n" + chunk_text
                        text_parts.append(chunk_text)
                        continuing_text = False
                        self.store.delta(turn, chunk_text)
                        emitted_text_in_turn = True
                        last_emitted_text_ended_with_newline = chunk_text.endswith("\n")
                    elif event.kind is StreamKind.TOOL_CALL and event.tool_call_id:
                        flush_thinking_buffer()
                        call_id = (
                            f"iter{iteration}-{event.tool_call_id}"
                            if event.tool_call_id.startswith("tool-call:") and iteration > 1
                            else event.tool_call_id
                        )
                        incoming_name = event.tool_name or ""
                        if incoming_name and self._names is not None:
                            incoming_name = self._names.name_in(incoming_name)
                        key_for = getattr(self.toolset, "key_for", None)
                        if incoming_name and callable(key_for):
                            # Every spelling the model may use becomes the stable key the rest of the loop compares.
                            incoming_name = key_for(incoming_name)
                        current = calls.setdefault(call_id, {"id": call_id, "name": incoming_name, "arguments": ""})
                        if incoming_name:
                            current["name"] = incoming_name
                        current["arguments"] += event.arguments_delta or ""
                        if self.toolset is not None and current["name"]:
                            now_mono = time.monotonic()
                            if call_id not in tool_gen_last_emit or (now_mono - tool_gen_last_emit[call_id]) >= 1.5:
                                tool_gen_last_emit[call_id] = now_mono
                                self._life(
                                    turn,
                                    "generating_tool_call",
                                    tool_name=current["name"],
                                    call_id=call_id,
                                    bytes_received=len(current["arguments"]),
                                    iteration=iteration,
                                )
                    elif event.kind is StreamKind.USAGE:
                        total_cost += event.cost or Decimal("0")
                        self._add_spending(event.cost)
                        if event.cost:
                            call_cost_reported = True
                        if event.usage:
                            # Some streaming protocols send input usage at the
                            # start and output usage at the end. Keep the latest
                            # known value per field and persist one provider-call
                            # total instead of adding cumulative snapshots.
                            input_tokens = event.usage.input_tokens if event.usage.input_tokens is not None else input_tokens
                            output_tokens = event.usage.output_tokens if event.usage.output_tokens is not None else output_tokens
                            reported_total_tokens = event.usage.total_tokens if event.usage.total_tokens is not None else reported_total_tokens
                            self.counters.note_usage(
                                input_tokens=event.usage.input_tokens,
                                output_tokens=event.usage.output_tokens,
                                cached_input_tokens=event.usage.cached_input_tokens,
                            )
                    elif event.kind is StreamKind.FINISH:
                        flush_thinking_buffer()
                        finish = event.finish_reason
                    elif event.kind is StreamKind.RATE_LIMIT:
                        rate_limited = True
                    elif event.kind is StreamKind.ERROR and event.error:
                        err_desc = f"{getattr(event.error, 'code', '')} {getattr(event.error, 'message', '')}"
                        if self._is_context_length_error(err_desc):
                            context_overflow_error = True
                        retryable_error = event.error.retryability.value == "SAFE"
                flush_thinking_buffer()
            except Exception as exc:
                if not reactive_compacted and not text_parts and not calls and self._is_context_length_error(exc) and self.clock() < deadline:
                    self._effect_finished(
                        turn, provider_effect_id,
                        state="NOT_APPLIED",
                        error_code="CONTEXT_LENGTH_EXCEEDED_COMPACTING",
                    )
                    reactive_compacted = True
                    if self._force_compact(messages, turn, tool_schemas):
                        continue
                is_keepalive_stall = isinstance(exc, ProviderError) and exc.category is ProviderErrorCategory.TIMEOUT and not text_parts and not calls
                effective_yielded = provider_yielded and not is_keepalive_stall
                # Once a stream yielded anything the request may have reached
                # the provider and charged or produced a result.  A restart
                # must reconcile it rather than issuing a duplicate request.
                self._effect_finished(
                    turn, provider_effect_id,
                    state="UNKNOWN" if effective_yielded else "NOT_APPLIED",
                    error_code="PROVIDER_STREAM_INTERRUPTED",
                )
                if effective_yielded:
                    self._emit(HookEvent.STOP_FAILURE, error_code="PROVIDER_STREAM_INTERRUPTED")
                    self._settle_quality(turn, "reconciliation_required", "PROVIDER_STREAM_INTERRUPTED")
                    return AgenticRunResult("reconciliation_required", iteration, action_count, "PROVIDER_STREAM_INTERRUPTED")
                recovery = self._recover_provider_failure(turn, exc, retries_used=provider_retries, deadline=deadline, code="PROVIDER_STREAM_FAILED")
                if recovery == "retry":
                    provider_retries += 1
                    continue
                if recovery == "fallback":
                    provider_retries = 0
                    continue
                if recovery == "cancel":
                    return self._cancel(turn, iteration, action_count)
                return self._fail(turn, "PROVIDER_STREAM_FAILED", iteration, action_count)
            # One provider call is over; fold its usage into the turn total
            # before the next call starts reporting its own.
            self.counters.settle_provider_call()
            model_turns += 1
            provider_total_tokens = reported_total_tokens
            if provider_total_tokens is None and input_tokens is not None and output_tokens is not None:
                provider_total_tokens = input_tokens + output_tokens
            usage_is_plausible = input_tokens is None or input_tokens <= self._maximum_plausible_input_tokens(request)
            if usage_is_plausible:
                total_tokens += provider_total_tokens or 0
                reporter = getattr(self.store, "record_usage", None)
                if callable(reporter) and any(value is not None for value in (input_tokens, output_tokens, provider_total_tokens)):
                    reporter(turn, input_tokens=input_tokens, output_tokens=output_tokens, total_tokens=provider_total_tokens)
                if not call_cost_reported and self._cost_estimator is not None and (input_tokens is not None or output_tokens is not None):
                    try:
                        estimated = self._cost_estimator(input_tokens, output_tokens)
                        if estimated is not None:
                            total_cost += Decimal(str(estimated))
                            self._add_spending(estimated)
                    except Exception:  # noqa: BLE001 - pricing never breaks a turn
                        pass
            if self.limits.max_provider_tokens is not None and total_tokens > self.limits.max_provider_tokens:
                self._effect_finished(turn, provider_effect_id, state="APPLIED", error_code="PROVIDER_TOKEN_LIMIT")
                return self._fail(turn, "PROVIDER_TOKEN_LIMIT", iteration, action_count)
            if self.limits.max_cost is not None and total_cost > self.limits.max_cost:
                self._effect_finished(turn, provider_effect_id, state="APPLIED", error_code="PROVIDER_COST_LIMIT")
                return self._fail(turn, "PROVIDER_COST_LIMIT", iteration, action_count)
            if context_overflow_error and not text_parts and not calls and not reactive_compacted and self.clock() < deadline:
                self._effect_finished(turn, provider_effect_id, state="NOT_APPLIED", error_code="CONTEXT_LENGTH_EXCEEDED_COMPACTING")
                reactive_compacted = True
                if self._force_compact(messages, turn, tool_schemas):
                    continue
            if (retryable_error or rate_limited) and not text_parts and not calls:
                can_retry = provider_retries < self.limits.max_provider_retries and self.clock() < deadline
                self._effect_finished(
                    turn, provider_effect_id, state="NOT_APPLIED",
                    error_code="PROVIDER_RETRY" if can_retry else "PROVIDER_RETRY_EXHAUSTED",
                )
                recovery = self._recover_provider_failure(turn, None, retries_used=provider_retries, deadline=deadline, code="PROVIDER_RETRY_EXHAUSTED")
                if recovery == "retry":
                    provider_retries += 1
                    continue
                if recovery == "fallback":
                    provider_retries = 0
                    continue
                if recovery == "cancel":
                    return self._cancel(turn, iteration, action_count)
                return self._fail(turn, "PROVIDER_RETRY_EXHAUSTED", iteration, action_count)
            if self._recover_from_output_limit(turn, provider_effect_id, finish, text_parts, calls, messages, request, final_iteration):
                continuing_text = not calls
                self._life(turn, "running")
                continue
            if (calls or (finish is not None and finish.value == "TOOL_CALLS")) and not final_iteration:
                self._effect_finished(
                    turn, provider_effect_id, state="APPLIED",
                    result_ref=f"conversation-turn:{turn_id}:provider:{iteration}",
                    private_result={"outcome": "tool_calls"},
                )
                self._life(turn, "waiting_tool", count=len(calls))
                if self.toolset is not None:
                    if self.limits.max_actions is not None and action_count + len(calls) > self.limits.max_actions:
                        return self._fail(turn, "ACTION_LIMIT", iteration, action_count)
                    results = self._run_toolset(turn, list(calls.values()))
                    if finish is not None and str(getattr(finish, "value", finish)).lower() == "length":
                        for result in results:
                            if str(result.get("status")) == "failed" and "truncated at max_tokens" not in str(result.get("content") or ""):
                                result["content"] = (
                                    f"{result.get('content') or ''} "
                                    "Hint: Ensure 'path' is provided first; if you are writing a large file, "
                                    "your output may have been truncated at max_tokens — write smaller modular "
                                    f"files or use {self._label('edit_file')} in chunks."
                                ).strip()
                    action_count += len(results)
                    self._advance_phase(turn, results)
                    tool_rounds += 1
                else:
                    try:
                        for _ in calls.values():
                            self._life(turn, "tool_started")
                        batch = self.actions.execute(list(calls.values()), turn, action_count=action_count, max_actions=self.limits.max_actions)
                    except MalformedToolCall:
                        self._life(turn, "tool_failed", code="MALFORMED_OR_DUPLICATE_TOOL_CALL")
                        return self._fail(turn, "MALFORMED_OR_DUPLICATE_TOOL_CALL", iteration, action_count)
                    action_count = batch.count
                    results = list(batch.results)
                    for call, result in zip(calls.values(), results):
                        try:
                            arguments = json.loads(call.get("arguments") or "{}")
                        except (TypeError, json.JSONDecodeError):
                            arguments = {}
                        if not isinstance(arguments, Mapping):
                            arguments = {}
                        name = str(call.get("name") or "tool")
                        status = str(result.get("status") or "failed")
                        self._life(
                            turn, "tool_finished", tool_name=name, invocation_id=str(call.get("id") or ""),
                            status=status, summary=str(result.get("summary") or f"{name} {status}"),
                            error_code=result.get("error_code"), result_ref=result.get("result_ref"),
                            tool_arguments=dict(arguments),
                        )
                messages.append(self._assistant_tool_message(
                    turn, text_parts, calls, thinking_parts,
                    thinking_signature=thinking_signature, redacted_thinking=redacted_blocks,
                ))
                # Record the trajectory so the *next* turn of this conversation
                # starts knowing what this one already read and wrote, instead
                # of rediscovering it.
                self._record_step(
                    turn, kind=transcript.STEP_ASSISTANT_TOOL_CALL,
                    payload=transcript.assistant_tool_call_payload("".join(text_parts), calls.values()),
                )
                for result in results:
                    self._record_step(
                        turn, kind=transcript.STEP_TOOL_RESULT,
                        payload=transcript.tool_result_payload(
                            call_id=str(result.get("id") or ""), name=str(result.get("name") or ""),
                            status=str(result.get("status") or ""), content=str(result.get("content") or ""),
                        ),
                        tool_name=str(result.get("name") or ""), tool_call_id=str(result.get("id") or ""),
                    )
                    messages.extend(self._tool_result_messages(turn, result))
                if self.cancelled(turn):
                    return self._cancel(turn, iteration, action_count)
                stop_reason = getattr(self.toolset, "stop_requested", None)
                if stop_reason:
                    self.store.delta(turn, f"\n\n⏹ Interrompido por hook: {stop_reason}")
                    self._life(turn, "completed")
                    self.store.finish(turn, code="HOOK_STOPPED")
                    self._settle_quality(turn, "completed")
                    return AgenticRunResult("completed", iteration, action_count, "HOOK_STOPPED")
                if max(self._consecutive_tool_failures.values(), default=0) >= 3:
                    return self._fail(turn, "REPEATED_TOOL_FAILURE", iteration, action_count)
                if self._phase_transition_note:
                    # Appended after every tool result this iteration produced,
                    # so it reads as a reaction to what the model just saw
                    # rather than preceding (and appearing to predict) it.
                    messages.append({"role": "user", "content": self._phase_transition_note})
                    self._phase_transition_note = None
                # Shrinking an already-sent result rewrites the prefix, which
                # is exactly what a prompt cache cannot survive. Where the
                # provider has a cache, this is deferred to compaction, whose
                # rewrite is unavoidable anyway; where it has none, shrinking
                # every iteration is still the cheapest thing to do.
                if not self._prefix_caching(turn):
                    self._age_tool_results(messages, keep_recent=max(RECENT_UNAGED_TOOL_RESULTS, len(results)))
                # ``ask_user`` deliberately ends this provider run.  A worker
                # must never stay blocked while a person considers a form, and
                # the next authenticated chat message starts the follow-up turn.
                if any(bool(result.get("wait_for_user")) for result in results):
                    self._life(turn, "waiting_user")
                    self._emit(HookEvent.STOP, stop_hook_active=False, stop_reason="waiting_user", last_assistant_message="".join(text_parts)[:4000])
                    self.store.finish(turn, code="WAITING_USER")
                    self._settle_quality(turn, "waiting_user")
                    return AgenticRunResult("waiting_user", iteration, action_count)
                if self.reconciliation_required(turn):
                    self._emit(HookEvent.STOP_FAILURE, error_code="EFFECT_RECONCILIATION_REQUIRED")
                    self._settle_quality(turn, "reconciliation_required", "EFFECT_RECONCILIATION_REQUIRED")
                    return AgenticRunResult("reconciliation_required", iteration, action_count, "EFFECT_RECONCILIATION_REQUIRED")
                if self.limits.safety_max_tool_rounds is not None and tool_rounds >= self.limits.safety_max_tool_rounds:
                    return self._pause_for_safety(turn, iteration, action_count)
                self._inject_queued_inputs(turn, messages)
                self._life(turn, "running")
                continue
            has_text = bool("".join(text_parts).strip())
            is_subagent = hasattr(self.store, "text") and hasattr(self.store, "agent_id")
            if not has_text and (not is_subagent or bool(thinking_parts) or (action_count > 0 and empty_response_retries < 2)):
                empty_response_retries += 1
                self._effect_finished(turn, provider_effect_id, state="APPLIED", private_result={"outcome": "empty"})
                if not final_iteration:
                    if thinking_parts and str(turn.get("provider", "")).lower() == "ollama":
                        messages.append({"role": "assistant", "content": "", "thinking": "".join(thinking_parts)})
                    messages.append({
                        "role": "user",
                        "content": (
                            "[Sistema] Sua resposta anterior veio sem texto e sem chamadas de ferramentas. "
                            f"Use as ferramentas disponíveis (como {self._labels('list_files', 'read_file', 'write_file', 'edit_file', 'run_command', 'verify_project')}) "
                            "para executar a tarefa ou responda diretamente ao usuário."
                        ),
                    })
                    self._life(turn, "running")
                continue
            if (finish is not None or has_text) and not (final_iteration and not has_text):
                completion_gate = getattr(self.toolset, "code_completion_gate", None)
                gate_ok, gate_reason = (True, None)
                if callable(completion_gate) and not turn.get("is_subagent") and (final_iteration or code_gate_rejections < 1):
                    try:
                        gate_ok, gate_reason = completion_gate()
                    except Exception:
                        # A quality ledger may improve a run, but an adapter
                        # fault must never make an otherwise valid turn hang.
                        gate_ok, gate_reason = True, None
                if not gate_ok:
                    self._life(turn, "code_validation_required", reason=gate_reason)
                    self._effect_finished(
                        turn, provider_effect_id, state="APPLIED",
                        result_ref=f"conversation-turn:{turn_id}:quality-gate:{iteration}",
                        private_result={"outcome": "code_validation_required"},
                    )
                    if final_iteration:
                        # The final provider response has already been streamed.
                        # Do not turn an otherwise deliverable answer into an
                        # opaque runtime error; attach the missing evidence as
                        # an explicit caveat and preserve it durably instead.
                        caveat = f"\n\nRessalva de verificação: {gate_reason}"
                        self.store.delta(turn, caveat)
                        self._settle_remaining_todos(turn, str(turn_id))
                        self._life(turn, "completed_with_caveats", reason=gate_reason)
                        self._emit(HookEvent.STOP, stop_hook_active=False, stop_reason="completed_with_caveats", last_assistant_message="".join(text_parts)[:4000])
                        self.store.finish(turn, code="CODE_VALIDATION_REQUIRED")
                        self._settle_quality(turn, "completed_with_caveats", "CODE_VALIDATION_REQUIRED")
                        return AgenticRunResult("completed_with_caveats", iteration, action_count, "CODE_VALIDATION_REQUIRED", budget_exhausted=True)
                    code_gate_rejections += 1
                    if has_text:
                        messages.append({"role": "assistant", "content": "".join(text_parts)})
                    messages.append({
                        "role": "user",
                        "content": f"[Sistema do Modo Code] {gate_reason} Continue a tarefa usando ferramentas; não responda como concluída.",
                    })
                    self._life(turn, "running")
                    continue
                if self._needs_verification() and not final_iteration:
                    # Independent of Code mode's stricter gate above: any turn
                    # that changed the workspace owes one look at what it did
                    # before it may answer. Skipped only when there is no
                    # budget left for another round -- the turn still
                    # completes, it just does so without that look.
                    self._effect_finished(
                        turn, provider_effect_id, state="APPLIED",
                        result_ref=f"conversation-turn:{turn_id}:verify-gate:{iteration}",
                        private_result={"outcome": "verification_required"},
                    )
                    if self.phases is not None:
                        previous_phase = self.phases.current
                        self.phases.force_verify()
                        self._life(turn, "phase_changed", phase=str(self.phases.current), previous_phase=str(previous_phase))
                    if has_text:
                        messages.append({"role": "assistant", "content": "".join(text_parts)})
                    messages.append({"role": "user", "content": VERIFY_REQUIRED_MESSAGE})
                    self._life(turn, "running")
                    continue
                stop_decision = self._emit(
                    HookEvent.STOP, stop_hook_active=self._stop_hook_active, stop_reason="completed",
                    last_assistant_message="".join(text_parts)[:4000],
                )
                if stop_decision is not None and stop_decision.blocked and not stop_decision.stop and self._stop_continuations < MAX_STOP_CONTINUATIONS:
                    # A Stop hook refused the answer: the model gets its reason and the turn goes on.
                    self._effect_finished(
                        turn, provider_effect_id, state="APPLIED",
                        result_ref=f"conversation-turn:{turn_id}:stop-hook:{iteration}",
                        private_result={"outcome": "stop_hook_blocked"},
                    )
                    self._stop_continuations += 1
                    self._stop_hook_active = True
                    if has_text:
                        messages.append({"role": "assistant", "content": "".join(text_parts)})
                    messages.append({"role": "user", "content": f"[Hook Stop] {stop_decision.reason or 'continue working'}"})
                    self._life(turn, "running")
                    continue
                self._effect_finished(
                    turn, provider_effect_id, state="APPLIED",
                    result_ref=f"conversation-message:{turn['assistant_message_id']}",
                    private_result={"outcome": "final"},
                )
                self._settle_remaining_todos(turn, str(turn_id))
                self._life(turn, "completed")
                self.store.finish(turn)
                self._settle_quality(turn, "completed")
                return AgenticRunResult("completed", iteration, action_count, budget_exhausted=final_iteration and (iteration > 1 or bool(calls)))
            self._effect_finished(turn, provider_effect_id, state="APPLIED", private_result={"outcome": "empty"})
        # Reaching this point means the loop ended without any provider answer
        # that carried text; a turn that produced text has already returned
        # "completed" above. This also covers a final iteration where the
        # provider ignored tool_choice="none" and returned only tool calls: the
        # model's requested tools are discarded rather than silently reported
        # as a completed turn.
        return self._fail(turn, "ITERATION_LIMIT", self.limits.max_iterations or 0, action_count)

    def _tool_schemas(self, turn: Mapping[str, object]) -> list[dict[str, object]]:
        if self.toolset is None:
            return self.actions.tool_schemas(turn)
        if self.phases is None:
            if getattr(self.toolset, "_code_mode_active", False) or turn.get("code_mode") == "code":
                allowed = set(CODE_MODE_CORE_TOOLS)
                active_mcp = getattr(self.toolset, "active_mcp_tool_names", ())
                if active_mcp:
                    allowed.update(active_mcp)
                try:
                    return self.toolset.schemas(allowed)
                except TypeError:
                    return self.toolset.schemas()
            return self.toolset.schemas()
        toolkits = self.contract.toolkits if self.contract is not None else None
        allowed = phases.tools_for(self.phases.current, toolkits)
        if not allowed:
            return []
        try:
            return self.toolset.schemas(allowed, phases.kinds_for(toolkits, phase=self.phases.current))
        except TypeError:
            # A toolset that predates phase-aware publication still works; it
            # simply publishes everything, as it did before.
            return self.toolset.schemas()

    def _context_usage(
        self,
        messages: list[dict[str, object]],
        window: list[dict[str, object]],
        tool_schemas: list[dict[str, object]],
    ) -> dict[str, object]:
        """Return bounded, provider-neutral context accounting for the UI.

        The counts use the same conservative four-characters-per-token estimate
        used by the trim algorithm. Provider-reported usage is intentionally not
        substituted here: it arrives after a request and cannot explain which
        local prompt component consumed the window.
        """
        full_system = self._estimated_tokens({"role": "system", "content": self.system_prompt}) if self.system_prompt else 0
        skills = self.skill_prompt_tokens
        tools = 0
        mcps = 0
        for schema in tool_schemas:
            name = str(((schema.get("function") or {}).get("name") if isinstance(schema.get("function"), Mapping) else "") or "")
            tokens = self._estimated_tokens(schema)
            kind = self.tool_kinds.get(name, "tool")
            if kind == "mcp":
                mcps += tokens
            elif kind == "skill":
                skills += tokens
            else:
                tools += tokens
        system_extras = sum(
            self._estimated_tokens(item) for item in window
            if item.get("role") == "system" and item.get("content") != self.system_prompt
        )
        system = max(0, full_system - self.skill_prompt_tokens) + system_extras
        # Instruction files and remembered facts live inside the system prompt; report them on their own.
        memory = min(self.memory_prompt_tokens, system)
        system -= memory
        pinned = getattr(self, "_pinned_index", None)
        pinned_message = messages[pinned] if isinstance(pinned, int) and 0 <= pinned < len(messages) else None
        input_tokens = 0
        history = 0
        for item in window:
            if item.get("role") == "system":
                continue
            tokens = self._estimated_tokens(item)
            if pinned_message is not None and item is pinned_message:
                input_tokens += tokens
            else:
                history += tokens
        used = system + memory + history + input_tokens + tools + skills + mcps
        limit = self.limits.context_window_tokens or self.limits.max_context_tokens
        kept_message_ids = {id(item) for item in window if item.get("role") != "system"}
        omitted = max(0, len(messages) - sum(1 for item in messages if id(item) in kept_message_ids))
        return {
            "used_tokens": used,
            "limit_tokens": limit,
            "percentage": min(100, round(used / limit * 100)) if limit else 0,
            "system_prompt_tokens": system,
            "history_tokens": history,
            "input_tokens": input_tokens,
            "tools_tokens": tools,
            "skills_tokens": skills,
            "mcps_tokens": mcps,
            "memory_tokens": memory,
            "free_tokens": max(0, limit - used),
            "omitted_messages": omitted,
            "compaction_count": self._compaction_count,
            "compaction_enabled": True,
        }

    def _prefix_caching(self, turn: Mapping[str, object]) -> bool:
        """Whether this turn's provider has a prompt cache worth protecting.

        A provider that reported cached input tokens at any point this turn
        counts as supporting it, which covers gateways that pass Anthropic's
        caching through under their own name.
        """
        if self.counters.cached_input_tokens:
            return True
        return str(turn.get("provider", "")).lower() in PREFIX_CACHING_PROVIDERS

    @staticmethod
    def _is_context_length_error(error: object) -> bool:
        text = str(error or "").lower()
        return any(
            needle in text
            for needle in (
                "context_length_exceeded",
                "prompt is too long",
                "maximum context length",
                "too many tokens",
                "context window",
                "request too large",
                "input is too long",
            )
        )

    def _is_code_compaction(self, turn: Mapping[str, object]) -> bool:
        if str(turn.get("code_mode") or "").strip().lower() == "code":
            return True
        return callable(getattr(self.toolset, "post_compact_enrichment", None))

    def _microcompact(
        self,
        messages: list[dict[str, object]],
        *,
        keep_recent: int = RECENT_UNAGED_TOOL_RESULTS,
        keep_recent_units: int | None = None,
        max_chars: int | None = None,
    ) -> int:
        """Level 1, no model call: clear every tool result except the ``keep_recent`` newest."""
        keep = max(0, int(keep_recent_units if keep_recent_units is not None else keep_recent))
        floor = int(max_chars) if max_chars is not None else MICROCOMPACT_MIN_CHARS
        pinned = getattr(self, "_pinned_index", _PIN_UNSET)
        protected: set[int] = set()
        if isinstance(pinned, int):
            protected = set(next((unit for unit in self._group_tool_units(messages) if pinned in unit), [pinned]))
        indexes = [index for index, message in enumerate(messages) if index not in protected and self._is_tool_result(message)]
        # Results after the last tool-call message are the batch the model has not read yet: never cleared.
        last_call = max((index for index, message in enumerate(messages) if self._is_tool_call_message(message)), default=-1)
        cleared = 0
        for index in indexes[: max(0, len(indexes) - keep)]:
            if index > last_call:
                continue
            message = messages[index]
            if bool(message.get("_tool_image_result")):
                if message.get("content") != "[earlier tool image omitted]":
                    message["content"] = "[earlier tool image omitted]"
                    cleared += 1
                continue
            content = message.get("content")
            if isinstance(content, str):
                if content != MICROCOMPACT_MARKER and len(content) > floor:
                    message["content"] = MICROCOMPACT_MARKER
                    cleared += 1
            elif isinstance(content, list):
                changed = False
                blocks: list[object] = []
                for block in content:
                    text = str(block.get("content", "")) if isinstance(block, Mapping) else ""
                    if isinstance(block, Mapping) and block.get("type") == "tool_result" and text != MICROCOMPACT_MARKER and len(text) > floor:
                        blocks.append({**block, "content": MICROCOMPACT_MARKER})
                        changed = True
                    else:
                        blocks.append(block)
                if changed:
                    message["content"] = blocks
                    cleared += 1
        return cleared

    def _force_compact(
        self,
        messages: list[dict[str, object]],
        turn: Mapping[str, object],
        tool_schemas: list[dict[str, object]],
    ) -> bool:
        """Level 3 reactive compaction: force immediate microcompaction + summary compaction on context overflow."""
        shrunk = self._microcompact(messages, keep_recent_units=1, max_chars=AGED_TOOL_RESULT_CHARS)
        before_len = len(messages)
        self._run_compaction(messages, turn, keep_units=min(2, CONTEXT_COMPACTION_KEEP_UNITS), allow_block=False)
        self._age_tool_results(messages, keep_recent=1)
        return shrunk > 0 or len(messages) < before_len

    def _maybe_compact(self, messages: list[dict[str, object]], turn: Mapping[str, object], tool_schemas: list[dict[str, object]]) -> None:
        """Clear old tool results first; summarize only when that is not enough."""
        if self._compaction_count >= 8 or len(messages) < CONTEXT_COMPACTION_KEEP_UNITS + 2:
            return
        estimate = self._estimated_tokens({"messages": messages, "tools": tool_schemas})
        limit = self.limits.max_context_tokens
        if estimate >= int(limit * MICROCOMPACT_THRESHOLD):
            before = estimate
            if self._microcompact(messages):
                estimate = self._estimated_tokens({"messages": messages, "tools": tool_schemas})
                self._persist_compact_boundary(turn, kind="microcompact", trigger="auto", pre_tokens=before, post_tokens=estimate)
                if self.context_reporting:
                    self._life(turn, "context_microcompacted", pre_tokens=before, post_tokens=estimate)
        threshold = self._autocompact_threshold()
        if threshold is None or estimate < threshold:
            return
        self._run_compaction(messages, turn, keep_units=CONTEXT_COMPACTION_KEEP_UNITS)

    def _autocompact_threshold(self) -> int | None:
        """Tokens at which the conversation is summarized; None when ``autoCompact`` is off."""
        setting = self.autocompact
        if setting is False:
            return None
        limit = self.limits.max_context_tokens
        if isinstance(setting, int) and not isinstance(setting, bool) and setting > 0:
            # Floored at a quarter of the context: a tiny threshold would summarize on nearly every request.
            return max(limit // 4, min(int(setting), limit))
        window = self.limits.context_window_tokens
        if window:
            reserve = (self.limits.max_output_tokens or AUTOCOMPACT_OUTPUT_RESERVE_TOKENS) + AUTOCOMPACT_MARGIN_TOKENS
            return max(1_000, min(limit, window - reserve))
        return int(limit * CONTEXT_COMPACTION_THRESHOLD)

    def _run_compaction(
        self,
        messages: list[dict[str, object]],
        turn: Mapping[str, object],
        *,
        keep_units: int = CONTEXT_COMPACTION_KEEP_UNITS,
        allow_block: bool = True,
        trigger: str = "auto",
        custom_instructions: str = "",
    ) -> bool:
        self._last_compaction = None
        self._last_compaction_block = None
        units = self._group_tool_units(messages)
        pinned = getattr(self, "_pinned_index", _PIN_UNSET)
        eligible = [unit for unit in units if pinned is _PIN_UNSET or pinned not in unit]
        if len(eligible) <= keep_units:
            return False
        compact_units = eligible[:-keep_units] if keep_units > 0 else list(eligible)
        compact_indices = {index for unit in compact_units for index in unit}
        if not compact_indices:
            return False
        # The stored boundary must cover exactly what was folded: the retained units
        # stay verbatim, so their messages must not be treated as summarized.
        folded_through = max(
            (
                int(messages[index]["_sequence"])
                for index in compact_indices
                if isinstance(messages[index].get("_sequence"), int) and not isinstance(messages[index].get("_sequence"), bool)
            ),
            default=None,
        )
        pre = self._emit(HookEvent.PRE_COMPACT, trigger=trigger, custom_instructions=custom_instructions)
        if pre is not None and pre.blocked and allow_block:
            self._last_compaction_block = pre.reason or "blocked by hook"
            return False
        pre_tokens = self._estimated_tokens({"messages": messages})
        if self.context_reporting:
            self._life(turn, "context_compacting", removed_messages=len(compact_indices), compaction_count=self._compaction_count + 1)
        source = self._compact_source(messages, compact_indices)
        instructions = [item for item in ([custom_instructions.strip()] if custom_instructions.strip() else []) if item]
        if pre is not None:
            instructions.extend(item for item in pre.additional_context if item)
        if instructions:
            source = "Instruções adicionais para o resumo:\n" + "\n".join(instructions) + "\n\n" + source
        summary = strip_analysis_block(self._request_compaction_summary(source, turn, code_mode=self._is_code_compaction(turn)))
        if not self._has_sections(summary):
            summary = self._fallback_compaction_summary(messages, compact_indices)
        enrichment = ""
        enricher = getattr(self.toolset, "post_compact_enrichment", None)
        if callable(enricher):
            try:
                enrichment = str(enricher() or "").strip()
            except Exception:
                enrichment = ""
        body = summary[:8_000]
        if enrichment:
            body = f"{body}\n\n{enrichment[:20_000]}"
        insert_at = min(compact_indices)
        pinned_item = messages[pinned] if isinstance(pinned, int) and 0 <= pinned < len(messages) else None
        replacement = compaction_message(body)
        retained = [item for index, item in enumerate(messages) if index not in compact_indices]
        original_to_retained = [(index, item) for index, item in enumerate(messages) if index not in compact_indices]
        position = next((offset for offset, (index, _item) in enumerate(original_to_retained) if index > insert_at), len(retained))
        retained.insert(position, replacement)
        messages[:] = retained
        # This rewrite already invalidates the cache, so it is the one place
        # where shrinking surviving results is free. Doing it here instead of
        # every iteration is what keeps the prefix append-only in between.
        self._age_tool_results(messages, keep_recent=keep_units)
        if pinned_item is not None:
            self._pinned_index = next((index for index, item in enumerate(messages) if item is pinned_item), None)
        self._compaction_count += 1
        post_tokens = self._estimated_tokens({"messages": messages})
        self._last_compaction = {"pre_tokens": pre_tokens, "post_tokens": post_tokens}
        if folded_through is not None:
            # No sequenced message was folded (only this turn's own steps): nothing
            # in the stored history is summarized, so no boundary is written.
            self._persist_compact_boundary(
                turn, kind="compact", trigger=trigger, pre_tokens=pre_tokens, post_tokens=post_tokens,
                summary=body, through_sequence=folded_through,
            )
        if self.context_reporting:
            self._life(
                turn, "context_compacted", removed_messages=len(compact_indices),
                summary_tokens=self._estimated_tokens(replacement), compaction_count=self._compaction_count,
                boundary="compact", trigger=trigger, pre_tokens=pre_tokens, post_tokens=post_tokens,
            )
        self._hooks(turn, "PostCompact", compaction_count=self._compaction_count)
        self._emit(HookEvent.POST_COMPACT, trigger=trigger, compaction_count=self._compaction_count, pre_tokens=pre_tokens, post_tokens=post_tokens)
        self._after_compaction(turn)
        return True

    def _persist_compact_boundary(self, turn: Mapping[str, object], **values: object) -> None:
        """Record the boundary so the next turn starts from the summary. A lost boundary costs a re-summary, never the turn."""
        recorder = getattr(self.store, "record_compact_boundary", None)
        if not callable(recorder):
            return
        try:
            recorder(turn, **values)
        except Exception:  # noqa: BLE001
            pass

    def _after_compaction(self, turn: Mapping[str, object]) -> None:
        """A compaction ends the prompt snapshot and opens a new session segment (SessionStart source=compact)."""
        conversation_id = str(turn.get("conversation_id") or "")
        clearer = getattr(self.store, "clear_prompt_snapshot", None)
        if callable(clearer) and conversation_id:
            try:
                clearer(conversation_id)
            except Exception:  # noqa: BLE001
                pass
        if self._prompt_renderer is not None:
            try:
                rendered = self._prompt_renderer()
                if isinstance(rendered, str) and rendered.strip():
                    self.system_prompt = rendered
            except Exception:  # noqa: BLE001 - the old prompt is still a valid prompt
                pass
        decision = self._emit(HookEvent.SESSION_START, source="compact")
        if decision is not None:
            for item in decision.additional_context:
                self.reminders.add("session_start", item)

    def _compact_source(self, messages: list[dict[str, object]], indices: set[int]) -> str:
        parts: list[str] = []
        # Keep the summarizer request below even a small model's window. The
        # normal turn budget is not the provider's full window, so this is a
        # deliberately conservative fraction rather than another full-sized
        # prompt.
        remaining = min(48_000, max(4_000, 2 * self.limits.max_context_tokens))
        for index in sorted(indices):
            try:
                encoded = json.dumps(messages[index], ensure_ascii=False, default=str)
            except (TypeError, ValueError):
                encoded = str(messages[index])
            if remaining <= 0:
                break
            parts.append(encoded[:remaining])
            remaining -= len(parts[-1])
        return "\n".join(parts)

    def _request_compaction_summary(self, source: str, turn: Mapping[str, object], *, code_mode: bool = False) -> str:
        instruction = COMPACTION_INSTRUCTION
        reported: Decimal | None = None
        input_tokens: int | None = None
        output_tokens: int | None = None
        try:
            events = self.provider.stream({
                "turn_id": str(turn.get("turn_id") or "") + ":context-compaction",
                "provider": str(turn.get("provider") or ""),
                "model": str(turn.get("model_id") or ""),
                "messages": [{"role": "system", "content": instruction}, {"role": "user", "content": source}],
                "tools": [],
                "max_output_tokens": min(1_600, max(256, self.limits.max_context_tokens // 4)),
            })
            parts: list[str] = []
            for raw in events:
                event = self._coerce(raw)
                if event.kind is StreamKind.TEXT and event.text:
                    parts.append(event.text)
                elif event.kind is StreamKind.USAGE:
                    if event.cost:
                        reported = (reported or Decimal("0")) + Decimal(str(event.cost))
                    if event.usage is not None:
                        input_tokens = event.usage.input_tokens if event.usage.input_tokens is not None else input_tokens
                        output_tokens = event.usage.output_tokens if event.usage.output_tokens is not None else output_tokens
            return "".join(parts).strip()
        except Exception:
            return ""
        finally:
            self._charge_summary(reported, input_tokens, output_tokens)

    @staticmethod
    def _has_sections(summary: str) -> bool:
        """Whether a provider reply is usable as a structured summary (all nine headings)."""
        return bool(summary) and all(section in summary for section in COMPACTION_SECTIONS)

    _has_code_sections = _has_sections

    @classmethod
    def _fallback_compaction_summary(cls, messages: list[dict[str, object]], indices: set[int]) -> str:
        """Build the nine Claude Code compaction sections locally when the provider cannot."""
        touched: list[str] = []
        user_messages: list[str] = []
        errors: list[str] = []
        for index in sorted(indices):
            item = messages[index]
            for call in item.get("tool_calls") or ():
                if not isinstance(call, Mapping):
                    continue
                function = call.get("function") if isinstance(call.get("function"), Mapping) else {}
                name = str(function.get("name") or "")
                arguments = str(function.get("arguments") or "")[:160]
                if name:
                    touched.append(f"- {name}({arguments})")
            content = item.get("content")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, Mapping) and block.get("type") == "tool_use":
                        touched.append(f"- {block.get('name')}({json.dumps(block.get('input') or {}, ensure_ascii=False)[:160]})")
            elif isinstance(content, str) and content.strip():
                role = str(item.get("role") or "")
                if role == "user":
                    user_messages.append(f"- {content.strip()[:200]}")
                elif role == "tool" and ("error" in content.lower() or "failed" in content.lower()):
                    errors.append(f"- {content.strip()[:200]}")
        sections = {
            CODE_COMPACTION_SECTIONS[0]: user_messages[:2] or ["- none"],
            CODE_COMPACTION_SECTIONS[1]: ["- preserved from workspace trajectory"],
            CODE_COMPACTION_SECTIONS[2]: touched[-12:] or ["- none"],
            CODE_COMPACTION_SECTIONS[3]: errors[-6:] or ["- none"],
            CODE_COMPACTION_SECTIONS[4]: touched[-6:] or ["- none"],
            CODE_COMPACTION_SECTIONS[5]: user_messages[-8:] or ["- none"],
            CODE_COMPACTION_SECTIONS[6]: user_messages[-2:] or ["- none"],
            CODE_COMPACTION_SECTIONS[7]: touched[-3:] or ["- none"],
            CODE_COMPACTION_SECTIONS[8]: ["- continue with the active request"],
        }
        return "\n".join(f"### {name}\n" + "\n".join(lines) for name, lines in sections.items())

    _fallback_code_compaction_summary = _fallback_compaction_summary

    @classmethod
    def _estimated_tokens(cls, message: Mapping[str, object] | object) -> int:
        """Cheap upper-bound estimate; four characters per token is the usual rule, with fixed image block cost."""
        if isinstance(message, Mapping):
            content = message.get("content")
            if isinstance(content, list):
                total = 4
                for block in content:
                    if isinstance(block, Mapping):
                        btype = str(block.get("type") or "")
                        if btype in {"image_url", "image", "input_image"}:
                            total += 1200
                            continue
                        if btype == "tool_result" and isinstance(block.get("content"), list):
                            for sub in block.get("content") or ():
                                if isinstance(sub, Mapping) and str(sub.get("type") or "") in {"image_url", "image", "input_image"}:
                                    total += 1200
                                else:
                                    try:
                                        sub_payload = json.dumps(sub, ensure_ascii=False, default=str)
                                    except (TypeError, ValueError):
                                        sub_payload = str(sub)
                                    total += max(1, len(sub_payload) // 4)
                            continue
                    try:
                        block_payload = json.dumps(block, ensure_ascii=False, default=str)
                    except (TypeError, ValueError):
                        block_payload = str(block)
                    total += max(1, len(block_payload) // 4)
                return max(1, total)
        try:
            payload = json.dumps(message, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            payload = str(message)
        return max(1, len(payload) // 4)

    def _request_messages(self, messages: list[dict[str, object]]) -> list[dict[str, object]]:
        """Keep the most recent exchange within budget without losing the ask.

        Dropping by message count loses the user's current request first, which
        is exactly the message the agent needs to still be working on. The pin
        is normally resolved once by ``run`` (see ``self._pinned_index``) before
        the loop starts, so it can never drift onto a later tool-result message
        that also carries ``role: "user"`` (the Anthropic shape) as ``messages``
        grows. When called without ``run`` having set it -- e.g. directly in a
        test -- the pin falls back to the last real user message in the list
        given, still excluding tool-result-shaped ones.

        A tool-calling assistant message and the tool-result messages that
        answer it are packed as one atomic unit: either both survive the cut or
        both are dropped, so a trim can never orphan a tool result whose call
        was evicted (both providers reject that shape).
        """
        budget = self.limits.max_context_tokens
        pinned_index = getattr(self, "_pinned_index", _PIN_UNSET)
        if pinned_index is _PIN_UNSET:
            pinned_index = self._last_user_index(messages)
        pinned = messages[pinned_index] if pinned_index is not None else None
        available = budget - (self._estimated_tokens(pinned) if pinned is not None else 0)
        kept: set[int] = {pinned_index} if pinned_index is not None else set()
        for unit in reversed(self._group_tool_units(messages)):
            if pinned_index is not None and pinned_index in unit:
                continue
            cost = sum(self._estimated_tokens(messages[index]) for index in unit)
            if cost > available:
                break
            available -= cost
            kept.update(unit)
        ordered_kept = sorted(kept)
        omitted = len(messages) - len(ordered_kept)
        marker = None
        if omitted > 0:
            # Naming the tools whose results left the window turns re-reading
            # into an informed choice. The previous wording ("re-read files or
            # re-run searches if you need their content") made it a reflex,
            # and re-running work already done is precisely what the loop is
            # being changed to stop doing.
            dropped = self._dropped_tool_names(messages, kept)
            detail = f" Saíram resultados de: {', '.join(dropped)}." if dropped else ""
            marker = {
                "role": "system",
                "content": (
                    f"[{omitted} mensagens anteriores foram omitidas para caber no orçamento de contexto."
                    f"{detail} O que já foi decidido e apurado está resumido acima.]"
                ),
            }
        window: list[dict[str, object]] = []
        previous = -1
        marker_inserted = False
        for index in ordered_kept:
            if marker is not None and not marker_inserted and index != previous + 1:
                window.append(marker)
                marker_inserted = True
            window.append(messages[index])
            previous = index
        if marker is not None and not marker_inserted:
            window.append(marker)
        if self.first_user_context:
            # excludeDynamicSystemPromptSections: per-machine context rides with the first message, not the system prompt.
            position = next((offset for offset, item in enumerate(window) if item.get("role") in {"user", "assistant"}), len(window))
            window.insert(position, {"role": "user", "content": system_reminder(self.first_user_context)})
        # The contract is rebuilt into every request instead of living in
        # ``messages``: a message can be trimmed away or folded into a
        # compaction summary, and the definition of the task must outlive
        # both. It stays out of the cached system prefix because it changes
        # whenever the agent revises it.
        preamble: list[dict[str, object]] = []
        if self.system_prompt:
            preamble.append({"role": "system", "content": self.system_prompt})
        if self.volatile_prompt:
            preamble.append({"role": "system", "content": self.volatile_prompt})
        if self.contract is not None:
            preamble.append({"role": "system", "content": self.contract.render()})
        return [*preamble, *window] if preamble else window

    @staticmethod
    def _dropped_tool_names(messages: list[dict[str, object]], kept: set[int]) -> list[str]:
        """Names of the tools whose calls fell outside the request window."""
        names: list[str] = []
        for index, message in enumerate(messages):
            if index in kept or message.get("role") != "assistant":
                continue
            for call in message.get("tool_calls") or ():
                if isinstance(call, Mapping):
                    function = call.get("function") if isinstance(call.get("function"), Mapping) else {}
                    name = str(function.get("name") or "")
                    if name and name not in names:
                        names.append(name)
            content = message.get("content")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, Mapping) and block.get("type") == "tool_use":
                        name = str(block.get("name") or "")
                        if name and name not in names:
                            names.append(name)
        return names[:8]

    @classmethod
    def _last_user_index(cls, messages: list[dict[str, object]]) -> int | None:
        """Index of the most recent real user request, ignoring tool results.

        Anthropic tool-result messages also carry ``role: "user"``; a naive
        "last user message" search would match those as the loop appends them.
        """
        return next(
            (
                index for index in range(len(messages) - 1, -1, -1)
                if messages[index].get("role") == "user" and not cls._is_tool_result_message(messages[index])
            ),
            None,
        )

    @classmethod
    def _group_tool_units(cls, messages: list[dict[str, object]]) -> list[list[int]]:
        """Group each tool-calling assistant message with the results answering it.

        Covers both provider shapes: OpenAI's assistant message carries
        ``tool_calls`` and results are ``role: "tool"``; Anthropic's assistant
        message has list content with ``tool_use`` blocks and results are
        ``role: "user"`` messages with list content carrying ``tool_result``
        blocks. Every other message is its own single-message unit.
        """
        units: list[list[int]] = []
        index = 0
        total = len(messages)
        while index < total:
            unit = [index]
            if cls._is_tool_call_message(messages[index]):
                follow = index + 1
                while follow < total and cls._is_tool_result_message(messages[follow]):
                    unit.append(follow)
                    follow += 1
                index = follow
            else:
                index += 1
            units.append(unit)
        return units

    @staticmethod
    def _is_tool_call_message(message: Mapping[str, object]) -> bool:
        if message.get("role") != "assistant":
            return False
        if message.get("tool_calls"):
            return True
        content = message.get("content")
        if isinstance(content, list):
            return any(isinstance(block, Mapping) and block.get("type") == "tool_use" for block in content)
        return False

    @staticmethod
    def _is_tool_result_message(message: Mapping[str, object]) -> bool:
        if message.get("role") == "tool" or bool(message.get("_tool_image_result")):
            return True
        if message.get("role") == "user":
            content = message.get("content")
            if isinstance(content, list):
                return any(isinstance(block, Mapping) and block.get("type") == "tool_result" for block in content)
        return False

    @staticmethod
    def _is_tool_result(message: Mapping[str, object]) -> bool:
        if message.get("role") == "tool" or bool(message.get("_tool_image_result")):
            return True
        content = message.get("content")
        return isinstance(content, list) and any(isinstance(block, Mapping) and block.get("type") == "tool_result" for block in content)

    @classmethod
    def _compress(cls, text: str) -> str:
        if "\n[compressed: " in text and text.endswith(" characters total; re-run the tool if you need the rest]"):
            return text
        if len(text) <= AGED_TOOL_RESULT_CHARS:
            return text
        return f"{text[:AGED_TOOL_RESULT_CHARS]}\n[compressed: {len(text)} characters total; re-run the tool if you need the rest]"

    @classmethod
    def _age_tool_results(cls, messages: list[dict[str, object]], keep_recent: int = RECENT_UNAGED_TOOL_RESULTS) -> list[dict[str, object]]:
        """Shrink tool output the model has already had a chance to read.

        The full result is what the model needed on the iteration right after
        the call. Re-sending it on every later iteration is pure cost, so older
        results keep only their head plus a pointer back to the tool.
        """
        indexes = [index for index, message in enumerate(messages) if cls._is_tool_result(message)]
        for index in indexes[: max(0, len(indexes) - max(0, int(keep_recent)))]:
            message = messages[index]
            if bool(message.get("_tool_image_result")):
                message["content"] = "[earlier tool image omitted]"
                continue
            content = message.get("content")
            if isinstance(content, str):
                message["content"] = cls._compress(content)
            elif isinstance(content, list):
                message["content"] = [
                    {**block, "content": cls._compress(str(block.get("content", "")))}
                    if isinstance(block, Mapping) and block.get("type") == "tool_result" else block
                    for block in content
                ]
        return messages

    @staticmethod
    def _maximum_plausible_input_tokens(request: Mapping[str, object]) -> int:
        """Reject provider telemetry that cannot describe the submitted prompt.

        A token can expand to a few encoded bytes, hence the intentionally wide
        four-token-per-character allowance. This catches fixed context-window
        values returned as usage without rejecting a genuinely large prompt.
        """
        payload = {"messages": request.get("messages", []), "tools": request.get("tools", [])}
        characters = len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
        return max(512, characters * 4)

    @staticmethod
    def _signature(name: str, arguments: Mapping[str, object]) -> str:
        try:
            return f"{name}:{json.dumps(arguments, sort_keys=True, ensure_ascii=False, default=str)}"
        except (TypeError, ValueError):
            return f"{name}:{arguments!r}"

    def _run_toolset(self, turn: dict[str, object], calls: list[dict[str, str]]) -> list[dict[str, object]]:
        """Execute the model's calls and return results it can actually read.

        Consecutive read-only calls run concurrently; anything that can write
        to the workspace, or a read-only call that isn't adjacent to another
        one, stays sequential so two calls never race on the same file and no
        call runs out of the order the model requested. Results are emitted
        in the order the model requested them.
        """
        from .agent_tools import AgentToolError, ToolOutcome, parse_arguments

        prepared: list[tuple[str, str, dict[str, object] | None, str | None]] = []
        for call in calls:
            name = str(call.get("name") or "")
            call_id = str(call.get("id") or "")
            argument_names = getattr(self.toolset, "argument_names", None)
            parsed_args: dict[str, object] | None = None
            parse_err: str | None = None
            names_translator = getattr(self, "_names", None)
            known_keys = names_translator.argument_names(name) if names_translator is not None else None
            if known_keys is None and callable(argument_names):
                known_keys = argument_names(name)
            try:
                parsed_args = parse_arguments(call.get("arguments") or "{}", known_keys)
                if names_translator is not None and isinstance(parsed_args, dict):
                    parsed_args = names_translator.arguments_in(name, parsed_args)
            except AgentToolError as error:
                parse_err = str(error)
                if name in {"write_file", "edit_file"} and "truncated at max_tokens" not in parse_err:
                    parse_err = (
                        f"{parse_err} Hint: Ensure 'path' is provided first; if you are writing a large file, "
                        "your output may have been truncated at max_tokens — write smaller modular "
                        f"files or use {self._label('edit_file')} in chunks."
                    )
            except Exception as error:  # noqa: BLE001 - model-chosen values must never crash the turn
                parsed_args = None
                parse_err = f"Invalid arguments for {name}: {type(error).__name__}"
            started_meta: dict[str, object] = {}
            if isinstance(parsed_args, Mapping):
                desc = parsed_args.get("description") or parsed_args.get("subject")
                if isinstance(desc, str) and desc.strip():
                    started_meta["description"] = desc.strip()[:200]
                t_path = (
                    parsed_args.get("path")
                    or parsed_args.get("file_path")
                    or parsed_args.get("notebook_path")
                    or parsed_args.get("plan_path")
                    or parsed_args.get("pattern")
                )
                if isinstance(t_path, str) and t_path.strip():
                    started_meta["target_path"] = t_path.strip()[:240]
                    started_meta["path"] = t_path.strip()[:240]
                cmd_val = parsed_args.get("command")
                if isinstance(cmd_val, str) and cmd_val.strip():
                    started_meta["command"] = cmd_val.strip()[:240]
            self._life(turn, "tool_started", tool_name=name, invocation_id=call_id, **started_meta)
            prepared.append((call_id, name, parsed_args, parse_err))

        # A call whose exact (name, arguments) signature already failed
        # earlier in this turn is never invoked again — neither in the pool
        # nor sequentially — it is short-circuited with the earlier error.
        duplicate = [error is None and self._signature(name, arguments) in self._failed_signatures for _, name, arguments, error in prepared]

        def duplicate_outcome(name: str, arguments: Mapping[str, object]) -> ToolOutcome:
            previous = self._failed_signatures[self._signature(name, arguments)]
            content = (
                f"{previous}\n\n[this exact call already failed in this turn; "
                "it was not run again — change the arguments or use a different tool]"
            )
            return ToolOutcome("failed", "Chamada repetida ignorada", content, {}, "DUPLICATE_TOOL_CALL")

        is_read_only = getattr(self.toolset, "is_read_only", None)
        is_mutating = getattr(self.toolset, "is_mutating", None)

        def cached_read(name: str, arguments: Mapping[str, object]) -> ToolOutcome | None:
            """The earlier result of this exact read, if nothing can have changed it.

            Two conditions, and the second is what makes this correct: the
            tool must be read-only, and no write-capable tool may have run
            since. A write to any path counts, because the runtime has no way
            to know which file a tool touched. ``run_command`` is not
            read-only, so a command that only reads is never served from here.
            """
            if name in {"check_subagent", "read_process_output", "preview_logs"}:
                return None
            if not (callable(is_read_only) and is_read_only(name)):
                return None
            remembered = self._succeeded_reads.get(self._signature(name, arguments))
            if remembered is None or remembered[1] != self._writes:
                return None
            content = (
                f"{remembered[0]}\n\n[resultado da chamada anterior a {self._label(name)} neste mesmo turno; "
                "nada foi escrito desde então, então o conteúdo não mudou]"
            )
            return ToolOutcome("succeeded", f"{name} reaproveitado da chamada anterior", content, {})

        repeated: list[ToolOutcome | None] = [
            None if error is not None or duplicate[i] else cached_read(name, arguments)
            for i, (_, name, arguments, error) in enumerate(prepared)
        ]

        eligible = [
            error is None
            and not duplicate[i]
            and repeated[i] is None
            and ((callable(is_read_only) and is_read_only(name)) or name in {"run_subagent", "ask_agent", "check_subagent"})
            for i, (_, name, _, error) in enumerate(prepared)
        ]

        # Walk the batch once, in the model's order. Each maximal run of
        # consecutive read-only calls is dispatched to the pool together;
        # everything else (a write-capable call, a duplicate of an already
        # failed call, or a run of exactly one read-only call) is invoked (or
        # short-circuited) in place before moving on. This keeps a write from
        # ever starting before an earlier call finishes, or from blocking a
        # later independent read.
        outcomes: dict[int, object] = {}
        checkpoint_lock = threading.Lock()

        def is_persistence_error(exc: BaseException) -> bool:
            raw = f"{type(exc).__module__}.{type(exc).__name__}: {exc}".lower()
            return any(
                marker in raw
                for marker in (
                    "sqlite3",
                    "integrityerror",
                    "operationalerror",
                    "[sql:",
                    "unique constraint failed",
                    "database is locked",
                    "sqlalchemy.exc",
                )
            )

        def transient_persistence_outcome(tool_name: str) -> ToolOutcome:
            return ToolOutcome(
                "failed",
                f"{tool_name} falhou temporariamente",
                "Tool execution encountered a transient internal persistence error; you may retry the call.",
                {"transient": True},
                "TOOL_TRANSIENT_ERROR",
            )

        def interrupted_outcome() -> ToolOutcome:
            return ToolOutcome("failed", "Interrompido pelo usuário", INTERRUPTED_TOOL_RESULT, {}, "INTERRUPTED_BY_USER")

        def invoke(index: int) -> object:
            call_id, name, arguments, _ = prepared[index]
            assert arguments is not None
            read_only = bool(callable(is_read_only) and is_read_only(name))
            # Older toolset adapters only expose is_read_only. Preserve their
            # conservative behaviour, while native toolsets distinguish
            # coordination/planning tools from calls that can mutate state.
            mutating = (
                bool(is_mutating(name))
                if callable(is_mutating)
                else (not read_only and name not in {"run_subagent", "ask_agent", "check_subagent"})
            )
            with checkpoint_lock:
                effect_id = self._effect_started(
                    turn,
                    kind="tool",
                    invocation_ref=f"tool:{call_id}",
                    request_ref=f"conversation-turn:{turn['turn_id']}:tool:{call_id}",
                )
                if mutating:
                    # Counted before the call, not after: a write that fails may
                    # still have changed something, so a later read must not be
                    # served from a result taken before it.
                    self._writes += 1
            try:
                try:
                    setattr(self.toolset, "_active_invocation_id", call_id)
                except Exception:
                    pass
                # Passed explicitly: the shared _active_invocation_id is not safe under the parallel pool.
                if _accepts_call_id(self.toolset.invoke):
                    outcome = self.toolset.invoke(name, arguments, call_id=call_id)
                else:
                    outcome = self.toolset.invoke(name, arguments)
            except Exception as exc:
                if is_persistence_error(exc):
                    with checkpoint_lock:
                        try:
                            self._effect_finished(
                                turn,
                                effect_id,
                                state="NOT_APPLIED",
                                error_code="TOOL_TRANSIENT_ERROR",
                            )
                        except Exception:
                            pass
                    return transient_persistence_outcome(name)
                with checkpoint_lock:
                    self._effect_finished(turn, effect_id, state="UNKNOWN", error_code="TOOL_INTERRUPTED")
                raise
            with checkpoint_lock:
                if read_only and outcome.status == "succeeded":
                    self._succeeded_reads[self._signature(name, arguments)] = (outcome.content, self._writes)
                state = "APPLIED" if outcome.status == "succeeded" else "NOT_APPLIED"
                # A normal failed ToolOutcome is a definite response from the
                # adapter: the model can read it and choose a different action.
                # Only an unexpected adapter failure is indeterminate for a
                # mutating call, because it might have crossed the effect boundary
                # without returning a receipt. Handlers may also opt in to that
                # treatment explicitly for a known exceptional integration.
                effect_unknown = outcome.error_code == "TOOL_FAILED" or outcome.payload.get("effect_state") == "unknown"
                if outcome.status != "succeeded" and mutating and effect_unknown:
                    state = "UNKNOWN"
                self._effect_finished(
                    turn, effect_id, state=state,
                    result_ref=f"conversation-turn:{turn['turn_id']}:tool-result:{call_id}",
                    error_code=outcome.error_code,
                    private_result={"status": outcome.status, "content": outcome.content[:12_000]},
                )
            return outcome

        index = 0
        while index < len(prepared):
            if self.cancelled(turn):
                # Calls not started yet still get a result: a call without one is a shape every provider rejects.
                for rest in range(index, len(prepared)):
                    if prepared[rest][3] is None and rest not in outcomes:
                        outcomes[rest] = interrupted_outcome()
                break
            if not eligible[index]:
                _, name, arguments, error = prepared[index]
                if duplicate[index]:
                    outcomes[index] = duplicate_outcome(name, arguments)
                elif repeated[index] is not None:
                    outcomes[index] = repeated[index]
                elif error is None:
                    outcomes[index] = invoke(index)
                index += 1
                continue
            run_end = index
            while run_end < len(prepared) and eligible[run_end]:
                run_end += 1
            run_indexes = list(range(index, run_end))
            if len(run_indexes) > 1:
                has_subagent_calls = any(
                    prepared[i][1] in {"run_subagent", "ask_agent", "check_subagent"}
                    for i in run_indexes
                )
                batch_max_workers = MAX_PARALLEL_SUBAGENT_TOOLS if has_subagent_calls else MAX_PARALLEL_TOOLS
                with ThreadPoolExecutor(max_workers=min(len(run_indexes), batch_max_workers)) as pool:
                    futures = {i: pool.submit(invoke, i) for i in run_indexes}
                    for i, future in futures.items():
                        try:
                            outcomes[i] = future.result()
                        except Exception as error:  # noqa: BLE001 - preserve per-tool failure parity
                            if is_persistence_error(error):
                                outcomes[i] = transient_persistence_outcome(prepared[i][1])
                            else:
                                message = f"{type(error).__name__}: {error}"
                                outcomes[i] = ToolOutcome("failed", f"{prepared[i][1]} falhou", message[:12_000], {}, "TOOL_FAILED")
            else:
                outcomes[index] = invoke(index)
            index = run_end

        results: list[dict[str, object]] = []
        for index, (call_id, name, arguments, error) in enumerate(prepared):
            if error is not None:
                self.counters.note_call(name, {}, "failed")
                sig = f"{name}:<invalid_json>"
                self._consecutive_tool_failures[sig] = self._consecutive_tool_failures.get(sig, 0) + 1
                self._life(turn, "tool_finished", tool_name=name, invocation_id=call_id, status="failed", summary=error, error_code="INVALID_ARGUMENTS", tool_arguments={})
                results.append({"id": call_id, "name": name, "status": "failed", "content": error, "error_code": "INVALID_ARGUMENTS"})
                continue
            outcome = outcomes[index]
            self.counters.note_call(name, arguments, outcome.status)
            try:
                self.ledger.note_tool_outcome(name, arguments, outcome.status)
            except Exception:  # noqa: BLE001 - observation never breaks a turn
                pass
            self._absorb_contract(outcome)
            self._absorb_verification(name, outcome)
            sig = self._signature(name, arguments)
            if outcome.status == "succeeded":
                self._consecutive_tool_failures.clear()
            else:
                self._consecutive_tool_failures[sig] = self._consecutive_tool_failures.get(sig, 0) + 1
            is_mutating_fn = getattr(self.toolset, "is_mutating", None)
            if outcome.status == "succeeded" and (
                name in {
                    "write_file",
                    "edit_file",
                    "notebook_edit",
                    "delete_file",
                    "run_command",
                    "powershell",
                    "preview_start",
                    "preview_stop",
                    "stop_process",
                    "verify_project",
                }
                or (callable(is_mutating_fn) and is_mutating_fn(name))
            ):
                self._failed_signatures.clear()
            elif outcome.status == "failed" and not duplicate[index] and not outcome.payload.get("transient"):
                self._failed_signatures[sig] = outcome.content
            self._life(
                turn, "tool_finished", tool_name=name, invocation_id=call_id, status=outcome.status,
                summary=outcome.summary, error_code=outcome.error_code, tool_payload=dict(outcome.payload),
                tool_arguments=dict(arguments),
            )
            self._hooks(turn, "PostToolUse", tool_name=name, status=outcome.status)
            results.append({
                "id": call_id, "name": name, "status": outcome.status,
                "content": outcome.content, "images": list(outcome.images or []),
                "wait_for_user": bool(outcome.payload.get("wait_for_user")),
                "error_code": outcome.error_code,
            })
        self._emit(
            HookEvent.POST_TOOL_BATCH,
            tool_calls=[
                {"tool_name": _public_tool_name(str(item.get("name") or "")), "tool_use_id": str(item.get("id") or ""), "status": str(item.get("status") or "")}
                for item in results
            ],
        )
        return results

    def _settle_remaining_todos(self, turn: dict[str, object], turn_id: str, *, failed: bool = False) -> None:
        if failed or str(turn.get("state") or "").lower() in {"failed", "cancelled", "blocked", "error"}:
            return
        complete_todos_fn = getattr(self.toolset, "complete_remaining_todos", None)
        if not callable(complete_todos_fn):
            return
        try:
            settled_todos = complete_todos_fn()
            if settled_todos:
                self._life(
                    turn,
                    "tool_finished",
                    tool_name="todo_write",
                    invocation_id=f"auto_todo_settle_{turn_id}",
                    status="succeeded",
                    summary=f"Lista de tarefas ({len(settled_todos)}/{len(settled_todos)} concluídas)",
                    tool_payload={
                        "todos": settled_todos,
                        "total": len(settled_todos),
                        "completed": len(settled_todos),
                        "in_progress": 0,
                        "pending": 0,
                        "tool_kind": "planning",
                    },
                )
        except Exception:
            pass

    def _load(self, turn_id: str) -> dict[str, object]:
        if hasattr(self.store, "load"):
            return self.store.load(turn_id)
        if hasattr(self.store, "get_turn"):
            return self.store.get_turn(turn_id)
        raise LookupError("turn store cannot load a turn")

    def _life(self, turn: dict[str, object], state: str, **payload: object) -> None:
        if hasattr(self.store, "lifecycle"):
            self.store.lifecycle(turn, state, **payload)

    def _effect_started(self, turn: dict[str, object], *, kind: str, invocation_ref: str, request_ref: str) -> str | None:
        recorder = getattr(self.store, "effect_started", None)
        if callable(recorder):
            return recorder(turn, kind=kind, invocation_ref=invocation_ref, request_ref=request_ref)
        return None

    def _effect_finished(
        self,
        turn: dict[str, object],
        effect_id: str | None,
        *,
        state: str,
        result_ref: str | None = None,
        error_code: str | None = None,
        private_result: Mapping[str, object] | None = None,
    ) -> None:
        recorder = getattr(self.store, "effect_finished", None)
        if callable(recorder) and effect_id is not None:
            recorder(
                turn, effect_id=effect_id, state=state, result_ref=result_ref,
                error_code=error_code, private_result=private_result,
            )

    def _emit(self, name: HookEvent, **payload: object):
        bus = getattr(self, "lifecycle", None)
        if bus is None:
            return None
        try:
            return bus.emit(name, **payload)
        except Exception:  # noqa: BLE001 - lifecycle never breaks a turn
            return None

    def _hooks(self, turn: Mapping[str, object], event: str, **payload: object) -> None:
        """Notify plugin hooks and surface their result. Never raises, never influences the turn."""
        if self.hook_engine is None:
            return
        try:
            outcomes = self.hook_engine.dispatch(user_id=str(turn.get("user_id") or ""), event=event, payload=dict(payload))
        except Exception:  # noqa: BLE001 - a hook never breaks the turn
            return
        for outcome in outcomes or ():
            summary = (getattr(outcome, "stdout", "") or "").strip() or getattr(outcome, "detail", "") or (getattr(outcome, "stderr", "") or "").strip()
            self._life(
                turn, "plugin_hook", hook_id=getattr(outcome, "hook_id", ""), hook_event=event,
                status=getattr(outcome, "status", ""), summary=summary[:2000],
            )

    def _advance_phase(self, turn: dict[str, object], results: list[dict[str, object]]) -> None:
        """Move the turn along, on evidence rather than on the model's say-so.

        Three things advance a phase: the agent committed to a contract, it
        reported a verification result, or the phase ran out of budget.
        Running out of budget never fails the turn -- it hands what exists to
        the next stage, which is the difference between an agent that stops
        usefully and one that stops at ITERATION_LIMIT with nothing to show.
        """
        if self.phases is None:
            return
        previous = self.phases.current
        wrote_contract = any(
            str(result.get("name")) == "write_contract" and str(result.get("status")) == "succeeded"
            for result in results
        )
        verification = self._pending_verification
        self._pending_verification = None
        if verification is not None and self.phases.current is Phase.VERIFY:
            # An explicit verification result is never "flailing": it is
            # exactly the work this phase exists to produce.
            self.phases.note_iteration(len(results), productive=True)
            if bool(verification.get("passed")):
                self._verified_at_change_events = self._toolset_change_events()
                self.phases.force_respond()
            elif self._verify_repair_rounds < MAX_VERIFY_REPAIR_ROUNDS:
                self._verify_repair_rounds += 1
                self.phases.force_execute()
                self._phase_transition_note = _repair_message(str(verification.get("findings") or ""))
            else:
                # Out of repair rounds: stop asking the model to fix it again
                # and let it answer, with whatever it last reported broken.
                self._verified_at_change_events = self._toolset_change_events()
                self.phases.force_respond()
                self._phase_transition_note = (
                    "[Sistema] O limite de rodadas de correção foi atingido. Responda agora informando "
                    "claramente o que permanece quebrado ou não verificado."
                )
            if self.phases.current is not previous:
                self._life(turn, "phase_changed", phase=str(self.phases.current), previous_phase=str(previous))
            return
        is_mutating = getattr(self.toolset, "is_mutating", None)
        productive = wrote_contract or any(
            str(result.get("status")) == "succeeded" and callable(is_mutating) and is_mutating(str(result.get("name")))
            for result in results
        )
        self.phases.note_iteration(len(results), productive=productive)
        self.phases.observe(wrote_contract=wrote_contract)
        if self.phases.current is Phase.EXECUTE and self.contract is None:
            # PLAN ended without a usable contract. A model too weak to fill
            # the schema still has to be able to work, so the request itself
            # becomes the contract rather than the turn stalling.
            self.contract = synthesize_contract(self._first_request_text)
        if self.phases.current is Phase.EXECUTE and self.contract is not None:
            self._apply_elastic_execute_budget()
        if self.phases.current is not previous:
            self._life(turn, "phase_changed", phase=str(self.phases.current), previous_phase=str(previous))

    def _apply_elastic_execute_budget(self) -> None:
        """Recomputed from the fixed default every time: see ``_elastic_execute_budget``."""
        if self.phases is None or self.contract is None:
            return
        self.phases.budgets[Phase.EXECUTE] = _elastic_execute_budget(self.contract, DEFAULT_PHASE_BUDGETS[Phase.EXECUTE])

    def _toolset_change_events(self) -> int:
        getter = getattr(self.toolset, "change_events", None)
        return getter() if callable(getter) else 0

    def _needs_verification(self) -> bool:
        """Whether the turn owes a VERIFY pass before it may answer.

        Independent of Code mode: any turn that changed the workspace and has
        not yet had those changes checked is subject to this, which is what
        makes the contract's acceptance criteria mean something in the
        ordinary case, not only when Code mode is explicitly on.
        """
        if self.toolset is None or self.phases is None:
            return False
        if self.phases.current in (Phase.VERIFY, Phase.RESPOND):
            return False
        return self._toolset_change_events() > self._verified_at_change_events

    def _absorb_verification(self, name: str, outcome: object) -> None:
        """Capture a verification result the moment the tool produces it.

        Mirrors ``_absorb_contract``: ``report_verification`` knows nothing
        about phases, it only returns whether things hold; this is where that
        takes effect, consumed once by ``_advance_phase``.
        """
        if name != "report_verification" or getattr(outcome, "status", None) != "succeeded":
            return
        payload = getattr(outcome, "payload", None)
        if isinstance(payload, Mapping):
            self._pending_verification = payload

    def _absorb_contract(self, outcome: object) -> None:
        """Adopt a contract the planning tool just produced, or count a refusal.

        ``write_contract`` deliberately knows nothing about the runtime; it
        returns the parsed contract on its payload and this is where it takes
        effect. Repeated rejections are counted so a model that cannot fill
        the schema still ends up with a workable contract instead of looping
        on the same validation error.
        """
        if getattr(outcome, "error_code", None) == "INVALID_CONTRACT":
            self._rejected_contracts += 1
            return
        payload = getattr(outcome, "payload", None)
        if not isinstance(payload, Mapping):
            return
        raw = payload.get("contract")
        if not isinstance(raw, Mapping):
            return
        try:
            self.contract = parse_contract(raw)
        except ContractError:
            return
        self._rejected_contracts = 0

    def _record_step(self, turn: dict[str, object], *, kind: str, payload: Mapping[str, object], **fields: object) -> None:
        """Append one step to the durable transcript, if the store keeps one.

        A store without the method (every in-memory test double, and the
        subagent view, whose trajectory is not the conversation's) simply
        records nothing, and a failure costs the next turn some context
        rather than costing this turn its run.
        """
        recorder = getattr(self.store, "record_step", None)
        if not callable(recorder):
            return
        try:
            recorder(turn, kind=kind, payload=payload, **fields)
        except Exception:  # noqa: BLE001 - the transcript never breaks a turn
            pass

    def _settle_quality(self, turn: Mapping[str, object], outcome: str, error_code: str | None = None) -> None:
        """Settle this turn exactly once, at its terminal: efficiency row and learning.

        Recovery can drive a turn to a terminal state more than once, and the
        row must describe the turn rather than the number of attempts, so the
        first terminal wins. Committing what the turn learned shares that same
        exactly-once gate deliberately: both are terminal-settlement side
        effects, and neither must depend on whether the store implements
        ``record_quality`` -- a test double without one still learns.
        """
        if self._quality_recorded:
            return
        self._quality_recorded = True
        if outcome in {"completed", "completed_with_caveats"}:
            self._commit_learning("project" if turn.get("project_id") else "user")
            if callable(self._on_completed):
                try:
                    self._on_completed()
                except Exception:  # noqa: BLE001 - after-turn work never changes the turn
                    pass
        recorder = getattr(self.store, "record_quality", None)
        if not callable(recorder):
            return
        started = self._started_at or self.clock()
        try:
            recorder(
                turn,
                counters=self.counters.as_row(),
                outcome=outcome,
                error_code=error_code,
                duration_ms=int((self.clock() - started).total_seconds() * 1000),
            )
        except Exception:  # noqa: BLE001 - telemetry never breaks a turn
            pass

    def _commit_learning(self, scope: str) -> None:
        """Hand what this turn taught to whoever knows how to store it.

        Runs once, after the answer has already been delivered, and only for
        a turn that actually reached a terminal state. The runtime deliberately
        does not know what a memory store is: the sink is supplied by the
        session, which owns that dependency.
        """
        if self._learning_committed or self._learning_sink is None:
            return
        self._learning_committed = True
        try:
            learned = self.ledger.mechanical_memories(scope)
            if learned:
                self._learning_sink(learned)
        except Exception:  # noqa: BLE001 - learning never breaks a turn
            pass

    def _run_manual_compaction(self, turn: dict[str, object]) -> AgenticRunResult:
        """/compact: summarize the whole conversation into a boundary, then end the turn."""
        marker = getattr(self.store, "mark_command_turn", None)
        if callable(marker):
            try:
                marker(turn)
            except Exception:  # noqa: BLE001 - an unmarked command turn only costs history space
                pass
        messages = [dict(item) for item in self.store.history_for_turn(turn)]
        if messages and str(messages[-1].get("content") or "").startswith("/compact"):
            messages = messages[:-1]
        self._pinned_index = None
        instructions = str(self.compact_request or "").strip()
        compacted = len(messages) >= 2 and self._run_compaction(
            messages, turn, keep_units=0, trigger="manual", custom_instructions=instructions,
        )
        if compacted:
            info = self._last_compaction or {}
            self.store.delta(turn, f"Conversa compactada: cerca de {info.get('pre_tokens', 0)} → {info.get('post_tokens', 0)} tokens.")
        elif self._last_compaction_block:
            self.store.delta(turn, f"⚠️ A compactação foi bloqueada por um hook: {self._last_compaction_block}")
        else:
            self.store.delta(turn, "Ainda não há histórico suficiente para compactar.")
        self._life(turn, "completed")
        self.store.finish(turn)
        self._settle_quality(turn, "completed")
        return AgenticRunResult("completed", 0, 0, None if compacted else "NOTHING_TO_COMPACT")

    def _pause_for_safety(self, turn: dict[str, object], iteration: int, actions: int) -> AgenticRunResult:
        """The safety ceiling ends the turn the way a question does: the person decides whether it goes on."""
        limit = self.limits.safety_max_tool_rounds
        self.store.delta(
            turn,
            f"\n\n⏸ Pausei depois de {limit} rodadas de ferramentas (limite de segurança `safetyMaxToolRounds`). "
            "Responda \"continuar\" para eu seguir de onde parei.",
        )
        self._life(turn, "waiting_user")
        self._emit(HookEvent.STOP, stop_hook_active=False, stop_reason="safety_max_tool_rounds", last_assistant_message="")
        # The stored turn must read waiting_user (only that code maps to it in the store); the
        # result keeps SAFETY_TOOL_ROUNDS as the reason.
        self.store.finish(turn, code="WAITING_USER")
        self._settle_quality(turn, "waiting_user", "SAFETY_TOOL_ROUNDS")
        return AgenticRunResult("waiting_user", iteration, actions, "SAFETY_TOOL_ROUNDS")

    def _fail(self, turn: dict[str, object], code: str, iteration: int, actions: int) -> AgenticRunResult:
        self._emit(HookEvent.STOP_FAILURE, error_code=code)
        self._life(turn, "failed", code=code)
        self.store.finish(turn, failed=True, code=code)
        self._settle_quality(turn, "failed", code)
        return AgenticRunResult("failed", iteration, actions, code)

    def _cancel(self, turn: dict[str, object], iteration: int, actions: int) -> AgenticRunResult:
        cancel = getattr(self.provider, "cancel", None)
        if callable(cancel):
            try:
                cancel({"turn_id": turn.get("turn_id"), "execution_id": turn.get("execution_id"), "reason": "turn_cancelled"})
            except Exception:
                pass
        self._life(turn, "cancelled", code="TURN_CANCELLED")
        self.store.finish(turn, failed=True, code="TURN_CANCELLED")
        self._settle_quality(turn, "cancelled", "TURN_CANCELLED")
        return AgenticRunResult("cancelled", iteration, actions, "TURN_CANCELLED")

    @staticmethod
    def _redacted_result(result: Mapping[str, object]) -> str:
        safe = {"status": result.get("status"), "summary": result.get("summary"), "result_ref": result.get("result_ref"), "error_code": result.get("error_code")}
        return str(safe)

    @classmethod
    def _assistant_tool_message(
        cls,
        turn: Mapping[str, object],
        text_parts: list[str],
        calls: Mapping[str, Mapping[str, str]],
        thinking_parts: list[str] | None = None,
        *,
        thinking_signature: str | None = None,
        redacted_thinking: list[str] | None = None,
    ) -> dict[str, object]:
        provider = str(turn.get("provider", "")).lower()
        if provider == "anthropic":
            blocks: list[dict[str, object]] = []
            # With extended thinking on, the signed thinking block must precede the tool calls it led to.
            if thinking_signature:
                blocks.append({"type": "thinking", "thinking": "".join(thinking_parts or []), "signature": thinking_signature})
            for data in redacted_thinking or ():
                blocks.append({"type": "redacted_thinking", "data": data})
            if text_parts:
                blocks.append({"type": "text", "text": "".join(text_parts)})
            for call in calls.values():
                try:
                    arguments = json.loads(call["arguments"])
                except (TypeError, json.JSONDecodeError):
                    arguments = {}
                blocks.append({"type": "tool_use", "id": call["id"], "name": call["name"], "input": arguments})
            return {"role": "assistant", "content": blocks}
        message: dict[str, object] = {
            "role": "assistant",
            "content": "".join(text_parts) or None,
            "tool_calls": [
                {"id": call["id"], "type": "function", "function": {"name": call["name"], "arguments": call["arguments"]}}
                for call in calls.values()
            ],
        }
        if provider == "ollama" and thinking_parts:
            message["thinking"] = "".join(thinking_parts)
        return message

    @classmethod
    def _tool_result_messages(cls, turn: Mapping[str, object], result: Mapping[str, object]) -> list[dict[str, object]]:
        """The tool's result, plus a user message when it carried images.

        Only Anthropic accepts an image inside a tool result, so the image is
        appended as an ordinary user message instead: that is understood by
        every provider this runtime speaks to.
        """
        # A toolset result carries the content the model asked for; the policy
        # path carries only a summary, which is the whole point of that path.
        content = str(result["content"]) if "content" in result else cls._redacted_result(result)
        if str(turn.get("provider", "")).lower() == "anthropic":
            messages: list[dict[str, object]] = [{"role": "user", "content": [{"type": "tool_result", "tool_use_id": str(result.get("id", "")), "content": content}]}]
        else:
            messages = [{"role": "tool", "tool_call_id": str(result.get("id", "")), "content": content}]
        images = [dict(item) for item in (result.get("images") or ()) if isinstance(item, Mapping)]
        if images:
            messages.append({"role": "user", "_tool_image_result": True, "content": [{"type": "text", "text": cls._image_caption(result, content)}, *images]})
        return messages

    @staticmethod
    def _image_caption(result: Mapping[str, object], content: str) -> str:
        """A browser screenshot is not "a file the user requested"; say what it actually is."""
        if str(result.get("name") or "") not in BROWSER_TOOL_NAMES:
            return "Conteúdo visual do arquivo solicitado:"
        first_line = content.split("\n", 1)[0].strip()
        return f"Captura da página atual ({first_line}):" if first_line else "Captura da página atual:"

    @staticmethod
    def _coerce(event: object) -> NormalizedStreamItem:
        if isinstance(event, NormalizedStreamItem):
            return event
        if isinstance(event, Mapping):
            kind_val = str(event.get("kind") or event.get("type") or "").lower()
            if kind_val == "keepalive":
                return NormalizedStreamItem(StreamKind.KEEPALIVE, int(event.get("sequence") or 1))
        event_type = type(event).__name__
        if event_type == "ContentDelta":
            return NormalizedStreamItem(StreamKind.TEXT, int(getattr(event, "sequence", 1)), text=str(getattr(event, "delta", "")))
        if event_type == "ToolCallDelta":
            delta = getattr(event, "delta", "")
            arguments = delta if isinstance(delta, str) else str(getattr(delta, "arguments", delta or ""))
            return NormalizedStreamItem(StreamKind.TOOL_CALL, int(getattr(event, "sequence", 1)), tool_call_id=str(getattr(event, "tool_call_id", "")), arguments_delta=arguments)
        if event_type == "UsageUpdated":
            cost = getattr(getattr(event, "cost", None), "amount", None)
            return NormalizedStreamItem(StreamKind.USAGE, int(getattr(event, "sequence", 1)), usage=getattr(event, "usage", None), cost=cost)
        if event_type == "StreamCompleted":
            outcome = getattr(event, "outcome", None)
            reason = "tool_calls" if type(outcome).__name__ == "ToolCallsRequested" else "stop"
            return NormalizedStreamItem(StreamKind.FINISH, int(getattr(event, "sequence", 1)), finish_reason=reason)
        if event_type == "StreamFailed":
            return NormalizedStreamItem(StreamKind.ERROR, int(getattr(event, "sequence", 1)), error=getattr(event, "error", None))
        if event_type == "StreamCancelled":
            return NormalizedStreamItem(StreamKind.ERROR, int(getattr(event, "sequence", 1)))
        kind = getattr(event, "delta", None)
        if kind is not None:
            return NormalizedStreamItem(StreamKind.TEXT, int(getattr(event, "sequence", 1)), text=str(kind))
        usage = getattr(event, "usage", None)
        if usage is not None:
            return NormalizedStreamItem(StreamKind.USAGE, int(getattr(event, "sequence", 1)), usage=usage)
        raise ValueError("provider returned an unsupported stream event")


__all__ = ["AgenticLimits", "AgenticRunResult", "AgenticTurnRuntime"]
