"""JSONL stream translator for OpenAI Codex CLI (`codex exec --json`)."""
from __future__ import annotations

import json
from typing import Any, Mapping
from uuid import uuid4

from agentos.providers.cli_adapter import (
    CliEvent,
    CliEventKind,
    CliInit,
    CliResult,
    CliUsage,
    RateLimitState,
)

FORBIDDEN_CODEX_ITEM_KINDS: frozenset[str] = frozenset(
    {"command_execution", "file_change", "web_search"}
)

_FORBIDDEN_INSPECT_KEYS: tuple[str, ...] = (
    "type",
    "kind",
    "item_type",
    "tool",
    "tool_name",
)


def _find_nested_forbidden_kind(
    node: Any,
    depth: int = 0,
) -> tuple[str, str] | None:
    if depth > 8:
        return None
    if isinstance(node, Mapping):
        for key in _FORBIDDEN_INSPECT_KEYS:
            val = node.get(key)
            if isinstance(val, str) and val in FORBIDDEN_CODEX_ITEM_KINDS:
                raw_id = node.get("id")
                item_id = raw_id if isinstance(raw_id, str) and raw_id else val
                return val, item_id
        for val in node.values():
            if isinstance(val, (Mapping, list, tuple)):
                found = _find_nested_forbidden_kind(val, depth + 1)
                if found is not None:
                    return found
    elif isinstance(node, (list, tuple)):
        for elem in node:
            if isinstance(elem, (Mapping, list, tuple)):
                found = _find_nested_forbidden_kind(elem, depth + 1)
                if found is not None:
                    return found
    return None


def _str_field(mapping: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        val = mapping.get(key)
        if isinstance(val, str) and val:
            return val
    return ""


def _non_neg_int(mapping: Mapping[str, Any], *keys: str) -> int:
    for key in keys:
        val = mapping.get(key)
        if isinstance(val, int) and not isinstance(val, bool) and val >= 0:
            return val
    return 0


def _optional_non_neg_int(mapping: Mapping[str, Any], *keys: str) -> int | None:
    for key in keys:
        val = mapping.get(key)
        if isinstance(val, int) and not isinstance(val, bool) and val >= 0:
            return val
    return None


def _parse_codex_usage(raw: Mapping[str, Any] | None) -> CliUsage:
    if not isinstance(raw, Mapping):
        return CliUsage()
    raw_input = _non_neg_int(raw, "input_tokens", "inputTokens")
    cached_input = _non_neg_int(
        raw,
        "cached_input_tokens",
        "cachedInputTokens",
        "cache_read_input_tokens",
        "cacheReadInputTokens",
    )
    cache_write = _non_neg_int(
        raw,
        "cache_write_input_tokens",
        "cacheWriteInputTokens",
        "cache_creation_input_tokens",
        "cacheCreationInputTokens",
    )
    output_tokens = _non_neg_int(raw, "output_tokens", "outputTokens")
    reasoning_tokens = _optional_non_neg_int(
        raw,
        "reasoning_output_tokens",
        "reasoningOutputTokens",
        "reasoning_tokens",
        "reasoningTokens",
    )
    return CliUsage(
        input_tokens=max(0, raw_input - cached_input),
        output_tokens=output_tokens,
        cache_read_input_tokens=cached_input,
        cache_creation_input_tokens=cache_write,
        reasoning_tokens=reasoning_tokens,
    )


def _extract_agent_message_text(item: Mapping[str, Any]) -> str:
    raw_text = item.get("text")
    if isinstance(raw_text, str):
        return raw_text
    content = item.get("content")
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, Mapping):
                txt = block.get("text")
                if isinstance(txt, str):
                    parts.append(txt)
        return "".join(parts)
    return ""


def _extract_reasoning_text(obj: Mapping[str, Any]) -> str:
    raw_text = obj.get("text")
    if isinstance(raw_text, str) and raw_text:
        return raw_text
    summary = obj.get("summary")
    if isinstance(summary, str):
        return summary
    if isinstance(summary, list):
        parts: list[str] = []
        for block in summary:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, Mapping):
                txt = block.get("text")
                if isinstance(txt, str):
                    parts.append(txt)
        return "".join(parts)
    return ""


def _parse_tool_arguments(raw_args: Any) -> dict[str, Any]:
    if isinstance(raw_args, Mapping):
        return dict(raw_args)
    if isinstance(raw_args, str) and raw_args.strip():
        try:
            parsed = json.loads(raw_args.strip())
            if isinstance(parsed, Mapping):
                return dict(parsed)
        except json.JSONDecodeError:
            pass
    return {}


def _extract_tool_result_text(item: Mapping[str, Any]) -> str:
    res = item.get("result")
    if isinstance(res, Mapping):
        content = res.get("content")
        if isinstance(content, list):
            parts: list[str] = []
            for block in content:
                if isinstance(block, str):
                    parts.append(block)
                elif isinstance(block, Mapping):
                    txt = block.get("text")
                    if isinstance(txt, str):
                        parts.append(txt)
            if parts:
                return "\n".join(parts)
        elif isinstance(content, str):
            return content
        txt = res.get("text")
        if isinstance(txt, str):
            return txt
    elif isinstance(res, str):
        return res

    err = item.get("error")
    if isinstance(err, Mapping):
        msg = _str_field(err, "message", "text", "error")
        if msg:
            return msg
    elif isinstance(err, str):
        return err
    return ""


def _extract_error_message(packet: Mapping[str, Any]) -> str:
    err = packet.get("error")
    if isinstance(err, Mapping):
        msg = _str_field(err, "message", "text", "type")
        if msg.strip():
            return msg
    elif isinstance(err, str) and err.strip():
        return err
    top_msg = _str_field(packet, "message")
    if top_msg.strip():
        return top_msg
    return "Codex CLI error"


def _compute_new_suffix(
    store: dict[str, str], item_id: str, full_text: str
) -> str:
    if not full_text:
        return ""
    prev = store.get(item_id, "")
    if full_text == prev:
        return ""
    if full_text.startswith(prev):
        delta = full_text[len(prev) :]
        store[item_id] = full_text
        return delta
    if prev.startswith(full_text):
        return ""
    store[item_id] = full_text
    return full_text


class CodexJsonlTranslator:
    """Line-by-line JSONL stream translator for `codex exec --json`."""

    def __init__(self, *, model: str = "") -> None:
        self._model: str = model.strip() if isinstance(model, str) else ""
        self.init: CliInit | None = None
        self.result: CliResult | None = None
        self.rate_limit: RateLimitState | None = None
        self.stop_reason: str | None = None
        self._latest_usage: CliUsage = CliUsage()
        self._emitted_text_by_item: dict[str, str] = {}
        self._emitted_thinking_by_item: dict[str, str] = {}
        self._started_tool_ids: set[str] = set()
        self._completed_tool_ids: set[str] = set()
        self._turn_text_parts: list[str] = []
        self._num_turns: int = 0
        self._pending_reconnect_error: tuple[str, Mapping[str, Any]] | None = None
        self._emitted_error_result_msg: str | None = None

    def latest_usage(self) -> CliUsage:
        if self.result is not None:
            return self.result.usage
        return self._latest_usage

    def _clear_turn_items(self) -> None:
        self._emitted_text_by_item.clear()
        self._emitted_thinking_by_item.clear()
        self._started_tool_ids.clear()
        self._completed_tool_ids.clear()
        self._turn_text_parts.clear()

    def reset_result(self) -> None:
        self.result = None
        self.stop_reason = None
        self._pending_reconnect_error = None
        self._emitted_error_result_msg = None
        self._clear_turn_items()

    def flush_pending(self) -> list[CliEvent]:
        if self.result is None and self._pending_reconnect_error is not None:
            msg, pkt = self._pending_reconnect_error
            self._pending_reconnect_error = None
            return self._emit_error_result(msg, pkt)
        self._pending_reconnect_error = None
        return []

    def _session_id(self, packet: Mapping[str, Any] | None = None) -> str:
        if self.init is not None and self.init.session_id:
            return self.init.session_id
        if isinstance(packet, Mapping):
            return _str_field(packet, "thread_id", "session_id")
        return ""

    def _emit_error_result(
        self, msg: str, packet: Mapping[str, Any]
    ) -> list[CliEvent]:
        self._pending_reconnect_error = None
        self.stop_reason = "error"
        self.result = CliResult(
            subtype="error",
            is_error=True,
            result_text=msg,
            stop_reason="error",
            num_turns=max(1, self._num_turns),
            duration_ms=0,
            session_id=self._session_id(packet),
            api_error_status=None,
            usage=self._latest_usage,
            model_usage={},
        )
        if self._emitted_error_result_msg == msg:
            return []
        self._emitted_error_result_msg = msg
        return [
            CliEvent(
                kind=CliEventKind.RESULT,
                text=msg,
                result=self.result,
                usage=self.result.usage,
                raw=packet,
            )
        ]

    def feed_line(self, line: str) -> list[CliEvent]:
        if not isinstance(line, str):
            return []
        stripped = line.strip()
        if not stripped:
            return []
        try:
            packet = json.loads(stripped)
        except json.JSONDecodeError:
            return []
        if not isinstance(packet, Mapping):
            return []

        pkt_type = _str_field(packet, "type", "kind")

        forbidden_hit = _find_nested_forbidden_kind(packet)
        if forbidden_hit is not None:
            bad_kind, bad_id = forbidden_hit
            return [
                CliEvent(
                    kind=CliEventKind.TOOL_USE_START,
                    tool_use_id=bad_id,
                    tool_name=bad_kind,
                    raw=packet,
                )
            ]

        if pkt_type == "thread.started":
            thread_id = _str_field(packet, "thread_id", "session_id")
            pkt_model = _str_field(packet, "model").strip()
            resolved_model = pkt_model or self._model or "codex"
            self.init = CliInit(
                session_id=thread_id,
                model=resolved_model,
                api_key_source="none",
                mcp_servers={},
                tools=(),
            )
            return [
                CliEvent(
                    kind=CliEventKind.INIT,
                    init=self.init,
                    raw=packet,
                )
            ]

        if pkt_type == "turn.started":
            self._num_turns += 1
            self.result = None
            self.stop_reason = None
            self._emitted_error_result_msg = None
            self._clear_turn_items()
            return []

        if pkt_type == "reasoning":
            target = (
                packet["item"]
                if isinstance(packet.get("item"), Mapping)
                else packet
            )
            item_id = _str_field(target, "id") or "_anon_reasoning"
            full_thinking = _extract_reasoning_text(target)
            delta = _compute_new_suffix(
                self._emitted_thinking_by_item, item_id, full_thinking
            )
            if delta:
                return [
                    CliEvent(
                        kind=CliEventKind.THINKING_DELTA,
                        text=delta,
                        message_id=item_id,
                        raw=packet,
                    )
                ]
            return []

        if pkt_type in ("item.started", "item.updated", "item.completed"):
            item = packet.get("item")
            if not isinstance(item, Mapping):
                return []
            item_type = _str_field(item, "type", "kind")
            item_id = _str_field(item, "id")

            if item_type in FORBIDDEN_CODEX_ITEM_KINDS:
                return [
                    CliEvent(
                        kind=CliEventKind.TOOL_USE_START,
                        tool_use_id=item_id or item_type,
                        tool_name=item_type,
                        raw=packet,
                    )
                ]

            if item_type == "agent_message":
                msg_key = item_id or "_anon_msg"
                full_text = _extract_agent_message_text(item)
                delta = _compute_new_suffix(
                    self._emitted_text_by_item, msg_key, full_text
                )
                events: list[CliEvent] = []
                if delta:
                    self._turn_text_parts.append(delta)
                    events.append(
                        CliEvent(
                            kind=CliEventKind.TEXT_DELTA,
                            text=delta,
                            message_id=item_id or None,
                            raw=packet,
                        )
                    )
                if pkt_type == "item.completed":
                    events.append(
                        CliEvent(
                            kind=CliEventKind.ASSISTANT_MESSAGE,
                            text=full_text,
                            message_id=item_id or None,
                            raw=packet,
                        )
                    )
                return events

            if item_type == "reasoning":
                r_key = item_id or "_anon_reasoning"
                full_thinking = _extract_reasoning_text(item)
                delta = _compute_new_suffix(
                    self._emitted_thinking_by_item, r_key, full_thinking
                )
                if delta:
                    return [
                        CliEvent(
                            kind=CliEventKind.THINKING_DELTA,
                            text=delta,
                            message_id=item_id or None,
                            raw=packet,
                        )
                    ]
                return []

            if item_type == "mcp_tool_call":
                call_id = item_id or f"mcp_call_{uuid4().hex[:8]}"
                tool_name = _str_field(item, "tool", "name")
                tool_input = _parse_tool_arguments(item.get("arguments"))
                if pkt_type == "item.started":
                    if call_id in self._started_tool_ids:
                        return []
                    self._started_tool_ids.add(call_id)
                    return [
                        CliEvent(
                            kind=CliEventKind.TOOL_USE_START,
                            tool_use_id=call_id,
                            tool_name=tool_name,
                            tool_input=tool_input,
                            raw=packet,
                        )
                    ]
                if pkt_type == "item.completed":
                    if call_id in self._completed_tool_ids:
                        return []
                    self._completed_tool_ids.add(call_id)
                    tool_events: list[CliEvent] = []
                    if call_id not in self._started_tool_ids:
                        self._started_tool_ids.add(call_id)
                        tool_events.append(
                            CliEvent(
                                kind=CliEventKind.TOOL_USE_START,
                                tool_use_id=call_id,
                                tool_name=tool_name,
                                tool_input=tool_input,
                                raw=packet,
                            )
                        )
                    tool_events.append(
                        CliEvent(
                            kind=CliEventKind.TOOL_USE_END,
                            tool_use_id=call_id,
                            tool_name=tool_name,
                            tool_input=tool_input,
                            raw=packet,
                        )
                    )
                    result_text = _extract_tool_result_text(item)
                    tool_events.append(
                        CliEvent(
                            kind=CliEventKind.TOOL_RESULT,
                            tool_use_id=call_id,
                            tool_name=tool_name,
                            text=result_text,
                            raw=packet,
                        )
                    )
                    return tool_events
                return []

            if item_type == "error":
                err_msg = _str_field(item, "message", "text").strip()
                if err_msg:
                    self._pending_reconnect_error = (err_msg, packet)
                return []

            return []

        if pkt_type == "turn.completed":
            self._pending_reconnect_error = None
            usage = _parse_codex_usage(
                packet.get("usage")
                if isinstance(packet.get("usage"), Mapping)
                else None
            )
            self._latest_usage = usage
            self.stop_reason = "end_turn"
            result_text = "".join(self._turn_text_parts)
            model_id = (
                self.init.model
                if self.init is not None and self.init.model
                else (self._model or "codex")
            )
            self.result = CliResult(
                subtype="success",
                is_error=False,
                result_text=result_text,
                stop_reason="end_turn",
                num_turns=max(1, self._num_turns),
                duration_ms=0,
                session_id=self._session_id(packet),
                api_error_status=None,
                usage=usage,
                model_usage={model_id: usage},
            )
            return [
                CliEvent(
                    kind=CliEventKind.USAGE,
                    usage=usage,
                    raw=packet,
                ),
                CliEvent(
                    kind=CliEventKind.RESULT,
                    text=result_text,
                    result=self.result,
                    usage=usage,
                    raw=packet,
                ),
            ]

        if pkt_type == "error":
            msg = _extract_error_message(packet)
            if msg.startswith("Reconnecting..."):
                self._pending_reconnect_error = (msg, packet)
                return []
            return self._emit_error_result(msg, packet)

        if pkt_type == "turn.failed":
            msg = _extract_error_message(packet)
            return self._emit_error_result(msg, packet)

        return []


__all__ = [
    "CodexJsonlTranslator",
    "FORBIDDEN_CODEX_ITEM_KINDS",
]
