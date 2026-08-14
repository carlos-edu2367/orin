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

MAX_PARALLEL_TOOLS = 4
AGED_TOOL_RESULT_CHARS = 400
BROWSER_TOOL_NAMES = frozenset({
    "browse_page", "browser_observe", "browser_click", "browser_fill",
    "browser_press", "browser_select", "browser_check", "browser_screenshot",
})

# Sentinel distinguishing "no pin resolved yet" from "resolved, and there is
# no user message to pin." Used only as the default for the ``_pinned_index``
# instance attribute, which ``run`` sets once per turn.
_PIN_UNSET = object()

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

    def __post_init__(self) -> None:
        if (self.max_actions is not None and self.max_actions < 0) or (self.max_iterations is not None and self.max_iterations < 1) or self.max_provider_retries < 0 or self.deadline <= timedelta(0):
            raise ValueError("agentic limits are invalid")
        if (self.max_output_tokens is not None and self.max_output_tokens < 1) or self.max_context_tokens < 1_000:
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
        toolset: object | None = None,
        system_prompt: str | None = None,
    ) -> None:
        self.store, self.provider = store, provider
        # ``toolset`` is the agent-facing path: its results are returned to the
        # model verbatim. ``actions`` remains the policy-projected path used by
        # the tool-runtime contract tests, where results are summaries only.
        self.toolset = toolset
        self.actions = actions or ActionLoop(None, None)
        self.limits = limits or AgenticLimits()
        self.clock = clock or (lambda: datetime.now(UTC))
        self.cancelled = cancelled or (lambda _turn: False)
        self.system_prompt = system_prompt
        self._closed = False

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
        deadline = self.clock() + self.limits.deadline
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
        provider_retries = 0
        total_tokens = 0
        total_cost = Decimal("0")
        iterations = range(1, self.limits.max_iterations + 1) if self.limits.max_iterations is not None else count(1)
        for iteration in iterations:
            if self.cancelled(turn):
                return self._cancel(turn, iteration, action_count)
            if self.clock() >= deadline:
                return self._fail(turn, "TURN_DEADLINE_EXCEEDED", iteration, action_count)
            remaining_tokens = None if self.limits.max_provider_tokens is None else self.limits.max_provider_tokens - total_tokens
            if remaining_tokens is not None and remaining_tokens <= 0:
                return self._fail(turn, "PROVIDER_TOKEN_LIMIT", iteration, action_count)
            final_iteration = self.limits.max_iterations is not None and iteration == self.limits.max_iterations
            window = self._request_messages(messages)
            if final_iteration:
                window = [*window, {"role": "system", "content": CLOSING_INSTRUCTION}]
            request = {
                "turn_id": turn_id, "provider": str(turn.get("provider", "")), "model": str(turn.get("model_id", "")),
                "messages": window, "tools": self._tool_schemas(turn),
                "max_output_tokens": min([value for value in (self.limits.max_output_tokens, remaining_tokens) if value is not None], default=None),
            }
            if final_iteration:
                request["tool_choice"] = "none"
            try:
                events = self.provider.stream(request)
            except Exception:
                if provider_retries < self.limits.max_provider_retries and self.clock() < deadline:
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
            try:
                for raw_event in events:
                    if self.cancelled(turn):
                        return self._cancel(turn, iteration, action_count)
                    if self.clock() >= deadline:
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
                    elif event.kind is StreamKind.FINISH:
                        finish = event.finish_reason
                    elif event.kind is StreamKind.RATE_LIMIT:
                        rate_limited = True
                    elif event.kind is StreamKind.ERROR and event.error:
                        retryable_error = event.error.retryability.value == "SAFE"
            except Exception:
                if provider_retries < self.limits.max_provider_retries and self.clock() < deadline:
                    provider_retries += 1
                    self._life(turn, "retrying", attempt=provider_retries)
                    continue
                return self._fail(turn, "PROVIDER_STREAM_FAILED", iteration, action_count)
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
                return self._fail(turn, "PROVIDER_TOKEN_LIMIT", iteration, action_count)
            if self.limits.max_cost is not None and total_cost > self.limits.max_cost:
                return self._fail(turn, "PROVIDER_COST_LIMIT", iteration, action_count)
            if (retryable_error or rate_limited) and not text_parts and not calls and provider_retries < self.limits.max_provider_retries and self.clock() < deadline:
                provider_retries += 1
                self._life(turn, "retrying", attempt=provider_retries)
                continue
            if (retryable_error or rate_limited) and not text_parts and not calls:
                return self._fail(turn, "PROVIDER_RETRY_EXHAUSTED", iteration, action_count)
            if (calls or (finish is not None and finish.value == "TOOL_CALLS")) and not final_iteration:
                self._life(turn, "waiting_tool", count=len(calls))
                if self.toolset is not None:
                    if self.limits.max_actions is not None and action_count + len(calls) > self.limits.max_actions:
                        return self._fail(turn, "ACTION_LIMIT", iteration, action_count)
                    results = self._run_toolset(turn, list(calls.values()))
                    action_count += len(results)
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
                for result in results:
                    messages.extend(self._tool_result_messages(turn, result))
                self._age_tool_results(messages, keep_recent=len(results))
                # ``ask_user`` deliberately ends this provider run.  A worker
                # must never stay blocked while a person considers a form, and
                # the next authenticated chat message starts the follow-up turn.
                if any(bool(result.get("wait_for_user")) for result in results):
                    self._life(turn, "waiting_user")
                    self.store.finish(turn, code="WAITING_USER")
                    return AgenticRunResult("waiting_user", iteration, action_count)
                self._life(turn, "running")
                continue
            if (finish is not None or text_parts) and not (final_iteration and not text_parts):
                self._life(turn, "completed")
                self.store.finish(turn)
                return AgenticRunResult("completed", iteration, action_count, budget_exhausted=final_iteration and (iteration > 1 or bool(calls)))
        # Reaching this point means the loop ended without any provider answer
        # that carried text; a turn that produced text has already returned
        # "completed" above. This also covers a final iteration where the
        # provider ignored tool_choice="none" and returned only tool calls: the
        # model's requested tools are discarded rather than silently reported
        # as a completed turn.
        return self._fail(turn, "ITERATION_LIMIT", self.limits.max_iterations or 0, action_count)

    def _tool_schemas(self, turn: Mapping[str, object]) -> list[dict[str, object]]:
        if self.toolset is not None:
            return self.toolset.schemas()
        return self.actions.tool_schemas(turn)

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
            marker = {"role": "system", "content": f"[{omitted} earlier messages omitted to stay within the context budget; re-read files or re-run searches if you need their content]"}
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
        if not self.system_prompt:
            return window
        return [{"role": "system", "content": self.system_prompt}, *window]

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
        index = 0
        while index < len(prepared):
            if not eligible[index]:
                _, name, arguments, error = prepared[index]
                if duplicate[index]:
                    outcomes[index] = duplicate_outcome(name, arguments)
                elif error is None:
                    outcomes[index] = self.toolset.invoke(name, arguments)
                index += 1
                continue
            run_end = index
            while run_end < len(prepared) and eligible[run_end]:
                run_end += 1
            run_indexes = list(range(index, run_end))
            if len(run_indexes) > 1:
                with ThreadPoolExecutor(max_workers=min(len(run_indexes), MAX_PARALLEL_TOOLS)) as pool:
                    futures = {i: pool.submit(self.toolset.invoke, prepared[i][1], prepared[i][2]) for i in run_indexes}
                    for i, future in futures.items():
                        try:
                            outcomes[i] = future.result()
                        except Exception as error:  # noqa: BLE001 - preserve per-tool failure parity
                            message = f"{type(error).__name__}: {error}"
                            outcomes[i] = ToolOutcome("failed", f"{prepared[i][1]} falhou", message[:12_000], {}, "TOOL_FAILED")
            else:
                outcomes[index] = self.toolset.invoke(prepared[index][1], prepared[index][2])
            index = run_end

        results: list[dict[str, object]] = []
        for index, (call_id, name, arguments, error) in enumerate(prepared):
            if error is not None:
                self._life(turn, "tool_finished", tool_name=name, invocation_id=call_id, status="failed", summary=error, error_code="INVALID_ARGUMENTS", tool_arguments={})
                results.append({"id": call_id, "name": name, "status": "failed", "content": error})
                continue
            outcome = outcomes[index]
            if outcome.status == "failed" and not duplicate[index]:
                self._failed_signatures[self._signature(name, arguments)] = outcome.content
            self._life(
                turn, "tool_finished", tool_name=name, invocation_id=call_id, status=outcome.status,
                summary=outcome.summary, error_code=outcome.error_code, tool_payload=dict(outcome.payload),
                tool_arguments=dict(arguments),
            )
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

    def _fail(self, turn: dict[str, object], code: str, iteration: int, actions: int) -> AgenticRunResult:
        self._life(turn, "failed", code=code)
        self.store.finish(turn, failed=True, code=code)
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
