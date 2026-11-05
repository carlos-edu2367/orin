from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import json
from typing import Any, Mapping
from uuid import uuid4

from agentos.providers.claude_cli.errors import RateLimitState
from agentos.providers.models import Measurement, ProviderUsage


@dataclass(frozen=True, slots=True)
class StreamUsage(ProviderUsage):
    """Extended usage snapshot preserving Anthropic prompt cache creation and read metrics."""

    cache_read_input_tokens: int | None = None
    cache_creation_input_tokens: int | None = None


def _int_field(mapping: Mapping[str, Any], *keys: str) -> int:
    for key in keys:
        val = mapping.get(key)
        if isinstance(val, int) and not isinstance(val, bool) and val >= 0:
            return val
    return 0


@dataclass(frozen=True, slots=True)
class CliUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0

    @property
    def context_tokens(self) -> int:
        return (
            self.input_tokens
            + self.cache_read_input_tokens
            + self.cache_creation_input_tokens
        )

    def to_stream_usage(self) -> StreamUsage:
        total_in = self.context_tokens
        return StreamUsage(
            input_tokens=total_in,
            output_tokens=self.output_tokens,
            total_tokens=total_in + self.output_tokens,
            cached_input_tokens=self.cache_read_input_tokens,
            reasoning_tokens=None,
            measurement=Measurement.CONFIRMED,
            cache_read_input_tokens=self.cache_read_input_tokens,
            cache_creation_input_tokens=self.cache_creation_input_tokens,
        )

    @classmethod
    def from_raw(cls, raw: Mapping[str, Any] | None) -> CliUsage:
        if not isinstance(raw, Mapping):
            return cls()
        return cls(
            input_tokens=_int_field(raw, "input_tokens", "inputTokens"),
            output_tokens=_int_field(raw, "output_tokens", "outputTokens"),
            cache_read_input_tokens=_int_field(
                raw, "cache_read_input_tokens", "cacheReadInputTokens"
            ),
            cache_creation_input_tokens=_int_field(
                raw, "cache_creation_input_tokens", "cacheCreationInputTokens"
            ),
        )


@dataclass(frozen=True, slots=True)
class CliInit:
    session_id: str
    model: str
    api_key_source: str
    mcp_servers: Mapping[str, str]
    tools: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CliResult:
    subtype: str
    is_error: bool
    result_text: str
    stop_reason: str | None
    num_turns: int
    duration_ms: int
    session_id: str
    api_error_status: int | None
    usage: CliUsage
    model_usage: Mapping[str, CliUsage]


class CliEventKind(StrEnum):
    INIT = "init"
    TEXT_DELTA = "text_delta"
    THINKING_DELTA = "thinking_delta"
    THINKING_SIGNATURE = "thinking_signature"
    TOOL_USE_START = "tool_use_start"
    TOOL_INPUT_DELTA = "tool_input_delta"
    TOOL_USE_END = "tool_use_end"
    ASSISTANT_MESSAGE = "assistant_message"
    TOOL_RESULT = "tool_result"
    USAGE = "usage"
    RATE_LIMIT = "rate_limit"
    COMPACT_BOUNDARY = "compact_boundary"
    RESULT = "result"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class CliEvent:
    kind: CliEventKind
    text: str = ""
    index: int | None = None
    tool_use_id: str | None = None
    tool_name: str | None = None
    tool_input: Mapping[str, Any] | None = None
    message_id: str | None = None
    usage: CliUsage | None = None
    raw: Mapping[str, Any] = field(default_factory=dict)


class StreamJsonParser:
    """Line-by-line parser for `claude -p --output-format stream-json --verbose --include-partial-messages`."""

    def __init__(self) -> None:
        self.init: CliInit | None = None
        self.result: CliResult | None = None
        self.rate_limit: RateLimitState | None = None
        self._current_message_id: str | None = None
        self._seen_usage_message_ids: set[str] = set()
        self._pending_tools: dict[int, dict[str, Any]] = {}
        self._emitted_tool_end_ids: set[str] = set()
        self._msg_usage_raw: dict[str, dict[str, Any]] = {}
        self._saw_stream_text_msgs: set[str] = set()
        self._saw_stream_thinking_msgs: set[str] = set()

    def _flush_pending_tool(self, index: int, raw: Mapping[str, Any]) -> list[CliEvent]:
        entry = self._pending_tools.pop(index, None)
        if not entry:
            return []
        call_id = str(entry.get("id") or f"call_{uuid4().hex[:12]}")
        if call_id in self._emitted_tool_end_ids:
            return []
        raw_name = str(entry.get("name") or "")
        json_parts: list[str] = entry.get("json_parts") or []
        parsed_input: Mapping[str, Any] = {}
        if json_parts:
            joined = "".join(json_parts).strip()
            try:
                candidate = json.loads(joined)
                if isinstance(candidate, Mapping):
                    parsed_input = dict(candidate)
            except json.JSONDecodeError:
                parsed_input = {}
        elif isinstance(entry.get("input"), Mapping):
            parsed_input = dict(entry["input"])

        self._emitted_tool_end_ids.add(call_id)
        return [
            CliEvent(
                kind=CliEventKind.TOOL_USE_END,
                index=index,
                tool_use_id=call_id,
                tool_name=raw_name,
                tool_input=parsed_input,
                message_id=entry.get("message_id"),
                raw=raw,
            )
        ]

    def feed_line(self, line: str) -> list[CliEvent]:
        stripped = (line or "").strip()
        if not stripped:
            return []
        try:
            packet = json.loads(stripped)
        except json.JSONDecodeError:
            return []
        if not isinstance(packet, Mapping):
            return []

        pkt_type = str(packet.get("type") or "")
        events: list[CliEvent] = []

        if pkt_type == "system":
            subtype = str(packet.get("subtype") or "")
            if subtype == "init":
                mcp_map: dict[str, str] = {}
                raw_servers = packet.get("mcp_servers")
                if isinstance(raw_servers, list):
                    for srv in raw_servers:
                        if isinstance(srv, Mapping):
                            s_name = str(srv.get("name") or "").strip()
                            s_status = str(srv.get("status") or "").strip()
                            if s_name:
                                mcp_map[s_name] = s_status
                elif isinstance(raw_servers, Mapping):
                    for k, v in raw_servers.items():
                        mcp_map[str(k)] = str(v)

                raw_tools = packet.get("tools")
                tools_tuple = (
                    tuple(str(t) for t in raw_tools if isinstance(t, str))
                    if isinstance(raw_tools, list)
                    else ()
                )
                self.init = CliInit(
                    session_id=str(packet.get("session_id") or ""),
                    model=str(packet.get("model") or ""),
                    api_key_source=str(packet.get("apiKeySource") or "none").strip(),
                    mcp_servers=mcp_map,
                    tools=tools_tuple,
                )
                events.append(CliEvent(kind=CliEventKind.INIT, raw=packet))
            elif subtype == "compact_boundary":
                events.append(CliEvent(kind=CliEventKind.COMPACT_BOUNDARY, raw=packet))
            return events

        if pkt_type == "rate_limit_event":
            info = packet.get("rate_limit_info")
            if isinstance(info, Mapping):
                self.rate_limit = RateLimitState.from_info(info)
            events.append(CliEvent(kind=CliEventKind.RATE_LIMIT, raw=packet))
            return events

        if pkt_type == "error":
            err_obj = packet.get("error")
            err_msg = (
                str(err_obj.get("message") or err_obj.get("type") or "")
                if isinstance(err_obj, Mapping)
                else str(err_obj or packet.get("message") or "Claude CLI error")
            )
            events.append(
                CliEvent(
                    kind=CliEventKind.ERROR,
                    text=err_msg or "Claude CLI error",
                    raw=packet,
                )
            )
            return events

        if pkt_type == "stream_event":
            event = packet.get("event")
            if not isinstance(event, Mapping):
                return events
            ev_type = str(event.get("type") or "")
            if ev_type == "error":
                err_obj = event.get("error")
                err_msg = (
                    str(err_obj.get("message") or err_obj.get("type") or "")
                    if isinstance(err_obj, Mapping)
                    else str(err_obj or "Claude stream error")
                )
                events.append(
                    CliEvent(
                        kind=CliEventKind.ERROR,
                        text=err_msg or "Claude stream error",
                        raw=packet,
                    )
                )
                return events

            if ev_type == "message_start":
                msg_obj = event.get("message")
                if isinstance(msg_obj, Mapping):
                    msg_id = str(msg_obj.get("id") or "")
                    if msg_id:
                        self._current_message_id = msg_id
                    if isinstance(msg_obj.get("usage"), Mapping):
                        bucket = self._msg_usage_raw.setdefault(
                            self._current_message_id or "_anon", {}
                        )
                        bucket.update(msg_obj["usage"])
                return events

            if ev_type == "message_delta":
                if isinstance(event.get("usage"), Mapping):
                    bucket = self._msg_usage_raw.setdefault(
                        self._current_message_id or "_anon", {}
                    )
                    bucket.update(event["usage"])
                return events

            if ev_type == "content_block_start":
                idx = int(event.get("index", 0))
                block = event.get("content_block")
                if isinstance(block, Mapping):
                    b_type = str(block.get("type") or "")
                    if b_type == "tool_use":
                        call_id = str(block.get("id") or f"call_{uuid4().hex[:12]}")
                        tool_name = str(block.get("name") or "")
                        self._pending_tools[idx] = {
                            "id": call_id,
                            "name": tool_name,
                            "input": block.get("input")
                            if isinstance(block.get("input"), Mapping)
                            else {},
                            "json_parts": [],
                            "message_id": self._current_message_id,
                        }
                        events.append(
                            CliEvent(
                                kind=CliEventKind.TOOL_USE_START,
                                index=idx,
                                tool_use_id=call_id,
                                tool_name=tool_name,
                                message_id=self._current_message_id,
                                raw=packet,
                            )
                        )
                    elif b_type == "thinking":
                        initial_thinking = str(block.get("thinking") or "")
                        if initial_thinking:
                            if self._current_message_id:
                                self._saw_stream_thinking_msgs.add(
                                    self._current_message_id
                                )
                            events.append(
                                CliEvent(
                                    kind=CliEventKind.THINKING_DELTA,
                                    text=initial_thinking,
                                    index=idx,
                                    message_id=self._current_message_id,
                                    raw=packet,
                                )
                            )
                return events

            if ev_type == "content_block_delta":
                idx = int(event.get("index", 0))
                delta = event.get("delta")
                if not isinstance(delta, Mapping):
                    return events
                d_type = str(delta.get("type") or "")
                if d_type == "thinking_delta":
                    thinking_piece = str(delta.get("thinking") or "")
                    if thinking_piece and self._current_message_id:
                        self._saw_stream_thinking_msgs.add(self._current_message_id)
                    events.append(
                        CliEvent(
                            kind=CliEventKind.THINKING_DELTA,
                            text=thinking_piece,
                            index=idx,
                            message_id=self._current_message_id,
                            raw=packet,
                        )
                    )
                elif d_type == "signature_delta":
                    sig_piece = str(delta.get("signature") or "")
                    if sig_piece:
                        events.append(
                            CliEvent(
                                kind=CliEventKind.THINKING_SIGNATURE,
                                text=sig_piece,
                                index=idx,
                                message_id=self._current_message_id,
                                raw=packet,
                            )
                        )
                elif d_type == "text_delta":
                    text_piece = str(delta.get("text") or "")
                    if text_piece:
                        if self._current_message_id:
                            self._saw_stream_text_msgs.add(self._current_message_id)
                        events.append(
                            CliEvent(
                                kind=CliEventKind.TEXT_DELTA,
                                text=text_piece,
                                index=idx,
                                message_id=self._current_message_id,
                                raw=packet,
                            )
                        )
                elif d_type == "input_json_delta":
                    partial_json = str(delta.get("partial_json") or "")
                    pending = self._pending_tools.get(idx)
                    if pending is not None and partial_json:
                        pending["json_parts"].append(partial_json)
                        events.append(
                            CliEvent(
                                kind=CliEventKind.TOOL_INPUT_DELTA,
                                text=partial_json,
                                index=idx,
                                tool_use_id=pending.get("id"),
                                tool_name=pending.get("name"),
                                message_id=pending.get("message_id"),
                                raw=packet,
                            )
                        )
                return events

            if ev_type == "content_block_stop":
                idx = int(event.get("index", 0))
                if idx in self._pending_tools:
                    events.extend(self._flush_pending_tool(idx, packet))
                return events

            return events

        if pkt_type == "assistant":
            for pending_idx in list(self._pending_tools.keys()):
                events.extend(self._flush_pending_tool(pending_idx, packet))
            msg = packet.get("message")
            if not isinstance(msg, Mapping):
                return events
            msg_id = str(msg.get("id") or self._current_message_id or f"msg_{uuid4().hex[:8]}")
            self._current_message_id = msg_id
            bucket = self._msg_usage_raw.setdefault(msg_id, {})
            if isinstance(msg.get("usage"), Mapping):
                bucket.update(msg["usage"])
            cli_usage = CliUsage.from_raw(bucket)

            content_list = msg.get("content")
            if isinstance(content_list, list):
                for block in content_list:
                    if not isinstance(block, Mapping):
                        continue
                    b_type = str(block.get("type") or "")
                    if (
                        b_type == "thinking"
                        and msg_id not in self._saw_stream_thinking_msgs
                    ):
                        thinking_text = str(block.get("thinking") or "")
                        if thinking_text:
                            self._saw_stream_thinking_msgs.add(msg_id)
                            events.append(
                                CliEvent(
                                    kind=CliEventKind.THINKING_DELTA,
                                    text=thinking_text,
                                    message_id=msg_id,
                                    raw=packet,
                                )
                            )
                    elif b_type == "text" and msg_id not in self._saw_stream_text_msgs:
                        raw_text = str(block.get("text") or "")
                        if raw_text:
                            self._saw_stream_text_msgs.add(msg_id)
                            events.append(
                                CliEvent(
                                    kind=CliEventKind.TEXT_DELTA,
                                    text=raw_text,
                                    message_id=msg_id,
                                    raw=packet,
                                )
                            )
                    elif b_type == "tool_use":
                        call_id = str(block.get("id") or f"call_{uuid4().hex[:12]}")
                        if call_id not in self._emitted_tool_end_ids:
                            tool_name = str(block.get("name") or "")
                            raw_input = (
                                dict(block["input"])
                                if isinstance(block.get("input"), Mapping)
                                else {}
                            )
                            self._emitted_tool_end_ids.add(call_id)
                            events.append(
                                CliEvent(
                                    kind=CliEventKind.TOOL_USE_START,
                                    tool_use_id=call_id,
                                    tool_name=tool_name,
                                    message_id=msg_id,
                                    raw=packet,
                                )
                            )
                            events.append(
                                CliEvent(
                                    kind=CliEventKind.TOOL_USE_END,
                                    tool_use_id=call_id,
                                    tool_name=tool_name,
                                    tool_input=raw_input,
                                    message_id=msg_id,
                                    raw=packet,
                                )
                            )

            if msg_id not in self._seen_usage_message_ids and (
                bucket or isinstance(msg.get("usage"), Mapping)
            ):
                self._seen_usage_message_ids.add(msg_id)
                events.append(
                    CliEvent(
                        kind=CliEventKind.USAGE,
                        message_id=msg_id,
                        usage=cli_usage,
                        raw=packet,
                    )
                )

            events.append(
                CliEvent(
                    kind=CliEventKind.ASSISTANT_MESSAGE,
                    message_id=msg_id,
                    usage=cli_usage,
                    raw=packet,
                )
            )
            return events

        if pkt_type == "user":
            msg = packet.get("message")
            if isinstance(msg, Mapping):
                content_list = msg.get("content")
                if isinstance(content_list, list):
                    for block in content_list:
                        if (
                            isinstance(block, Mapping)
                            and block.get("type") == "tool_result"
                        ):
                            tool_use_id = str(block.get("tool_use_id") or "")
                            raw_content = block.get("content")
                            text_parts: list[str] = []
                            if isinstance(raw_content, str):
                                text_parts.append(raw_content)
                            elif isinstance(raw_content, list):
                                for part in raw_content:
                                    if isinstance(part, Mapping) and isinstance(
                                        part.get("text"), str
                                    ):
                                        text_parts.append(part["text"])
                            events.append(
                                CliEvent(
                                    kind=CliEventKind.TOOL_RESULT,
                                    tool_use_id=tool_use_id,
                                    text="\n".join(text_parts),
                                    raw=packet,
                                )
                            )
            return events

        if pkt_type == "result":
            for pending_idx in list(self._pending_tools.keys()):
                events.extend(self._flush_pending_tool(pending_idx, packet))
            subtype = str(packet.get("subtype") or "")
            is_error = packet.get("is_error") is True or subtype.startswith("error")
            result_text = str(
                packet.get("result")
                or packet.get("error")
                or ""
            )
            stop_reason = (
                str(packet["stop_reason"])
                if packet.get("stop_reason") is not None
                else None
            )
            num_turns_raw = packet.get("num_turns")
            num_turns = (
                int(num_turns_raw)
                if isinstance(num_turns_raw, int) and not isinstance(num_turns_raw, bool)
                else 0
            )
            duration_raw = packet.get("duration_ms")
            duration_ms = (
                int(duration_raw)
                if isinstance(duration_raw, (int, float))
                and not isinstance(duration_raw, bool)
                else 0
            )
            api_status_raw = packet.get("api_error_status")
            api_error_status = (
                int(api_status_raw)
                if isinstance(api_status_raw, int)
                and not isinstance(api_status_raw, bool)
                else None
            )
            usage = CliUsage.from_raw(
                packet.get("usage")
                if isinstance(packet.get("usage"), Mapping)
                else None
            )
            model_usage: dict[str, CliUsage] = {}
            raw_model_usage = packet.get("modelUsage")
            if isinstance(raw_model_usage, Mapping):
                for m_id, m_val in raw_model_usage.items():
                    if isinstance(m_val, Mapping):
                        model_usage[str(m_id)] = CliUsage.from_raw(m_val)

            self.result = CliResult(
                subtype=subtype,
                is_error=is_error,
                result_text=result_text,
                stop_reason=stop_reason,
                num_turns=num_turns,
                duration_ms=duration_ms,
                session_id=str(packet.get("session_id") or ""),
                api_error_status=api_error_status,
                usage=usage,
                model_usage=model_usage,
            )
            events.append(
                CliEvent(
                    kind=CliEventKind.RESULT,
                    text=result_text,
                    usage=usage,
                    raw=packet,
                )
            )
            return events

        return events
