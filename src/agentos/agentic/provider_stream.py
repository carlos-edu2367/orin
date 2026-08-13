"""Provider-facing stream normalization for the agentic runtime.

Provider payloads stay at this boundary.  The runtime only sees bounded,
typed events and never receives credentials, response headers, or raw errors.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
import json
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Iterator, Mapping

import httpx

from agentos.providers.models import FinishReason, ProviderError, ProviderErrorCategory, ProviderUsage, Retryability


# Anthropic rejects a request without max_tokens, so a turn that configures no
# output cap still needs a number for that provider alone. This is the largest
# value every current Anthropic model accepts; a model with a smaller ceiling
# needs an explicit AgenticLimits.max_output_tokens.
ANTHROPIC_REQUIRED_MAX_TOKENS = 8192


class StreamKind(StrEnum):
    TEXT = "text"
    TOOL_CALL = "tool-call"
    USAGE = "usage"
    FINISH = "finish"
    RATE_LIMIT = "rate-limit"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class RateLimitInfo:
    remaining: int | None = None
    reset_after_seconds: int | None = None
    limit: int | None = None


@dataclass(frozen=True, slots=True)
class NormalizedStreamItem:
    kind: StreamKind
    sequence: int
    text: str | None = None
    tool_call_id: str | None = None
    tool_name: str | None = None
    arguments_delta: str | None = None
    usage: ProviderUsage | None = None
    cost: Decimal | None = None
    finish_reason: FinishReason | None = None
    rate_limit: RateLimitInfo | None = None
    error: ProviderError | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", StreamKind(self.kind))
        if self.finish_reason is not None and not isinstance(self.finish_reason, FinishReason):
            object.__setattr__(self, "finish_reason", _finish(self.finish_reason))


def _anthropic_tool_choice(value: object) -> dict[str, str]:
    if isinstance(value, Mapping):
        choice_type = str(value.get("type") or "").lower()
        if choice_type == "function":
            choice_type = "tool"
        if choice_type == "required":
            choice_type = "any"
        if choice_type == "tool":
            name = value.get("name")
            function = value.get("function")
            if name is None and isinstance(function, Mapping):
                name = function.get("name")
            if name:
                return {"type": "tool", "name": str(name)}
        if choice_type in {"auto", "any", "none"}:
            return {"type": choice_type}
    choice_type = str(value).lower()
    if choice_type == "required":
        choice_type = "any"
    if choice_type not in {"auto", "any", "none"}:
        choice_type = "auto"
    return {"type": choice_type}

def _finish(value: object) -> FinishReason:
    mapping = {"stop": FinishReason.STOP, "length": FinishReason.LENGTH, "tool_calls": FinishReason.TOOL_CALLS, "tool_use": FinishReason.TOOL_CALLS, "content_filter": FinishReason.CONTENT_FILTER, "refusal": FinishReason.REFUSAL, "error": FinishReason.ERROR}
    return mapping.get(str(value).lower(), FinishReason.UNKNOWN)


def _usage(value: object) -> ProviderUsage:
    if not isinstance(value, Mapping):
        return ProviderUsage()
    def integer(*names: str) -> int | None:
        for name in names:
            raw = value.get(name)
            if isinstance(raw, int) and not isinstance(raw, bool) and raw >= 0:
                return raw
        return None
    input_tokens = integer("prompt_tokens", "input_tokens")
    output_tokens = integer("completion_tokens", "output_tokens")
    total_tokens = integer("total_tokens")
    if total_tokens is None and input_tokens is not None and output_tokens is not None:
        total_tokens = input_tokens + output_tokens
    return ProviderUsage(input_tokens, output_tokens, total_tokens, measurement="CONFIRMED")


def _cost(value: object) -> Decimal | None:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return amount if amount >= 0 else None


def _safe_error(payload: Mapping[str, Any]) -> ProviderError:
    error = payload.get("error") if isinstance(payload.get("error"), Mapping) else payload
    category = ProviderErrorCategory.UNKNOWN
    status = str(error.get("type") or error.get("error_type") or error.get("code") or "provider_error").lower()
    if "rate" in status or "quota" in status:
        category = ProviderErrorCategory.RATE_LIMITED
    elif "timeout" in status:
        category = ProviderErrorCategory.TIMEOUT
    elif "auth" in status or "permission" in status:
        category = ProviderErrorCategory.AUTHENTICATION
    retryability = Retryability.SAFE if category in {ProviderErrorCategory.RATE_LIMITED, ProviderErrorCategory.TIMEOUT, ProviderErrorCategory.CONNECTION, ProviderErrorCategory.PROVIDER_INTERNAL} else Retryability.NEVER
    candidate = str(error.get("code") or error.get("error_type") or "PROVIDER_ERROR").upper()
    safe_code = candidate if candidate.replace("_", "").isalnum() and len(candidate) <= 64 else "PROVIDER_ERROR"
    return ProviderError(category, safe_code, "provider stream failed", retryability)


def project_rate_limit_headers(headers: Mapping[str, str]) -> RateLimitInfo:
    def integer(*names: str) -> int | None:
        for name in names:
            raw = headers.get(name) or headers.get(name.lower())
            try:
                value = int(raw) if raw is not None else None
            except (TypeError, ValueError):
                value = None
            if value is not None and value >= 0:
                return value
        return None
    return RateLimitInfo(integer("x-ratelimit-remaining", "x-ratelimit-remaining-requests"), integer("x-ratelimit-reset", "retry-after"), integer("x-ratelimit-limit", "x-ratelimit-limit-requests"))


def normalize_sse(lines: Iterable[str | bytes], *, provider: str) -> Iterator[NormalizedStreamItem]:
    """Normalize OpenAI-compatible/OpenRouter and Anthropic SSE lines."""
    sequence = 0
    anthropic_tools: dict[int, tuple[str, str]] = {}
    openai_tools: dict[int, str] = {}
    for raw in lines:
        line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
        if not line.startswith("data:"):
            continue
        body = line[5:].strip()
        if not body or body == "[DONE]":
            continue
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            sequence += 1
            yield NormalizedStreamItem(StreamKind.ERROR, sequence, error=ProviderError(ProviderErrorCategory.INVALID_RESPONSE, "INVALID_SSE_JSON", "provider stream failed"))
            continue
        if not isinstance(payload, Mapping):
            continue
        if payload.get("type") == "error" or "error" in payload:
            sequence += 1
            yield NormalizedStreamItem(StreamKind.ERROR, sequence, error=_safe_error(payload))
            continue
        if provider == "anthropic":
            event_type = payload.get("type")
            if event_type == "content_block_start":
                block = payload.get("content_block") or {}
                if isinstance(block, Mapping) and block.get("type") == "tool_use":
                    index = int(payload.get("index", 0))
                    anthropic_tools[index] = (str(block.get("id") or f"tool-call:{index}"), str(block.get("name") or ""))
                continue
            if event_type == "content_block_delta":
                delta = payload.get("delta") or {}
                if not isinstance(delta, Mapping):
                    continue
                if delta.get("type") == "text_delta" and isinstance(delta.get("text"), str):
                    sequence += 1
                    yield NormalizedStreamItem(StreamKind.TEXT, sequence, text=delta["text"])
                elif delta.get("type") == "input_json_delta":
                    index = int(payload.get("index", 0))
                    call_id, name = anthropic_tools.get(index, (f"tool-call:{index}", ""))
                    sequence += 1
                    yield NormalizedStreamItem(StreamKind.TOOL_CALL, sequence, tool_call_id=call_id, tool_name=name, arguments_delta=str(delta.get("partial_json") or ""))
                continue
            if event_type in {"message_start", "message_delta"} and payload.get("usage") is not None:
                sequence += 1
                yield NormalizedStreamItem(StreamKind.USAGE, sequence, usage=_usage(payload.get("usage")), cost=_cost(payload.get("cost")))
            if event_type == "message_delta" and isinstance(payload.get("delta"), Mapping) and payload["delta"].get("stop_reason"):
                sequence += 1
                yield NormalizedStreamItem(StreamKind.FINISH, sequence, finish_reason=_finish(payload["delta"]["stop_reason"]))
            continue
        choices = payload.get("choices") or []
        choice = choices[0] if choices and isinstance(choices[0], Mapping) else {}
        delta = choice.get("delta") if isinstance(choice, Mapping) else {}
        if isinstance(delta, Mapping):
            if isinstance(delta.get("content"), str) and delta["content"]:
                sequence += 1
                yield NormalizedStreamItem(StreamKind.TEXT, sequence, text=delta["content"])
            for item in delta.get("tool_calls") or ():
                if not isinstance(item, Mapping):
                    continue
                function = item.get("function") if isinstance(item.get("function"), Mapping) else {}
                sequence += 1
                index = int(item.get("index", 0))
                call_id = str(item.get("id") or openai_tools.get(index) or f"tool-call:{index}")
                openai_tools[index] = call_id
                yield NormalizedStreamItem(StreamKind.TOOL_CALL, sequence, tool_call_id=call_id, tool_name=str(function.get("name") or "") or None, arguments_delta=str(function.get("arguments") or ""))
        if payload.get("usage") is not None:
            sequence += 1
            yield NormalizedStreamItem(StreamKind.USAGE, sequence, usage=_usage(payload["usage"]), cost=_cost(payload.get("cost")))
        if isinstance(choice, Mapping) and choice.get("finish_reason") is not None:
            sequence += 1
            yield NormalizedStreamItem(StreamKind.FINISH, sequence, finish_reason=_finish(choice["finish_reason"]))


def normalize_ndjson(lines: Iterable[str | bytes]) -> Iterator[NormalizedStreamItem]:
    """Normalize Ollama's native ``/api/chat`` stream: one JSON object per line.

    The native API is used instead of Ollama's OpenAI-compatible endpoint
    because only it accepts ``options.num_ctx``; the compatible one leaves a
    local model pinned at Ollama's 4096-token default.  The cost is this
    second normalizer, since the native stream is NDJSON rather than SSE.
    """
    sequence = 0
    tool_calls = 0
    saw_tool_call = False
    for raw in lines:
        line = (raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)).strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            sequence += 1
            yield NormalizedStreamItem(StreamKind.ERROR, sequence, error=ProviderError(ProviderErrorCategory.INVALID_RESPONSE, "INVALID_NDJSON", "provider stream failed"))
            continue
        if not isinstance(payload, Mapping):
            continue
        if payload.get("error") is not None:
            sequence += 1
            yield NormalizedStreamItem(StreamKind.ERROR, sequence, error=_safe_error(payload))
            continue
        message = payload.get("message")
        if isinstance(message, Mapping):
            content = message.get("content")
            if isinstance(content, str) and content:
                sequence += 1
                yield NormalizedStreamItem(StreamKind.TEXT, sequence, text=content)
            for item in message.get("tool_calls") or ():
                if not isinstance(item, Mapping):
                    continue
                function = item.get("function") if isinstance(item.get("function"), Mapping) else {}
                # Ollama emits no call id, and each call arrives complete in a
                # single chunk rather than as argument deltas. A stream-scoped
                # counter is what keeps two distinct calls from colliding on
                # one id and being merged into one by the runtime.
                tool_calls += 1
                saw_tool_call = True
                sequence += 1
                yield NormalizedStreamItem(
                    StreamKind.TOOL_CALL, sequence,
                    tool_call_id=f"tool-call:{tool_calls}",
                    tool_name=str(function.get("name") or "") or None,
                    arguments_delta=json.dumps(function.get("arguments") or {}),
                )
        if payload.get("done") is True:
            sequence += 1
            yield NormalizedStreamItem(StreamKind.USAGE, sequence, usage=_usage({
                "prompt_tokens": payload.get("prompt_eval_count"),
                "completion_tokens": payload.get("eval_count"),
            }))
            sequence += 1
            # Ollama reports "stop" even when the turn ends in a tool call, so
            # the observed calls decide the reason rather than done_reason.
            yield NormalizedStreamItem(StreamKind.FINISH, sequence, finish_reason=FinishReason.TOOL_CALLS if saw_tool_call else _finish(payload.get("done_reason")))


class HTTPProviderStreamTransport:
    """Small, injectable HTTP transport used only by the worker/runtime edge."""
    def __init__(self, *, provider: str, base_url: str, api_key: str, model: str, client: httpx.Client | None = None, num_ctx: int | None = None) -> None:
        self.provider, self.base_url, self.model = provider, base_url.rstrip("/"), model
        self._api_key = api_key
        self._num_ctx = num_ctx
        self._client = client or httpx.Client(timeout=60)
        self._owns_client = client is None

    def __repr__(self) -> str:
        return f"HTTPProviderStreamTransport(provider={self.provider!r}, base_url={self.base_url!r}, model={self.model!r})"

    @staticmethod
    def _with_cached_tail(messages: list[dict[str, object]]) -> list[dict[str, object]]:
        """Mark the last content block of the last message as a cache breakpoint.

        The conversation only ever grows by appending -- the same history
        (including every tool result) is resent on each loop iteration and on
        every later turn of the same conversation, and until now none of it
        was cached. Marking the tail lets Anthropic reuse the cached prefix
        for everything except what was just appended, instead of billing the
        whole growing transcript at full price on every call. Builds new
        list/dict objects rather than mutating in place: the runtime keeps and
        reuses these message dicts across iterations, and mutating one would
        leave a stray, stale cache_control marker on it once it is no longer
        the last message -- silently burning through the 4-breakpoint cap.
        """
        if not messages:
            return messages
        *head, last = messages
        content = last.get("content")
        if isinstance(content, str) and content:
            marked = {**last, "content": [{"type": "text", "text": content, "cache_control": {"type": "ephemeral"}}]}
        elif isinstance(content, list) and content:
            new_content = [*content[:-1], {**content[-1], "cache_control": {"type": "ephemeral"}}]
            marked = {**last, "content": new_content}
        else:
            return messages
        return [*head, marked]

    def _anthropic_request(self, messages: list, tools: list, tool_choice: object, requested: object) -> tuple[str, dict[str, str], dict[str, object]]:
        system_items = [item for item in messages if item.get("role") == "system"]
        messages = [item for item in messages if item.get("role") != "system"]
        messages = self._with_cached_tail(messages)
        # Anthropic requires max_tokens, so an uncapped turn still has to
        # name a number here; every other provider simply omits the field.
        payload: dict[str, object] = {"model": self.model, "max_tokens": int(requested) if requested else ANTHROPIC_REQUIRED_MAX_TOKENS, "messages": messages, "stream": True}
        if system_items:
            # The first system item is the fixed agent prompt, byte-identical
            # across every iteration of a turn (and most turns of a
            # conversation) -- that is the part worth caching. Anything after
            # it (a context-budget trim marker, the final-iteration closing
            # instruction) is call-specific and would invalidate a cache
            # entry keyed on the whole joined string, so it stays out of the
            # cached prefix as separate, uncached blocks instead.
            payload["system"] = [
                {"type": "text", "text": str(item.get("content", "")), **({"cache_control": {"type": "ephemeral"}} if index == 0 else {})}
                for index, item in enumerate(system_items)
            ]
        if tools:
            projected = [
                {
                    "name": item.get("name") or item.get("function", {}).get("name"),
                    "description": item.get("description") or item.get("function", {}).get("description", ""),
                    "input_schema": item.get("input_schema") or item.get("function", {}).get("parameters", {}),
                }
                for item in tools
            ]
            projected[-1] = {**projected[-1], "cache_control": {"type": "ephemeral"}}
            payload["tools"] = projected
            if tool_choice is not None:
                payload["tool_choice"] = _anthropic_tool_choice(tool_choice)
        headers = {"x-api-key": self._api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
        return f"{self.base_url}/messages", headers, payload

    def _openai_request(self, messages: list, tools: list, tool_choice: object, requested: object) -> tuple[str, dict[str, str], dict[str, object]]:
        payload: dict[str, object] = {
            "model": self.model, "messages": messages, "stream": True,
            # Without this an OpenAI-compatible stream omits usage entirely
            # and the turn records no tokens at all.
            "stream_options": {"include_usage": True},
        }
        # No cap configured means no cap sent: the provider then allows the
        # model its own maximum, instead of us cutting a long reply short.
        if requested:
            payload["max_tokens"] = int(requested)
        if tools:
            payload["tools"] = tools
            if tool_choice is not None:
                payload["tool_choice"] = str(tool_choice)
        headers = {"content-type": "application/json"}
        if self._api_key:
            headers["authorization"] = f"Bearer {self._api_key}"
        return f"{self.base_url}/chat/completions", headers, payload

    @staticmethod
    def _ollama_messages(messages: list[dict[str, object]]) -> list[dict[str, object]]:
        """Adapt the internal tool history to Ollama's native chat shape."""
        tool_names: dict[str, str] = {}
        converted: list[dict[str, object]] = []
        for message in messages:
            role = message.get("role")
            if role == "assistant" and isinstance(message.get("tool_calls"), list):
                calls: list[dict[str, object]] = []
                for index, item in enumerate(message["tool_calls"]):
                    if not isinstance(item, Mapping):
                        continue
                    function = item.get("function") if isinstance(item.get("function"), Mapping) else {}
                    name = str(function.get("name") or "")
                    arguments = function.get("arguments")
                    if isinstance(arguments, str):
                        try:
                            arguments = json.loads(arguments)
                        except json.JSONDecodeError:
                            arguments = {}
                    if not isinstance(arguments, Mapping):
                        arguments = {}
                    call_id = item.get("id")
                    if call_id is not None and name:
                        tool_names[str(call_id)] = name
                    calls.append({
                        "type": "function",
                        "function": {"index": index, "name": name, "arguments": dict(arguments)},
                    })
                native_message: dict[str, object] = {"role": "assistant", "tool_calls": calls}
                content = message.get("content")
                if isinstance(content, str) and content:
                    native_message["content"] = content
                converted.append(native_message)
                continue
            if role == "tool":
                call_id = str(message.get("tool_call_id") or "")
                converted.append({
                    "role": "tool",
                    "tool_name": tool_names.get(call_id) or str(message.get("tool_name") or call_id),
                    "content": str(message.get("content") or ""),
                })
                continue
            converted.append(dict(message))
        return converted

    def _ollama_request(self, messages: list, tools: list, tool_choice: object, requested: object) -> tuple[str, dict[str, str], dict[str, object]]:
        payload: dict[str, object] = {"model": self.model, "messages": self._ollama_messages(messages), "stream": True}
        options: dict[str, object] = {}
        # Ollama defaults to a 4096-token window regardless of what the model
        # can hold, which is well under this loop's system prompt plus tool
        # schemas. num_ctx is the whole reason the native API is used here.
        if self._num_ctx:
            options["num_ctx"] = int(self._num_ctx)
        if requested:
            options["num_predict"] = int(requested)
        if options:
            payload["options"] = options
        # The native API has no tool_choice. The runtime's closing "none"
        # iteration is honored by withholding the declarations entirely --
        # stricter than the hint every other provider gets.
        withheld = isinstance(tool_choice, str) and tool_choice.lower() == "none"
        if tools and not withheld:
            payload["tools"] = tools
        headers = {"content-type": "application/json"}
        if self._api_key:
            headers["authorization"] = f"Bearer {self._api_key}"
        return f"{self.base_url}/api/chat", headers, payload

    def _request_for(self, messages: list, tools: list, tool_choice: object, requested: object) -> tuple[str, dict[str, str], dict[str, object]]:
        if self.provider == "anthropic":
            return self._anthropic_request(messages, tools, tool_choice, requested)
        if self.provider == "ollama":
            return self._ollama_request(messages, tools, tool_choice, requested)
        return self._openai_request(messages, tools, tool_choice, requested)

    def stream(self, request: Mapping[str, object]) -> Iterator[NormalizedStreamItem]:
        endpoint, headers, payload = self._request_for(
            list(request.get("messages") or []),
            list(request.get("tools") or []),
            request.get("tool_choice"),
            request.get("max_output_tokens"),
        )
        with self._client.stream("POST", endpoint, headers=headers, json=payload) as response:
            response.raise_for_status()
            limit = project_rate_limit_headers(response.headers)
            has_limit = any(value is not None for value in (limit.remaining, limit.reset_after_seconds, limit.limit))
            if has_limit:
                yield NormalizedStreamItem(StreamKind.RATE_LIMIT, 1, rate_limit=limit)
            events = (
                normalize_ndjson(response.iter_lines()) if self.provider == "ollama"
                else normalize_sse(response.iter_lines(), provider=self.provider)
            )
            for item in events:
                yield replace(item, sequence=item.sequence + (1 if has_limit else 0))

    def close(self) -> None:
        if self._owns_client:
            self._client.close()


__all__ = ["ANTHROPIC_REQUIRED_MAX_TOKENS", "HTTPProviderStreamTransport", "NormalizedStreamItem", "RateLimitInfo", "StreamKind", "normalize_ndjson", "normalize_sse", "project_rate_limit_headers"]
