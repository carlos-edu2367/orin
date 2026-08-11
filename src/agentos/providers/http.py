"""HTTP adapters for the configured model providers.

The module deliberately keeps provider payloads, response IDs, headers and
credentials at this edge.  Public callers only receive RFC 501 types.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from threading import RLock
from typing import Any, Iterator
from uuid import uuid4
import json

import httpx

from agentos.execution.models import CancellationReason

from .models import (
    AccountingFinality,
    AwaitProviderTerminal,
    CancelAccepted,
    CancelProviderInvocation,
    CancelProviderResult,
    ContentDelta,
    ContentRole,
    GenerationCancelled,
    GenerationFailed,
    GenerationIndeterminate,
    GenerationSucceeded,
    ModelMessage,
    ProviderCost,
    ProviderError,
    ProviderErrorCategory,
    ProviderInvocationRequest,
    ProviderInvocationSnapshot,
    ProviderRef,
    ProviderStream,
    ProviderStreamEvent,
    ProviderTerminalSnapshot,
    ProviderTerminalState,
    ProviderUsage,
    ReadProviderStream,
    RequestAcceptance,
    Retryability,
    StreamCompleted,
    StreamFailed,
    StreamOpened,
    TextPart,
    ToolCallRequest,
    ToolCallsRequested,
    ToolResultPart,
    AuthorizedProviderInvocationQuery,
)
from .provider import ProviderInvocationValidator


@dataclass(frozen=True, slots=True)
class ProviderHTTPSettings:
    provider_ref: ProviderRef
    base_url: str
    api_key: str = field(repr=False)
    model: str
    timeout_seconds: float = 60.0

    def __post_init__(self) -> None:
        if not str(self.base_url).startswith(("https://", "http://")):
            raise ValueError("provider base_url must be an HTTP URL")
        if not self.api_key.strip():
            raise ValueError("provider API key is required")
        if not self.model.strip():
            raise ValueError("provider model is required")


@dataclass(slots=True)
class _ActiveStream:
    request: ProviderInvocationRequest
    response: httpx.Response | None
    iterator: Iterator[str] | None
    events: list[ProviderStreamEvent]
    content: list[str] = field(default_factory=list)
    terminal: ProviderTerminalSnapshot | None = None


class HTTPProviderAdapter:
    """Base adapter for OpenAI-compatible and Anthropic HTTP APIs."""

    provider_kind = "openai"

    def __init__(self, settings: ProviderHTTPSettings, *, client: httpx.Client | None = None) -> None:
        self._settings = settings
        self._client = client or httpx.Client(timeout=settings.timeout_seconds)
        self._owns_client = client is None
        self._validator = ProviderInvocationValidator()
        self._streams: dict[str, _ActiveStream] = {}
        self._terminals: dict[str, ProviderTerminalSnapshot] = {}
        self._terminal_contexts: dict[str, object] = {}
        self._invocations: dict[str, ProviderInvocationSnapshot] = {}
        self._lock = RLock()

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def generate(self, request: ProviderInvocationRequest):
        rejected = self._validate(request)
        if rejected is not None:
            return rejected
        try:
            response = self._client.post(
                self._endpoint(), headers=self._headers(), json=self._payload(request, stream=False),
                timeout=request.limits.timeout.total_seconds(),
            )
        except httpx.TimeoutException:
            return self._failure(request, ProviderErrorCategory.TIMEOUT, "PROVIDER_TIMEOUT", Retryability.SAFE)
        except httpx.HTTPError:
            return self._failure(request, ProviderErrorCategory.CONNECTION, "PROVIDER_CONNECTION", Retryability.SAFE)
        if response.status_code >= 400:
            return self._failure_from_response(request, response)
        try:
            outcome = self._outcome(request, response.json())
        except (TypeError, ValueError, KeyError, json.JSONDecodeError):
            outcome = self._failure(request, ProviderErrorCategory.INVALID_RESPONSE, "PROVIDER_INVALID_RESPONSE")
        self._record(request, outcome)
        return outcome

    def open_stream(self, request: ProviderInvocationRequest) -> ProviderStream:
        stream_id = f"provider-stream:{uuid4()}"
        opened_at = datetime.now(UTC)
        opened = StreamOpened(stream_id, 1)
        rejected = self._validate(request)
        if rejected is not None:
            terminal = self._terminal(request, stream_id, rejected, opened_at)
            self._streams[stream_id] = _ActiveStream(request, None, None, [opened, StreamFailed(stream_id, 2, rejected.error, rejected.usage, rejected.cost)], terminal=terminal)
            return ProviderStream(stream_id, request.invocation_id, opened_at)
        try:
            response = self._client.send(self._client.build_request("POST", self._endpoint(), headers=self._headers(), json=self._payload(request, stream=True)), stream=True)
            if response.status_code >= 400:
                failed = self._failure_from_response(request, response)
                terminal = self._terminal(request, stream_id, failed, opened_at)
                self._streams[stream_id] = _ActiveStream(request, None, None, [opened, StreamFailed(stream_id, 2, failed.error, failed.usage, failed.cost)], terminal=terminal)
            else:
                self._streams[stream_id] = _ActiveStream(request, response, response.iter_lines(), [opened])
        except httpx.HTTPError:
            failed = self._failure(request, ProviderErrorCategory.CONNECTION, "PROVIDER_CONNECTION", Retryability.SAFE)
            terminal = self._terminal(request, stream_id, failed, opened_at)
            self._streams[stream_id] = _ActiveStream(request, None, None, [opened, StreamFailed(stream_id, 2, failed.error, failed.usage, failed.cost)], terminal=terminal)
        return ProviderStream(stream_id, request.invocation_id, opened_at)

    def read_stream(self, request: ReadProviderStream) -> list[ProviderStreamEvent]:
        with self._lock:
            state = self._streams.get(str(request.stream_id))
            if state is None or state.request.context != request.context:
                return []
            self._drain(state, str(request.stream_id), request.maximum_events)
            return [event for event in state.events if event.sequence > request.after_sequence][:request.maximum_events]

    def cancel(self, request: CancelProviderInvocation) -> CancelProviderResult:
        with self._lock:
            for stream_id, state in self._streams.items():
                if str(state.request.invocation_id) != str(request.invocation_id) or state.request.context != request.context:
                    continue
                if state.terminal is not None:
                    from .models import AlreadyTerminal
                    return AlreadyTerminal(state.terminal.state, state.terminal.terminal_ref)
                if state.response is not None:
                    state.response.close()
                outcome = GenerationCancelled(request.invocation_id, request.reason, ProviderUsage(), ProviderCost())
                sequence = len(state.events) + 1
                from .models import StreamCancelled
                state.events.append(StreamCancelled(stream_id, sequence, request.reason, outcome.usage, outcome.cost))
                state.terminal = self._terminal(state.request, stream_id, outcome, datetime.now(UTC))
                self._record(state.request, outcome)
                return CancelAccepted(datetime.now(UTC), state.terminal.terminal_ref)
        from .models import CancelRejected
        return CancelRejected("invocation is not active")

    def await_terminal(self, request: AwaitProviderTerminal) -> ProviderTerminalSnapshot:
        with self._lock:
            terminal = self._terminals.get(str(request.terminal_ref))
            if terminal is not None and str(terminal.invocation_id) == str(request.invocation_id) and self._terminal_contexts.get(str(request.terminal_ref)) == request.context:
                return terminal
            for stream_id, state in self._streams.items():
                if str(state.request.invocation_id) == str(request.invocation_id) and state.request.context == request.context:
                    self._drain(state, stream_id, 10_000)
                    if state.terminal is not None:
                        return state.terminal
        failed = self._failure_for_request(request.context, request.invocation_id, ProviderErrorCategory.TIMEOUT, "TERMINAL_NOT_AVAILABLE")
        return self._terminal_for_context(request.context, request.invocation_id, None, failed, datetime.now(UTC))

    def inspect(self, query: AuthorizedProviderInvocationQuery):
        snapshot = self._invocations.get(str(query.invocation_id))
        if snapshot is None or snapshot.context != query.context:
            raise LookupError("provider invocation is not available in this scope")
        return snapshot

    def _validate(self, request: ProviderInvocationRequest):
        try:
            self._validator.validate(request)
            if request.selection.primary.provider_ref != self._settings.provider_ref:
                raise ValueError("provider selection does not match adapter")
            if any(not isinstance(part, TextPart) for message in request.messages for part in message.parts):
                raise ValueError("content part requires an authorized provider-content adapter")
        except Exception as error:
            category = getattr(error, "category", ProviderErrorCategory.POLICY_REJECTED)
            return self._failure(request, category, getattr(error, "code", "PROVIDER_REQUEST_REJECTED"))
        return None

    def _endpoint(self) -> str:
        return self._settings.base_url.rstrip("/") + ("/v1/messages" if self.provider_kind == "anthropic" else "/chat/completions")

    def _headers(self) -> dict[str, str]:
        if self.provider_kind == "anthropic":
            return {"x-api-key": self._settings.api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
        return {"authorization": f"Bearer {self._settings.api_key}", "content-type": "application/json"}

    def _payload(self, request: ProviderInvocationRequest, *, stream: bool) -> dict[str, Any]:
        messages = [{"role": message.role.value.lower(), "content": "".join(part.text for part in message.parts if isinstance(part, TextPart))} for message in request.messages]
        if self.provider_kind == "anthropic":
            system = "\n".join(item["content"] for item in messages if item["role"] == "system")
            payload: dict[str, Any] = {"model": self._settings.model, "max_tokens": request.limits.maximum_output_tokens, "messages": [item for item in messages if item["role"] != "system"], "stream": stream}
            if system:
                payload["system"] = system
        else:
            payload = {"model": self._settings.model, "messages": messages, "max_tokens": request.limits.maximum_output_tokens}
            if stream:
                payload["stream"] = True
        if request.sampling.temperature is not None:
            payload["temperature"] = float(request.sampling.temperature)
        if request.tools:
            payload["tools"] = [{"type": "function", "function": {"name": tool.name, "description": tool.description, "parameters": json.loads(tool.input_schema)}} for tool in request.tools]
        return payload

    def _outcome(self, request: ProviderInvocationRequest, body: dict[str, Any]):
        usage = self._usage(body.get("usage", {}))
        cost = ProviderCost()
        if self.provider_kind == "anthropic":
            blocks = body.get("content") or []
            calls = tuple(ToolCallRequest(block["id"], block["name"], block.get("input", {})) for block in blocks if block.get("type") == "tool_use")
            text = "".join(str(block.get("text", "")) for block in blocks if block.get("type") == "text")
            if calls:
                return ToolCallsRequested(request.invocation_id, calls, (TextPart(text),) if text else (), usage, cost)
            return GenerationSucceeded(request.invocation_id, ModelMessage((TextPart(text),)), usage, cost)
        choice = (body.get("choices") or [None])[0]
        if not isinstance(choice, dict) or not isinstance(choice.get("message"), dict):
            raise ValueError("missing choice")
        message = choice["message"]
        calls = tuple(ToolCallRequest(item.get("id", f"tool-call:{index}"), item.get("function", {}).get("name", ""), self._parse_arguments(item.get("function", {}).get("arguments", {}))) for index, item in enumerate(message.get("tool_calls") or []))
        if calls:
            return ToolCallsRequested(request.invocation_id, calls, (), usage, cost)
        return GenerationSucceeded(request.invocation_id, ModelMessage((TextPart(str(message.get("content") or "")),)), usage, cost)

    @staticmethod
    def _parse_arguments(value: Any) -> Any:
        if isinstance(value, str):
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return {"unparsed": value}
        return value

    @staticmethod
    def _usage(value: Any) -> ProviderUsage:
        if not isinstance(value, dict):
            return ProviderUsage()
        return ProviderUsage(value.get("prompt_tokens", value.get("input_tokens")), value.get("completion_tokens", value.get("output_tokens")), value.get("total_tokens"), measurement="CONFIRMED")

    def _failure_from_response(self, request: ProviderInvocationRequest, response: httpx.Response):
        category = self._category_for_status(response.status_code)
        retryability = Retryability.SAFE if category in {ProviderErrorCategory.RATE_LIMITED, ProviderErrorCategory.PROVIDER_INTERNAL} else Retryability.NEVER
        return self._failure(request, category, f"PROVIDER_HTTP_{response.status_code}", retryability, RequestAcceptance.NO)

    @staticmethod
    def _category_for_status(status: int) -> ProviderErrorCategory:
        if status in {401}:
            return ProviderErrorCategory.AUTHENTICATION
        if status in {403}:
            return ProviderErrorCategory.AUTHORIZATION
        if status in {404}:
            return ProviderErrorCategory.MODEL_UNAVAILABLE
        if status in {408, 504}:
            return ProviderErrorCategory.TIMEOUT
        if status == 429:
            return ProviderErrorCategory.RATE_LIMITED
        if 400 <= status < 500:
            return ProviderErrorCategory.INVALID_REQUEST
        return ProviderErrorCategory.PROVIDER_INTERNAL

    def _failure(self, request: ProviderInvocationRequest, category: ProviderErrorCategory, code: str, retryability: Retryability = Retryability.NEVER, accepted: RequestAcceptance = RequestAcceptance.UNKNOWN):
        outcome = GenerationFailed(request.invocation_id, ProviderError(category, code, "provider operation failed", retryability, request_accepted=accepted, provider_ref=self._settings.provider_ref), ProviderUsage(), ProviderCost())
        self._record(request, outcome)
        return outcome

    def _failure_for_request(self, context, invocation_id, category, code):
        return GenerationIndeterminate(invocation_id, ProviderError(category, code, "provider terminal is unavailable", provider_ref=self._settings.provider_ref), ProviderUsage(), ProviderCost())

    def _record(self, request: ProviderInvocationRequest, outcome) -> None:
        state = ProviderTerminalState.SUCCEEDED if isinstance(outcome, (GenerationSucceeded, ToolCallsRequested)) else ProviderTerminalState.CANCELLED if isinstance(outcome, GenerationCancelled) else ProviderTerminalState.FAILED
        self._invocations[str(request.invocation_id)] = ProviderInvocationSnapshot(request.invocation_id, request.context, request.selection.selection_ref, state, outcome, outcome.usage, outcome.cost)

    def _terminal(self, request, stream_id, outcome, now):
        terminal = self._terminal_for_context(request.context, request.invocation_id, stream_id, outcome, now)
        self._terminals[str(terminal.terminal_ref)] = terminal
        self._terminal_contexts[str(terminal.terminal_ref)] = request.context
        return terminal

    def _terminal_for_context(self, context, invocation_id, stream_id, outcome, now):
        state = ProviderTerminalState.SUCCEEDED if isinstance(outcome, (GenerationSucceeded, ToolCallsRequested)) else ProviderTerminalState.CANCELLED if isinstance(outcome, GenerationCancelled) else ProviderTerminalState.FAILED
        return ProviderTerminalSnapshot(f"provider-terminal:{uuid4()}", invocation_id, stream_id, state, outcome, outcome.usage, outcome.cost, AccountingFinality.FINAL_UNAVAILABLE, now, now + timedelta(minutes=5))

    def _drain(self, state: _ActiveStream, stream_id: str, maximum_events: int) -> None:
        if state.terminal is not None or state.iterator is None:
            return
        while len(state.events) < maximum_events + 1:
            try:
                line = next(state.iterator)
            except StopIteration:
                outcome = GenerationSucceeded(state.request.invocation_id, ModelMessage((TextPart("".join(state.content)),)), ProviderUsage(), ProviderCost())
                state.events.append(StreamCompleted(stream_id, len(state.events) + 1, outcome))
                state.terminal = self._terminal(state.request, stream_id, outcome, datetime.now(UTC))
                self._record(state.request, outcome)
                return
            if not line or not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                continue
            try:
                chunk = json.loads(payload)
                delta = self._stream_delta(chunk)
            except (json.JSONDecodeError, TypeError, KeyError):
                failed = self._failure(state.request, ProviderErrorCategory.INVALID_RESPONSE, "PROVIDER_INVALID_STREAM")
                state.events.append(StreamFailed(stream_id, len(state.events) + 1, failed.error, failed.usage, failed.cost))
                state.terminal = self._terminal(state.request, stream_id, failed, datetime.now(UTC))
                return
            if delta:
                state.content.append(delta)
                state.events.append(ContentDelta(stream_id, len(state.events) + 1, delta))

    def _stream_delta(self, chunk: dict[str, Any]) -> str:
        if self.provider_kind == "anthropic":
            return str((chunk.get("delta") or {}).get("text") or "")
        choices = chunk.get("choices") or []
        return str((((choices[0] if choices else {}).get("delta") or {}).get("content")) or "")


class OpenAIHTTPAdapter(HTTPProviderAdapter):
    provider_kind = "openai"


class OpenRouterHTTPAdapter(OpenAIHTTPAdapter):
    provider_kind = "openrouter"


class AnthropicHTTPAdapter(HTTPProviderAdapter):
    provider_kind = "anthropic"


__all__ = ["ProviderHTTPSettings", "HTTPProviderAdapter", "OpenAIHTTPAdapter", "OpenRouterHTTPAdapter", "AnthropicHTTPAdapter"]
