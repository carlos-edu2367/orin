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

CLOSING_INSTRUCTION = (
    "You have reached this turn's action budget. Do not request any more tools. "
    "Answer now with what you already accomplished, state plainly what is still missing, "
    "and say what the next step would be."
)


@dataclass(frozen=True, slots=True)
class AgenticLimits:
    max_actions: int = 8
    max_iterations: int | None = 8
    deadline: timedelta = timedelta(seconds=120)
    max_provider_retries: int = 1
    max_provider_tokens: int | None = None
    max_cost: Decimal | None = None
    max_output_tokens: int = 1024
    max_context_tokens: int = 60_000

    def __post_init__(self) -> None:
        if self.max_actions < 0 or (self.max_iterations is not None and self.max_iterations < 1) or self.max_provider_retries < 0 or self.deadline <= timedelta(0):
            raise ValueError("agentic limits are invalid")
        if self.max_output_tokens < 1 or self.max_context_tokens < 1_000:
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

    def run(self, turn_id: str, *, turn: dict[str, object] | None = None) -> AgenticRunResult:
        turn = turn or self._load(turn_id)
        deadline = self.clock() + self.limits.deadline
        self._life(turn, "running")
        messages = list(self.store.history_for_turn(turn))
        action_count = 0
        self._failed_signatures: dict[str, str] = {}
        provider_retries = 0
        total_tokens = 0
        total_cost = Decimal("0")
        iterations = range(1, self.limits.max_iterations + 1) if self.limits.max_iterations is not None else count(1)
        for iteration in iterations:
            if self.cancelled(turn):
                return self._cancel(turn, iteration, action_count)
            if self.clock() >= deadline:
                return self._fail(turn, "TURN_DEADLINE_EXCEEDED", iteration, action_count)
            remaining_tokens = self.limits.max_output_tokens if self.limits.max_provider_tokens is None else self.limits.max_provider_tokens - total_tokens
            if remaining_tokens <= 0:
                return self._fail(turn, "PROVIDER_TOKEN_LIMIT", iteration, action_count)
            final_iteration = self.limits.max_iterations is not None and iteration == self.limits.max_iterations
            window = self._request_messages(messages)
            if final_iteration:
                window = [*window, {"role": "system", "content": CLOSING_INSTRUCTION}]
            request = {
                "turn_id": turn_id, "provider": str(turn.get("provider", "")), "model": str(turn.get("model_id", "")),
                "messages": window, "tools": self._tool_schemas(turn),
                "max_output_tokens": min(self.limits.max_output_tokens, remaining_tokens),
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
                    if action_count + len(calls) > self.limits.max_actions:
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
                    for result in results:
                        self._life(turn, "tool_finished", status=result["status"], result_ref=result.get("result_ref"))
                messages.append(self._assistant_tool_message(turn, text_parts, calls))
                messages.extend(self._tool_result_message(turn, result) for result in results)
                self._life(turn, "running")
                continue
            if (finish is not None or text_parts) and not (final_iteration and not text_parts):
                self._life(turn, "completed")
                self.store.finish(turn)
                return AgenticRunResult("completed", iteration, action_count, budget_exhausted=final_iteration)
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

        Dropping by message count loses the user's original request first, which
        is exactly the message the agent needs to still be working on. The first
        user message is pinned and the rest of the budget goes to the newest
        messages.
        """
        budget = self.limits.max_context_tokens
        pinned_index = next((index for index, item in enumerate(messages) if item.get("role") == "user"), None)
        pinned = messages[pinned_index] if pinned_index is not None else None
        available = budget - (self._estimated_tokens(pinned) if pinned is not None else 0)
        tail: list[dict[str, object]] = []
        for index in range(len(messages) - 1, -1, -1):
            if index == pinned_index:
                continue
            cost = self._estimated_tokens(messages[index])
            if cost > available:
                break
            available -= cost
            tail.append(messages[index])
        tail.reverse()
        omitted = len(messages) - len(tail) - (1 if pinned is not None else 0)
        window: list[dict[str, object]] = []
        if pinned is not None:
            window.append(pinned)
        if omitted > 0:
            window.append({"role": "system", "content": f"[{omitted} earlier messages omitted to stay within the context budget; re-read files or re-run searches if you need their content]"})
        window.extend(tail)
        if not self.system_prompt:
            return window
        return [{"role": "system", "content": self.system_prompt}, *window]

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
            try:
                prepared.append((call_id, name, parse_arguments(call.get("arguments") or "{}"), None))
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
                        outcomes[i] = future.result()
            else:
                outcomes[index] = self.toolset.invoke(prepared[index][1], prepared[index][2])
            index = run_end

        results: list[dict[str, object]] = []
        for index, (call_id, name, arguments, error) in enumerate(prepared):
            if error is not None:
                self._life(turn, "tool_finished", tool_name=name, invocation_id=call_id, status="failed", summary=error, error_code="INVALID_ARGUMENTS")
                results.append({"id": call_id, "name": name, "status": "failed", "content": error})
                continue
            outcome = outcomes.get(index) or self.toolset.invoke(name, arguments)
            if outcome.status == "failed" and not duplicate[index]:
                self._failed_signatures[self._signature(name, arguments)] = outcome.content
            self._life(
                turn, "tool_finished", tool_name=name, invocation_id=call_id, status=outcome.status,
                summary=outcome.summary, error_code=outcome.error_code, tool_payload=dict(outcome.payload),
            )
            results.append({"id": call_id, "name": name, "status": outcome.status, "content": outcome.content})
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
    def _assistant_tool_message(cls, turn: Mapping[str, object], text_parts: list[str], calls: Mapping[str, Mapping[str, str]]) -> dict[str, object]:
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
        return {
            "role": "assistant",
            "content": "".join(text_parts) or None,
            "tool_calls": [
                {"id": call["id"], "type": "function", "function": {"name": call["name"], "arguments": call["arguments"]}}
                for call in calls.values()
            ],
        }

    @classmethod
    def _tool_result_message(cls, turn: Mapping[str, object], result: Mapping[str, object]) -> dict[str, object]:
        # A toolset result carries the content the model asked for; the policy
        # path carries only a summary, which is the whole point of that path.
        content = str(result["content"]) if "content" in result else cls._redacted_result(result)
        if str(turn.get("provider", "")).lower() == "anthropic":
            return {"role": "user", "content": [{"type": "tool_result", "tool_use_id": str(result.get("id", "")), "content": content}]}
        return {"role": "tool", "tool_call_id": str(result.get("id", "")), "content": content}

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
