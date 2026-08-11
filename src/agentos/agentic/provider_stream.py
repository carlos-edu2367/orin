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


class HTTPProviderStreamTransport:
    """Small, injectable HTTP transport used only by the worker/runtime edge."""
    def __init__(self, *, provider: str, base_url: str, api_key: str, model: str, client: httpx.Client | None = None) -> None:
        self.provider, self.base_url, self.model = provider, base_url.rstrip("/"), model
        self._api_key = api_key
        self._client = client or httpx.Client(timeout=60)
        self._owns_client = client is None

    def __repr__(self) -> str:
        return f"HTTPProviderStreamTransport(provider={self.provider!r}, base_url={self.base_url!r}, model={self.model!r})"

    def stream(self, request: Mapping[str, object]) -> Iterator[NormalizedStreamItem]:
        messages = list(request.get("messages") or [])
        tools = list(request.get("tools") or [])
        if self.provider == "anthropic":
            system = "\n".join(str(item.get("content", "")) for item in messages if item.get("role") == "system")
            messages = [item for item in messages if item.get("role") != "system"]
            payload: dict[str, object] = {"model": self.model, "max_tokens": int(request.get("max_output_tokens") or 1024), "messages": messages, "stream": True}
            if system:
                payload["system"] = system
            if tools:
                payload["tools"] = [
                    {
                        "name": item.get("name") or item.get("function", {}).get("name"),
                        "description": item.get("description") or item.get("function", {}).get("description", ""),
                        "input_schema": item.get("input_schema") or item.get("function", {}).get("parameters", {}),
                    }
                    for item in tools
                ]
            endpoint = f"{self.base_url}/messages"
            headers = {"x-api-key": self._api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
        else:
            payload = {"model": self.model, "messages": messages, "stream": True, "max_tokens": int(request.get("max_output_tokens") or 1024)}
            if tools:
                payload["tools"] = tools
            endpoint = f"{self.base_url}/chat/completions"
            headers = {"content-type": "application/json"}
            if self._api_key:
                headers["authorization"] = f"Bearer {self._api_key}"
        with self._client.stream("POST", endpoint, headers=headers, json=payload) as response:
            response.raise_for_status()
            limit = project_rate_limit_headers(response.headers)
            has_limit = any(value is not None for value in (limit.remaining, limit.reset_after_seconds, limit.limit))
            if has_limit:
                yield NormalizedStreamItem(StreamKind.RATE_LIMIT, 1, rate_limit=limit)
            for item in normalize_sse(response.iter_lines(), provider=self.provider):
                yield replace(item, sequence=item.sequence + (1 if has_limit else 0))

    def close(self) -> None:
        if self._owns_client:
            self._client.close()


__all__ = ["HTTPProviderStreamTransport", "NormalizedStreamItem", "RateLimitInfo", "StreamKind", "normalize_sse", "project_rate_limit_headers"]
