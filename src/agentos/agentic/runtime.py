"""Durable provider/action turn loop."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from itertools import count
import json
from typing import Callable, Mapping

from .action_loop import ActionLoop, MalformedToolCall
from .provider_stream import NormalizedStreamItem, StreamKind
from .contract import ContractError, TaskContract, parse as parse_contract, synthesize as synthesize_contract
from .quality import TurnQualityCounters
from . import phases, transcript
from .phases import Phase, PhaseController

MAX_PARALLEL_TOOLS = 4
AGED_TOOL_RESULT_CHARS = 400

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
BROWSER_TOOL_NAMES = frozenset({
    "browse_page", "browser_observe", "browser_click", "browser_fill",
    "browser_press", "browser_select", "browser_check", "browser_screenshot",
})

# Sentinel distinguishing "no pin resolved yet" from "resolved, and there is
# no user message to pin." Used only as the default for the ``_pinned_index``
# instance attribute, which ``run`` sets once per turn.
_PIN_UNSET = object()

# A compacted summary has one job: let the model keep working without going
# back to the tools. Free prose loses exactly the parts that make that
# possible -- the paths, the numbers, the decisions already taken -- so the
# summary is requested and rendered under fixed headings instead.
COMPACTION_SECTIONS = ("Arquivos tocados", "Decisões", "Dados apurados", "Pendências")
COMPACTION_HEADING = "## Contexto compactado"
# The old header ended with "use os arquivos e ferramentas para confirmar
# detalhes", which invited the model to re-run everything it had just been
# told. The summary is authoritative for what it contains.
COMPACTION_HEADER = (
    f"{COMPACTION_HEADING}\n"
    "[Este resumo é confiável para caminhos, decisões e valores registrados abaixo. "
    "Releia um arquivo apenas se esperar que ele tenha mudado desde então.]"
)
COMPACTION_INSTRUCTION = (
    "You are compacting a conversation so another agent can continue the work without "
    "repeating it. Reply with exactly these four markdown sections, in this order, even "
    "when a section is empty (write '- nenhum'):\n"
    + "\n".join(f"### {section}" for section in COMPACTION_SECTIONS)
    + "\nUnder 'Arquivos tocados' list each path with what was done to it. Under 'Decisões' "
    "list each decision with its reason. Under 'Dados apurados' list every concrete value, "
    "number or identifier established so far, verbatim. Under 'Pendências' list what is "
    "still open. Preserve exact paths and figures. Do not invent anything."
)

CLOSING_INSTRUCTION = (
    "You have reached this turn's action budget. Do not request any more tools. "
    "Answer now with what you already accomplished, state plainly what is still missing, "
    "and say what the next step would be."
)


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

    def __post_init__(self) -> None:
        if (self.max_actions is not None and self.max_actions < 0) or (self.max_iterations is not None and self.max_iterations < 1) or self.max_provider_retries < 0 or self.deadline <= timedelta(0):
            raise ValueError("agentic limits are invalid")
        if (self.max_output_tokens is not None and self.max_output_tokens < 1) or self.max_context_tokens < 1_000 or (self.context_window_tokens is not None and self.context_window_tokens < 1_000):
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
        context_reporting: bool = False,
        hook_engine=None,
        phase_controller: PhaseController | None = None,
    ) -> None:
        self.store, self.provider = store, provider
        self.hook_engine = hook_engine
        # ``toolset`` is the agent-facing path: its results are returned to the
        # model verbatim. ``actions`` remains the policy-projected path used by
        # the tool-runtime contract tests, where results are summaries only.
        self.toolset = toolset
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
        self.context_reporting = bool(context_reporting)
        self._compaction_count = 0
        self._closed = False
        self.counters = TurnQualityCounters()
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
        self._first_request_text = ""

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

    def run(self, turn_id: str, *, turn: dict[str, object] | None = None) -> AgenticRunResult:
        turn = turn or self._load(turn_id)
        self._started_at = self.clock()
        deadline = self._started_at + self.limits.deadline
        self._life(turn, "running")
        messages = list(self.store.history_for_turn(turn))
        action_count = 0
        self._failed_signatures: dict[str, str] = {}
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
        total_tokens = 0
        total_cost = Decimal("0")
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
            final_iteration = (
                (self.limits.max_iterations is not None and iteration == self.limits.max_iterations)
                or (self.phases is not None and self.phases.is_final)
            )
            tool_schemas = self._tool_schemas(turn)
            self._maybe_compact(messages, turn, tool_schemas)
            window = self._request_messages(messages)
            if self.phases is not None:
                # Last, so the stage the agent is in is the nearest thing to
                # the conversation rather than something it read paragraphs ago.
                window = [*window, {"role": "system", "content": phases.PHASE_INSTRUCTIONS[self.phases.current]}]
            if final_iteration:
                window = [*window, {"role": "system", "content": CLOSING_INSTRUCTION}]
            context = self._context_usage(messages, window, tool_schemas)
            if self.context_reporting:
                self._life(turn, "context_updated", **context)
            request = {
                "turn_id": turn_id, "provider": str(turn.get("provider", "")), "model": str(turn.get("model_id", "")),
                "messages": window, "tools": tool_schemas,
                "max_output_tokens": min([value for value in (self.limits.max_output_tokens, remaining_tokens) if value is not None], default=None),
            }
            if final_iteration:
                request["tool_choice"] = "none"
            provider_effect_id = self._effect_started(
                turn,
                kind="provider",
                invocation_ref=f"provider:{iteration}:{provider_retries + 1}",
                request_ref=f"conversation-turn:{turn_id}:provider:{iteration}",
            )
            try:
                events = self.provider.stream(request)
            except Exception:
                self._effect_finished(
                    turn, provider_effect_id, state="NOT_APPLIED", error_code="PROVIDER_STREAM_NOT_STARTED"
                )
                if not provider_yielded and provider_retries < self.limits.max_provider_retries and self.clock() < deadline:
                    provider_retries += 1
                    self._life(turn, "retrying", attempt=provider_retries)
                    continue
                return self._fail(turn, "PROVIDER_STREAM_FAILED", iteration, action_count)
            text_parts: list[str] = []
            thinking_parts: list[str] = []
            calls: dict[str, dict[str, str]] = {}
            input_tokens: int | None = None
            output_tokens: int | None = None
            reported_total_tokens: int | None = None
            finish = None
            retryable_error = False
            rate_limited = False
            provider_yielded = False
            try:
                for raw_event in events:
                    provider_yielded = True
                    if self.cancelled(turn):
                        self._effect_finished(turn, provider_effect_id, state="UNKNOWN", error_code="PROVIDER_CANCELLED_DURING_EFFECT")
                        return self._cancel(turn, iteration, action_count)
                    if self.clock() >= deadline:
                        self._effect_finished(turn, provider_effect_id, state="UNKNOWN", error_code="PROVIDER_DEADLINE_DURING_EFFECT")
                        return self._fail(turn, "TURN_DEADLINE_EXCEEDED", iteration, action_count)
                    event = self._coerce(raw_event)
                    if event.thinking:
                        thinking_parts.append(event.thinking)
                    if event.kind is StreamKind.TEXT and event.text:
                        text_parts.append(event.text)
                        self.store.delta(turn, event.text)
                    elif event.kind is StreamKind.TOOL_CALL and event.tool_call_id:
                        current = calls.setdefault(event.tool_call_id, {"id": event.tool_call_id, "name": event.tool_name or "", "arguments": ""})
                        if event.tool_name:
                            current["name"] = event.tool_name
                        current["arguments"] += event.arguments_delta or ""
                    elif event.kind is StreamKind.USAGE:
                        total_cost += event.cost or Decimal("0")
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
                        finish = event.finish_reason
                    elif event.kind is StreamKind.RATE_LIMIT:
                        rate_limited = True
                    elif event.kind is StreamKind.ERROR and event.error:
                        retryable_error = event.error.retryability.value == "SAFE"
            except Exception:
                # Once a stream yielded anything the request may have reached
                # the provider and charged or produced a result.  A restart
                # must reconcile it rather than issuing a duplicate request.
                self._effect_finished(
                    turn, provider_effect_id,
                    state="UNKNOWN" if provider_yielded else "NOT_APPLIED",
                    error_code="PROVIDER_STREAM_INTERRUPTED",
                )
                if provider_retries < self.limits.max_provider_retries and self.clock() < deadline:
                    provider_retries += 1
                    self._life(turn, "retrying", attempt=provider_retries)
                    continue
                return self._fail(turn, "PROVIDER_STREAM_FAILED", iteration, action_count)
            # One provider call is over; fold its usage into the turn total
            # before the next call starts reporting its own.
            self.counters.settle_provider_call()
            provider_total_tokens = reported_total_tokens
            if provider_total_tokens is None and input_tokens is not None and output_tokens is not None:
                provider_total_tokens = input_tokens + output_tokens
            usage_is_plausible = input_tokens is None or input_tokens <= self._maximum_plausible_input_tokens(request)
            if usage_is_plausible:
                total_tokens += provider_total_tokens or 0
                reporter = getattr(self.store, "record_usage", None)
                if callable(reporter) and any(value is not None for value in (input_tokens, output_tokens, provider_total_tokens)):
                    reporter(turn, input_tokens=input_tokens, output_tokens=output_tokens, total_tokens=provider_total_tokens)
            if self.limits.max_provider_tokens is not None and total_tokens > self.limits.max_provider_tokens:
                self._effect_finished(turn, provider_effect_id, state="APPLIED", error_code="PROVIDER_TOKEN_LIMIT")
                return self._fail(turn, "PROVIDER_TOKEN_LIMIT", iteration, action_count)
            if self.limits.max_cost is not None and total_cost > self.limits.max_cost:
                self._effect_finished(turn, provider_effect_id, state="APPLIED", error_code="PROVIDER_COST_LIMIT")
                return self._fail(turn, "PROVIDER_COST_LIMIT", iteration, action_count)
            if (retryable_error or rate_limited) and not text_parts and not calls and provider_retries < self.limits.max_provider_retries and self.clock() < deadline:
                self._effect_finished(turn, provider_effect_id, state="NOT_APPLIED", error_code="PROVIDER_RETRY")
                provider_retries += 1
                self._life(turn, "retrying", attempt=provider_retries)
                continue
            if (retryable_error or rate_limited) and not text_parts and not calls:
                self._effect_finished(turn, provider_effect_id, state="NOT_APPLIED", error_code="PROVIDER_RETRY_EXHAUSTED")
                return self._fail(turn, "PROVIDER_RETRY_EXHAUSTED", iteration, action_count)
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
                    action_count += len(results)
                    self._advance_phase(turn, results)
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
                messages.append(self._assistant_tool_message(turn, text_parts, calls, thinking_parts))
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
                # Shrinking an already-sent result rewrites the prefix, which
                # is exactly what a prompt cache cannot survive. Where the
                # provider has a cache, this is deferred to compaction, whose
                # rewrite is unavoidable anyway; where it has none, shrinking
                # every iteration is still the cheapest thing to do.
                if not self._prefix_caching(turn):
                    self._age_tool_results(messages, keep_recent=len(results))
                # ``ask_user`` deliberately ends this provider run.  A worker
                # must never stay blocked while a person considers a form, and
                # the next authenticated chat message starts the follow-up turn.
                if any(bool(result.get("wait_for_user")) for result in results):
                    self._life(turn, "waiting_user")
                    self.store.finish(turn, code="WAITING_USER")
                    self._settle_quality(turn, "waiting_user")
                    return AgenticRunResult("waiting_user", iteration, action_count)
                if self.reconciliation_required(turn):
                    self._settle_quality(turn, "reconciliation_required", "EFFECT_RECONCILIATION_REQUIRED")
                    return AgenticRunResult("reconciliation_required", iteration, action_count, "EFFECT_RECONCILIATION_REQUIRED")
                self._life(turn, "running")
                continue
            if (finish is not None or text_parts) and not (final_iteration and not text_parts):
                self._effect_finished(
                    turn, provider_effect_id, state="APPLIED",
                    result_ref=f"conversation-message:{turn['assistant_message_id']}",
                    private_result={"outcome": "final"},
                )
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
            return self.toolset.schemas()
        toolkits = self.contract.toolkits if self.contract is not None else None
        allowed = phases.tools_for(self.phases.current, toolkits)
        if not allowed:
            return []
        try:
            return self.toolset.schemas(allowed, phases.kinds_for(toolkits))
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
        used = system + history + input_tokens + tools + skills + mcps
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

    def _maybe_compact(self, messages: list[dict[str, object]], turn: Mapping[str, object], tool_schemas: list[dict[str, object]]) -> None:
        """Summarize old exchanges before ordinary trimming becomes lossy."""
        if self._compaction_count >= 8 or len(messages) < CONTEXT_COMPACTION_KEEP_UNITS + 2:
            return
        estimate = self._estimated_tokens({"messages": messages, "tools": tool_schemas})
        limit = self.limits.max_context_tokens
        if estimate < int(limit * CONTEXT_COMPACTION_THRESHOLD):
            return
        units = self._group_tool_units(messages)
        pinned = getattr(self, "_pinned_index", _PIN_UNSET)
        eligible = [unit for unit in units if pinned is _PIN_UNSET or pinned not in unit]
        if len(eligible) <= CONTEXT_COMPACTION_KEEP_UNITS:
            return
        compact_units = eligible[:-CONTEXT_COMPACTION_KEEP_UNITS]
        compact_indices = {index for unit in compact_units for index in unit}
        if not compact_indices:
            return
        if self.context_reporting:
            self._life(turn, "context_compacting", removed_messages=len(compact_indices), compaction_count=self._compaction_count + 1)
        source = self._compact_source(messages, compact_indices)
        summary = self._request_compaction_summary(source, turn)
        if not self._has_sections(summary):
            summary = self._fallback_compaction_summary(messages, compact_indices)
        insert_at = min(compact_indices)
        pinned_item = messages[pinned] if isinstance(pinned, int) and 0 <= pinned < len(messages) else None
        replacement = {"role": "system", "content": f"{COMPACTION_HEADER}\n{summary[:8_000]}"}
        retained = [item for index, item in enumerate(messages) if index not in compact_indices]
        original_to_retained = [(index, item) for index, item in enumerate(messages) if index not in compact_indices]
        position = next((offset for offset, (index, _item) in enumerate(original_to_retained) if index > insert_at), len(retained))
        retained.insert(position, replacement)
        messages[:] = retained
        # This rewrite already invalidates the cache, so it is the one place
        # where shrinking surviving results is free. Doing it here instead of
        # every iteration is what keeps the prefix append-only in between.
        self._age_tool_results(messages, keep_recent=CONTEXT_COMPACTION_KEEP_UNITS)
        if pinned_item is not None:
            self._pinned_index = next((index for index, item in enumerate(messages) if item is pinned_item), None)
        self._compaction_count += 1
        if self.context_reporting:
            self._life(turn, "context_compacted", removed_messages=len(compact_indices), summary_tokens=self._estimated_tokens(replacement), compaction_count=self._compaction_count)
        self._hooks(turn, "PostCompact", compaction_count=self._compaction_count)

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

    def _request_compaction_summary(self, source: str, turn: Mapping[str, object]) -> str:
        try:
            events = self.provider.stream({
                "turn_id": str(turn.get("turn_id") or "") + ":context-compaction",
                "provider": str(turn.get("provider") or ""),
                "model": str(turn.get("model_id") or ""),
                "messages": [{"role": "system", "content": COMPACTION_INSTRUCTION}, {"role": "user", "content": source}],
                "tools": [],
                "max_output_tokens": min(1_600, max(256, self.limits.max_context_tokens // 4)),
            })
            parts: list[str] = []
            for raw in events:
                event = self._coerce(raw)
                if event.kind is StreamKind.TEXT and event.text:
                    parts.append(event.text)
            return "".join(parts).strip()
        except Exception:
            return ""

    @staticmethod
    def _has_sections(summary: str) -> bool:
        """Whether a provider reply is usable as a structured summary.

        A model that ignored the format and answered in prose is worse than
        the local fallback, which at least keeps the paths and the tool names.
        """
        return bool(summary) and all(section in summary for section in COMPACTION_SECTIONS)

    @classmethod
    def _fallback_compaction_summary(cls, messages: list[dict[str, object]], indices: set[int]) -> str:
        """Build the same four sections locally when the provider cannot.

        This runs when the summarizer call fails or answers in the wrong
        shape. It cannot infer decisions, but it can name every tool whose
        result is being folded away and every path those calls mentioned --
        which is what stops the model from rediscovering them.
        """
        touched: list[str] = []
        pending: list[str] = []
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
            elif isinstance(content, str) and content.strip() and str(item.get("role")) == "user":
                pending.append(f"- {content.strip()[:200]}")
        sections = {
            COMPACTION_SECTIONS[0]: touched[-12:] or ["- nenhum"],
            COMPACTION_SECTIONS[1]: ["- não registradas: a compactação semântica não ficou disponível"],
            COMPACTION_SECTIONS[2]: ["- não registrados: a compactação semântica não ficou disponível"],
            COMPACTION_SECTIONS[3]: pending[-6:] or ["- nenhuma"],
        }
        return "\n".join(f"### {name}\n" + "\n".join(lines) for name, lines in sections.items())

    @staticmethod
    def _estimated_tokens(message: Mapping[str, object]) -> int:
        """Cheap upper-bound estimate; four characters per token is the usual rule."""
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
        if message.get("role") == "tool":
            return True
        if message.get("role") == "user":
            content = message.get("content")
            if isinstance(content, list):
                return any(isinstance(block, Mapping) and block.get("type") == "tool_result" for block in content)
        return False

    @staticmethod
    def _is_tool_result(message: Mapping[str, object]) -> bool:
        if message.get("role") == "tool":
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
    def _age_tool_results(cls, messages: list[dict[str, object]], keep_recent: int) -> None:
        """Shrink tool output the model has already had a chance to read.

        The full result is what the model needed on the iteration right after
        the call. Re-sending it on every later iteration is pure cost, so older
        results keep only their head plus a pointer back to the tool.
        """
        indexes = [index for index, message in enumerate(messages) if cls._is_tool_result(message)]
        for index in indexes[: max(0, len(indexes) - max(0, int(keep_recent)))]:
            message = messages[index]
            content = message.get("content")
            if isinstance(content, str):
                message["content"] = cls._compress(content)
            elif isinstance(content, list):
                message["content"] = [
                    {**block, "content": cls._compress(str(block.get("content", "")))}
                    if isinstance(block, Mapping) and block.get("type") == "tool_result" else block
                    for block in content
                ]

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
            self._life(turn, "tool_started", tool_name=name, invocation_id=call_id)
            argument_names = getattr(self.toolset, "argument_names", None)
            try:
                prepared.append((call_id, name, parse_arguments(call.get("arguments") or "{}", argument_names(name) if callable(argument_names) else None), None))
            except AgentToolError as error:
                prepared.append((call_id, name, None, str(error)))

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
        eligible = [
            error is None and not duplicate[i] and callable(is_read_only) and is_read_only(name)
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

        def invoke(index: int) -> object:
            call_id, name, arguments, _ = prepared[index]
            assert arguments is not None
            effect_id = self._effect_started(
                turn,
                kind="tool",
                invocation_ref=f"tool:{call_id}",
                request_ref=f"conversation-turn:{turn['turn_id']}:tool:{call_id}",
            )
            try:
                outcome = self.toolset.invoke(name, arguments)
            except Exception:
                self._effect_finished(turn, effect_id, state="UNKNOWN", error_code="TOOL_INTERRUPTED")
                raise
            state = "APPLIED" if outcome.status == "succeeded" else "NOT_APPLIED"
            # A write-capable tool can fail after an external system accepted
            # part of the work.  Its adapter did not provide a receipt, so it
            # is intentionally held for reconciliation instead of retried.
            if outcome.status != "succeeded" and not (callable(is_read_only) and is_read_only(name)):
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
            if not eligible[index]:
                _, name, arguments, error = prepared[index]
                if duplicate[index]:
                    outcomes[index] = duplicate_outcome(name, arguments)
                elif error is None:
                    outcomes[index] = invoke(index)
                index += 1
                continue
            run_end = index
            while run_end < len(prepared) and eligible[run_end]:
                run_end += 1
            run_indexes = list(range(index, run_end))
            if len(run_indexes) > 1:
                with ThreadPoolExecutor(max_workers=min(len(run_indexes), MAX_PARALLEL_TOOLS)) as pool:
                    futures = {i: pool.submit(invoke, i) for i in run_indexes}
                    for i, future in futures.items():
                        try:
                            outcomes[i] = future.result()
                        except Exception as error:  # noqa: BLE001 - preserve per-tool failure parity
                            message = f"{type(error).__name__}: {error}"
                            outcomes[i] = ToolOutcome("failed", f"{prepared[i][1]} falhou", message[:12_000], {}, "TOOL_FAILED")
            else:
                outcomes[index] = invoke(index)
            index = run_end

        results: list[dict[str, object]] = []
        for index, (call_id, name, arguments, error) in enumerate(prepared):
            if error is not None:
                self.counters.note_call(name, {}, "failed")
                self._life(turn, "tool_finished", tool_name=name, invocation_id=call_id, status="failed", summary=error, error_code="INVALID_ARGUMENTS", tool_arguments={})
                results.append({"id": call_id, "name": name, "status": "failed", "content": error})
                continue
            outcome = outcomes[index]
            self.counters.note_call(name, arguments, outcome.status)
            self._absorb_contract(outcome)
            if outcome.status == "failed" and not duplicate[index]:
                self._failed_signatures[self._signature(name, arguments)] = outcome.content
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
            })
        return results

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

        Two things advance a phase: the agent committed to a contract, or the
        phase ran out of budget. Running out never fails the turn -- it hands
        what exists to the next stage, which is the difference between an
        agent that stops usefully and one that stops at ITERATION_LIMIT with
        nothing to show.
        """
        if self.phases is None:
            return
        previous = self.phases.current
        wrote_contract = any(
            str(result.get("name")) == "write_contract" and str(result.get("status")) == "succeeded"
            for result in results
        )
        self.phases.note_iteration(len(results))
        self.phases.observe(wrote_contract=wrote_contract)
        if self.phases.current is Phase.EXECUTE and self.contract is None:
            # PLAN ended without a usable contract. A model too weak to fill
            # the schema still has to be able to work, so the request itself
            # becomes the contract rather than the turn stalling.
            self.contract = synthesize_contract(self._first_request_text)
        if self.phases.current is not previous:
            self._life(turn, "phase_changed", phase=str(self.phases.current), previous_phase=str(previous))

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
        """Persist this turn's efficiency row exactly once, at its terminal.

        Recovery can drive a turn to a terminal state more than once, and the
        row must describe the turn rather than the number of attempts, so the
        first terminal wins. A store without the method (every in-memory test
        double) simply records nothing.
        """
        if self._quality_recorded:
            return
        recorder = getattr(self.store, "record_quality", None)
        if not callable(recorder):
            return
        self._quality_recorded = True
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

    def _fail(self, turn: dict[str, object], code: str, iteration: int, actions: int) -> AgenticRunResult:
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
    def _assistant_tool_message(cls, turn: Mapping[str, object], text_parts: list[str], calls: Mapping[str, Mapping[str, str]], thinking_parts: list[str] | None = None) -> dict[str, object]:
        provider = str(turn.get("provider", "")).lower()
        if provider == "anthropic":
            blocks: list[dict[str, object]] = []
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
            messages.append({"role": "user", "content": [{"type": "text", "text": cls._image_caption(result, content)}, *images]})
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
